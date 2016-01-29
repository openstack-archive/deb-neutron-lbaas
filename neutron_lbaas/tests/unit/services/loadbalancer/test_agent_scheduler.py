# Copyright (c) 2013 OpenStack Foundation.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import mock
from neutron.api import extensions
from neutron.api.v2 import attributes
from neutron.common import constants
from neutron import context
from neutron.extensions import agent
from neutron import manager
from neutron.plugins.common import constants as plugin_const
from neutron.tests.common import helpers
from neutron.tests.unit.api import test_extensions
from neutron.tests.unit.db import test_agentschedulers_db
from neutron.tests.unit.extensions import test_agent
from oslo_config import cfg
import six
from webob import exc

from neutron_lbaas.extensions import lbaas_agentscheduler
from neutron_lbaas.extensions import loadbalancer
from neutron_lbaas.tests import base
from neutron_lbaas.tests.unit.db.loadbalancer import test_db_loadbalancer

LBAAS_HOSTA = 'hosta'


class AgentSchedulerTestMixIn(test_agentschedulers_db.AgentSchedulerTestMixIn):
    def _list_pools_hosted_by_lbaas_agent(self, agent_id,
                                          expected_code=exc.HTTPOk.code,
                                          admin_context=True):
        path = "/agents/%s/%s.%s" % (agent_id,
                                     lbaas_agentscheduler.LOADBALANCER_POOLS,
                                     self.fmt)
        return self._request_list(path, expected_code=expected_code,
                                  admin_context=admin_context)

    def _get_lbaas_agent_hosting_pool(self, pool_id,
                                      expected_code=exc.HTTPOk.code,
                                      admin_context=True):
        path = "/lb/pools/%s/%s.%s" % (pool_id,
                                       lbaas_agentscheduler.LOADBALANCER_AGENT,
                                       self.fmt)
        return self._request_list(path, expected_code=expected_code,
                                  admin_context=admin_context)


class LBaaSAgentSchedulerTestCase(test_agent.AgentDBTestMixIn,
                                  AgentSchedulerTestMixIn,
                                  test_db_loadbalancer.LoadBalancerTestMixin,
                                  base.NeutronDbPluginV2TestCase):
    fmt = 'json'
    plugin_str = 'neutron.plugins.ml2.plugin.Ml2Plugin'

    def setUp(self):
        # Save the global RESOURCE_ATTRIBUTE_MAP
        self.saved_attr_map = {}
        for res, attrs in six.iteritems(attributes.RESOURCE_ATTRIBUTE_MAP):
            self.saved_attr_map[res] = attrs.copy()
        service_plugins = {
            'lb_plugin_name': test_db_loadbalancer.DB_LB_PLUGIN_KLASS}

        # default provider should support agent scheduling
        self.set_override([('LOADBALANCER:lbaas:neutron_lbaas.services.'
              'loadbalancer.drivers.haproxy.plugin_driver.'
              'HaproxyOnHostPluginDriver:default')])

        super(LBaaSAgentSchedulerTestCase, self).setUp(
            self.plugin_str, service_plugins=service_plugins)
        ext_mgr = extensions.PluginAwareExtensionManager.get_instance()
        self.ext_api = test_extensions.setup_extensions_middleware(ext_mgr)
        self.adminContext = context.get_admin_context()
        # Add the resources to the global attribute map
        # This is done here as the setup process won't
        # initialize the main API router which extends
        # the global attribute map
        attributes.RESOURCE_ATTRIBUTE_MAP.update(
            agent.RESOURCE_ATTRIBUTE_MAP)
        self.addCleanup(self.restore_attribute_map)

    def restore_attribute_map(self):
        # Restore the original RESOURCE_ATTRIBUTE_MAP
        attributes.RESOURCE_ATTRIBUTE_MAP = self.saved_attr_map

    def test_report_states(self):
        self._register_agent_states(lbaas_agents=True)
        agents = self._list_agents()
        self.assertEqual(6, len(agents['agents']))

    def test_pool_scheduling_on_pool_creation(self):
        self._register_agent_states(lbaas_agents=True)
        with self.pool() as pool:
            lbaas_agent = self._get_lbaas_agent_hosting_pool(
                pool['pool']['id'])
            self.assertIsNotNone(lbaas_agent)
            self.assertEqual(constants.AGENT_TYPE_LOADBALANCER,
                             lbaas_agent['agent']['agent_type'])
            pools = self._list_pools_hosted_by_lbaas_agent(
                lbaas_agent['agent']['id'])
            self.assertEqual(1, len(pools['pools']))
            self.assertEqual(pool['pool'], pools['pools'][0])

    def test_schedule_pool_with_disabled_agent(self):
        lbaas_hosta = {
            'binary': 'neutron-loadbalancer-agent',
            'host': LBAAS_HOSTA,
            'topic': 'LOADBALANCER_AGENT',
            'configurations': {'device_drivers': ['haproxy_ns']},
            'agent_type': constants.AGENT_TYPE_LOADBALANCER}
        helpers._register_agent(lbaas_hosta)
        with self.pool() as pool:
            lbaas_agent = self._get_lbaas_agent_hosting_pool(
                pool['pool']['id'])
            self.assertIsNotNone(lbaas_agent)

        agents = self._list_agents()
        self._disable_agent(agents['agents'][0]['id'])
        pool = {'pool': {'name': 'test',
                         'subnet_id': 'test',
                         'lb_method': 'ROUND_ROBIN',
                         'protocol': 'HTTP',
                         'admin_state_up': True,
                         'tenant_id': 'test',
                         'description': 'test'}}
        lbaas_plugin = manager.NeutronManager.get_service_plugins()[
            plugin_const.LOADBALANCER]
        self.assertRaises(loadbalancer.NoEligibleBackend,
                          lbaas_plugin.create_pool, self.adminContext, pool)
        pools = lbaas_plugin.get_pools(self.adminContext)
        self.assertEqual('ERROR', pools[0]['status'])
        self.assertEqual('No eligible backend',
                         pools[0]['status_description'])

    def test_schedule_pool_with_down_agent(self):
        lbaas_hosta = {
            'binary': 'neutron-loadbalancer-agent',
            'host': LBAAS_HOSTA,
            'topic': 'LOADBALANCER_AGENT',
            'configurations': {'device_drivers': ['haproxy_ns']},
            'agent_type': constants.AGENT_TYPE_LOADBALANCER}
        helpers._register_agent(lbaas_hosta)
        is_agent_down_str = 'neutron.db.agents_db.AgentDbMixin.is_agent_down'
        with mock.patch(is_agent_down_str) as mock_is_agent_down:
            mock_is_agent_down.return_value = False
            with self.pool() as pool:
                lbaas_agent = self._get_lbaas_agent_hosting_pool(
                    pool['pool']['id'])
            self.assertIsNotNone(lbaas_agent)
        with mock.patch(is_agent_down_str) as mock_is_agent_down:
            mock_is_agent_down.return_value = True
            pool = {'pool': {'name': 'test',
                             'subnet_id': 'test',
                             'lb_method': 'ROUND_ROBIN',
                             'protocol': 'HTTP',
                             'provider': 'lbaas',
                             'admin_state_up': True,
                             'tenant_id': 'test',
                             'description': 'test'}}
            lbaas_plugin = manager.NeutronManager.get_service_plugins()[
                plugin_const.LOADBALANCER]
            self.assertRaises(loadbalancer.NoEligibleBackend,
                              lbaas_plugin.create_pool,
                              self.adminContext, pool)
            pools = lbaas_plugin.get_pools(self.adminContext)
            self.assertEqual('ERROR', pools[0]['status'])
            self.assertEqual('No eligible backend',
                             pools[0]['status_description'])

    def test_pool_unscheduling_on_pool_deletion(self):
        self._register_agent_states(lbaas_agents=True)
        with self.pool(do_delete=False) as pool:
            lbaas_agent = self._get_lbaas_agent_hosting_pool(
                pool['pool']['id'])
            self.assertIsNotNone(lbaas_agent)
            self.assertEqual(constants.AGENT_TYPE_LOADBALANCER,
                             lbaas_agent['agent']['agent_type'])
            pools = self._list_pools_hosted_by_lbaas_agent(
                lbaas_agent['agent']['id'])
            self.assertEqual(1, len(pools['pools']))
            self.assertEqual(pool['pool'], pools['pools'][0])

            req = self.new_delete_request('pools',
                                          pool['pool']['id'])
            res = req.get_response(self.ext_api)
            self.assertEqual(exc.HTTPNoContent.code, res.status_int)
            pools = self._list_pools_hosted_by_lbaas_agent(
                lbaas_agent['agent']['id'])
            self.assertEqual(0, len(pools['pools']))

    def test_pool_scheduling_non_admin_access(self):
        self._register_agent_states(lbaas_agents=True)
        with self.pool() as pool:
            self._get_lbaas_agent_hosting_pool(
                pool['pool']['id'],
                expected_code=exc.HTTPForbidden.code,
                admin_context=False)
            self._list_pools_hosted_by_lbaas_agent(
                'fake_id',
                expected_code=exc.HTTPForbidden.code,
                admin_context=False)


class LeastPoolAgentSchedulerTestCase(LBaaSAgentSchedulerTestCase):

    def setUp(self):
        # Setting LeastPoolAgentScheduler as scheduler
        cfg.CONF.set_override(
            'loadbalancer_pool_scheduler_driver',
            'neutron_lbaas.services.loadbalancer.'
            'agent_scheduler.LeastPoolAgentScheduler')

        super(LeastPoolAgentSchedulerTestCase, self).setUp()
