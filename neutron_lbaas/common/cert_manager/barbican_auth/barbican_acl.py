# Copyright (c) 2014-2016 Rackspace US, Inc
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

"""
Barbican ACL auth class for Barbican certificate handling
"""
from barbicanclient import client as barbican_client
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils

from neutron_lbaas._i18n import _LE
from neutron_lbaas.common.cert_manager.barbican_auth import common
from neutron_lbaas.common import keystone

LOG = logging.getLogger(__name__)

CONF = cfg.CONF


class BarbicanACLAuth(common.BarbicanAuth):
    _barbican_client = None

    @classmethod
    def get_barbican_client(cls, project_id=None):
        if not cls._barbican_client:
            try:
                cls._barbican_client = barbican_client.Client(
                    session=keystone.get_session(),
                    region_name=CONF.service_auth.region,
                    interface=CONF.service_auth.endpoint_type
                )
            except Exception:
                with excutils.save_and_reraise_exception():
                    LOG.exception(_LE("Error creating Barbican client"))
        return cls._barbican_client
