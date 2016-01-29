# Copyright 2013 Mirantis, Inc.
# All Rights Reserved.
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

import contextlib

import mock

from neutron_lbaas.services.loadbalancer.drivers.haproxy import cfg
from neutron_lbaas.tests import base


class TestHaproxyCfg(base.BaseTestCase):
    def test_save_config(self):
        with contextlib.nested(
                mock.patch('neutron_lbaas.services.loadbalancer.'
                           'drivers.haproxy.cfg._build_global'),
                mock.patch('neutron_lbaas.services.loadbalancer.'
                           'drivers.haproxy.cfg._build_defaults'),
                mock.patch('neutron_lbaas.services.loadbalancer.'
                           'drivers.haproxy.cfg._build_frontend'),
                mock.patch('neutron_lbaas.services.loadbalancer.'
                           'drivers.haproxy.cfg._build_backend'),
                mock.patch('neutron.common.utils.replace_file')
        ) as (b_g, b_d, b_f, b_b, replace):
            test_config = ['globals', 'defaults', 'frontend', 'backend']
            b_g.return_value = [test_config[0]]
            b_d.return_value = [test_config[1]]
            b_f.return_value = [test_config[2]]
            b_b.return_value = [test_config[3]]

            cfg.save_config('test_path', mock.Mock())
            replace.assert_called_once_with('test_path',
                                            '\n'.join(test_config))

    def test_build_global(self):
        expected_opts = ['global',
                         '\tdaemon',
                         '\tuser nobody',
                         '\tgroup test_group',
                         '\tlog /dev/log local0',
                         '\tlog /dev/log local1 notice',
                         '\tstats socket test_path mode 0666 level user']
        opts = cfg._build_global(mock.Mock(), 'test_path', 'test_group')
        self.assertEqual(expected_opts, list(opts))

    def test_build_defaults(self):
        expected_opts = ['defaults',
                         '\tlog global',
                         '\tretries 3',
                         '\toption redispatch',
                         '\ttimeout connect 5000',
                         '\ttimeout client 50000',
                         '\ttimeout server 50000']
        opts = cfg._build_defaults(mock.Mock())
        self.assertEqual(expected_opts, list(opts))

    def test_build_frontend(self):
        test_config = {'vip': {'id': 'vip_id',
                               'protocol': 'HTTP',
                               'port': {'fixed_ips': [
                                   {'ip_address': '10.0.0.2'}]
                               },
                               'protocol_port': 80,
                               'connection_limit': 2000,
                               'admin_state_up': True,
                               },
                       'pool': {'id': 'pool_id'}}
        expected_opts = ['frontend vip_id',
                         '\toption tcplog',
                         '\tbind 10.0.0.2:80',
                         '\tmode http',
                         '\tdefault_backend pool_id',
                         '\tmaxconn 2000',
                         '\toption forwardfor']
        opts = cfg._build_frontend(test_config)
        self.assertEqual(expected_opts, list(opts))

        test_config['vip']['connection_limit'] = -1
        expected_opts.remove('\tmaxconn 2000')
        opts = cfg._build_frontend(test_config)
        self.assertEqual(expected_opts, list(opts))

        test_config['vip']['admin_state_up'] = False
        expected_opts.append('\tdisabled')
        opts = cfg._build_frontend(test_config)
        self.assertEqual(expected_opts, list(opts))

    def test_build_backend(self):
        test_config = {'pool': {'id': 'pool_id',
                                'protocol': 'HTTP',
                                'lb_method': 'ROUND_ROBIN',
                                'admin_state_up': True},
                       'members': [{'status': 'ACTIVE',
                                    'admin_state_up': True,
                                    'id': 'member1_id',
                                    'address': '10.0.0.3',
                                    'protocol_port': 80,
                                    'weight': 1},
                                   {'status': 'INACTIVE',
                                    'admin_state_up': True,
                                    'id': 'member2_id',
                                    'address': '10.0.0.4',
                                    'protocol_port': 80,
                                    'weight': 1},
                                   {'status': 'PENDING_CREATE',
                                    'admin_state_up': True,
                                    'id': 'member3_id',
                                    'address': '10.0.0.5',
                                    'protocol_port': 80,
                                    'weight': 1}],
                       'healthmonitors': [{'admin_state_up': True,
                                           'delay': 3,
                                           'max_retries': 4,
                                           'timeout': 2,
                                           'type': 'TCP'}],
                       'vip': {'session_persistence': {'type': 'HTTP_COOKIE'}}}
        expected_opts = ['backend pool_id',
                         '\tmode http',
                         '\tbalance roundrobin',
                         '\toption forwardfor',
                         '\ttimeout check 2s',
                         '\tcookie SRV insert indirect nocache',
                         '\tserver member1_id 10.0.0.3:80 weight 1 '
                         'check inter 3s fall 4 cookie member1_id',
                         '\tserver member2_id 10.0.0.4:80 weight 1 '
                         'check inter 3s fall 4 cookie member2_id',
                         '\tserver member3_id 10.0.0.5:80 weight 1 '
                         'check inter 3s fall 4 cookie member3_id']
        opts = cfg._build_backend(test_config)
        self.assertEqual(expected_opts, list(opts))

        test_config['pool']['admin_state_up'] = False
        expected_opts.append('\tdisabled')
        opts = cfg._build_backend(test_config)
        self.assertEqual(expected_opts, list(opts))

    def test_get_server_health_option(self):
        test_config = {'healthmonitors': [{'admin_state_up': False,
                                           'delay': 3,
                                           'max_retries': 4,
                                           'timeout': 2,
                                           'type': 'TCP',
                                           'http_method': 'GET',
                                           'url_path': '/',
                                           'expected_codes': '200'}]}
        self.assertEqual(('', []), cfg._get_server_health_option(test_config))

        self.assertEqual(('', []), cfg._get_server_health_option(test_config))

        test_config['healthmonitors'][0]['admin_state_up'] = True
        expected = (' check inter 3s fall 4', ['timeout check 2s'])
        self.assertEqual(expected, cfg._get_server_health_option(test_config))

        test_config['healthmonitors'][0]['type'] = 'HTTPS'
        expected = (' check inter 3s fall 4',
                    ['timeout check 2s',
                     'option httpchk GET /',
                     'http-check expect rstatus 200',
                     'option ssl-hello-chk'])
        self.assertEqual(expected, cfg._get_server_health_option(test_config))

    def test_has_http_cookie_persistence(self):
        config = {'vip': {'session_persistence': {'type': 'HTTP_COOKIE'}}}
        self.assertTrue(cfg._has_http_cookie_persistence(config))

        config = {'vip': {'session_persistence': {'type': 'SOURCE_IP'}}}
        self.assertFalse(cfg._has_http_cookie_persistence(config))

        config = {'vip': {'session_persistence': {}}}
        self.assertFalse(cfg._has_http_cookie_persistence(config))

    def test_get_session_persistence(self):
        config = {'vip': {'session_persistence': {'type': 'SOURCE_IP'}}}
        self.assertEqual(['stick-table type ip size 10k', 'stick on src'],
                         cfg._get_session_persistence(config))

        config = {'vip': {'session_persistence': {'type': 'HTTP_COOKIE'}},
                  'members': []}
        self.assertEqual([], cfg._get_session_persistence(config))

        config = {'vip': {'session_persistence': {'type': 'HTTP_COOKIE'}}}
        self.assertEqual([], cfg._get_session_persistence(config))

        config = {'vip': {'session_persistence': {'type': 'HTTP_COOKIE'}},
                  'members': [{'id': 'member1_id'}]}
        self.assertEqual(['cookie SRV insert indirect nocache'],
                         cfg._get_session_persistence(config))

        config = {'vip': {'session_persistence': {'type': 'APP_COOKIE',
                                                  'cookie_name': 'test'}}}
        self.assertEqual(['appsession test len 56 timeout 3h'],
                         cfg._get_session_persistence(config))

        config = {'vip': {'session_persistence': {'type': 'APP_COOKIE'}}}
        self.assertEqual([], cfg._get_session_persistence(config))

        config = {'vip': {'session_persistence': {'type': 'UNSUPPORTED'}}}
        self.assertEqual([], cfg._get_session_persistence(config))

    def test_expand_expected_codes(self):
        exp_codes = ''
        self.assertEqual(set([]), cfg._expand_expected_codes(exp_codes))
        exp_codes = '200'
        self.assertEqual(set(['200']), cfg._expand_expected_codes(exp_codes))
        exp_codes = '200, 201'
        self.assertEqual(set(['200', '201']),
                         cfg._expand_expected_codes(exp_codes))
        exp_codes = '200, 201,202'
        self.assertEqual(set(['200', '201', '202']),
                         cfg._expand_expected_codes(exp_codes))
        exp_codes = '200-202'
        self.assertEqual(set(['200', '201', '202']),
                         cfg._expand_expected_codes(exp_codes))
        exp_codes = '200-202, 205'
        self.assertEqual(set(['200', '201', '202', '205']),
                         cfg._expand_expected_codes(exp_codes))
        exp_codes = '200, 201-203'
        self.assertEqual(set(['200', '201', '202', '203']),
                         cfg._expand_expected_codes(exp_codes))
        exp_codes = '200, 201-203, 205'
        self.assertEqual(set(['200', '201', '202', '203', '205']),
                         cfg._expand_expected_codes(exp_codes))
        exp_codes = '201-200, 205'
        self.assertEqual(set(['205']), cfg._expand_expected_codes(exp_codes))
