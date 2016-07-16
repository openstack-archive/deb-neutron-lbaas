#
# Copyright 2013 Radware LTD.
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
import copy

from neutron.api.v2 import attributes as attrs
from neutron.api.v2 import base as napi_base
from neutron import context as ncontext
from neutron.db import servicetype_db as st_db
from neutron.extensions import flavors
from neutron import manager
from neutron.plugins.common import constants
from neutron.services.flavors import flavors_plugin
from neutron.services import provider_configuration as pconf
from neutron.services import service_base
from neutron_lib import constants as n_constants
from neutron_lib import exceptions as n_exc
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import encodeutils
from oslo_utils import excutils
import six

from neutron_lbaas._i18n import _LI, _LE
from neutron_lbaas import agent_scheduler as agent_scheduler_v2
import neutron_lbaas.common.cert_manager
from neutron_lbaas.common.tls_utils import cert_parser
from neutron_lbaas.db.loadbalancer import loadbalancer_db as ldb
from neutron_lbaas.db.loadbalancer import loadbalancer_dbv2 as ldbv2
from neutron_lbaas.db.loadbalancer import models
from neutron_lbaas.extensions import l7
from neutron_lbaas.extensions import lb_graph as lb_graph_ext
from neutron_lbaas.extensions import lbaas_agentschedulerv2
from neutron_lbaas.extensions import loadbalancer as lb_ext
from neutron_lbaas.extensions import loadbalancerv2
from neutron_lbaas.extensions import sharedpools
from neutron_lbaas.services.loadbalancer import agent_scheduler
from neutron_lbaas.services.loadbalancer import constants as lb_const
from neutron_lbaas.services.loadbalancer import data_models
LOG = logging.getLogger(__name__)
CERT_MANAGER_PLUGIN = neutron_lbaas.common.cert_manager.get_backend()


def verify_lbaas_mutual_exclusion():
    """Verifies lbaas v1 and lbaas v2 cannot be active concurrently."""
    plugins = set([LoadBalancerPlugin.__name__, LoadBalancerPluginv2.__name__])
    cfg_sps = set([sp.split('.')[-1] for sp in cfg.CONF.service_plugins])

    if len(plugins.intersection(cfg_sps)) >= 2:
        msg = _LE("Cannot have service plugins %(v1)s and %(v2)s active at "
                  "the same time!") % {'v1': LoadBalancerPlugin.__name__,
                                       'v2': LoadBalancerPluginv2.__name__}
        LOG.error(msg)
        raise SystemExit(1)


def add_provider_configuration(type_manager, service_type):
    type_manager.add_provider_configuration(
        service_type,
        pconf.ProviderConfiguration('neutron_lbaas'))


class LoadBalancerPlugin(ldb.LoadBalancerPluginDb,
                         agent_scheduler.LbaasAgentSchedulerDbMixin):
    """Implementation of the Neutron Loadbalancer Service Plugin.

    This class manages the workflow of LBaaS request/response.
    Most DB related works are implemented in class
    loadbalancer_db.LoadBalancerPluginDb.
    """
    supported_extension_aliases = ["lbaas",
                                   "lbaas_agent_scheduler",
                                   "service-type"]
    path_prefix = lb_ext.LOADBALANCER_PREFIX

    # lbaas agent notifiers to handle agent update operations;
    # can be updated by plugin drivers while loading;
    # will be extracted by neutron manager when loading service plugins;
    agent_notifiers = {}

    def __init__(self):
        """Initialization for the loadbalancer service plugin."""
        self.service_type_manager = st_db.ServiceTypeManager.get_instance()
        add_provider_configuration(
            self.service_type_manager, constants.LOADBALANCER)
        self._load_drivers()
        super(LoadBalancerPlugin, self).subscribe()

    def _load_drivers(self):
        """Loads plugin-drivers specified in configuration."""
        self.drivers, self.default_provider = service_base.load_drivers(
            constants.LOADBALANCER, self)

        # NOTE(blogan): this method MUST be called after
        # service_base.load_drivers to correctly verify
        verify_lbaas_mutual_exclusion()

        ctx = ncontext.get_admin_context()
        # stop service in case provider was removed, but resources were not
        self._check_orphan_pool_associations(ctx, self.drivers.keys())

    def _check_orphan_pool_associations(self, context, provider_names):
        """Checks remaining associations between pools and providers.

        If admin has not undeployed resources with provider that was deleted
        from configuration, neutron service is stopped. Admin must delete
        resources prior to removing providers from configuration.
        """
        pools = self.get_pools(context)
        lost_providers = set([pool['provider'] for pool in pools
                              if pool['provider'] not in provider_names])
        # resources are left without provider - stop the service
        if lost_providers:
            LOG.error(_LE("Delete associated loadbalancer pools before "
                          "removing providers %s"), list(lost_providers))
            raise SystemExit(1)

    def _get_driver_for_provider(self, provider):
        if provider in self.drivers:
            return self.drivers[provider]
        # raise if not associated (should never be reached)
        raise n_exc.Invalid(_LE("Error retrieving driver for provider %s") %
                            provider)

    def _get_driver_for_pool(self, context, pool_id):
        pool = self.get_pool(context, pool_id)
        try:
            return self.drivers[pool['provider']]
        except KeyError:
            raise n_exc.Invalid(_LE("Error retrieving provider for pool %s") %
                                pool_id)

    def get_plugin_type(self):
        return constants.LOADBALANCER

    def get_plugin_description(self):
        return "Neutron LoadBalancer Service Plugin"

    def create_vip(self, context, vip):
        v = super(LoadBalancerPlugin, self).create_vip(context, vip)
        driver = self._get_driver_for_pool(context, v['pool_id'])
        driver.create_vip(context, v)
        return v

    def update_vip(self, context, id, vip):
        if 'status' not in vip['vip']:
            vip['vip']['status'] = constants.PENDING_UPDATE
        old_vip = self.get_vip(context, id)
        v = super(LoadBalancerPlugin, self).update_vip(context, id, vip)
        driver = self._get_driver_for_pool(context, v['pool_id'])
        driver.update_vip(context, old_vip, v)
        return v

    def _delete_db_vip(self, context, id):
        # proxy the call until plugin inherits from DBPlugin
        super(LoadBalancerPlugin, self).delete_vip(context, id)

    def delete_vip(self, context, id):
        self.update_status(context, ldb.Vip,
                           id, constants.PENDING_DELETE)
        v = self.get_vip(context, id)
        driver = self._get_driver_for_pool(context, v['pool_id'])
        driver.delete_vip(context, v)

    def _get_provider_name(self, context, pool):
        if ('provider' in pool and
            pool['provider'] != n_constants.ATTR_NOT_SPECIFIED):
            provider_name = pconf.normalize_provider_name(pool['provider'])
            self.validate_provider(provider_name)
            return provider_name
        else:
            if not self.default_provider:
                raise pconf.DefaultServiceProviderNotFound(
                    service_type=constants.LOADBALANCER)
            return self.default_provider

    def create_pool(self, context, pool):
        # This validation is because the new API version also has a resource
        # called pool and these attributes have to be optional in the old API
        # so they are not required attributes of the new.  Its complicated.
        if pool['pool']['lb_method'] == n_constants.ATTR_NOT_SPECIFIED:
            raise loadbalancerv2.RequiredAttributeNotSpecified(
                attr_name='lb_method')
        if pool['pool']['subnet_id'] == n_constants.ATTR_NOT_SPECIFIED:
            raise loadbalancerv2.RequiredAttributeNotSpecified(
                attr_name='subnet_id')

        provider_name = self._get_provider_name(context, pool['pool'])
        p = super(LoadBalancerPlugin, self).create_pool(context, pool)

        self.service_type_manager.add_resource_association(
            context,
            constants.LOADBALANCER,
            provider_name, p['id'])
        # need to add provider name to pool dict,
        # because provider was not known to db plugin at pool creation
        p['provider'] = provider_name
        driver = self.drivers[provider_name]
        try:
            driver.create_pool(context, p)
        except lb_ext.NoEligibleBackend:
            # that should catch cases when backend of any kind
            # is not available (agent, appliance, etc)
            self.update_status(context, ldb.Pool,
                               p['id'], constants.ERROR,
                               "No eligible backend")
            raise lb_ext.NoEligibleBackend(pool_id=p['id'])
        return p

    def update_pool(self, context, id, pool):
        if 'status' not in pool['pool']:
            pool['pool']['status'] = constants.PENDING_UPDATE
        old_pool = self.get_pool(context, id)
        p = super(LoadBalancerPlugin, self).update_pool(context, id, pool)
        driver = self._get_driver_for_provider(p['provider'])
        driver.update_pool(context, old_pool, p)
        return p

    def _delete_db_pool(self, context, id):
        # proxy the call until plugin inherits from DBPlugin
        # rely on uuid uniqueness:
        try:
            with context.session.begin(subtransactions=True):
                self.service_type_manager.del_resource_associations(
                    context, [id])
                super(LoadBalancerPlugin, self).delete_pool(context, id)
        except Exception:
            # that should not happen
            # if it's still a case - something goes wrong
            # log the error and mark the pool as ERROR
            LOG.error(_LE('Failed to delete pool %s, putting it in ERROR '
                          'state'),
                      id)
            with excutils.save_and_reraise_exception():
                self.update_status(context, ldb.Pool,
                                   id, constants.ERROR)

    def delete_pool(self, context, id):
        # check for delete conditions and update the status
        # within a transaction to avoid a race
        with context.session.begin(subtransactions=True):
            self.update_status(context, ldb.Pool,
                               id, constants.PENDING_DELETE)
            self._ensure_pool_delete_conditions(context, id)
        p = self.get_pool(context, id)
        driver = self._get_driver_for_provider(p['provider'])
        driver.delete_pool(context, p)

    def create_member(self, context, member):
        m = super(LoadBalancerPlugin, self).create_member(context, member)
        driver = self._get_driver_for_pool(context, m['pool_id'])
        driver.create_member(context, m)
        return m

    def update_member(self, context, id, member):
        if 'status' not in member['member']:
            member['member']['status'] = constants.PENDING_UPDATE
        old_member = self.get_member(context, id)
        m = super(LoadBalancerPlugin, self).update_member(context, id, member)
        driver = self._get_driver_for_pool(context, m['pool_id'])
        driver.update_member(context, old_member, m)
        return m

    def _delete_db_member(self, context, id):
        # proxy the call until plugin inherits from DBPlugin
        super(LoadBalancerPlugin, self).delete_member(context, id)

    def delete_member(self, context, id):
        self.update_status(context, ldb.Member,
                           id, constants.PENDING_DELETE)
        m = self.get_member(context, id)
        driver = self._get_driver_for_pool(context, m['pool_id'])
        driver.delete_member(context, m)

    def _validate_hm_parameters(self, delay, timeout):
        if delay < timeout:
            raise lb_ext.DelayOrTimeoutInvalid()

    def create_health_monitor(self, context, health_monitor):
        new_hm = health_monitor['health_monitor']
        self._validate_hm_parameters(new_hm['delay'], new_hm['timeout'])

        hm = super(LoadBalancerPlugin, self).create_health_monitor(
            context,
            health_monitor
        )
        return hm

    def update_health_monitor(self, context, id, health_monitor):
        new_hm = health_monitor['health_monitor']
        old_hm = self.get_health_monitor(context, id)
        delay = new_hm.get('delay', old_hm.get('delay'))
        timeout = new_hm.get('timeout', old_hm.get('timeout'))
        self._validate_hm_parameters(delay, timeout)

        hm = super(LoadBalancerPlugin, self).update_health_monitor(
            context,
            id,
            health_monitor
        )

        with context.session.begin(subtransactions=True):
            qry = context.session.query(
                ldb.PoolMonitorAssociation
            ).filter_by(monitor_id=hm['id']).join(ldb.Pool)
            for assoc in qry:
                driver = self._get_driver_for_pool(context, assoc['pool_id'])
                driver.update_pool_health_monitor(context, old_hm,
                                                  hm, assoc['pool_id'])
        return hm

    def _delete_db_pool_health_monitor(self, context, hm_id, pool_id):
        super(LoadBalancerPlugin, self).delete_pool_health_monitor(context,
                                                                   hm_id,
                                                                   pool_id)

    def _delete_db_health_monitor(self, context, id):
        super(LoadBalancerPlugin, self).delete_health_monitor(context, id)

    def create_pool_health_monitor(self, context, health_monitor, pool_id):
        retval = super(LoadBalancerPlugin, self).create_pool_health_monitor(
            context,
            health_monitor,
            pool_id
        )
        monitor_id = health_monitor['health_monitor']['id']
        hm = self.get_health_monitor(context, monitor_id)
        driver = self._get_driver_for_pool(context, pool_id)
        driver.create_pool_health_monitor(context, hm, pool_id)
        return retval

    def delete_pool_health_monitor(self, context, id, pool_id):
        self.update_pool_health_monitor(context, id, pool_id,
                                        constants.PENDING_DELETE)
        hm = self.get_health_monitor(context, id)
        driver = self._get_driver_for_pool(context, pool_id)
        driver.delete_pool_health_monitor(context, hm, pool_id)

    def stats(self, context, pool_id):
        driver = self._get_driver_for_pool(context, pool_id)
        stats_data = driver.stats(context, pool_id)
        # if we get something from the driver -
        # update the db and return the value from db
        # else - return what we have in db
        if stats_data:
            super(LoadBalancerPlugin, self).update_pool_stats(
                context,
                pool_id,
                stats_data
            )
        return super(LoadBalancerPlugin, self).stats(context,
                                                     pool_id)

    def populate_vip_graph(self, context, vip):
        """Populate the vip with: pool, members, healthmonitors."""

        pool = self.get_pool(context, vip['pool_id'])
        vip['pool'] = pool
        vip['members'] = [self.get_member(context, member_id)
                          for member_id in pool['members']]
        vip['health_monitors'] = [self.get_health_monitor(context, hm_id)
                                  for hm_id in pool['health_monitors']]
        return vip

    def validate_provider(self, provider):
        if provider not in self.drivers:
            raise pconf.ServiceProviderNotFound(
                provider=provider, service_type=constants.LOADBALANCER)


class LoadBalancerPluginv2(loadbalancerv2.LoadBalancerPluginBaseV2):
    """Implementation of the Neutron Loadbalancer Service Plugin.

    This class manages the workflow of LBaaS request/response.
    Most DB related works are implemented in class
    loadbalancer_db.LoadBalancerPluginDb.
    """
    supported_extension_aliases = ["lbaasv2",
                                   "shared_pools",
                                   "l7",
                                   "lbaas_agent_schedulerv2",
                                   "service-type",
                                   "lb-graph",
                                   "hm_max_retries_down"]
    path_prefix = loadbalancerv2.LOADBALANCERV2_PREFIX

    agent_notifiers = (
        agent_scheduler_v2.LbaasAgentSchedulerDbMixin.agent_notifiers)

    def __init__(self):
        """Initialization for the loadbalancer service plugin."""
        self.db = ldbv2.LoadBalancerPluginDbv2()
        self.service_type_manager = st_db.ServiceTypeManager.get_instance()
        add_provider_configuration(
            self.service_type_manager, constants.LOADBALANCERV2)
        self._load_drivers()
        self.start_rpc_listeners()
        self.db.subscribe()

    def start_rpc_listeners(self):
        listeners = []
        for driver in self.drivers.values():
            if hasattr(driver, 'start_rpc_listeners'):
                listener = driver.start_rpc_listeners()
                listeners.append(listener)
        return listeners

    def _load_drivers(self):
        """Loads plugin-drivers specified in configuration."""
        self.drivers, self.default_provider = service_base.load_drivers(
            constants.LOADBALANCERV2, self)

        # NOTE(blogan): this method MUST be called after
        # service_base.load_drivers to correctly verify
        verify_lbaas_mutual_exclusion()

        ctx = ncontext.get_admin_context()
        # stop service in case provider was removed, but resources were not
        self._check_orphan_loadbalancer_associations(ctx, self.drivers.keys())

    def _check_orphan_loadbalancer_associations(self, context, provider_names):
        """Checks remaining associations between loadbalancers and providers.

        If admin has not undeployed resources with provider that was deleted
        from configuration, neutron service is stopped. Admin must delete
        resources prior to removing providers from configuration.
        """
        loadbalancers = self.db.get_loadbalancers(context)
        lost_providers = set(
            [lb.provider.provider_name
             for lb in loadbalancers
             if lb.provider.provider_name not in provider_names])
        # resources are left without provider - stop the service
        if lost_providers:
            msg = _LE("Delete associated load balancers before "
                      "removing providers %s") % list(lost_providers)
            LOG.error(msg)
            raise SystemExit(1)

    def _get_driver_for_provider(self, provider):
        try:
            return self.drivers[provider]
        except KeyError:
            # raise if not associated (should never be reached)
            raise n_exc.Invalid(_LE("Error retrieving driver for provider "
                                    "%s") % provider)

    def _get_driver_for_loadbalancer(self, context, loadbalancer_id):
        lb = self.db.get_loadbalancer(context, loadbalancer_id)
        try:
            return self.drivers[lb.provider.provider_name]
        except KeyError:
            raise n_exc.Invalid(
                _LE("Error retrieving provider for load balancer. Possible "
                    "providers are %s.") % self.drivers.keys()
            )

    def _get_provider_name(self, entity):
        if ('provider' in entity and
                entity['provider'] != n_constants.ATTR_NOT_SPECIFIED):
            provider_name = pconf.normalize_provider_name(entity['provider'])
            del entity['provider']
            self.validate_provider(provider_name)
            return provider_name
        else:
            if not self.default_provider:
                raise pconf.DefaultServiceProviderNotFound(
                    service_type=constants.LOADBALANCER)
            if entity.get('provider'):
                del entity['provider']
            return self.default_provider

    def _call_driver_operation(self, context, driver_method, db_entity,
                               old_db_entity=None, **kwargs):
        manager_method = "%s.%s" % (driver_method.__self__.__class__.__name__,
                                    driver_method.__name__)
        LOG.info(_LI("Calling driver operation %s") % manager_method)
        try:
            if old_db_entity:
                driver_method(context, old_db_entity, db_entity, **kwargs)
            else:
                driver_method(context, db_entity, **kwargs)
        # catching and reraising agent issues
        except (lbaas_agentschedulerv2.NoEligibleLbaasAgent,
                lbaas_agentschedulerv2.NoActiveLbaasAgent) as no_agent:
            raise no_agent
        except Exception as e:
            LOG.exception(_LE("There was an error in the driver"))
            self._handle_driver_error(context, db_entity)
            raise loadbalancerv2.DriverError(msg=e)

    def _handle_driver_error(self, context, db_entity):
        lb_id = db_entity.root_loadbalancer.id
        self.db.update_status(context, models.LoadBalancer, lb_id,
                              constants.ERROR)

    def _validate_session_persistence_info(self, sp_info):
        """Performs sanity check on session persistence info.

        :param sp_info: Session persistence info
        """
        if not sp_info:
            return
        if sp_info['type'] == lb_const.SESSION_PERSISTENCE_APP_COOKIE:
            if not sp_info.get('cookie_name'):
                raise loadbalancerv2.SessionPersistenceConfigurationInvalid(
                    msg="'cookie_name' should be specified for %s"
                        " session persistence." % sp_info['type'])
        else:
            if 'cookie_name' in sp_info:
                raise loadbalancerv2.SessionPersistenceConfigurationInvalid(
                    msg="'cookie_name' is not allowed for %s"
                        " session persistence" % sp_info['type'])

    def get_plugin_type(self):
        return constants.LOADBALANCERV2

    def get_plugin_description(self):
        return "Neutron LoadBalancer Service Plugin v2"

    def _insert_provider_name_from_flavor(self, context, loadbalancer):
        """Select provider based on flavor."""

        # TODO(jwarendt) Support passing flavor metainfo from the
        # selected flavor profile into the provider, not just selecting
        # the provider, when flavor templating arrives.

        if ('provider' in loadbalancer and
            loadbalancer['provider'] != n_constants.ATTR_NOT_SPECIFIED):
            raise loadbalancerv2.ProviderFlavorConflict()

        plugin = manager.NeutronManager.get_service_plugins().get(
            constants.FLAVORS)
        if not plugin:
            raise loadbalancerv2.FlavorsPluginNotLoaded()

        # Will raise FlavorNotFound if doesn't exist
        fl_db = flavors_plugin.FlavorsPlugin.get_flavor(
            plugin,
            context,
            loadbalancer['flavor_id'])

        if fl_db['service_type'] != constants.LOADBALANCERV2:
            raise flavors.InvalidFlavorServiceType(
                service_type=fl_db['service_type'])

        if not fl_db['enabled']:
            raise flavors.FlavorDisabled()

        providers = flavors_plugin.FlavorsPlugin.get_flavor_next_provider(
            plugin,
            context,
            fl_db['id'])

        provider = providers[0].get('provider')

        LOG.debug("Selected provider %s" % provider)

        loadbalancer['provider'] = provider

    def _get_tweaked_resource_attribute_map(self):
        memo = {id(n_constants.ATTR_NOT_SPECIFIED):
                n_constants.ATTR_NOT_SPECIFIED}
        ram = copy.deepcopy(attrs.RESOURCE_ATTRIBUTE_MAP, memo=memo)
        del ram['listeners']['loadbalancer_id']
        del ram['pools']['listener_id']
        del ram['healthmonitors']['pool_id']
        for resource in ram:
            if resource in lb_graph_ext.EXISTING_ATTR_GRAPH_ATTR_MAP:
                ram[resource].update(
                    lb_graph_ext.EXISTING_ATTR_GRAPH_ATTR_MAP[resource])
        return ram

    def _prepare_loadbalancer_graph(self, context, loadbalancer):
        """Prepares the entire user requested body of a load balancer graph

        To minimize code duplication, this method reuses the neutron API
        controller's method to do all the validation, conversion, and
        defaulting of each resource.  This reuses the RESOURCE_ATTRIBUTE_MAP
        and SUB_RESOURCE_ATTRIBUTE_MAP from the extension to enable this.
        """
        # NOTE(blogan): it is assumed the loadbalancer attributes have already
        # passed through the prepare_request_body method by nature of the
        # normal neutron wsgi workflow.  So we start with listeners since
        # that probably has not passed through the neutron wsgi workflow.
        ram = self._get_tweaked_resource_attribute_map()
        # NOTE(blogan): members are not populated in the attributes.RAM so
        # our only option is to use the original extension definition of member
        # to validate.  If members ever need something added to it then it too
        # will need to be added here.
        prepped_lb = napi_base.Controller.prepare_request_body(
            context, {'loadbalancer': loadbalancer}, True, 'loadbalancer',
            ram['loadbalancers']
        )
        sub_ram = loadbalancerv2.SUB_RESOURCE_ATTRIBUTE_MAP
        sub_ram.update(l7.SUB_RESOURCE_ATTRIBUTE_MAP)
        prepped_listeners = []
        for listener in loadbalancer.get('listeners', []):
            prepped_listener = napi_base.Controller.prepare_request_body(
                context, {'listener': listener}, True, 'listener',
                ram['listeners'])
            l7policies = listener.get('l7policies')
            if l7policies and l7policies != n_constants.ATTR_NOT_SPECIFIED:
                prepped_policies = []
                for policy in l7policies:
                    prepped_policy = napi_base.Controller.prepare_request_body(
                        context, {'l7policy': policy}, True, 'l7policy',
                        ram['l7policies'])
                    l7rules = policy.get('rules')
                    redirect_pool = policy.get('redirect_pool')
                    if l7rules and l7rules != n_constants.ATTR_NOT_SPECIFIED:
                        prepped_rules = []
                        for rule in l7rules:
                            prepped_rule = (
                                napi_base.Controller.prepare_request_body(
                                    context, {'l7rule': rule}, True, 'l7rule',
                                    sub_ram['rules']['parameters']))
                            prepped_rules.append(prepped_rule)
                        prepped_policy['l7_rules'] = prepped_rules
                    if (redirect_pool and
                            redirect_pool != n_constants.ATTR_NOT_SPECIFIED):
                        prepped_r_pool = (
                            napi_base.Controller.prepare_request_body(
                                context, {'pool': redirect_pool}, True, 'pool',
                                ram['pools']))
                        prepped_r_members = []
                        for member in redirect_pool.get('members', []):
                            prepped_r_member = (
                                napi_base.Controller.prepare_request_body(
                                    context, {'member': member},
                                    True, 'member',
                                    sub_ram['members']['parameters']))
                            prepped_r_members.append(prepped_r_member)
                        prepped_r_pool['members'] = prepped_r_members
                        r_hm = redirect_pool.get('healthmonitor')
                        if r_hm and r_hm != n_constants.ATTR_NOT_SPECIFIED:
                            prepped_r_hm = (
                                napi_base.Controller.prepare_request_body(
                                    context, {'healthmonitor': r_hm},
                                    True, 'healthmonitor',
                                    ram['healthmonitors']))
                            prepped_r_pool['healthmonitor'] = prepped_r_hm
                        prepped_policy['redirect_pool'] = redirect_pool
                    prepped_policies.append(prepped_policy)
                prepped_listener['l7_policies'] = prepped_policies
            pool = listener.get('default_pool')
            if pool and pool != n_constants.ATTR_NOT_SPECIFIED:
                prepped_pool = napi_base.Controller.prepare_request_body(
                    context, {'pool': pool}, True, 'pool',
                    ram['pools'])
                prepped_members = []
                for member in pool.get('members', []):
                    prepped_member = napi_base.Controller.prepare_request_body(
                        context, {'member': member}, True, 'member',
                        sub_ram['members']['parameters'])
                    prepped_members.append(prepped_member)
                prepped_pool['members'] = prepped_members
                hm = pool.get('healthmonitor')
                if hm and hm != n_constants.ATTR_NOT_SPECIFIED:
                    prepped_hm = napi_base.Controller.prepare_request_body(
                        context, {'healthmonitor': hm}, True, 'healthmonitor',
                        ram['healthmonitors'])
                    prepped_pool['healthmonitor'] = prepped_hm
                prepped_listener['default_pool'] = prepped_pool
            prepped_listeners.append(prepped_listener)
        prepped_lb['listeners'] = prepped_listeners
        return loadbalancer

    def create_loadbalancer(self, context, loadbalancer):
        loadbalancer = loadbalancer.get('loadbalancer')
        if loadbalancer['flavor_id'] != n_constants.ATTR_NOT_SPECIFIED:
            self._insert_provider_name_from_flavor(context, loadbalancer)
        else:
            del loadbalancer['flavor_id']
        provider_name = self._get_provider_name(loadbalancer)
        driver = self.drivers[provider_name]
        lb_db = self.db.create_loadbalancer(
            context, loadbalancer,
            allocate_vip=not driver.load_balancer.allocates_vip)
        self.service_type_manager.add_resource_association(
            context,
            constants.LOADBALANCERV2,
            provider_name, lb_db.id)
        create_method = (driver.load_balancer.create_and_allocate_vip
                         if driver.load_balancer.allocates_vip
                         else driver.load_balancer.create)
        self._call_driver_operation(context, create_method, lb_db)
        return self.db.get_loadbalancer(context, lb_db.id).to_api_dict()

    def create_graph(self, context, graph):
        loadbalancer = graph.get('graph', {}).get('loadbalancer')
        loadbalancer = self._prepare_loadbalancer_graph(context, loadbalancer)
        if loadbalancer['flavor_id'] != n_constants.ATTR_NOT_SPECIFIED:
            self._insert_provider_name_from_flavor(context, loadbalancer)
        else:
            del loadbalancer['flavor_id']
        provider_name = self._get_provider_name(loadbalancer)
        driver = self.drivers[provider_name]
        if not driver.load_balancer.allows_create_graph:
            raise lb_graph_ext.ProviderCannotCreateLoadBalancerGraph
        lb_db = self.db.create_loadbalancer_graph(
            context, loadbalancer,
            allocate_vip=not driver.load_balancer.allocates_vip)
        self.service_type_manager.add_resource_association(
            context, constants.LOADBALANCERV2, provider_name, lb_db.id)
        create_method = (driver.load_balancer.create_and_allocate_vip
                         if driver.load_balancer.allocates_vip
                         else driver.load_balancer.create)
        self._call_driver_operation(context, create_method, lb_db)
        api_lb = {'loadbalancer': self.db.get_loadbalancer(
            context, lb_db.id).to_api_dict(full_graph=True)}
        return api_lb

    def update_loadbalancer(self, context, id, loadbalancer):
        loadbalancer = loadbalancer.get('loadbalancer')
        old_lb = self.db.get_loadbalancer(context, id)
        self.db.test_and_set_status(context, models.LoadBalancer, id,
                                    constants.PENDING_UPDATE)
        try:
            updated_lb = self.db.update_loadbalancer(
                context, id, loadbalancer)
        except Exception as exc:
            self.db.update_status(context, models.LoadBalancer, id,
                                  old_lb.provisioning_status)
            raise exc
        driver = self._get_driver_for_provider(old_lb.provider.provider_name)
        self._call_driver_operation(context,
                                    driver.load_balancer.update,
                                    updated_lb, old_db_entity=old_lb)
        return self.db.get_loadbalancer(context, id).to_api_dict()

    def delete_loadbalancer(self, context, id):
        old_lb = self.db.get_loadbalancer(context, id)
        if old_lb.listeners:
            raise loadbalancerv2.EntityInUse(
                entity_using=models.Listener.NAME,
                id=old_lb.listeners[0].id,
                entity_in_use=models.LoadBalancer.NAME)
        if old_lb.pools:
            raise loadbalancerv2.EntityInUse(
                entity_using=models.PoolV2.NAME,
                id=old_lb.pools[0].id,
                entity_in_use=models.LoadBalancer.NAME)
        self.db.test_and_set_status(context, models.LoadBalancer, id,
                                    constants.PENDING_DELETE)
        driver = self._get_driver_for_provider(old_lb.provider.provider_name)
        db_lb = self.db.get_loadbalancer(context, id)
        self._call_driver_operation(
            context, driver.load_balancer.delete, db_lb)

    def get_loadbalancer(self, context, id, fields=None):
        return self.db.get_loadbalancer(context, id).to_api_dict()

    def get_loadbalancers(self, context, filters=None, fields=None):
        return [loadbalancer.to_api_dict() for loadbalancer in
                self.db.get_loadbalancers(context, filters=filters)]

    def _validate_tls(self, listener, curr_listener=None):
        def validate_tls_container(container_ref):
            cert_mgr = CERT_MANAGER_PLUGIN.CertManager()

            if curr_listener:
                lb_id = curr_listener['loadbalancer_id']
                tenant_id = curr_listener['tenant_id']
            else:
                lb_id = listener.get('loadbalancer_id')
                tenant_id = listener.get('tenant_id')

            try:
                cert_container = cert_mgr.get_cert(
                    project_id=tenant_id,
                    cert_ref=container_ref,
                    resource_ref=cert_mgr.get_service_url(lb_id))
            except Exception as e:
                if hasattr(e, 'status_code') and e.status_code == 404:
                    raise loadbalancerv2.TLSContainerNotFound(
                        container_id=container_ref)
                else:
                    # Could be a keystone configuration error...
                    err_msg = encodeutils.exception_to_unicode(e)
                    raise loadbalancerv2.CertManagerError(
                        ref=container_ref, reason=err_msg
                    )

            try:
                cert_parser.validate_cert(
                    cert_container.get_certificate(),
                    private_key=cert_container.get_private_key(),
                    private_key_passphrase=(
                        cert_container.get_private_key_passphrase()),
                    intermediates=cert_container.get_intermediates())
            except Exception as e:
                cert_mgr.delete_cert(
                    project_id=tenant_id,
                    cert_ref=container_ref,
                    resource_ref=cert_mgr.get_service_url(lb_id))
                raise loadbalancerv2.TLSContainerInvalid(
                    container_id=container_ref, reason=str(e))

        def validate_tls_containers(to_validate):
            for container_ref in to_validate:
                validate_tls_container(container_ref)

        to_validate = []
        if not listener['default_tls_container_ref']:
            raise loadbalancerv2.TLSDefaultContainerNotSpecified()
        if not curr_listener:
            to_validate.extend([listener['default_tls_container_ref']])
            if 'sni_container_refs' in listener:
                to_validate.extend(listener['sni_container_refs'])
        elif curr_listener['provisioning_status'] == constants.ERROR:
            to_validate.extend(curr_listener['default_tls_container_id'])
            to_validate.extend([
                container['tls_container_id'] for container in (
                    curr_listener['sni_containers'])])
        else:
            if (curr_listener['default_tls_container_id'] !=
                    listener['default_tls_container_ref']):
                to_validate.extend([listener['default_tls_container_ref']])

            if ('sni_container_refs' in listener and
                    [container['tls_container_id'] for container in (
                        curr_listener['sni_containers'])] !=
                    listener['sni_container_refs']):
                to_validate.extend(listener['sni_container_refs'])

        if len(to_validate) > 0:
            validate_tls_containers(to_validate)

        return len(to_validate) > 0

    def _check_pool_loadbalancer_match(self, context, pool_id, lb_id):
        lb = self.db.get_loadbalancer(context, lb_id)
        pool = self.db.get_pool(context, pool_id)
        if not lb.id == pool.loadbalancer.id:
            raise sharedpools.ListenerAndPoolMustBeOnSameLoadbalancer()

    def create_listener(self, context, listener):
        listener = listener.get('listener')
        lb_id = listener.get('loadbalancer_id')
        default_pool_id = listener.get('default_pool_id')
        if default_pool_id:
            self._check_pool_exists(context, default_pool_id)
            # Get the loadbalancer from the default_pool_id
            if not lb_id:
                default_pool = self.db.get_pool(context, default_pool_id)
                lb_id = default_pool.loadbalancer.id
                listener['loadbalancer_id'] = lb_id
            else:
                self._check_pool_loadbalancer_match(
                    context, default_pool_id, lb_id)
        elif not lb_id:
            raise sharedpools.ListenerMustHaveLoadbalancer()
        self.db.test_and_set_status(context, models.LoadBalancer, lb_id,
                                    constants.PENDING_UPDATE)

        try:
            if listener['protocol'] == lb_const.PROTOCOL_TERMINATED_HTTPS:
                self._validate_tls(listener)
            listener_db = self.db.create_listener(context, listener)
        except Exception as exc:
            self.db.update_loadbalancer_provisioning_status(
                context, lb_id)
            raise exc
        driver = self._get_driver_for_loadbalancer(
            context, listener_db.loadbalancer_id)
        self._call_driver_operation(
            context, driver.listener.create, listener_db)

        return self.db.get_listener(context, listener_db.id).to_api_dict()

    def _check_listener_pool_lb_match(self, context, listener_id, pool_id):
        listener = self.db.get_listener(context, listener_id)
        pool = self.db.get_pool(context, pool_id)
        if not listener.loadbalancer.id == pool.loadbalancer.id:
            raise sharedpools.ListenerAndPoolMustBeOnSameLoadbalancer()

    def update_listener(self, context, id, listener):
        listener = listener.get('listener')
        curr_listener_db = self.db.get_listener(context, id)
        default_pool_id = listener.get('default_pool_id')
        if default_pool_id:
            self._check_listener_pool_lb_match(
                context, id, default_pool_id)
        self.db.test_and_set_status(context, models.Listener, id,
                                    constants.PENDING_UPDATE)
        try:
            curr_listener = curr_listener_db.to_dict()

            default_tls_container_ref = listener.get(
                'default_tls_container_ref')
            if not default_tls_container_ref:
                listener['default_tls_container_ref'] = (
                    # NOTE(blogan): not changing to ref bc this dictionary is
                    # created from a data model
                    curr_listener['default_tls_container_id'])
            if 'sni_container_refs' not in listener:
                listener['sni_container_ids'] = [
                    container.tls_container_id for container in (
                        curr_listener['sni_containers'])]

            tls_containers_changed = False
            if curr_listener['protocol'] == lb_const.PROTOCOL_TERMINATED_HTTPS:
                tls_containers_changed = self._validate_tls(
                    listener, curr_listener=curr_listener)
            listener_db = self.db.update_listener(
                context, id, listener,
                tls_containers_changed=tls_containers_changed)
        except Exception as exc:
            self.db.update_status(
                context,
                models.LoadBalancer,
                curr_listener_db.loadbalancer.id,
                provisioning_status=constants.ACTIVE
            )
            self.db.update_status(
                context,
                models.Listener,
                curr_listener_db.id,
                provisioning_status=constants.ACTIVE
            )
            raise exc

        driver = self._get_driver_for_loadbalancer(
            context, listener_db.loadbalancer_id)
        self._call_driver_operation(
            context,
            driver.listener.update,
            listener_db,
            old_db_entity=curr_listener_db)

        return self.db.get_listener(context, id).to_api_dict()

    def delete_listener(self, context, id):
        old_listener = self.db.get_listener(context, id)
        if old_listener.l7_policies:
            raise loadbalancerv2.EntityInUse(
                entity_using=models.Listener.NAME,
                id=old_listener.l7_policies[0].id,
                entity_in_use=models.L7Policy.NAME)
        self.db.test_and_set_status(context, models.Listener, id,
                                    constants.PENDING_DELETE)
        listener_db = self.db.get_listener(context, id)

        driver = self._get_driver_for_loadbalancer(
            context, listener_db.loadbalancer_id)
        self._call_driver_operation(
            context, driver.listener.delete, listener_db)

    def get_listener(self, context, id, fields=None):
        return self.db.get_listener(context, id).to_api_dict()

    def get_listeners(self, context, filters=None, fields=None):
        return [listener.to_api_dict() for listener in self.db.get_listeners(
            context, filters=filters)]

    def create_pool(self, context, pool):
        pool = pool.get('pool')
        listener_id = pool.get('listener_id')
        listeners = pool.get('listeners', [])
        if listener_id:
            listeners.append(listener_id)
        lb_id = pool.get('loadbalancer_id')
        db_listeners = []
        for l in listeners:
            db_l = self.db.get_listener(context, l)
            db_listeners.append(db_l)
            # Take the pool's loadbalancer_id from the first listener found
            # if it wasn't specified in the API call.
            if not lb_id:
                lb_id = db_l.loadbalancer.id
            # All specified listeners must be on the same loadbalancer
            if db_l.loadbalancer.id != lb_id:
                raise sharedpools.ListenerAndPoolMustBeOnSameLoadbalancer()
            if db_l.default_pool_id:
                raise sharedpools.ListenerDefaultPoolAlreadySet(
                    listener_id=db_l.id, pool_id=db_l.default_pool_id)
        if not lb_id:
            raise sharedpools.PoolMustHaveLoadbalancer()
        pool['loadbalancer_id'] = lb_id
        self._validate_session_persistence_info(
            pool.get('session_persistence'))
        # SQLAlchemy gets strange ideas about populating the pool if we don't
        # blank out the listeners at this point.
        del pool['listener_id']
        pool['listeners'] = []
        db_pool = self.db.create_pool(context, pool)
        self.db.test_and_set_status(context, models.LoadBalancer,
                                    db_pool.loadbalancer_id,
                                    constants.PENDING_UPDATE)
        for db_l in db_listeners:
            try:
                self.db.update_listener(context, db_l.id,
                                        {'default_pool_id': db_pool.id})
            except Exception as exc:
                self.db.update_loadbalancer_provisioning_status(
                    context, db_pool.loadbalancer_id)
                raise exc
        # Reload the pool from the DB to re-populate pool.listeners
        # before calling the driver
        db_pool = self.db.get_pool(context, db_pool.id)
        driver = self._get_driver_for_loadbalancer(
            context, db_pool.loadbalancer_id)
        self._call_driver_operation(context, driver.pool.create, db_pool)
        return db_pool.to_api_dict()

    def update_pool(self, context, id, pool):
        pool = pool.get('pool')
        self._validate_session_persistence_info(
            pool.get('session_persistence'))
        old_pool = self.db.get_pool(context, id)
        self.db.test_and_set_status(context, models.PoolV2, id,
                                    constants.PENDING_UPDATE)
        try:
            updated_pool = self.db.update_pool(context, id, pool)
        except Exception as exc:
            self.db.update_loadbalancer_provisioning_status(
                context, old_pool.root_loadbalancer.id)
            raise exc

        driver = self._get_driver_for_loadbalancer(
            context, updated_pool.loadbalancer_id)
        self._call_driver_operation(context,
                                    driver.pool.update,
                                    updated_pool,
                                    old_db_entity=old_pool)

        return self.db.get_pool(context, id).to_api_dict()

    def delete_pool(self, context, id):
        old_lb = self.db.get_pool(context, id)
        if old_lb.healthmonitor:
            raise loadbalancerv2.EntityInUse(
                entity_using=models.HealthMonitorV2.NAME,
                id=old_lb.healthmonitor.id,
                entity_in_use=models.PoolV2.NAME)
        self.db.test_and_set_status(context, models.PoolV2, id,
                                    constants.PENDING_DELETE)
        db_pool = self.db.get_pool(context, id)

        driver = self._get_driver_for_loadbalancer(
            context, db_pool.loadbalancer_id)
        self._call_driver_operation(context, driver.pool.delete, db_pool)

    def get_pools(self, context, filters=None, fields=None):
        return [pool.to_api_dict() for pool in self.db.get_pools(
            context, filters=filters)]

    def get_pool(self, context, id, fields=None):
        return self.db.get_pool(context, id).to_api_dict()

    def _check_pool_exists(self, context, pool_id):
        if not self.db._resource_exists(context, models.PoolV2, pool_id):
            raise loadbalancerv2.EntityNotFound(name=models.PoolV2.NAME,
                                                id=pool_id)

    def create_pool_member(self, context, pool_id, member):
        member = member.get('member')
        self.db.check_subnet_exists(context, member['subnet_id'])
        db_pool = self.db.get_pool(context, pool_id)
        self.db.test_and_set_status(context, models.LoadBalancer,
                                    db_pool.root_loadbalancer.id,
                                    constants.PENDING_UPDATE)
        try:
            member_db = self.db.create_pool_member(context, member, pool_id)
        except Exception as exc:
            self.db.update_loadbalancer_provisioning_status(
                context, db_pool.root_loadbalancer.id)
            raise exc

        driver = self._get_driver_for_loadbalancer(
            context, member_db.pool.loadbalancer_id)
        self._call_driver_operation(context,
                                    driver.member.create,
                                    member_db)

        return self.db.get_pool_member(context, member_db.id).to_api_dict()

    def update_pool_member(self, context, id, pool_id, member):
        self._check_pool_exists(context, pool_id)
        member = member.get('member')
        old_member = self.db.get_pool_member(context, id)
        self.db.test_and_set_status(context, models.MemberV2, id,
                                    constants.PENDING_UPDATE)
        try:
            updated_member = self.db.update_pool_member(context, id, member)
        except Exception as exc:
            self.db.update_loadbalancer_provisioning_status(
                context, old_member.pool.loadbalancer.id)
            raise exc

        driver = self._get_driver_for_loadbalancer(
            context, updated_member.pool.loadbalancer_id)
        self._call_driver_operation(context,
                                    driver.member.update,
                                    updated_member,
                                    old_db_entity=old_member)

        return self.db.get_pool_member(context, id).to_api_dict()

    def delete_pool_member(self, context, id, pool_id):
        self._check_pool_exists(context, pool_id)
        self.db.test_and_set_status(context, models.MemberV2, id,
                                    constants.PENDING_DELETE)
        db_member = self.db.get_pool_member(context, id)

        driver = self._get_driver_for_loadbalancer(
            context, db_member.pool.loadbalancer_id)
        self._call_driver_operation(context,
                                    driver.member.delete,
                                    db_member)

    def get_pool_members(self, context, pool_id, filters=None, fields=None):
        self._check_pool_exists(context, pool_id)
        if not filters:
            filters = {}
        filters['pool_id'] = [pool_id]
        return [mem.to_api_dict() for mem in self.db.get_pool_members(
            context, filters=filters)]

    def get_pool_member(self, context, id, pool_id, fields=None):
        self._check_pool_exists(context, pool_id)
        return self.db.get_pool_member(context, id).to_api_dict()

    def _check_pool_already_has_healthmonitor(self, context, pool_id):
        pool = self.db.get_pool(context, pool_id)
        if pool.healthmonitor:
            raise loadbalancerv2.OneHealthMonitorPerPool(
                pool_id=pool_id, hm_id=pool.healthmonitor.id)

    def create_healthmonitor(self, context, healthmonitor):
        healthmonitor = healthmonitor.get('healthmonitor')
        pool_id = healthmonitor.pop('pool_id')
        self._check_pool_exists(context, pool_id)
        self._check_pool_already_has_healthmonitor(context, pool_id)
        db_pool = self.db.get_pool(context, pool_id)
        self.db.test_and_set_status(context, models.LoadBalancer,
                                    db_pool.root_loadbalancer.id,
                                    constants.PENDING_UPDATE)
        try:
            db_hm = self.db.create_healthmonitor_on_pool(context, pool_id,
                                                         healthmonitor)
        except Exception as exc:
            self.db.update_loadbalancer_provisioning_status(
                context, db_pool.root_loadbalancer.id)
            raise exc
        driver = self._get_driver_for_loadbalancer(
            context, db_hm.pool.loadbalancer_id)
        self._call_driver_operation(context,
                                    driver.health_monitor.create,
                                    db_hm)
        return self.db.get_healthmonitor(context, db_hm.id).to_api_dict()

    def update_healthmonitor(self, context, id, healthmonitor):
        healthmonitor = healthmonitor.get('healthmonitor')
        old_hm = self.db.get_healthmonitor(context, id)
        self.db.test_and_set_status(context, models.HealthMonitorV2, id,
                                    constants.PENDING_UPDATE)
        try:
            updated_hm = self.db.update_healthmonitor(context, id,
                                                      healthmonitor)
        except Exception as exc:
            self.db.update_loadbalancer_provisioning_status(
                context, old_hm.root_loadbalancer.id)
            raise exc

        driver = self._get_driver_for_loadbalancer(
            context, updated_hm.pool.loadbalancer_id)
        self._call_driver_operation(context,
                                    driver.health_monitor.update,
                                    updated_hm,
                                    old_db_entity=old_hm)

        return self.db.get_healthmonitor(context, updated_hm.id).to_api_dict()

    def delete_healthmonitor(self, context, id):
        self.db.test_and_set_status(context, models.HealthMonitorV2, id,
                                    constants.PENDING_DELETE)
        db_hm = self.db.get_healthmonitor(context, id)

        driver = self._get_driver_for_loadbalancer(
            context, db_hm.pool.loadbalancer_id)
        self._call_driver_operation(
            context, driver.health_monitor.delete, db_hm)

    def get_healthmonitor(self, context, id, fields=None):
        return self.db.get_healthmonitor(context, id).to_api_dict()

    def get_healthmonitors(self, context, filters=None, fields=None):
        return [hm.to_api_dict() for hm in self.db.get_healthmonitors(
            context, filters=filters)]

    def stats(self, context, loadbalancer_id):
        lb = self.db.get_loadbalancer(context, loadbalancer_id)
        driver = self._get_driver_for_loadbalancer(context, loadbalancer_id)
        stats_data = driver.load_balancer.stats(context, lb)
        # if we get something from the driver -
        # update the db and return the value from db
        # else - return what we have in db
        if stats_data:
            self.db.update_loadbalancer_stats(context, loadbalancer_id,
                                              stats_data)
        db_stats = self.db.stats(context, loadbalancer_id)
        return {'stats': db_stats.to_api_dict()}

    def create_l7policy(self, context, l7policy):
        l7policy = l7policy.get('l7policy')
        l7policy_db = self.db.create_l7policy(context, l7policy)

        if l7policy_db.attached_to_loadbalancer():
            driver = self._get_driver_for_loadbalancer(
                context, l7policy_db.listener.loadbalancer_id)
            self._call_driver_operation(context,
                                        driver.l7policy.create,
                                        l7policy_db)

        return l7policy_db.to_dict()

    def update_l7policy(self, context, id, l7policy):
        l7policy = l7policy.get('l7policy')
        old_l7policy = self.db.get_l7policy(context, id)
        self.db.test_and_set_status(context, models.L7Policy, id,
                                    constants.PENDING_UPDATE)
        try:
            updated_l7policy = self.db.update_l7policy(
                context, id, l7policy)
        except Exception as exc:
            self.db.update_loadbalancer_provisioning_status(
                context, old_l7policy.root_loadbalancer.id)
            raise exc

        if (updated_l7policy.attached_to_loadbalancer() or
                old_l7policy.attached_to_loadbalancer()):
            if updated_l7policy.attached_to_loadbalancer():
                driver = self._get_driver_for_loadbalancer(
                    context, updated_l7policy.listener.loadbalancer_id)
            else:
                driver = self._get_driver_for_loadbalancer(
                    context, old_l7policy.listener.loadbalancer_id)
            self._call_driver_operation(context,
                                        driver.l7policy.update,
                                        updated_l7policy,
                                        old_db_entity=old_l7policy)

        return self.db.get_l7policy(context, updated_l7policy.id).to_api_dict()

    def delete_l7policy(self, context, id):
        self.db.test_and_set_status(context, models.L7Policy, id,
                                    constants.PENDING_DELETE)
        l7policy_db = self.db.get_l7policy(context, id)

        if l7policy_db.attached_to_loadbalancer():
            driver = self._get_driver_for_loadbalancer(
                context, l7policy_db.listener.loadbalancer_id)
            self._call_driver_operation(context, driver.l7policy.delete,
                                        l7policy_db)
        else:
            self.db.delete_l7policy(context, id)

    def get_l7policies(self, context, filters=None, fields=None):
        return [policy.to_api_dict() for policy in self.db.get_l7policies(
            context, filters=filters)]

    def get_l7policy(self, context, id, fields=None):
        return self.db.get_l7policy(context, id).to_api_dict()

    def _check_l7policy_exists(self, context, l7policy_id):
        if not self.db._resource_exists(context, models.L7Policy, l7policy_id):
            raise loadbalancerv2.EntityNotFound(name=models.L7Policy.NAME,
                                                id=l7policy_id)

    def create_l7policy_rule(self, context, rule, l7policy_id):
        rule = rule.get('rule')
        rule_db = self.db.create_l7policy_rule(context, rule, l7policy_id)

        if rule_db.attached_to_loadbalancer():
            driver = self._get_driver_for_loadbalancer(
                context, rule_db.policy.listener.loadbalancer_id)
            self._call_driver_operation(context,
                                        driver.l7rule.create,
                                        rule_db)
        else:
            self.db.update_status(context, models.L7Rule, rule_db.id,
                                  lb_const.DEFERRED)

        return rule_db.to_dict()

    def update_l7policy_rule(self, context, id, rule, l7policy_id):
        rule = rule.get('rule')
        old_rule_db = self.db.get_l7policy_rule(context, id, l7policy_id)
        self.db.test_and_set_status(context, models.L7Rule, id,
                                    constants.PENDING_UPDATE)
        try:
            upd_rule_db = self.db.update_l7policy_rule(
                context, id, rule, l7policy_id)
        except Exception as exc:
            self.db.update_loadbalancer_provisioning_status(
                context, old_rule_db.root_loadbalancer.id)
            raise exc

        if (upd_rule_db.attached_to_loadbalancer() or
                old_rule_db.attached_to_loadbalancer()):
            if upd_rule_db.attached_to_loadbalancer():
                driver = self._get_driver_for_loadbalancer(
                    context, upd_rule_db.policy.listener.loadbalancer_id)
            else:
                driver = self._get_driver_for_loadbalancer(
                    context, old_rule_db.policy.listener.loadbalancer_id)
            self._call_driver_operation(context,
                                        driver.l7rule.update,
                                        upd_rule_db,
                                        old_db_entity=old_rule_db)
        else:
            self.db.update_status(context, models.L7Rule, id,
                                  lb_const.DEFERRED)

        return upd_rule_db.to_dict()

    def delete_l7policy_rule(self, context, id, l7policy_id):
        self.db.test_and_set_status(context, models.L7Rule, id,
                                    constants.PENDING_DELETE)
        rule_db = self.db.get_l7policy_rule(context, id, l7policy_id)

        if rule_db.attached_to_loadbalancer():
            driver = self._get_driver_for_loadbalancer(
                context, rule_db.policy.listener.loadbalancer_id)
            self._call_driver_operation(context, driver.l7rule.delete,
                                        rule_db)
        else:
            self.db.delete_l7policy_rule(context, id, l7policy_id)

    def get_l7policy_rules(self, context, l7policy_id,
                           filters=None, fields=None):
        self._check_l7policy_exists(context, l7policy_id)
        return [rule.to_api_dict() for rule in self.db.get_l7policy_rules(
            context, l7policy_id, filters=filters)]

    def get_l7policy_rule(self, context, id, l7policy_id, fields=None):
        self._check_l7policy_exists(context, l7policy_id)
        return self.db.get_l7policy_rule(
            context, id, l7policy_id).to_api_dict()

    def validate_provider(self, provider):
        if provider not in self.drivers:
            raise pconf.ServiceProviderNotFound(
                provider=provider, service_type=constants.LOADBALANCERV2)

    def _default_status(self, obj, exclude=None, **kw):
        exclude = exclude or []
        status = {}
        status["id"] = obj.id
        if "provisioning_status" not in exclude:
            status["provisioning_status"] = obj.provisioning_status
        if "operating_status" not in exclude:
            status["operating_status"] = obj.operating_status
        for key, value in six.iteritems(kw):
            status[key] = value
        try:
            status['name'] = getattr(obj, 'name')
        except AttributeError:
            pass
        return status

    def _disable_entity_and_children(self, obj):
        DISABLED = lb_const.DISABLED
        d = {}
        if isinstance(obj, data_models.LoadBalancer):
            d = {'loadbalancer': {'id': obj.id, 'operating_status': DISABLED,
                'provisioning_status': obj.provisioning_status,
                'name': obj.name, 'listeners': []}}
            for listener in obj.listeners:
                listener_dict = self._disable_entity_and_children(listener)
                d['loadbalancer']['listeners'].append(listener_dict)
        if isinstance(obj, data_models.Listener):
            d = {'id': obj.id, 'operating_status': DISABLED,
                 'provisioning_status': obj.provisioning_status,
                 'name': obj.name, 'pools': [], 'l7policies': []}
            if obj.default_pool:
                pool_dict = self._disable_entity_and_children(obj.default_pool)
                d['pools'].append(pool_dict)
            for policy in obj.l7_policies:
                policy_dict = self._disable_entity_and_children(policy)
                d['l7policies'].append(policy_dict)
        if isinstance(obj, data_models.L7Policy):
            d = {'id': obj.id,
                 'provisioning_status': obj.provisioning_status,
                 'name': obj.name, 'rules': []}
            for rule in obj.rules:
                rule_dict = self._disable_entity_and_children(rule)
                d['rules'].append(rule_dict)
        if isinstance(obj, data_models.L7Rule):
            d = {'id': obj.id,
                 'provisioning_status': obj.provisioning_status,
                 'type': obj.type}
        if isinstance(obj, data_models.Pool):
            d = {'id': obj.id, 'operating_status': DISABLED,
                 'provisioning_status': obj.provisioning_status,
                 'name': obj.name, 'members': [], 'healthmonitor': {}}
            for member in obj.members:
                member_dict = self._disable_entity_and_children(member)
                d['members'].append(member_dict)
            d['healthmonitor'] = self._disable_entity_and_children(
                obj.healthmonitor)
        if isinstance(obj, data_models.HealthMonitor):
            d = {'id': obj.id, 'provisioning_status': obj.provisioning_status,
                 'type': obj.type}
        if isinstance(obj, data_models.Member):
            d = {'id': obj.id, 'operating_status': DISABLED,
                 'provisioning_status': obj.provisioning_status,
                 'address': obj.address, 'protocol_port': obj.protocol_port}
        return d

    def statuses(self, context, loadbalancer_id):
        OS = "operating_status"
        lb = self.db.get_loadbalancer(context, loadbalancer_id)
        if not lb.admin_state_up:
            return {"statuses": self._disable_entity_and_children(lb)}
        lb_status = self._default_status(lb, listeners=[], pools=[])
        statuses = {"statuses": {"loadbalancer": lb_status}}
        if self._is_degraded(lb):
            self._set_degraded(lb_status)
        for curr_listener in lb.listeners:
            if not curr_listener.admin_state_up:
                lb_status["listeners"].append(
                    self._disable_entity_and_children(curr_listener)
                )
                continue
            listener_status = self._default_status(curr_listener,
                                                   pools=[], l7policies=[])
            lb_status["listeners"].append(listener_status)
            if self._is_degraded(curr_listener):
                self._set_degraded(lb_status)

            for policy in curr_listener.l7_policies:
                if not policy.admin_state_up:
                    listener_status["l7policies"].append(
                        self._disable_entity_and_children(policy))
                    continue
                policy_opts = {"action": policy.action, "rules": []}
                policy_status = self._default_status(policy, exclude=[OS],
                                                     **policy_opts)
                listener_status["l7policies"].append(policy_status)
                if self._is_degraded(policy, exclude=[OS]):
                    self._set_degraded(policy_status, listener_status,
                                       lb_status)
                for rule in policy.rules:
                    if not rule.admin_state_up:
                        policy_status["rules"].append(
                            self._disable_entity_and_children(rule))
                        continue
                    rule_opts = {"type": rule.type}
                    rule_status = self._default_status(rule, exclude=[OS],
                                                       **rule_opts)
                    policy_status["rules"].append(rule_status)
                    if self._is_degraded(rule, exclude=[OS]):
                        self._set_degraded(rule_status, policy_status,
                                           listener_status,
                                           lb_status)
            if not curr_listener.default_pool:
                continue
            if not curr_listener.default_pool.admin_state_up:
                listener_status["pools"].append(
                    self._disable_entity_and_children(
                        curr_listener.default_pool))
                continue
            pool_status = self._default_status(curr_listener.default_pool,
                                              members=[], healthmonitor={})
            listener_status["pools"].append(pool_status)
            if (pool_status["id"] not in
                [ps["id"] for ps in lb_status["pools"]]):
                lb_status["pools"].append(pool_status)
            if self._is_degraded(curr_listener.default_pool):
                self._set_degraded(self, listener_status, lb_status)
            members = curr_listener.default_pool.members
            for curr_member in members:
                if not curr_member.admin_state_up:
                    pool_status["members"].append(
                        self._disable_entity_and_children(curr_member))
                    continue
                member_opts = {"address": curr_member.address,
                               "protocol_port": curr_member.protocol_port}
                member_status = self._default_status(curr_member,
                                                     **member_opts)
                pool_status["members"].append(member_status)
                if self._is_degraded(curr_member):
                    self._set_degraded(pool_status, listener_status,
                                       lb_status)
            healthmonitor = curr_listener.default_pool.healthmonitor
            if healthmonitor:
                if not healthmonitor.admin_state_up:
                    dhm = self._disable_entity_and_children(healthmonitor)
                    hm_status = dhm
                else:
                    hm_status = self._default_status(healthmonitor,
                                exclude=[OS], type=healthmonitor.type)
                    if self._is_degraded(healthmonitor, exclude=[OS]):
                        self._set_degraded(pool_status, listener_status,
                                           lb_status)
            else:
                hm_status = {}
            pool_status["healthmonitor"] = hm_status

        # Needed for pools not associated with a listener
        for curr_pool in lb.pools:
            if curr_pool.id in [ps["id"] for ps in lb_status["pools"]]:
                continue
            if not curr_pool.admin_state_up:
                lb_status["pools"].append(
                    self._disable_entity_and_children(curr_pool))
                continue
            pool_status = self._default_status(curr_pool, members=[],
                                               healthmonitor={})
            lb_status["pools"].append(pool_status)
            if self._is_degraded(curr_pool):
                self._set_degraded(lb_status)
            members = curr_pool.members
            for curr_member in members:
                if not curr_member.admin_state_up:
                    pool_status["members"].append(
                        self._disable_entity_and_children(curr_member))
                    continue
                member_opts = {"address": curr_member.address,
                               "protocol_port": curr_member.protocol_port}
                member_status = self._default_status(curr_member,
                                                     **member_opts)
                pool_status["members"].append(member_status)
                if self._is_degraded(curr_member):
                    self._set_degraded(pool_status, lb_status)
            healthmonitor = curr_pool.healthmonitor
            if healthmonitor:
                if not healthmonitor.admin_state_up:
                    dhm = self._disable_entity_and_children(healthmonitor)
                    pool_status["healthmonitor"] = dhm
                else:
                    hm_status = self._default_status(healthmonitor,
                                exclude=[OS], type=healthmonitor.type)
                    if self._is_degraded(healthmonitor, exclude=[OS]):
                        self._set_degraded(pool_status, listener_status,
                                           lb_status)
            else:
                hm_status = {}
            pool_status["healthmonitor"] = hm_status
        return statuses

    def _set_degraded(self, *objects):
        for obj in objects:
            obj["operating_status"] = lb_const.DEGRADED

    def _is_degraded(self, obj, exclude=None):
        exclude = exclude or []
        if "provisioning_status" not in exclude:
            if obj.provisioning_status == constants.ERROR:
                return True
        if "operating_status" not in exclude:
            if ((obj.operating_status != lb_const.ONLINE) and
                (obj.operating_status != lb_const.NO_MONITOR)):
                return True
        return False

    # NOTE(brandon-logan): these need to be concrete methods because the
    # neutron request pipeline calls these methods before the plugin methods
    # are ever called
    def get_members(self, context, filters=None, fields=None):
        pass

    def get_member(self, context, id, fields=None):
        pass
