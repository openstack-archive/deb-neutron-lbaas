# Copyright 2015 NEC Corporation.  All rights reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from oslo_config import cfg

from neutron_lbaas.common import cert_manager
from neutron_lbaas.common.cert_manager import barbican_cert_manager as bcm
from neutron_lbaas.common.cert_manager import get_backend
from neutron_lbaas.common.cert_manager import local_cert_manager as lcm
from neutron_lbaas.tests import base


class TestCertManager(base.BaseTestCase):

    def setUp(self):
        cert_manager._CERT_MANAGER_PLUGIN = None
        super(TestCertManager, self).setUp()

    def test_barbican_cert_manager(self):
        cfg.CONF.set_override(
            'cert_manager_type',
            'barbican',
            group='certificates')
        self.assertEqual(get_backend().CertManager,
                         bcm.CertManager)

    def test_local_cert_manager(self):
        cfg.CONF.set_override(
            'cert_manager_type',
            'local',
            group='certificates')
        self.assertEqual(get_backend().CertManager,
                         lcm.CertManager)
