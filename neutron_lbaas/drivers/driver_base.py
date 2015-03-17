# Copyright 2014 A10 Networks
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

from neutron_lbaas.db.loadbalancer import models
from neutron_lbaas.drivers import driver_mixins


class NotImplementedManager(object):
    """Helper class to make any subclass of LoadBalancerBaseDriver explode if
    it is missing any of the required object managers.
    """

    def create(self, context, obj):
        raise NotImplementedError()

    def update(self, context, old_obj, obj):
        raise NotImplementedError()

    def delete(self, context, obj):
        raise NotImplementedError()


class LoadBalancerBaseDriver(object):
    """LBaaSv2 object model drivers should subclass LoadBalancerBaseDriver,
    and initialize the following manager classes to create, update, and delete
    the various load balancer objects.
    """

    load_balancer = NotImplementedManager()
    listener = NotImplementedManager()
    pool = NotImplementedManager()
    member = NotImplementedManager()
    health_monitor = NotImplementedManager()

    def __init__(self, plugin):
        self.plugin = plugin


class BaseLoadBalancerManager(driver_mixins.BaseRefreshMixin,
                              driver_mixins.BaseStatsMixin,
                              driver_mixins.BaseManagerMixin):
    model_class = models.LoadBalancer

    @property
    def db_delete_method(self):
        return self.driver.plugin.db.delete_loadbalancer


class BaseListenerManager(driver_mixins.BaseManagerMixin):
    model_class = models.Listener

    @property
    def db_delete_method(self):
        return self.driver.plugin.db.delete_listener


class BasePoolManager(driver_mixins.BaseManagerMixin):
    model_class = models.PoolV2

    @property
    def db_delete_method(self):
        return self.driver.plugin.db.delete_pool


class BaseMemberManager(driver_mixins.BaseManagerMixin):
    model_class = models.MemberV2

    @property
    def db_delete_method(self):
        return self.driver.plugin.db.delete_member


class BaseHealthMonitorManager(driver_mixins.BaseManagerMixin):
    model_class = models.HealthMonitorV2

    @property
    def db_delete_method(self):
        return self.driver.plugin.db.delete_healthmonitor
