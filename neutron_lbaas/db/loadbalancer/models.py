# Copyright (c) 2014 OpenStack Foundation.
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


from neutron.api.v2 import attributes as attr
from neutron.db import model_base
from neutron.db import models_v2
from neutron.db import servicetype_db as st_db
import sqlalchemy as sa
from sqlalchemy.ext import orderinglist
from sqlalchemy import orm

from neutron_lbaas._i18n import _
from neutron_lbaas.services.loadbalancer import constants as lb_const


class SessionPersistenceV2(model_base.BASEV2):

    __tablename__ = "lbaas_sessionpersistences"

    pool_id = sa.Column(sa.String(36),
                        sa.ForeignKey("lbaas_pools.id"),
                        primary_key=True,
                        nullable=False)
    type = sa.Column(sa.Enum(*lb_const.SUPPORTED_SP_TYPES,
                             name="lbaas_sesssionpersistences_typev2"),
                     nullable=False)
    cookie_name = sa.Column(sa.String(1024), nullable=True)


class LoadBalancerStatistics(model_base.BASEV2):
    """Represents load balancer statistics."""

    NAME = 'loadbalancer_stats'

    __tablename__ = "lbaas_loadbalancer_statistics"

    loadbalancer_id = sa.Column(sa.String(36),
                                sa.ForeignKey("lbaas_loadbalancers.id"),
                                primary_key=True,
                                nullable=False)
    bytes_in = sa.Column(sa.BigInteger, nullable=False)
    bytes_out = sa.Column(sa.BigInteger, nullable=False)
    active_connections = sa.Column(sa.BigInteger, nullable=False)
    total_connections = sa.Column(sa.BigInteger, nullable=False)

    @orm.validates('bytes_in', 'bytes_out',
                   'active_connections', 'total_connections')
    def validate_non_negative_int(self, key, value):
        if value < 0:
            data = {'key': key, 'value': value}
            raise ValueError(_('The %(key)s field can not have '
                               'negative value. '
                               'Current value is %(value)d.') % data)
        return value


class MemberV2(model_base.BASEV2, models_v2.HasId, models_v2.HasTenant):
    """Represents a v2 neutron load balancer member."""

    NAME = 'member'

    __tablename__ = "lbaas_members"

    __table_args__ = (
        sa.schema.UniqueConstraint('pool_id', 'address', 'protocol_port',
                                   name='uniq_pool_address_port_v2'),
    )
    pool_id = sa.Column(sa.String(36), sa.ForeignKey("lbaas_pools.id"),
                        nullable=False)
    address = sa.Column(sa.String(64), nullable=False)
    protocol_port = sa.Column(sa.Integer, nullable=False)
    weight = sa.Column(sa.Integer, nullable=True)
    admin_state_up = sa.Column(sa.Boolean(), nullable=False)
    subnet_id = sa.Column(sa.String(36), nullable=True)
    provisioning_status = sa.Column(sa.String(16), nullable=False)
    operating_status = sa.Column(sa.String(16), nullable=False)
    name = sa.Column(sa.String(attr.NAME_MAX_LEN), nullable=True)

    @property
    def root_loadbalancer(self):
        return self.pool.loadbalancer


class HealthMonitorV2(model_base.BASEV2, models_v2.HasId, models_v2.HasTenant):
    """Represents a v2 neutron load balancer healthmonitor."""

    NAME = 'healthmonitor'

    __tablename__ = "lbaas_healthmonitors"

    type = sa.Column(sa.Enum(*lb_const.SUPPORTED_HEALTH_MONITOR_TYPES,
                             name="healthmonitors_typev2"),
                     nullable=False)
    delay = sa.Column(sa.Integer, nullable=False)
    timeout = sa.Column(sa.Integer, nullable=False)
    max_retries = sa.Column(sa.Integer, nullable=False)
    http_method = sa.Column(sa.String(16), nullable=True)
    url_path = sa.Column(sa.String(255), nullable=True)
    expected_codes = sa.Column(sa.String(64), nullable=True)
    provisioning_status = sa.Column(sa.String(16), nullable=False)
    admin_state_up = sa.Column(sa.Boolean(), nullable=False)
    name = sa.Column(sa.String(attr.NAME_MAX_LEN), nullable=True)

    @property
    def root_loadbalancer(self):
        return self.pool.loadbalancer


class LoadBalancer(model_base.BASEV2, models_v2.HasId, models_v2.HasTenant):
    """Represents a v2 neutron load balancer."""

    NAME = 'loadbalancer'

    __tablename__ = "lbaas_loadbalancers"

    name = sa.Column(sa.String(255))
    description = sa.Column(sa.String(255))
    vip_subnet_id = sa.Column(sa.String(36), nullable=False)
    vip_port_id = sa.Column(sa.String(36), sa.ForeignKey(
        'ports.id', name='fk_lbaas_loadbalancers_ports_id'))
    vip_address = sa.Column(sa.String(36))
    provisioning_status = sa.Column(sa.String(16), nullable=False)
    operating_status = sa.Column(sa.String(16), nullable=False)
    admin_state_up = sa.Column(sa.Boolean(), nullable=False)
    vip_port = orm.relationship(models_v2.Port)
    stats = orm.relationship(
        LoadBalancerStatistics,
        uselist=False,
        backref=orm.backref("loadbalancer", uselist=False),
        cascade="all, delete-orphan",
        lazy='joined')
    provider = orm.relationship(
        st_db.ProviderResourceAssociation,
        uselist=False,
        lazy="joined",
        primaryjoin="LoadBalancer.id==ProviderResourceAssociation.resource_id",
        foreign_keys=[st_db.ProviderResourceAssociation.resource_id],
        # this is only for old API backwards compatibility because when a load
        # balancer is deleted the pool ID should be the same as the load
        # balancer ID and should not be cleared out in this table
        viewonly=True
    )
    flavor_id = sa.Column(sa.String(36), sa.ForeignKey(
        'flavors.id', name='fk_lbaas_loadbalancers_flavors_id'))

    @property
    def root_loadbalancer(self):
        return self


class PoolV2(model_base.BASEV2, models_v2.HasId, models_v2.HasTenant):
    """Represents a v2 neutron load balancer pool."""

    NAME = 'pool'

    __tablename__ = "lbaas_pools"

    name = sa.Column(sa.String(255), nullable=True)
    description = sa.Column(sa.String(255), nullable=True)
    loadbalancer_id = sa.Column(sa.String(36), sa.ForeignKey(
        "lbaas_loadbalancers.id"))
    healthmonitor_id = sa.Column(sa.String(36),
                                 sa.ForeignKey("lbaas_healthmonitors.id"),
                                 unique=True,
                                 nullable=True)
    protocol = sa.Column(sa.Enum(*lb_const.POOL_SUPPORTED_PROTOCOLS,
                                 name="pool_protocolsv2"),
                         nullable=False)
    lb_algorithm = sa.Column(sa.Enum(*lb_const.SUPPORTED_LB_ALGORITHMS,
                                     name="lb_algorithmsv2"),
                             nullable=False)
    admin_state_up = sa.Column(sa.Boolean(), nullable=False)
    provisioning_status = sa.Column(sa.String(16), nullable=False)
    operating_status = sa.Column(sa.String(16), nullable=False)
    members = orm.relationship(MemberV2,
                               backref=orm.backref("pool", uselist=False),
                               cascade="all, delete-orphan",
                               lazy='joined')
    healthmonitor = orm.relationship(
        HealthMonitorV2,
        backref=orm.backref("pool", uselist=False),
        lazy='joined')
    session_persistence = orm.relationship(
        SessionPersistenceV2,
        uselist=False,
        backref=orm.backref("pool", uselist=False),
        cascade="all, delete-orphan",
        lazy='joined')
    loadbalancer = orm.relationship(
        LoadBalancer, uselist=False,
        backref=orm.backref("pools", uselist=True),
        lazy='joined')

    @property
    def root_loadbalancer(self):
        return self.loadbalancer

    # No real relationship here. But we want to fake a pool having a
    # 'listener_id' sometimes for API back-ward compatibility purposes.
    @property
    def listener(self):
        if self.listeners:
            return self.listeners[0]
        else:
            return None


class SNI(model_base.BASEV2):

    """Many-to-many association between Listener and TLS container ids
    Making the SNI certificates list, ordered using the position
    """

    NAME = 'sni'

    __tablename__ = "lbaas_sni"

    listener_id = sa.Column(sa.String(36),
                            sa.ForeignKey("lbaas_listeners.id"),
                            primary_key=True,
                            nullable=False)
    tls_container_id = sa.Column(sa.String(128),
                                 primary_key=True,
                                 nullable=False)
    position = sa.Column(sa.Integer)

    @property
    def root_loadbalancer(self):
        return self.listener.loadbalancer


class L7Rule(model_base.BASEV2, models_v2.HasId, models_v2.HasTenant):
    """Represents L7 Rule."""

    NAME = 'l7rule'

    __tablename__ = "lbaas_l7rules"

    l7policy_id = sa.Column(sa.String(36),
                            sa.ForeignKey("lbaas_l7policies.id"),
                            nullable=False)
    type = sa.Column(sa.Enum(*lb_const.SUPPORTED_L7_RULE_TYPES,
                             name="l7rule_typesv2"),
                     nullable=False)
    compare_type = sa.Column(sa.Enum(*lb_const.SUPPORTED_L7_RULE_COMPARE_TYPES,
                                     name="l7rule_compare_typev2"),
                             nullable=False)
    invert = sa.Column(sa.Boolean(), nullable=False)
    key = sa.Column(sa.String(255), nullable=True)
    value = sa.Column(sa.String(255), nullable=False)
    provisioning_status = sa.Column(sa.String(16), nullable=False)
    admin_state_up = sa.Column(sa.Boolean(), nullable=False)

    @property
    def root_loadbalancer(self):
        return self.policy.listener.loadbalancer


class L7Policy(model_base.BASEV2, models_v2.HasId, models_v2.HasTenant):
    """Represents L7 Policy."""

    NAME = 'l7policy'

    __tablename__ = "lbaas_l7policies"

    name = sa.Column(sa.String(255), nullable=True)
    description = sa.Column(sa.String(255), nullable=True)
    listener_id = sa.Column(sa.String(36),
                            sa.ForeignKey("lbaas_listeners.id"),
                            nullable=False)
    action = sa.Column(sa.Enum(*lb_const.SUPPORTED_L7_POLICY_ACTIONS,
                               name="l7policy_action_typesv2"),
                       nullable=False)
    redirect_pool_id = sa.Column(sa.String(36),
                                 sa.ForeignKey("lbaas_pools.id"),
                                 nullable=True)
    redirect_url = sa.Column(sa.String(255),
                             nullable=True)
    position = sa.Column(sa.Integer, nullable=False)
    provisioning_status = sa.Column(sa.String(16), nullable=False)
    admin_state_up = sa.Column(sa.Boolean(), nullable=False)
    rules = orm.relationship(
        L7Rule,
        uselist=True,
        lazy="joined",
        primaryjoin="L7Policy.id==L7Rule.l7policy_id",
        foreign_keys=[L7Rule.l7policy_id],
        cascade="all, delete-orphan",
        backref=orm.backref("policy")
    )
    redirect_pool = orm.relationship(
        PoolV2, backref=orm.backref("l7_policies", uselist=True),
        lazy='joined')

    @property
    def root_loadbalancer(self):
        return self.listener.loadbalancer


class Listener(model_base.BASEV2, models_v2.HasId, models_v2.HasTenant):
    """Represents a v2 neutron listener."""

    NAME = 'listener'

    __tablename__ = "lbaas_listeners"

    __table_args__ = (
        sa.schema.UniqueConstraint('loadbalancer_id', 'protocol_port',
                                   name='uniq_loadbalancer_listener_port'),
    )

    name = sa.Column(sa.String(255))
    description = sa.Column(sa.String(255))
    default_pool_id = sa.Column(sa.String(36), sa.ForeignKey("lbaas_pools.id"),
                                nullable=True)
    loadbalancer_id = sa.Column(sa.String(36), sa.ForeignKey(
        "lbaas_loadbalancers.id"))
    protocol = sa.Column(sa.Enum(*lb_const.LISTENER_SUPPORTED_PROTOCOLS,
                                 name="listener_protocolsv2"),
                         nullable=False)
    default_tls_container_id = sa.Column(sa.String(128),
                                         default=None, nullable=True)
    sni_containers = orm.relationship(
            SNI,
            backref=orm.backref("listener", uselist=False),
            uselist=True,
            lazy="joined",
            primaryjoin="Listener.id==SNI.listener_id",
            order_by='SNI.position',
            collection_class=orderinglist.ordering_list(
                'position'),
            foreign_keys=[SNI.listener_id],
            cascade="all, delete-orphan"
    )
    protocol_port = sa.Column(sa.Integer, nullable=False)
    connection_limit = sa.Column(sa.Integer)
    admin_state_up = sa.Column(sa.Boolean(), nullable=False)
    provisioning_status = sa.Column(sa.String(16), nullable=False)
    operating_status = sa.Column(sa.String(16), nullable=False)
    default_pool = orm.relationship(
        PoolV2, backref=orm.backref("listeners"), lazy='joined')
    loadbalancer = orm.relationship(
        LoadBalancer,
        backref=orm.backref("listeners", uselist=True),
        lazy='joined')
    l7_policies = orm.relationship(
        L7Policy,
        uselist=True,
        lazy="joined",
        primaryjoin="Listener.id==L7Policy.listener_id",
        order_by="L7Policy.position",
        collection_class=orderinglist.ordering_list('position', count_from=1),
        foreign_keys=[L7Policy.listener_id],
        cascade="all, delete-orphan",
        backref=orm.backref("listener"))

    @property
    def root_loadbalancer(self):
        return self.loadbalancer
