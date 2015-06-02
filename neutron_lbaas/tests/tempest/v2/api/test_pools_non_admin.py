#    Copyright 2015 Rackspace
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

from tempest_lib.common.utils import data_utils
from tempest_lib import decorators
from tempest_lib import exceptions as ex

from neutron_lbaas.tests.tempest.lib import test
from neutron_lbaas.tests.tempest.v2.api import base

PROTOCOL_PORT = 80


class TestPools(base.BaseTestCase):

    """
    Tests the following operations in the Neutron-LBaaS API using the
    REST client for Pools:

        list pools
        create pool
        get pool
        update pool
        delete pool
    """

    @classmethod
    def resource_setup(cls):
        super(TestPools, cls).resource_setup()
        if not test.is_extension_enabled('lbaas', 'network'):
            msg = "lbaas extension not enabled."
            raise cls.skipException(msg)
        network_name = data_utils.rand_name('network-')
        cls.network = cls.create_network(network_name)
        cls.subnet = cls.create_subnet(cls.network)
        cls.load_balancer = cls._create_load_balancer(
            tenant_id=cls.subnet.get('tenant_id'),
            vip_subnet_id=cls.subnet.get('id'))

    def increment_protocol_port(self):
        global PROTOCOL_PORT
        PROTOCOL_PORT += 1

    def _prepare_and_create_pool(self, protocol=None, lb_algorithm=None,
                                 listener_id=None, **kwargs):
        self.increment_protocol_port()
        if not protocol:
            protocol = 'HTTP'
        if not lb_algorithm:
            lb_algorithm = 'ROUND_ROBIN'
        if not listener_id:
            listener = self._create_listener(
                loadbalancer_id=self.load_balancer.get('id'),
                protocol='HTTP', protocol_port=PROTOCOL_PORT)
            listener_id = listener.get('id')
        response = self._create_pool(protocol=protocol,
                                     lb_algorithm=lb_algorithm,
                                     listener_id=listener_id,
                                     **kwargs)
        return response

    @test.attr(type='smoke')
    def test_list_pools_empty(self):
        """Test get pools when empty"""
        pools = self.pools_client.list_pools()
        self.assertEqual([], pools)

    @test.attr(type='smoke')
    def test_list_pools_one(self):
        """Test get pools with one pool"""
        new_pool = self._prepare_and_create_pool()
        new_pool = self.pools_client.get_pool(new_pool['id'])
        pools = self.pools_client.list_pools()
        self.assertEqual(1, len(pools))
        self.assertIn(new_pool, pools)
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_list_pools_two(self):
        """Test get pools with two pools"""
        new_pool1 = self._prepare_and_create_pool()
        new_pool2 = self._prepare_and_create_pool()
        pools = self.pools_client.list_pools()
        self.assertEqual(2, len(pools))
        self.assertIn(new_pool1, pools)
        self.assertIn(new_pool2, pools)
        self._delete_pool(new_pool1.get('id'))
        self._delete_pool(new_pool2.get('id'))

    @test.attr(type='smoke')
    def test_get_pool(self):
        """Test get pool"""
        new_pool = self._prepare_and_create_pool()
        pool = self.pools_client.get_pool(new_pool.get('id'))
        self.assertEqual(new_pool, pool)
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_create_pool(self):
        """Test create pool"""
        new_pool = self._prepare_and_create_pool()
        pool = self.pools_client.get_pool(new_pool.get('id'))
        self.assertEqual(new_pool, pool)
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='negative')
    def test_create_pool_missing_required_fields(self):
        """Test create pool with a missing required fields"""
        tenant_id = self.subnet.get('tenant_id')
        self.assertRaises(ex.BadRequest, self._create_pool,
                          tenant_id=tenant_id,
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='smoke')
    def test_create_pool_missing_tenant_field(self):
        """Test create pool with a missing required tenant field"""
        tenant_id = self.subnet.get('tenant_id')
        new_pool = self._prepare_and_create_pool(
            protocol='HTTP',
            lb_algorithm='ROUND_ROBIN')
        pool = self.pools_client.get_pool(new_pool.get('id'))
        pool_tenant = pool['tenant_id']
        self.assertEqual(tenant_id, pool_tenant)
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='negative')
    def test_create_pool_missing_protocol_field(self):
        """Test create pool with a missing required protocol field"""
        self.increment_protocol_port()
        listener = self.listeners_client.create_listener(
            loadbalancer_id=self.load_balancer.get('id'),
            protocol='HTTP', protocol_port=PROTOCOL_PORT)
        listener_id = listener.get('id')
        tenant_id = self.subnet.get('tenant_id')
        self.assertRaises(ex.BadRequest, self._create_pool,
                          tenant_id=tenant_id,
                          listener_id=listener_id,
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='negative')
    def test_create_pool_missing_lb_algorithm_field(self):
        """Test create pool with a missing required lb algorithm field"""
        self.increment_protocol_port()
        listener = self.listeners_client.create_listener(
            loadbalancer_id=self.load_balancer.get('id'),
            protocol='HTTP', protocol_port=PROTOCOL_PORT)
        listener_id = listener.get('id')
        tenant_id = self.subnet.get('tenant_id')
        self.assertRaises(ex.BadRequest, self._create_pool,
                          tenant_id=tenant_id,
                          listener_id=listener_id,
                          protocol='HTTP')

    @test.attr(type='negative')
    def test_create_pool_missing_listener_id_field(self):
        """Test create pool with a missing required listener id field"""
        tenant_id = self.subnet.get('tenant_id')
        self.assertRaises(ex.BadRequest, self._create_pool,
                          tenant_id=tenant_id,
                          lb_algorithm='ROUND_ROBIN',
                          protocol='HTTP')

    @test.attr(type='smoke')
    def test_create_pool_missing_description_field(self):
        """Test create pool with missing description field"""
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        new_pool = self._prepare_and_create_pool()
        pool_initial = self.pools_client.get_pool(new_pool.get('id'))
        desc = pool_initial.get('description')
        self.assertEqual(desc, "")
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_create_pool_missing_name_field(self):
        """Test create pool with a missing name field"""
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        new_pool = self._prepare_and_create_pool()
        pool_initial = self.pools_client.get_pool(new_pool.get('id'))
        name = pool_initial.get('name')
        self.assertEqual(name, "")
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_create_pool_missing_admin_state_up_field(self):
        """Test create pool with a missing admin_state_up field"""
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        new_pool = self._prepare_and_create_pool()
        pool_initial = self.pools_client.get_pool(new_pool.get('id'))
        state = pool_initial.get('admin_state_up')
        self.assertEqual(state, True)
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_create_pool_missing_session_pers_field(self):
        """Test create pool with a missing session_pers field"""
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        new_pool = self._prepare_and_create_pool()
        pool_initial = self.pools_client.get_pool(new_pool.get('id'))
        sess = pool_initial.get('session_persistence')
        self.assertIsNone(sess)
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='negative')
    def test_create_pool_invalid_protocol(self):
        """Test create pool with an invalid protocol"""
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol='UDP',
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='negative')
    def test_create_pool_invalid_session_persistence_field(self):
        """Test create pool with invalid session persistance field"""
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol='HTTP',
                          session_persistence={'type': 'HTTP'},
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='negative')
    def test_create_pool_invalid_algorithm(self):
        """Test create pool with an invalid algorithm"""
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol='HTTP',
                          lb_algorithm='LEAST_CON')

    @test.attr(type='negative')
    def test_create_pool_invalid_admin_state_up(self):
        """Test create pool with an invalid admin state up field"""
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol='HTTP',
                          admin_state_up="$!1%9823",
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='negative')
    def test_create_pool_invalid_listener_field(self):
        """Test create pool with invalid listener field"""
        tenant_id = self.subnet.get('tenant_id')
        self.assertRaises(ex.BadRequest, self._create_pool,
                          tenant_id=tenant_id,
                          lb_algorithm='ROUND_ROBIN',
                          protocol='HTTP',
                          listener_id="$@5$%$7863")

    @test.attr(type='negative')
    def test_create_pool_invalid_tenant_id_field(self):
        """Test create pool with invalid tenant_id field"""
        self.increment_protocol_port()
        listener = self.listeners_client.create_listener(
            loadbalancer_id=self.load_balancer.get('id'),
            protocol='HTTP', protocol_port=PROTOCOL_PORT)
        listener_id = listener.get('id')
        self.assertRaises(ex.BadRequest, self._create_pool,
                          tenant_id="*&7653^%&",
                          lb_algorithm='ROUND_ROBIN',
                          protocol='HTTP',
                          listener_id=listener_id)

    @test.attr(type='negative')
    def test_create_pool_incorrect_attribute(self):
        """Test create a pool with an extra, incorrect field"""
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol='HTTP',
                          lb_algorithm='ROUND_ROBIN',
                          protocol_port=80)

    @test.attr(type='negative')
    def test_create_pool_empty_listener_field(self):
        """Test create pool with empty listener field"""
        tenant_id = self.subnet.get('tenant_id')
        self.assertRaises(ex.BadRequest, self._create_pool,
                          tenant_id=tenant_id,
                          lb_algorithm='ROUND_ROBIN',
                          protocol='HTTP',
                          listener_id="")

    @test.attr(type='smoke')
    def test_create_pool_empty_description_field(self):
        """Test create pool with empty description field"""
        new_pool = self._prepare_and_create_pool(
            description="")
        pool = self.pools_client.get_pool(new_pool.get('id'))
        pool_desc = pool.get('description')
        self.assertEqual(pool_desc, '')
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_create_pool_empty_name_field(self):
        """Test create pool with empty name field"""
        new_pool = self._prepare_and_create_pool(
            name="")
        pool = self.pools_client.get_pool(new_pool.get('id'))
        pool_name = pool.get('name')
        self.assertEqual(pool_name, '')
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='negative')
    def test_create_pool_empty_protocol(self):
        """Test create pool with an empty protocol"""
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol="",
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='negative')
    def test_create_pool_empty_session_persistence_field(self):
        """Test create pool with empty session persistence field"""
        self.assertRaises(ex.BadRequest, self._create_pool,
                          session_persistence="",
                          protocol='HTTP',
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='negative')
    def test_create_pool_empty_algorithm(self):
        """Test create pool with an empty algorithm"""
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol='HTTP',
                          lb_algorithm="")

    @test.attr(type='negative')
    def test_create_pool_empty_admin_state_up(self):
        """Test create pool with an invalid admin state up field"""
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol='HTTP',
                          admin_state_up="",
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='negative')
    def test_create_pool_empty_tenant_field(self):
        """Test create pool with empty tenant field"""
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol='HTTP',
                          tenant_id="",
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='negative')
    def test_create_pool_for_other_tenant_field(self):
        """Test create pool for other tenant field"""
        tenant = 'deffb4d7c0584e89a8ec99551565713c'
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol='HTTP',
                          tenant_id=tenant,
                          lb_algorithm='ROUND_ROBIN')

    @decorators.skip_because(bug="1434717")
    @test.attr(type='negative')
    def test_create_pool_invalid_name_field(self):
        """
        known bug with input more than 255 chars
        Test create pool with invalid name field
        """
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol='HTTP',
                          lb_algorithm='ROUND_ROBIN',
                          name='n' * 256)

    @decorators.skip_because(bug="1434717")
    @test.attr(type='negative')
    def test_create_pool_invalid_desc_field(self):
        """
        known bug with input more than 255 chars
        Test create pool with invalid desc field
        """
        self.assertRaises(ex.BadRequest, self._create_pool,
                          protocol='HTTP',
                          lb_algorithm='ROUND_ROBIN',
                          description='d' * 256)

    @test.attr(type='negative')
    def test_create_pool_with_session_persistence_unsupported_type(self):
        """Test create a pool with an incorrect type value
        for session persistence
        """
        self.assertRaises(ex.BadRequest, self._create_pool,
                          session_persistence={'type': 'UNSUPPORTED'},
                          protocol='HTTP',
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='smoke')
    def test_create_pool_with_session_persistence_http_cookie(self):
        """Test create a pool with session_persistence type=HTTP_COOKIE"""
        new_pool = self._prepare_and_create_pool(
            session_persistence={'type': 'HTTP_COOKIE'})
        pool = self.pools_client.get_pool(new_pool.get('id'))
        self.assertEqual(new_pool, pool)
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_create_pool_with_session_persistence_app_cookie(self):
        """Test create a pool with session_persistence type=APP_COOKIE"""
        new_pool = self._prepare_and_create_pool(
            session_persistence={'type': 'APP_COOKIE',
                                 'cookie_name': 'sessionId'})
        pool = self.pools_client.get_pool(new_pool.get('id'))
        self.assertEqual(new_pool, pool)
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='negative')
    def test_create_pool_with_session_persistence_redundant_cookie_name(self):
        """Test create a pool with session_persistence with cookie_name
        for type=HTTP_COOKIE
        """
        self.assertRaises(ex.BadRequest, self._create_pool,
                          session_persistence={'type': 'HTTP_COOKIE',
                                               'cookie_name': 'sessionId'},
                          protocol='HTTP',
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='negative')
    def test_create_pool_with_session_persistence_without_cookie_name(self):
        """Test create a pool with session_persistence without
        cookie_name for type=APP_COOKIE
        """
        self.assertRaises(ex.BadRequest, self._create_pool,
                          session_persistence={'type': 'APP_COOKIE'},
                          protocol='HTTP',
                          lb_algorithm='ROUND_ROBIN')

    @test.attr(type='smoke')
    def test_update_pool(self):
        """Test update pool"""
        new_pool = self._prepare_and_create_pool()
        desc = 'testing update with new description'
        pool = self._update_pool(new_pool.get('id'),
                                 description=desc)
        self.assertEqual(desc, pool.get('description'))
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_update_pool_missing_name(self):
        """Test update pool with missing name"""
        new_pool = self._prepare_and_create_pool()
        pool_initial = self.pools_client.get_pool(new_pool.get('id'))
        name = pool_initial.get('name')
        pool = self.pools_client.update_pool(new_pool.get('id'))
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        self.assertEqual(name, pool.get('name'))
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_update_pool_missing_description(self):
        """Test update pool with missing description"""
        new_pool = self._prepare_and_create_pool()
        pool_initial = self.pools_client.get_pool(new_pool.get('id'))
        desc = pool_initial.get('description')
        pool = self.pools_client.update_pool(new_pool.get('id'))
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        self.assertEqual(desc, pool.get('description'))
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_update_pool_missing_admin_state_up(self):
        """Test update pool with missing admin state up field"""
        new_pool = self._prepare_and_create_pool()
        pool_initial = self.pools_client.get_pool(new_pool.get('id'))
        admin = pool_initial.get('admin_state_up')
        pool = self.pools_client.update_pool(new_pool.get('id'))
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        self.assertEqual(admin, pool.get('admin_state_up'))
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_update_pool_missing_session_persistence(self):
        """Test update pool with missing session persistence"""
        new_pool = self._prepare_and_create_pool()
        pool_initial = self.pools_client.get_pool(new_pool.get('id'))
        sess_pers = pool_initial.get('session_persistence')
        pool = self.pools_client.update_pool(new_pool.get('id'))
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        self.assertAlmostEqual(sess_pers, pool.get('session_persistence'))
        self._delete_pool(new_pool.get('id'))

    @decorators.skip_because(bug="1434717")
    @test.attr(type='negative')
    def test_update_pool_invalid_name(self):
        """Test update pool with invalid name"""
        new_pool = self._prepare_and_create_pool()
        self.assertRaises(ex.BadRequest, self.pools_client.update_pool,
                          new_pool.get('id'), name='n' * 256)
        self._delete_pool(new_pool.get('id'))

    @decorators.skip_because(bug="1434717")
    @test.attr(type='negative')
    def test_update_pool_invalid_desc(self):
        """Test update pool with invalid desc"""
        new_pool = self._prepare_and_create_pool()
        self.assertRaises(ex.BadRequest, self.pools_client.update_pool,
                          new_pool.get('id'),
                          description='d' * 256)
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='negative')
    def test_update_pool_invalid_admin_state_up(self):
        """Test update pool with an invalid admin_state_up"""
        new_pool = self._prepare_and_create_pool()
        self.assertRaises(ex.BadRequest, self.pools_client.update_pool,
                          new_pool.get('id'), admin_state_up='hello')
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='negative')
    def test_update_pool_invalid_session_persistence(self):
        """Test update pool with an invalid session pers. field"""
        new_pool = self._prepare_and_create_pool()
        self.assertRaises(ex.BadRequest, self.pools_client.update_pool,
                          new_pool.get('id'),
                          session_persistence={'type': 'Hello'})
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_update_pool_empty_name(self):
        """Test update pool with empty name"""
        new_pool = self._prepare_and_create_pool()
        pool = self.pools_client.update_pool(new_pool.get('id'),
                                             name="")
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        self.assertEqual(pool.get('name'), "")
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_update_pool_empty_description(self):
        """Test update pool with empty description"""
        new_pool = self._prepare_and_create_pool()
        pool = self.pools_client.update_pool(new_pool.get('id'),
                                             description="")
        self._wait_for_load_balancer_status(self.load_balancer.get('id'))
        self.assertEqual(pool.get('description'), "")
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='negative')
    def test_update_pool_empty_admin_state_up(self):
        """Test update pool with empty admin state up"""
        new_pool = self._prepare_and_create_pool()
        self.assertRaises(ex.BadRequest, self.pools_client.update_pool,
                          new_pool.get('id'), admin_state_up="")
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='negative')
    def test_update_pool_empty_session_persistence(self):
        """Test update pool with empty session persistence field"""
        new_pool = self._prepare_and_create_pool()
        self.assertRaises(ex.BadRequest, self.pools_client.update_pool,
                          new_pool.get('id'),
                          session_persistence="")
        self.pools_client.delete_pool(new_pool.get('id'))

    @test.attr(type='negative')
    def test_update_pool_invalid_attribute(self):
        """Test update pool with an invalid attribute"""
        new_pool = self._prepare_and_create_pool()
        self.assertRaises(ex.BadRequest, self._update_pool,
                          new_pool.get('id'), lb_algorithm='ROUNDED')
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='negative')
    def test_update_pool_incorrect_attribute(self):
        """Test update a pool with an extra, incorrect field"""
        new_pool = self._prepare_and_create_pool()
        self.assertRaises(ex.BadRequest, self._update_pool,
                          new_pool.get('id'), protocol='HTTPS')
        self._delete_pool(new_pool.get('id'))

    @test.attr(type='smoke')
    def test_delete_pool(self):
        """Test delete pool"""
        new_pool = self._prepare_and_create_pool()
        pool = self.pools_client.get_pool(new_pool.get('id'))
        self.assertEqual(new_pool, pool)
        self._delete_pool(new_pool.get('id'))
        self.assertRaises(ex.NotFound, self.pools_client.get_pool,
                          new_pool.get('id'))

    @test.attr(type='smoke')
    def test_delete_invalid_pool(self):
        """Test delete pool that doesn't exist"""
        new_pool = self._prepare_and_create_pool()
        pool = self.pools_client.get_pool(new_pool.get('id'))
        self.assertEqual(new_pool, pool)
        self._delete_pool(new_pool.get('id'))
        self.assertRaises(ex.NotFound, self._delete_pool,
                          new_pool.get('id'))
