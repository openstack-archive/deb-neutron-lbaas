# Copyright 2013 New Dream Network, LLC (DreamHost)
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

import uuid

from neutron.common import constants as q_const
from neutron.common import exceptions as n_exc
from neutron.common import rpc as n_rpc
from neutron.db import agents_db
from neutron.extensions import portbindings
from neutron.plugins.common import constants as np_const
from neutron.services import provider_configuration as provconf
from oslo_config import cfg
from oslo_log import log as logging
import oslo_messaging
from oslo_utils import importutils

from neutron_lbaas._i18n import _, _LW
from neutron_lbaas.db.loadbalancer import loadbalancer_db
from neutron_lbaas.extensions import lbaas_agentscheduler
from neutron_lbaas.services.loadbalancer import constants as l_const
from neutron_lbaas.services.loadbalancer.drivers import abstract_driver

LOG = logging.getLogger(__name__)

POOL_SCHEDULERS = 'pool_schedulers'

AGENT_SCHEDULER_OPTS = [
    cfg.StrOpt('loadbalancer_pool_scheduler_driver',
               default='neutron_lbaas.services.loadbalancer.agent_scheduler'
                       '.ChanceScheduler',
               help=_('Driver to use for scheduling '
                      'pool to a default loadbalancer agent')),
]

cfg.CONF.register_opts(AGENT_SCHEDULER_OPTS)


class DriverNotSpecified(n_exc.NeutronException):
    message = _("Device driver for agent should be specified "
                "in plugin driver.")


class LoadBalancerCallbacks(object):

    # history
    #   1.0 Initial version
    #   2.0 Generic API for agent based drivers
    #       - get_logical_device() handling changed;
    #       - pool_deployed() and update_status() methods added;
    target = oslo_messaging.Target(version='2.0')

    def __init__(self, plugin):
        super(LoadBalancerCallbacks, self).__init__()
        self.plugin = plugin

    def get_ready_devices(self, context, host=None):
        with context.session.begin(subtransactions=True):
            agents = self.plugin.get_lbaas_agents(context,
                                                  filters={'host': [host]})
            if not agents:
                return []
            elif len(agents) > 1:
                LOG.warning(_LW('Multiple lbaas agents found on host %s'),
                            host)
            pools = self.plugin.list_pools_on_lbaas_agent(context,
                                                          agents[0].id)
            pool_ids = [pool['id'] for pool in pools['pools']]

            qry = context.session.query(loadbalancer_db.Pool.id)
            qry = qry.filter(loadbalancer_db.Pool.id.in_(pool_ids))
            qry = qry.filter(
                loadbalancer_db.Pool.status.in_(
                    np_const.ACTIVE_PENDING_STATUSES))
            up = True  # makes pep8 and sqlalchemy happy
            qry = qry.filter(loadbalancer_db.Pool.admin_state_up == up)
            return [id for id, in qry]

    def get_logical_device(self, context, pool_id=None):
        with context.session.begin(subtransactions=True):
            qry = context.session.query(loadbalancer_db.Pool)
            qry = qry.filter_by(id=pool_id)
            pool = qry.one()
            retval = {}
            retval['pool'] = self.plugin._make_pool_dict(pool)

            if pool.vip:
                retval['vip'] = self.plugin._make_vip_dict(pool.vip)
                retval['vip']['port'] = (
                    self.plugin._core_plugin._make_port_dict(pool.vip.port)
                )
                for fixed_ip in retval['vip']['port']['fixed_ips']:
                    fixed_ip['subnet'] = (
                        self.plugin._core_plugin.get_subnet(
                            context,
                            fixed_ip['subnet_id']
                        )
                    )
            retval['members'] = [
                self.plugin._make_member_dict(m)
                for m in pool.members if (
                    m.status in np_const.ACTIVE_PENDING_STATUSES or
                    m.status == np_const.INACTIVE)
            ]
            retval['healthmonitors'] = [
                self.plugin._make_health_monitor_dict(hm.healthmonitor)
                for hm in pool.monitors
                if hm.status in np_const.ACTIVE_PENDING_STATUSES
            ]
            retval['driver'] = (
                self.plugin.drivers[pool.provider.provider_name].device_driver)

            return retval

    def pool_deployed(self, context, pool_id):
        with context.session.begin(subtransactions=True):
            qry = context.session.query(loadbalancer_db.Pool)
            qry = qry.filter_by(id=pool_id)
            pool = qry.one()

            # set all resources to active
            if pool.status in np_const.ACTIVE_PENDING_STATUSES:
                pool.status = np_const.ACTIVE

            if (pool.vip and pool.vip.status in
                    np_const.ACTIVE_PENDING_STATUSES):
                pool.vip.status = np_const.ACTIVE

            for m in pool.members:
                if m.status in np_const.ACTIVE_PENDING_STATUSES:
                    m.status = np_const.ACTIVE

            for hm in pool.monitors:
                if hm.status in np_const.ACTIVE_PENDING_STATUSES:
                    hm.status = np_const.ACTIVE

    def update_status(self, context, obj_type, obj_id, status):
        model_mapping = {
            'pool': loadbalancer_db.Pool,
            'vip': loadbalancer_db.Vip,
            'member': loadbalancer_db.Member,
            'health_monitor': loadbalancer_db.PoolMonitorAssociation
        }
        if obj_type not in model_mapping:
            raise n_exc.Invalid(_('Unknown object type: %s') % obj_type)
        try:
            if obj_type == 'health_monitor':
                self.plugin.update_pool_health_monitor(
                    context, obj_id['monitor_id'], obj_id['pool_id'], status)
            else:
                self.plugin.update_status(
                    context, model_mapping[obj_type], obj_id, status)
        except n_exc.NotFound:
            # update_status may come from agent on an object which was
            # already deleted from db with other request
            LOG.warning(_LW('Cannot update status: %(obj_type)s %(obj_id)s '
                            'not found in the DB, it was probably deleted '
                            'concurrently'),
                        {'obj_type': obj_type, 'obj_id': obj_id})

    def pool_destroyed(self, context, pool_id=None):
        """Agent confirmation hook that a pool has been destroyed.

        This method exists for subclasses to change the deletion
        behavior.
        """
        pass

    def plug_vip_port(self, context, port_id=None, host=None):
        if not port_id:
            return

        try:
            port = self.plugin._core_plugin.get_port(
                context,
                port_id
            )
        except n_exc.PortNotFound:
            LOG.debug('Unable to find port %s to plug.', port_id)
            return

        port['admin_state_up'] = True
        port['device_owner'] = 'neutron:' + np_const.LOADBALANCER
        port['device_id'] = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(host)))
        port[portbindings.HOST_ID] = host
        self.plugin._core_plugin.update_port(
            context,
            port_id,
            {'port': port}
        )

    def unplug_vip_port(self, context, port_id=None, host=None):
        if not port_id:
            return

        try:
            port = self.plugin._core_plugin.get_port(
                context,
                port_id
            )
        except n_exc.PortNotFound:
            LOG.debug('Unable to find port %s to unplug. This can occur when '
                      'the Vip has been deleted first.',
                      port_id)
            return

        port['admin_state_up'] = False
        port['device_owner'] = ''
        port['device_id'] = ''

        try:
            self.plugin._core_plugin.update_port(
                context,
                port_id,
                {'port': port}
            )

        except n_exc.PortNotFound:
            LOG.debug('Unable to find port %s to unplug.  This can occur when '
                      'the Vip has been deleted first.',
                      port_id)

    def update_pool_stats(self, context, pool_id=None, stats=None, host=None):
        self.plugin.update_pool_stats(context, pool_id, data=stats)


class LoadBalancerAgentApi(object):
    """Plugin side of plugin to agent RPC API."""

    # history
    #   1.0 Initial version
    #   1.1 Support agent_updated call
    #   2.0 Generic API for agent based drivers
    #       - modify/reload/destroy_pool methods were removed;
    #       - added methods to handle create/update/delete for every lbaas
    #       object individually;

    def __init__(self, topic):
        target = oslo_messaging.Target(topic=topic, version='2.0')
        self.client = n_rpc.get_client(target)

    def create_vip(self, context, vip, host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'create_vip', vip=vip)

    def update_vip(self, context, old_vip, vip, host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'update_vip', old_vip=old_vip, vip=vip)

    def delete_vip(self, context, vip, host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'delete_vip', vip=vip)

    def create_pool(self, context, pool, host, driver_name):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'create_pool', pool=pool, driver_name=driver_name)

    def update_pool(self, context, old_pool, pool, host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'update_pool', old_pool=old_pool, pool=pool)

    def delete_pool(self, context, pool, host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'delete_pool', pool=pool)

    def create_member(self, context, member, host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'create_member', member=member)

    def update_member(self, context, old_member, member, host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'update_member', old_member=old_member,
                   member=member)

    def delete_member(self, context, member, host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'delete_member', member=member)

    def create_pool_health_monitor(self, context, health_monitor, pool_id,
                                   host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'create_pool_health_monitor',
                   health_monitor=health_monitor, pool_id=pool_id)

    def update_pool_health_monitor(self, context, old_health_monitor,
                                   health_monitor, pool_id, host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'update_pool_health_monitor',
                   old_health_monitor=old_health_monitor,
                   health_monitor=health_monitor, pool_id=pool_id)

    def delete_pool_health_monitor(self, context, health_monitor, pool_id,
                                   host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'delete_pool_health_monitor',
                health_monitor=health_monitor, pool_id=pool_id)

    def agent_updated(self, context, admin_state_up, host):
        cctxt = self.client.prepare(server=host)
        cctxt.cast(context, 'agent_updated',
                   payload={'admin_state_up': admin_state_up})


class AgentDriverBase(abstract_driver.LoadBalancerAbstractDriver):

    # name of device driver that should be used by the agent;
    # vendor specific plugin drivers must override it;
    device_driver = None

    def __init__(self, plugin):
        if not self.device_driver:
            raise DriverNotSpecified()

        self.agent_rpc = LoadBalancerAgentApi(l_const.LOADBALANCER_AGENT)

        self.plugin = plugin
        self._set_callbacks_on_plugin()
        self.plugin.agent_notifiers.update(
            {q_const.AGENT_TYPE_LOADBALANCER: self.agent_rpc})

        pool_sched_driver = provconf.get_provider_driver_class(
            cfg.CONF.loadbalancer_pool_scheduler_driver, POOL_SCHEDULERS)
        self.pool_scheduler = importutils.import_object(pool_sched_driver)

    def _set_callbacks_on_plugin(self):
        # other agent based plugin driver might already set callbacks on plugin
        if hasattr(self.plugin, 'agent_callbacks'):
            return

        self.plugin.agent_endpoints = [
            LoadBalancerCallbacks(self.plugin),
            agents_db.AgentExtRpcCallback(self.plugin)
        ]
        self.plugin.conn = n_rpc.create_connection()
        self.plugin.conn.create_consumer(
            l_const.LOADBALANCER_PLUGIN,
            self.plugin.agent_endpoints,
            fanout=False)
        self.plugin.conn.consume_in_threads()

    def get_pool_agent(self, context, pool_id):
        agent = self.plugin.get_lbaas_agent_hosting_pool(context, pool_id)
        if not agent:
            raise lbaas_agentscheduler.NoActiveLbaasAgent(pool_id=pool_id)
        return agent['agent']

    def create_vip(self, context, vip):
        agent = self.get_pool_agent(context, vip['pool_id'])
        self.agent_rpc.create_vip(context, vip, agent['host'])

    def update_vip(self, context, old_vip, vip):
        agent = self.get_pool_agent(context, vip['pool_id'])
        if vip['status'] in np_const.ACTIVE_PENDING_STATUSES:
            self.agent_rpc.update_vip(context, old_vip, vip, agent['host'])
        else:
            self.agent_rpc.delete_vip(context, vip, agent['host'])

    def delete_vip(self, context, vip):
        self.plugin._delete_db_vip(context, vip['id'])
        agent = self.get_pool_agent(context, vip['pool_id'])
        self.agent_rpc.delete_vip(context, vip, agent['host'])

    def create_pool(self, context, pool):
        agent = self.pool_scheduler.schedule(self.plugin, context, pool,
                                             self.device_driver)
        if not agent:
            raise lbaas_agentscheduler.NoEligibleLbaasAgent(pool_id=pool['id'])
        self.agent_rpc.create_pool(context, pool, agent['host'],
                                   self.device_driver)

    def update_pool(self, context, old_pool, pool):
        agent = self.get_pool_agent(context, pool['id'])
        if pool['status'] in np_const.ACTIVE_PENDING_STATUSES:
            self.agent_rpc.update_pool(context, old_pool, pool,
                                       agent['host'])
        else:
            self.agent_rpc.delete_pool(context, pool, agent['host'])

    def delete_pool(self, context, pool):
        # get agent first to know host as binding will be deleted
        # after pool is deleted from db
        agent = self.plugin.get_lbaas_agent_hosting_pool(context, pool['id'])
        self.plugin._delete_db_pool(context, pool['id'])
        if agent:
            self.agent_rpc.delete_pool(context, pool, agent['agent']['host'])

    def create_member(self, context, member):
        agent = self.get_pool_agent(context, member['pool_id'])
        self.agent_rpc.create_member(context, member, agent['host'])

    def update_member(self, context, old_member, member):
        agent = self.get_pool_agent(context, member['pool_id'])
        # member may change pool id
        if member['pool_id'] != old_member['pool_id']:
            old_pool_agent = self.plugin.get_lbaas_agent_hosting_pool(
                context, old_member['pool_id'])
            if old_pool_agent:
                self.agent_rpc.delete_member(context, old_member,
                                             old_pool_agent['agent']['host'])
            self.agent_rpc.create_member(context, member, agent['host'])
        else:
            self.agent_rpc.update_member(context, old_member, member,
                                         agent['host'])

    def delete_member(self, context, member):
        self.plugin._delete_db_member(context, member['id'])
        agent = self.get_pool_agent(context, member['pool_id'])
        self.agent_rpc.delete_member(context, member, agent['host'])

    def create_pool_health_monitor(self, context, healthmon, pool_id):
        # healthmon is not used here
        agent = self.get_pool_agent(context, pool_id)
        self.agent_rpc.create_pool_health_monitor(context, healthmon,
                                                  pool_id, agent['host'])

    def update_pool_health_monitor(self, context, old_health_monitor,
                                   health_monitor, pool_id):
        agent = self.get_pool_agent(context, pool_id)
        self.agent_rpc.update_pool_health_monitor(context, old_health_monitor,
                                                  health_monitor, pool_id,
                                                  agent['host'])

    def delete_pool_health_monitor(self, context, health_monitor, pool_id):
        self.plugin._delete_db_pool_health_monitor(
            context, health_monitor['id'], pool_id
        )

        agent = self.get_pool_agent(context, pool_id)
        self.agent_rpc.delete_pool_health_monitor(context, health_monitor,
                                                  pool_id, agent['host'])

    def stats(self, context, pool_id):
        pass
