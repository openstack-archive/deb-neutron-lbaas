# Copyright 2014-2015 Rackspace
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

from oslo_log import log as logging

from neutron_lbaas._i18n import _LW
from neutron_lbaas.drivers.haproxy import synchronous_namespace_driver

LOG = logging.getLogger(__name__)
LOG.warning(_LW("This path has been deprecated. "
                "Use neutron_lbaas.drivers.haproxy."
                "synchronous_namespace_driver instead."))


class HaproxyNSDriver(synchronous_namespace_driver.HaproxyNSDriver):
    pass
