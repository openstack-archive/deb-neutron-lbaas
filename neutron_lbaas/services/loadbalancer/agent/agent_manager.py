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

from neutron.agent import rpc as agent_rpc
from neutron.common import constants as n_const
from neutron.common import exceptions as n_exc
from neutron import context as ncontext
from neutron.plugins.common import constants as np_const
from neutron.services import provider_configuration as provconfig
from oslo_config import cfg
from oslo_log import log as logging
import oslo_messaging
from oslo_service import loopingcall
from oslo_service import periodic_task
from oslo_utils import importutils

from neutron_lbaas._i18n import _, _LE, _LI
from neutron_lbaas.services.loadbalancer.agent import agent_api
from neutron_lbaas.services.loadbalancer import constants as l_const

LOG = logging.getLogger(__name__)

DEVICE_DRIVERS = 'device_drivers'

OPTS = [
    cfg.MultiStrOpt(
        'device_driver',
        default=['neutron_lbaas.services.loadbalancer.drivers'
                 '.haproxy.namespace_driver.HaproxyNSDriver'],
        help=_('Drivers used to manage loadbalancing devices'),
    ),
]


class DeviceNotFoundOnAgent(n_exc.NotFound):
    message = _('Unknown device with pool_id %(pool_id)s')


class LbaasAgentManager(periodic_task.PeriodicTasks):

    # history
    #   1.0 Initial version
    #   1.1 Support agent_updated call
    #   2.0 Generic API for agent based drivers
    #       - modify/reload/destroy_pool methods were removed;
    #       - added methods to handle create/update/delete for every lbaas
    #       object individually;
    target = oslo_messaging.Target(version='2.0')

    def __init__(self, conf):
        super(LbaasAgentManager, self).__init__(conf)
        self.conf = conf
        self.context = ncontext.get_admin_context_without_session()
        self.plugin_rpc = agent_api.LbaasAgentApi(
            l_const.LOADBALANCER_PLUGIN,
            self.context,
            self.conf.host
        )
        self._load_drivers()

        self.agent_state = {
            'binary': 'neutron-lbaas-agent',
            'host': conf.host,
            'topic': l_const.LOADBALANCER_AGENT,
            'configurations': {'device_drivers': self.device_drivers.keys()},
            'agent_type': n_const.AGENT_TYPE_LOADBALANCER,
            'start_flag': True}
        self.admin_state_up = True

        self._setup_state_rpc()
        self.needs_resync = False
        # pool_id->device_driver_name mapping used to store known instances
        self.instance_mapping = {}

    def _load_drivers(self):
        self.device_drivers = {}
        for driver in self.conf.device_driver:
            driver = provconfig.get_provider_driver_class(driver,
                                                          DEVICE_DRIVERS)
            try:
                driver_inst = importutils.import_object(
                    driver,
                    self.conf,
                    self.plugin_rpc
                )
            except ImportError:
                msg = _('Error importing loadbalancer device driver: %s')
                raise SystemExit(msg % driver)

            driver_name = driver_inst.get_name()
            if driver_name not in self.device_drivers:
                self.device_drivers[driver_name] = driver_inst
            else:
                msg = _('Multiple device drivers with the same name found: %s')
                raise SystemExit(msg % driver_name)

    def _setup_state_rpc(self):
        self.state_rpc = agent_rpc.PluginReportStateAPI(
            l_const.LOADBALANCER_PLUGIN)
        report_interval = self.conf.AGENT.report_interval
        if report_interval:
            heartbeat = loopingcall.FixedIntervalLoopingCall(
                self._report_state)
            heartbeat.start(interval=report_interval)

    def _report_state(self):
        try:
            instance_count = len(self.instance_mapping)
            self.agent_state['configurations']['instances'] = instance_count
            self.state_rpc.report_state(self.context,
                                        self.agent_state)
            self.agent_state.pop('start_flag', None)
        except Exception:
            LOG.exception(_LE("Failed reporting state!"))

    def initialize_service_hook(self, started_by):
        self.sync_state()

    @periodic_task.periodic_task
    def periodic_resync(self, context):
        if self.needs_resync:
            self.needs_resync = False
            self.sync_state()

    @periodic_task.periodic_task(spacing=6)
    def collect_stats(self, context):
        for pool_id, driver_name in self.instance_mapping.items():
            driver = self.device_drivers[driver_name]
            try:
                stats = driver.get_stats(pool_id)
                if stats:
                    self.plugin_rpc.update_pool_stats(pool_id, stats)
            except Exception:
                LOG.exception(_LE('Error updating statistics on pool %s'),
                              pool_id)
                self.needs_resync = True

    def sync_state(self):
        known_instances = set(self.instance_mapping.keys())
        try:
            ready_instances = set(self.plugin_rpc.get_ready_devices())

            for deleted_id in known_instances - ready_instances:
                self._destroy_pool(deleted_id)

            for pool_id in ready_instances:
                self._reload_pool(pool_id)

        except Exception:
            LOG.exception(_LE('Unable to retrieve ready devices'))
            self.needs_resync = True

        self.remove_orphans()

    def _get_driver(self, pool_id):
        if pool_id not in self.instance_mapping:
            raise DeviceNotFoundOnAgent(pool_id=pool_id)

        driver_name = self.instance_mapping[pool_id]
        return self.device_drivers[driver_name]

    def _reload_pool(self, pool_id):
        try:
            logical_config = self.plugin_rpc.get_logical_device(pool_id)
            driver_name = logical_config['driver']
            if driver_name not in self.device_drivers:
                LOG.error(_LE('No device driver on agent: %s.'), driver_name)
                self.plugin_rpc.update_status(
                    'pool', pool_id, np_const.ERROR)
                return

            self.device_drivers[driver_name].deploy_instance(logical_config)
            self.instance_mapping[pool_id] = driver_name
            self.plugin_rpc.pool_deployed(pool_id)
        except Exception:
            LOG.exception(_LE('Unable to deploy instance for pool: %s'),
                          pool_id)
            self.needs_resync = True

    def _destroy_pool(self, pool_id):
        driver = self._get_driver(pool_id)
        try:
            driver.undeploy_instance(pool_id, delete_namespace=True)
            del self.instance_mapping[pool_id]
            self.plugin_rpc.pool_destroyed(pool_id)
        except Exception:
            LOG.exception(_LE('Unable to destroy device for pool: %s'),
                          pool_id)
            self.needs_resync = True

    def remove_orphans(self):
        for driver_name in self.device_drivers:
            pool_ids = [pool_id for pool_id in self.instance_mapping
                        if self.instance_mapping[pool_id] == driver_name]
            try:
                self.device_drivers[driver_name].remove_orphans(pool_ids)
            except NotImplementedError:
                pass  # Not all drivers will support this

    def _handle_failed_driver_call(self, operation, obj_type, obj_id, driver):
        LOG.exception(_LE('%(operation)s %(obj)s %(id)s failed on device '
                          'driver %(driver)s'),
                      {'operation': operation.capitalize(), 'obj': obj_type,
                       'id': obj_id, 'driver': driver})
        self.plugin_rpc.update_status(obj_type, obj_id, np_const.ERROR)

    def _update_status(self, obj_type, obj_id, admin_state_up):
        if admin_state_up:
            self.plugin_rpc.update_status(obj_type, obj_id, np_const.ACTIVE)
        else:
            self.plugin_rpc.update_status(obj_type, obj_id, l_const.DISABLED)

    def create_vip(self, context, vip):
        driver = self._get_driver(vip['pool_id'])
        try:
            driver.create_vip(vip)
        except Exception:
            self._handle_failed_driver_call('create', 'vip', vip['id'],
                                            driver.get_name())
        else:
            self._update_status('vip', vip['id'], vip['admin_state_up'])

    def update_vip(self, context, old_vip, vip):
        driver = self._get_driver(vip['pool_id'])
        try:
            driver.update_vip(old_vip, vip)
        except Exception:
            self._handle_failed_driver_call('update', 'vip', vip['id'],
                                            driver.get_name())
        else:
            self._update_status('vip', vip['id'], vip['admin_state_up'])

    def delete_vip(self, context, vip):
        driver = self._get_driver(vip['pool_id'])
        driver.delete_vip(vip)

    def create_pool(self, context, pool, driver_name):
        if driver_name not in self.device_drivers:
            LOG.error(_LE('No device driver on agent: %s.'), driver_name)
            self.plugin_rpc.update_status('pool', pool['id'], np_const.ERROR)
            return

        driver = self.device_drivers[driver_name]
        try:
            driver.create_pool(pool)
        except Exception:
            self._handle_failed_driver_call('create', 'pool', pool['id'],
                                            driver.get_name())
        else:
            self.instance_mapping[pool['id']] = driver_name
            self._update_status('pool', pool['id'], pool['admin_state_up'])

    def update_pool(self, context, old_pool, pool):
        driver = self._get_driver(pool['id'])
        try:
            driver.update_pool(old_pool, pool)
        except Exception:
            self._handle_failed_driver_call('update', 'pool', pool['id'],
                                            driver.get_name())
        else:
            self._update_status('pool', pool['id'], pool['admin_state_up'])

    def delete_pool(self, context, pool):
        driver = self._get_driver(pool['id'])
        driver.delete_pool(pool)
        del self.instance_mapping[pool['id']]

    def create_member(self, context, member):
        driver = self._get_driver(member['pool_id'])
        try:
            driver.create_member(member)
        except Exception:
            self._handle_failed_driver_call('create', 'member', member['id'],
                                            driver.get_name())
        else:
            self._update_status('member', member['id'],
                                member['admin_state_up'])

    def update_member(self, context, old_member, member):
        driver = self._get_driver(member['pool_id'])
        try:
            driver.update_member(old_member, member)
        except Exception:
            self._handle_failed_driver_call('update', 'member', member['id'],
                                            driver.get_name())
        else:
            self._update_status('member', member['id'],
                                member['admin_state_up'])

    def delete_member(self, context, member):
        driver = self._get_driver(member['pool_id'])
        driver.delete_member(member)

    def create_pool_health_monitor(self, context, health_monitor, pool_id):
        driver = self._get_driver(pool_id)
        assoc_id = {'pool_id': pool_id, 'monitor_id': health_monitor['id']}
        try:
            driver.create_pool_health_monitor(health_monitor, pool_id)
        except Exception:
            self._handle_failed_driver_call(
                'create', 'health_monitor', assoc_id, driver.get_name())
        else:
            self._update_status('health_monitor', assoc_id,
                                health_monitor['admin_state_up'])

    def update_pool_health_monitor(self, context, old_health_monitor,
                                   health_monitor, pool_id):
        driver = self._get_driver(pool_id)
        assoc_id = {'pool_id': pool_id, 'monitor_id': health_monitor['id']}
        try:
            driver.update_pool_health_monitor(old_health_monitor,
                                              health_monitor,
                                              pool_id)
        except Exception:
            self._handle_failed_driver_call(
                'update', 'health_monitor', assoc_id, driver.get_name())
        else:
            self._update_status('health_monitor', assoc_id,
                                health_monitor['admin_state_up'])

    def delete_pool_health_monitor(self, context, health_monitor, pool_id):
        driver = self._get_driver(pool_id)
        driver.delete_pool_health_monitor(health_monitor, pool_id)

    def agent_updated(self, context, payload):
        """Handle the agent_updated notification event."""
        if payload['admin_state_up'] != self.admin_state_up:
            self.admin_state_up = payload['admin_state_up']
            if self.admin_state_up:
                self.needs_resync = True
            else:
                for pool_id in self.instance_mapping.keys():
                    LOG.info(_LI("Destroying pool %s due to agent disabling"),
                             pool_id)
                    self._destroy_pool(pool_id)
            LOG.info(_LI("Agent_updated by server side %s!"), payload)
