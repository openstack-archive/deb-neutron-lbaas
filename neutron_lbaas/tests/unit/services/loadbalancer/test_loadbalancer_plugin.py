# Copyright 2012 OpenStack Foundation.
# All Rights Reserved.
#
#  Licensed under the Apache License, Version 2.0 (the "License"); you may
#  not use this file except in compliance with the License. You may obtain
#  a copy of the License at
#
#       http://www.apache.org/licenses/LICENSE-2.0
#
#  Unless required by applicable law or agreed to in writing, software
#  distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#  WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#  License for the specific language governing permissions and limitations
#  under the License.

import copy

import mock
from neutron.api.v2 import attributes as attr
from neutron.plugins.common import constants
from neutron.tests.unit.api.v2 import test_base
from oslo_utils import uuidutils
from webob import exc

from neutron_lbaas.extensions import loadbalancer
from neutron_lbaas.extensions import loadbalancerv2
from neutron_lbaas.tests import base


_uuid = uuidutils.generate_uuid
_get_path = test_base._get_path


class LoadBalancerExtensionTestCase(base.ExtensionTestCase):
    fmt = 'json'

    def setUp(self):
        super(LoadBalancerExtensionTestCase, self).setUp()
        self._setUpExtension(
            'neutron_lbaas.extensions.loadbalancer.LoadBalancerPluginBase',
            constants.LOADBALANCER, loadbalancer.RESOURCE_ATTRIBUTE_MAP,
            loadbalancer.Loadbalancer, 'lb', use_quota=True)

    def test_vip_create(self):
        vip_id = _uuid()
        data = {'vip': {'name': 'vip1',
                        'description': 'descr_vip1',
                        'subnet_id': _uuid(),
                        'address': '127.0.0.1',
                        'protocol_port': 80,
                        'protocol': 'HTTP',
                        'pool_id': _uuid(),
                        'session_persistence': {'type': 'HTTP_COOKIE'},
                        'connection_limit': 100,
                        'admin_state_up': True,
                        'tenant_id': _uuid()}}
        return_value = copy.copy(data['vip'])
        return_value.update({'status': "ACTIVE", 'id': vip_id})

        instance = self.plugin.return_value
        instance.create_vip.return_value = return_value
        res = self.api.post(_get_path('lb/vips', fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/%s' % self.fmt)
        instance.create_vip.assert_called_with(mock.ANY,
                                               vip=data)
        self.assertEqual(exc.HTTPCreated.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('vip', res)
        self.assertEqual(return_value, res['vip'])

    def test_vip_create_with_connection_limit_smaller_than_min_value(self):
        data = {'vip': {'name': 'vip1',
                        'description': 'descr_vip1',
                        'subnet_id': _uuid(),
                        'address': '127.0.0.1',
                        'protocol_port': 80,
                        'protocol': 'HTTP',
                        'pool_id': _uuid(),
                        'session_persistence': {'type': 'HTTP_COOKIE'},
                        'connection_limit': -4,
                        'admin_state_up': True,
                        'tenant_id': _uuid()}}
        res = self.api.post(_get_path('lb/vips', fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/%s' % self.fmt,
                            expect_errors=True)
        self.assertEqual(exc.HTTPBadRequest.code, res.status_int)

    def test_vip_list(self):
        vip_id = _uuid()
        return_value = [{'name': 'vip1',
                         'admin_state_up': True,
                         'tenant_id': _uuid(),
                         'id': vip_id}]

        instance = self.plugin.return_value
        instance.get_vips.return_value = return_value

        res = self.api.get(_get_path('lb/vips', fmt=self.fmt))

        instance.get_vips.assert_called_with(mock.ANY, fields=mock.ANY,
                                             filters=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)

    def test_vip_update(self):
        vip_id = _uuid()
        update_data = {'vip': {'admin_state_up': False}}
        return_value = {'name': 'vip1',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'status': "ACTIVE",
                        'id': vip_id}

        instance = self.plugin.return_value
        instance.update_vip.return_value = return_value

        res = self.api.put(_get_path('lb/vips', id=vip_id, fmt=self.fmt),
                           self.serialize(update_data))

        instance.update_vip.assert_called_with(mock.ANY, vip_id,
                                               vip=update_data)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('vip', res)
        self.assertEqual(return_value, res['vip'])

    def test_vip_update_with_connection_limit_smaller_than_min_value(self):
        vip_id = _uuid()
        data = {'vip': {'connection_limit': -4}}
        res = self.api.put(_get_path('lb/vips', id=vip_id, fmt=self.fmt),
                           self.serialize(data),
                           content_type='application/%s' % self.fmt,
                           expect_errors=True)
        self.assertEqual(exc.HTTPBadRequest.code, res.status_int)

    def test_vip_get(self):
        vip_id = _uuid()
        return_value = {'name': 'vip1',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'status': "ACTIVE",
                        'id': vip_id}

        instance = self.plugin.return_value
        instance.get_vip.return_value = return_value

        res = self.api.get(_get_path('lb/vips', id=vip_id, fmt=self.fmt))

        instance.get_vip.assert_called_with(mock.ANY, vip_id,
                                            fields=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('vip', res)
        self.assertEqual(return_value, res['vip'])

    def test_vip_delete(self):
        self._test_entity_delete('vip')

    def test_pool_create(self):
        pool_id = _uuid()
        hm_id = _uuid()
        data = {'pool': {'name': 'pool1',
                         'description': 'descr_pool1',
                         'subnet_id': _uuid(),
                         'protocol': 'HTTP',
                         'lb_method': 'ROUND_ROBIN',
                         'health_monitors': [hm_id],
                         'admin_state_up': True,
                         'tenant_id': _uuid()}}
        return_value = copy.copy(data['pool'])
        return_value['provider'] = 'lbaas'
        return_value.update({'status': "ACTIVE", 'id': pool_id})

        instance = self.plugin.return_value
        instance.create_pool.return_value = return_value
        res = self.api.post(_get_path('lb/pools', fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/%s' % self.fmt)
        data['pool']['provider'] = attr.ATTR_NOT_SPECIFIED
        instance.create_pool.assert_called_with(mock.ANY,
                                                pool=data)
        self.assertEqual(exc.HTTPCreated.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('pool', res)
        self.assertEqual(return_value, res['pool'])

    def test_pool_list(self):
        pool_id = _uuid()
        return_value = [{'name': 'pool1',
                         'admin_state_up': True,
                         'tenant_id': _uuid(),
                         'id': pool_id}]

        instance = self.plugin.return_value
        instance.get_pools.return_value = return_value

        res = self.api.get(_get_path('lb/pools', fmt=self.fmt))

        instance.get_pools.assert_called_with(mock.ANY, fields=mock.ANY,
                                              filters=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)

    def test_pool_update(self):
        pool_id = _uuid()
        update_data = {'pool': {'admin_state_up': False}}
        return_value = {'name': 'pool1',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'status': "ACTIVE",
                        'id': pool_id}

        instance = self.plugin.return_value
        instance.update_pool.return_value = return_value

        res = self.api.put(_get_path('lb/pools', id=pool_id, fmt=self.fmt),
                           self.serialize(update_data))

        instance.update_pool.assert_called_with(mock.ANY, pool_id,
                                                pool=update_data)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('pool', res)
        self.assertEqual(return_value, res['pool'])

    def test_pool_get(self):
        pool_id = _uuid()
        return_value = {'name': 'pool1',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'status': "ACTIVE",
                        'id': pool_id}

        instance = self.plugin.return_value
        instance.get_pool.return_value = return_value

        res = self.api.get(_get_path('lb/pools', id=pool_id, fmt=self.fmt))

        instance.get_pool.assert_called_with(mock.ANY, pool_id,
                                             fields=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('pool', res)
        self.assertEqual(return_value, res['pool'])

    def test_pool_delete(self):
        self._test_entity_delete('pool')

    def test_pool_stats(self):
        pool_id = _uuid()

        stats = {'stats': 'dummy'}
        instance = self.plugin.return_value
        instance.stats.return_value = stats

        path = _get_path('lb/pools', id=pool_id,
                         action="stats", fmt=self.fmt)
        res = self.api.get(path)

        instance.stats.assert_called_with(mock.ANY, pool_id)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('stats', res)
        self.assertEqual(stats['stats'], res['stats'])

    def test_member_create(self):
        member_id = _uuid()
        data = {'member': {'pool_id': _uuid(),
                           'address': '127.0.0.1',
                           'protocol_port': 80,
                           'weight': 1,
                           'admin_state_up': True,
                           'tenant_id': _uuid()}}
        return_value = copy.copy(data['member'])
        return_value.update({'status': "ACTIVE", 'id': member_id})

        instance = self.plugin.return_value
        instance.create_member.return_value = return_value
        res = self.api.post(_get_path('lb/members', fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/%s' % self.fmt)
        instance.create_member.assert_called_with(mock.ANY,
                                                  member=data)
        self.assertEqual(exc.HTTPCreated.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('member', res)
        self.assertEqual(return_value, res['member'])

    def test_member_list(self):
        member_id = _uuid()
        return_value = [{'name': 'member1',
                         'admin_state_up': True,
                         'tenant_id': _uuid(),
                         'id': member_id}]

        instance = self.plugin.return_value
        instance.get_members.return_value = return_value

        res = self.api.get(_get_path('lb/members', fmt=self.fmt))

        instance.get_members.assert_called_with(mock.ANY, fields=mock.ANY,
                                                filters=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)

    def test_member_update(self):
        member_id = _uuid()
        update_data = {'member': {'admin_state_up': False}}
        return_value = {'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'status': "ACTIVE",
                        'id': member_id}

        instance = self.plugin.return_value
        instance.update_member.return_value = return_value

        res = self.api.put(_get_path('lb/members', id=member_id,
                                     fmt=self.fmt),
                           self.serialize(update_data))

        instance.update_member.assert_called_with(mock.ANY, member_id,
                                                  member=update_data)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('member', res)
        self.assertEqual(return_value, res['member'])

    def test_member_get(self):
        member_id = _uuid()
        return_value = {'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'status': "ACTIVE",
                        'id': member_id}

        instance = self.plugin.return_value
        instance.get_member.return_value = return_value

        res = self.api.get(_get_path('lb/members', id=member_id,
                                     fmt=self.fmt))

        instance.get_member.assert_called_with(mock.ANY, member_id,
                                               fields=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('member', res)
        self.assertEqual(return_value, res['member'])

    def test_member_delete(self):
        self._test_entity_delete('member')

    def test_health_monitor_create(self):
        health_monitor_id = _uuid()
        data = {'health_monitor': {'type': 'HTTP',
                                   'delay': 2,
                                   'timeout': 1,
                                   'max_retries': 3,
                                   'http_method': 'GET',
                                   'url_path': '/path',
                                   'expected_codes': '200-300',
                                   'admin_state_up': True,
                                   'tenant_id': _uuid()}}
        return_value = copy.copy(data['health_monitor'])
        return_value.update({'status': "ACTIVE", 'id': health_monitor_id})

        instance = self.plugin.return_value
        instance.create_health_monitor.return_value = return_value
        res = self.api.post(_get_path('lb/health_monitors',
                                      fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/%s' % self.fmt)
        instance.create_health_monitor.assert_called_with(mock.ANY,
                                                          health_monitor=data)
        self.assertEqual(exc.HTTPCreated.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('health_monitor', res)
        self.assertEqual(return_value, res['health_monitor'])

    def test_health_monitor_create_with_timeout_negative(self):
        data = {'health_monitor': {'type': 'HTTP',
                                   'delay': 2,
                                   'timeout': -1,
                                   'max_retries': 3,
                                   'http_method': 'GET',
                                   'url_path': '/path',
                                   'expected_codes': '200-300',
                                   'admin_state_up': True,
                                   'tenant_id': _uuid()}}
        res = self.api.post(_get_path('lb/health_monitors',
                                      fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/%s' % self.fmt,
                            expect_errors=True)
        self.assertEqual(400, res.status_int)

    def test_health_monitor_list(self):
        health_monitor_id = _uuid()
        return_value = [{'type': 'HTTP',
                         'admin_state_up': True,
                         'tenant_id': _uuid(),
                         'id': health_monitor_id}]

        instance = self.plugin.return_value
        instance.get_health_monitors.return_value = return_value

        res = self.api.get(_get_path('lb/health_monitors', fmt=self.fmt))

        instance.get_health_monitors.assert_called_with(
            mock.ANY, fields=mock.ANY, filters=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)

    def test_health_monitor_update(self):
        health_monitor_id = _uuid()
        update_data = {'health_monitor': {'admin_state_up': False}}
        return_value = {'type': 'HTTP',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'status': "ACTIVE",
                        'id': health_monitor_id}

        instance = self.plugin.return_value
        instance.update_health_monitor.return_value = return_value

        res = self.api.put(_get_path('lb/health_monitors',
                                     id=health_monitor_id,
                                     fmt=self.fmt),
                           self.serialize(update_data))

        instance.update_health_monitor.assert_called_with(
            mock.ANY, health_monitor_id, health_monitor=update_data)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('health_monitor', res)
        self.assertEqual(return_value, res['health_monitor'])

    def test_health_monitor_get(self):
        health_monitor_id = _uuid()
        return_value = {'type': 'HTTP',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'status': "ACTIVE",
                        'id': health_monitor_id}

        instance = self.plugin.return_value
        instance.get_health_monitor.return_value = return_value

        res = self.api.get(_get_path('lb/health_monitors',
                                     id=health_monitor_id,
                                     fmt=self.fmt))

        instance.get_health_monitor.assert_called_with(
            mock.ANY, health_monitor_id, fields=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('health_monitor', res)
        self.assertEqual(return_value, res['health_monitor'])

    def test_health_monitor_delete(self):
        self._test_entity_delete('health_monitor')

    def test_create_pool_health_monitor(self):
        health_monitor_id = _uuid()
        data = {'health_monitor': {'id': health_monitor_id,
                                   'tenant_id': _uuid()}}

        return_value = copy.copy(data['health_monitor'])
        instance = self.plugin.return_value
        instance.create_pool_health_monitor.return_value = return_value
        res = self.api.post('/lb/pools/id1/health_monitors',
                            self.serialize(data),
                            content_type='application/%s' % self.fmt)
        instance.create_pool_health_monitor.assert_called_with(
            mock.ANY, pool_id='id1', health_monitor=data)
        self.assertEqual(exc.HTTPCreated.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('health_monitor', res)
        self.assertEqual(return_value, res['health_monitor'])

    def test_delete_pool_health_monitor(self):
        health_monitor_id = _uuid()

        res = self.api.delete('/lb/pools/id1/health_monitors/%s' %
                              health_monitor_id)

        instance = self.plugin.return_value
        instance.delete_pool_health_monitor.assert_called_with(
            mock.ANY, health_monitor_id, pool_id='id1')
        self.assertEqual(exc.HTTPNoContent.code, res.status_int)


class LoadBalancerExtensionV2TestCase(base.ExtensionTestCase):
    fmt = 'json'

    def setUp(self):
        super(LoadBalancerExtensionV2TestCase, self).setUp()
        self._setUpExtension(
            'neutron_lbaas.extensions.loadbalancerv2.LoadBalancerPluginBaseV2',
            constants.LOADBALANCERV2, loadbalancerv2.RESOURCE_ATTRIBUTE_MAP,
            loadbalancerv2.Loadbalancerv2, 'lbaas', use_quota=True)

    def test_loadbalancer_create(self):
        lb_id = _uuid()
        data = {'loadbalancer': {'name': 'lb1',
                                 'description': 'descr_lb1',
                                 'tenant_id': _uuid(),
                                 'vip_subnet_id': _uuid(),
                                 'admin_state_up': True,
                                 'vip_address': '127.0.0.1'}}
        return_value = copy.copy(data['loadbalancer'])
        return_value.update({'id': lb_id})

        instance = self.plugin.return_value
        instance.create_loadbalancer.return_value = return_value

        res = self.api.post(_get_path('lbaas/loadbalancers', fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/{0}'.format(self.fmt))
        data['loadbalancer'].update({'provider': attr.ATTR_NOT_SPECIFIED,
                                     'flavor_id': attr.ATTR_NOT_SPECIFIED})
        instance.create_loadbalancer.assert_called_with(mock.ANY,
                                                        loadbalancer=data)

        self.assertEqual(exc.HTTPCreated.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('loadbalancer', res)
        self.assertEqual(return_value, res['loadbalancer'])

    def test_loadbalancer_create_invalid_flavor(self):
        data = {'loadbalancer': {'name': 'lb1',
                                 'description': 'descr_lb1',
                                 'tenant_id': _uuid(),
                                 'vip_subnet_id': _uuid(),
                                 'admin_state_up': True,
                                 'flavor_id': 123,
                                 'vip_address': '127.0.0.1'}}
        res = self.api.post(_get_path('lbaas/loadbalancers', fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/{0}'.format(self.fmt),
                            expect_errors=True)
        self.assertEqual(400, res.status_int)

    def test_loadbalancer_create_valid_flavor(self):
        data = {'loadbalancer': {'name': 'lb1',
                                 'description': 'descr_lb1',
                                 'tenant_id': _uuid(),
                                 'vip_subnet_id': _uuid(),
                                 'admin_state_up': True,
                                 'flavor_id': _uuid(),
                                 'vip_address': '127.0.0.1'}}
        res = self.api.post(_get_path('lbaas/loadbalancers', fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/{0}'.format(self.fmt),
                            expect_errors=True)
        self.assertEqual(201, res.status_int)

    def test_loadbalancer_list(self):
        lb_id = _uuid()
        return_value = [{'name': 'lb1',
                         'admin_state_up': True,
                         'tenant_id': _uuid(),
                         'id': lb_id}]

        instance = self.plugin.return_value
        instance.get_loadbalancers.return_value = return_value

        res = self.api.get(_get_path('lbaas/loadbalancers', fmt=self.fmt))

        instance.get_loadbalancers.assert_called_with(mock.ANY,
                                                      fields=mock.ANY,
                                                      filters=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)

    def test_loadbalancer_update(self):
        lb_id = _uuid()
        update_data = {'loadbalancer': {'admin_state_up': False}}
        return_value = {'name': 'lb1',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'id': lb_id}

        instance = self.plugin.return_value
        instance.update_loadbalancer.return_value = return_value

        res = self.api.put(_get_path('lbaas/loadbalancers',
                                     id=lb_id,
                                     fmt=self.fmt),
                           self.serialize(update_data))

        instance.update_loadbalancer.assert_called_with(
            mock.ANY, lb_id, loadbalancer=update_data)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('loadbalancer', res)
        self.assertEqual(return_value, res['loadbalancer'])

    def test_loadbalancer_get(self):
        lb_id = _uuid()
        return_value = {'name': 'lb1',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'id': lb_id}

        instance = self.plugin.return_value
        instance.get_loadbalancer.return_value = return_value

        res = self.api.get(_get_path('lbaas/loadbalancers',
                                     id=lb_id,
                                     fmt=self.fmt))

        instance.get_loadbalancer.assert_called_with(mock.ANY, lb_id,
                                                     fields=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('loadbalancer', res)
        self.assertEqual(return_value, res['loadbalancer'])

    def test_loadbalancer_delete(self):
        self._test_entity_delete('loadbalancer')

    def test_listener_create(self):
        listener_id = _uuid()
        data = {'listener': {'tenant_id': _uuid(),
                             'name': 'listen-name-1',
                             'description': 'listen-1-desc',
                             'protocol': 'HTTP',
                             'protocol_port': 80,
                             'default_tls_container_ref': None,
                             'sni_container_refs': [],
                             'connection_limit': 100,
                             'admin_state_up': True,
                             'loadbalancer_id': _uuid()}}
        return_value = copy.copy(data['listener'])
        return_value.update({'id': listener_id})
        del return_value['loadbalancer_id']

        instance = self.plugin.return_value
        instance.create_listener.return_value = return_value

        res = self.api.post(_get_path('lbaas/listeners', fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/{0}'.format(self.fmt))
        instance.create_listener.assert_called_with(mock.ANY,
                                                    listener=data)

        self.assertEqual(exc.HTTPCreated.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('listener', res)
        self.assertEqual(return_value, res['listener'])

    def test_listener_create_with_tls(self):
        listener_id = _uuid()
        tls_ref = 'http://example.ref/uuid'
        sni_refs = ['http://example.ref/uuid',
                    'http://example.ref/uuid1']
        data = {'listener': {'tenant_id': _uuid(),
                             'name': 'listen-name-1',
                             'description': 'listen-1-desc',
                             'protocol': 'HTTP',
                             'protocol_port': 80,
                             'default_tls_container_ref': tls_ref,
                             'sni_container_refs': sni_refs,
                             'connection_limit': 100,
                             'admin_state_up': True,
                             'loadbalancer_id': _uuid()}}
        return_value = copy.copy(data['listener'])
        return_value.update({'id': listener_id})
        del return_value['loadbalancer_id']

        instance = self.plugin.return_value
        instance.create_listener.return_value = return_value

        res = self.api.post(_get_path('lbaas/listeners', fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/{0}'.format(self.fmt))
        instance.create_listener.assert_called_with(mock.ANY,
                                                    listener=data)

        self.assertEqual(res.status_int, exc.HTTPCreated.code)
        res = self.deserialize(res)
        self.assertIn('listener', res)
        self.assertEqual(res['listener'], return_value)

    def test_listener_create_with_connection_limit_less_than_min_value(self):
        data = {'listener': {'tenant_id': _uuid(),
                             'name': 'listen-name-1',
                             'description': 'listen-1-desc',
                             'protocol': 'HTTP',
                             'protocol_port': 80,
                             'default_tls_container_ref': None,
                             'sni_container_refs': [],
                             'connection_limit': -4,
                             'admin_state_up': True,
                             'loadbalancer_id': _uuid()}}

        res = self.api.post(_get_path('lbaas/listeners', fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/{0}'.format(self.fmt),
                            expect_errors=True)
        self.assertEqual(exc.HTTPBadRequest.code, res.status_int)

    def test_listener_list(self):
        listener_id = _uuid()
        return_value = [{'admin_state_up': True,
                         'tenant_id': _uuid(),
                         'id': listener_id}]

        instance = self.plugin.return_value
        instance.get_listeners.return_value = return_value

        res = self.api.get(_get_path('lbaas/listeners', fmt=self.fmt))

        instance.get_listeners.assert_called_with(mock.ANY,
                                                  fields=mock.ANY,
                                                  filters=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)

    def test_listener_update(self):
        listener_id = _uuid()
        update_data = {'listener': {'admin_state_up': False}}
        return_value = {'name': 'listener1',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'id': listener_id}

        instance = self.plugin.return_value
        instance.update_listener.return_value = return_value

        res = self.api.put(_get_path('lbaas/listeners',
                                     id=listener_id,
                                     fmt=self.fmt),
                           self.serialize(update_data))

        instance.update_listener.assert_called_with(
            mock.ANY, listener_id, listener=update_data)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('listener', res)
        self.assertEqual(return_value, res['listener'])

    def test_listener_update_with_tls(self):
        listener_id = _uuid()
        tls_ref = 'http://example.ref/uuid'
        sni_refs = ['http://example.ref/uuid',
                    'http://example.ref/uuid1']
        update_data = {'listener': {'admin_state_up': False}}
        return_value = {'name': 'listener1',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'id': listener_id,
                        'default_tls_container_ref': tls_ref,
                        'sni_container_refs': sni_refs}

        instance = self.plugin.return_value
        instance.update_listener.return_value = return_value

        res = self.api.put(_get_path('lbaas/listeners',
                                     id=listener_id,
                                     fmt=self.fmt),
                           self.serialize(update_data))

        instance.update_listener.assert_called_with(
            mock.ANY, listener_id, listener=update_data)
        self.assertEqual(res.status_int, exc.HTTPOk.code)
        res = self.deserialize(res)
        self.assertIn('listener', res)
        self.assertEqual(res['listener'], return_value)

    def test_listener_update_with_connection_limit_less_than_min_value(self):
        listener_id = _uuid()
        update_data = {'listener': {'connection_limit': -4}}
        res = self.api.put(_get_path('lbaas/listeners',
                                     id=listener_id,
                                     fmt=self.fmt),
                           self.serialize(update_data),
                           expect_errors=True)
        self.assertEqual(exc.HTTPBadRequest.code, res.status_int)

    def test_listener_get(self):
        listener_id = _uuid()
        return_value = {'name': 'listener1',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'id': listener_id}

        instance = self.plugin.return_value
        instance.get_listener.return_value = return_value

        res = self.api.get(_get_path('lbaas/listeners',
                                     id=listener_id,
                                     fmt=self.fmt))

        instance.get_listener.assert_called_with(mock.ANY, listener_id,
                                                 fields=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('listener', res)
        self.assertEqual(return_value, res['listener'])

    def test_listener_delete(self):
        self._test_entity_delete('listener')

    def test_pool_create(self):
        pool_id = _uuid()
        data = {'pool': {'name': 'pool1',
                         'description': 'descr_pool1',
                         'protocol': 'HTTP',
                         'lb_algorithm': 'ROUND_ROBIN',
                         'admin_state_up': True,
                         'tenant_id': _uuid(),
                         'listener_id': _uuid(),
                         'session_persistence': {}}}
        return_value = copy.copy(data['pool'])
        return_value.update({'id': pool_id})
        del return_value['listener_id']

        instance = self.plugin.return_value
        instance.create_pool.return_value = return_value
        res = self.api.post(_get_path('lbaas/pools', fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/%s' % self.fmt)
        instance.create_pool.assert_called_with(mock.ANY, pool=data)
        self.assertEqual(exc.HTTPCreated.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('pool', res)
        self.assertEqual(return_value, res['pool'])

    def test_pool_list(self):
        pool_id = _uuid()
        return_value = [{'name': 'pool1',
                         'admin_state_up': True,
                         'tenant_id': _uuid(),
                         'id': pool_id}]

        instance = self.plugin.return_value
        instance.get_pools.return_value = return_value

        res = self.api.get(_get_path('lbaas/pools', fmt=self.fmt))

        instance.get_pools.assert_called_with(mock.ANY, fields=mock.ANY,
                                              filters=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)

    def test_pool_update(self):
        pool_id = _uuid()
        update_data = {'pool': {'admin_state_up': False}}
        return_value = {'name': 'pool1',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'id': pool_id}

        instance = self.plugin.return_value
        instance.update_pool.return_value = return_value

        res = self.api.put(_get_path('lbaas/pools', id=pool_id,
                                     fmt=self.fmt),
                           self.serialize(update_data))

        instance.update_pool.assert_called_with(mock.ANY, pool_id,
                                                pool=update_data)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('pool', res)
        self.assertEqual(return_value, res['pool'])

    def test_pool_get(self):
        pool_id = _uuid()
        return_value = {'name': 'pool1',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'id': pool_id}

        instance = self.plugin.return_value
        instance.get_pool.return_value = return_value

        res = self.api.get(_get_path('lbaas/pools', id=pool_id,
                                     fmt=self.fmt))

        instance.get_pool.assert_called_with(mock.ANY, pool_id,
                                             fields=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('pool', res)
        self.assertEqual(return_value, res['pool'])

    def test_pool_delete(self):
        self._test_entity_delete('pool')

    def test_pool_member_create(self):
        subnet_id = _uuid()
        member_id = _uuid()
        data = {'member': {'address': '10.0.0.1',
                           'protocol_port': 80,
                           'weight': 1,
                           'subnet_id': subnet_id,
                           'admin_state_up': True,
                           'tenant_id': _uuid(),
                           'name': 'member1'}}
        return_value = copy.copy(data['member'])
        return_value.update({'id': member_id})

        instance = self.plugin.return_value
        instance.create_pool_member.return_value = return_value
        res = self.api.post(_get_path('lbaas/pools/pid1/members',
                                      fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/%s'
                                         % self.fmt)
        instance.create_pool_member.assert_called_with(mock.ANY,
                                                       pool_id='pid1',
                                                       member=data)
        self.assertEqual(exc.HTTPCreated.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('member', res)
        self.assertEqual(return_value, res['member'])

    def test_pool_member_list(self):
        member_id = _uuid()
        return_value = [{'name': 'member1',
                         'admin_state_up': True,
                         'tenant_id': _uuid(),
                         'id': member_id,
                         'name': 'member1'}]

        instance = self.plugin.return_value
        instance.get_pools.return_value = return_value

        res = self.api.get(_get_path('lbaas/pools/pid1/members',
                                     fmt=self.fmt))

        instance.get_pool_members.assert_called_with(mock.ANY,
                                                     fields=mock.ANY,
                                                     filters=mock.ANY,
                                                     pool_id='pid1')
        self.assertEqual(exc.HTTPOk.code, res.status_int)

    def test_pool_member_update(self):
        member_id = _uuid()
        update_data = {'member': {'admin_state_up': False}}
        return_value = {'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'id': member_id,
                        'name': 'member1'}

        instance = self.plugin.return_value
        instance.update_pool_member.return_value = return_value

        res = self.api.put(_get_path('lbaas/pools/pid1/members',
                                     id=member_id,
                                     fmt=self.fmt),
                           self.serialize(update_data))

        instance.update_pool_member.assert_called_with(
            mock.ANY, member_id, pool_id='pid1',
            member=update_data)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('member', res)
        self.assertEqual(return_value, res['member'])

    def test_pool_member_get(self):
        member_id = _uuid()
        return_value = {'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'id': member_id,
                        'name': 'member1'}

        instance = self.plugin.return_value
        instance.get_pool_member.return_value = return_value

        res = self.api.get(_get_path('lbaas/pools/pid1/members',
                                     id=member_id, fmt=self.fmt))

        instance.get_pool_member.assert_called_with(mock.ANY,
                                                    member_id,
                                                    fields=mock.ANY,
                                                    pool_id='pid1')
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('member', res)
        self.assertEqual(return_value, res['member'])

    def test_pool_member_delete(self):
        entity_id = _uuid()
        res = self.api.delete(
            test_base._get_path('lbaas/pools/pid1/members',
                                id=entity_id, fmt=self.fmt))
        delete_entity = getattr(self.plugin.return_value,
                                "delete_pool_member")
        delete_entity.assert_called_with(mock.ANY, entity_id,
                                         pool_id='pid1')
        self.assertEqual(exc.HTTPNoContent.code, res.status_int)

    def test_health_monitor_create(self):
        health_monitor_id = _uuid()
        data = {'healthmonitor': {'type': 'HTTP',
                                  'delay': 2,
                                  'timeout': 1,
                                  'max_retries': 3,
                                  'http_method': 'GET',
                                  'url_path': '/path',
                                  'expected_codes': '200-300',
                                  'admin_state_up': True,
                                  'tenant_id': _uuid(),
                                  'pool_id': _uuid(),
                                  'name': 'monitor1'}}
        return_value = copy.copy(data['healthmonitor'])
        return_value.update({'id': health_monitor_id})
        del return_value['pool_id']

        instance = self.plugin.return_value
        instance.create_healthmonitor.return_value = return_value
        res = self.api.post(_get_path('lbaas/healthmonitors',
                                      fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/%s' % self.fmt)
        instance.create_healthmonitor.assert_called_with(
            mock.ANY, healthmonitor=data)
        self.assertEqual(exc.HTTPCreated.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('healthmonitor', res)
        self.assertEqual(return_value, res['healthmonitor'])

    def test_health_monitor_create_with_timeout_negative(self):
        data = {'healthmonitor': {'type': 'HTTP',
                                  'delay': 2,
                                  'timeout': -1,
                                  'max_retries': 3,
                                  'http_method': 'GET',
                                  'url_path': '/path',
                                  'expected_codes': '200-300',
                                  'admin_state_up': True,
                                  'tenant_id': _uuid(),
                                  'pool_id': _uuid(),
                                  'name': 'monitor1'}}
        res = self.api.post(_get_path('lbaas/healthmonitors',
                                      fmt=self.fmt),
                            self.serialize(data),
                            content_type='application/%s' % self.fmt,
                            expect_errors=True)
        self.assertEqual(400, res.status_int)

    def test_health_monitor_list(self):
        health_monitor_id = _uuid()
        return_value = [{'type': 'HTTP',
                         'admin_state_up': True,
                         'tenant_id': _uuid(),
                         'id': health_monitor_id,
                         'name': 'monitor1'}]

        instance = self.plugin.return_value
        instance.get_healthmonitors.return_value = return_value

        res = self.api.get(_get_path('lbaas/healthmonitors', fmt=self.fmt))

        instance.get_healthmonitors.assert_called_with(
            mock.ANY, fields=mock.ANY, filters=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)

    def test_health_monitor_update(self):
        health_monitor_id = _uuid()
        update_data = {'healthmonitor': {'admin_state_up': False}}
        return_value = {'type': 'HTTP',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'id': health_monitor_id,
                        'name': 'monitor1'}

        instance = self.plugin.return_value
        instance.update_healthmonitor.return_value = return_value

        res = self.api.put(_get_path('lbaas/healthmonitors',
                                     id=health_monitor_id,
                                     fmt=self.fmt),
                           self.serialize(update_data))

        instance.update_healthmonitor.assert_called_with(
            mock.ANY, health_monitor_id, healthmonitor=update_data)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('healthmonitor', res)
        self.assertEqual(return_value, res['healthmonitor'])

    def test_health_monitor_get(self):
        health_monitor_id = _uuid()
        return_value = {'type': 'HTTP',
                        'admin_state_up': False,
                        'tenant_id': _uuid(),
                        'id': health_monitor_id,
                        'name': 'monitor1'}

        instance = self.plugin.return_value
        instance.get_healthmonitor.return_value = return_value

        res = self.api.get(_get_path('lbaas/healthmonitors',
                                     id=health_monitor_id,
                                     fmt=self.fmt))

        instance.get_healthmonitor.assert_called_with(
            mock.ANY, health_monitor_id, fields=mock.ANY)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('healthmonitor', res)
        self.assertEqual(return_value, res['healthmonitor'])

    def test_health_monitor_delete(self):
        entity_id = _uuid()
        res = self.api.delete(
            test_base._get_path('lbaas/healthmonitors',
                                id=entity_id, fmt=self.fmt))
        delete_entity = getattr(self.plugin.return_value,
                                "delete_healthmonitor")
        delete_entity.assert_called_with(mock.ANY, entity_id)
        self.assertEqual(exc.HTTPNoContent.code, res.status_int)

    def test_load_balancer_stats(self):
        load_balancer_id = _uuid()

        stats = {'stats': 'dummy'}
        instance = self.plugin.return_value
        instance.stats.return_value = stats

        path = _get_path('lbaas/loadbalancers', id=load_balancer_id,
                         action="stats", fmt=self.fmt)
        res = self.api.get(path)

        instance.stats.assert_called_with(mock.ANY, load_balancer_id)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('stats', res)
        self.assertEqual(stats['stats'], res['stats'])

    def test_load_balancer_statuses(self):
        load_balancer_id = _uuid()

        statuses = {'statuses': {'loadbalancer': {}}}
        instance = self.plugin.return_value
        instance.statuses.return_value = statuses
        path = _get_path('lbaas/loadbalancers', id=load_balancer_id,
                         action="statuses", fmt=self.fmt)
        res = self.api.get(path)
        instance.statuses.assert_called_with(mock.ANY, load_balancer_id)
        self.assertEqual(exc.HTTPOk.code, res.status_int)
        res = self.deserialize(res)
        self.assertIn('statuses', res)
        self.assertEqual(statuses['statuses'], res['statuses'])
