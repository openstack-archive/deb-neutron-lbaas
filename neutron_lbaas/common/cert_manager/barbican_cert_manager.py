# Copyright 2014, 2015 Rackspace US, Inc.
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

from barbicanclient import client as barbican_client
from neutron.plugins.common import constants
from oslo_config import cfg
from oslo_log import log as logging
from oslo_utils import excutils

from neutron_lbaas._i18n import _LI, _LW, _LE
from neutron_lbaas.common.cert_manager import cert_manager
from neutron_lbaas.common import keystone

LOG = logging.getLogger(__name__)

CONF = cfg.CONF


class Cert(cert_manager.Cert):
    """Representation of a Cert based on the Barbican CertificateContainer."""
    def __init__(self, cert_container):
        if not isinstance(cert_container,
                          barbican_client.containers.CertificateContainer):
            raise TypeError(_LE(
                "Retrieved Barbican Container is not of the correct type "
                "(certificate)."))
        self._cert_container = cert_container

    # Container secrets are accessed upon query and can return as None,
    # don't return the payload if the secret is not available.

    def get_certificate(self):
        if self._cert_container.certificate:
            return self._cert_container.certificate.payload

    def get_intermediates(self):
        if self._cert_container.intermediates:
            return self._cert_container.intermediates.payload

    def get_private_key(self):
        if self._cert_container.private_key:
            return self._cert_container.private_key.payload

    def get_private_key_passphrase(self):
        if self._cert_container.private_key_passphrase:
            return self._cert_container.private_key_passphrase.payload


class BarbicanKeystoneAuth(object):
    _keystone_session = None
    _barbican_client = None

    @classmethod
    def get_barbican_client(cls):
        """Creates a Barbican client object.

        :returns: a Barbican Client object
        :raises Exception: if the client cannot be created
        """
        if not cls._barbican_client:
            try:
                cls._keystone_session = keystone.get_session()
                cls._barbican_client = barbican_client.Client(
                    session=cls._keystone_session
                )
            except Exception:
                # Barbican (because of Keystone-middleware) sometimes masks
                #  exceptions strangely -- this will reraise the original
                #  exception, while also providiung useful feedback in the
                #  logs for debugging
                with excutils.save_and_reraise_exception():
                    LOG.exception(_LE("Error creating Barbican client"))
        return cls._barbican_client


class CertManager(cert_manager.CertManager):
    """Certificate Manager that wraps the Barbican client API."""
    @staticmethod
    def store_cert(certificate, private_key, intermediates=None,
                   private_key_passphrase=None, expiration=None,
                   name='Octavia TLS Cert', **kwargs):
        """Stores a certificate in the certificate manager.

        :param certificate: PEM encoded TLS certificate
        :param private_key: private key for the supplied certificate
        :param intermediates: ordered and concatenated intermediate certs
        :param private_key_passphrase: optional passphrase for the supplied key
        :param expiration: the expiration time of the cert in ISO 8601 format
        :param name: a friendly name for the cert

        :returns: the container_ref of the stored cert
        :raises Exception: if certificate storage fails
        """
        connection = BarbicanKeystoneAuth.get_barbican_client()

        LOG.info(_LI(
            "Storing certificate container '{0}' in Barbican."
        ).format(name))

        certificate_secret = None
        private_key_secret = None
        intermediates_secret = None
        pkp_secret = None

        try:
            certificate_secret = connection.secrets.create(
                payload=certificate,
                expiration=expiration,
                name="Certificate"
            )
            private_key_secret = connection.secrets.create(
                payload=private_key,
                expiration=expiration,
                name="Private Key"
            )
            certificate_container = connection.containers.create_certificate(
                name=name,
                certificate=certificate_secret,
                private_key=private_key_secret
            )
            if intermediates:
                intermediates_secret = connection.secrets.create(
                    payload=intermediates,
                    expiration=expiration,
                    name="Intermediates"
                )
                certificate_container.intermediates = intermediates_secret
            if private_key_passphrase:
                pkp_secret = connection.secrets.create(
                    payload=private_key_passphrase,
                    expiration=expiration,
                    name="Private Key Passphrase"
                )
                certificate_container.private_key_passphrase = pkp_secret

            certificate_container.store()
            return certificate_container.container_ref
        # Barbican (because of Keystone-middleware) sometimes masks
        #  exceptions strangely -- this will catch anything that it raises and
        #  reraise the original exception, while also providing useful
        #  feedback in the logs for debugging
        except Exception:
            for secret in [certificate_secret, private_key_secret,
                      intermediates_secret, pkp_secret]:
                if secret and secret.secret_ref:
                    old_ref = secret.secret_ref
                    try:
                        secret.delete()
                        LOG.info(_LI(
                            "Deleted secret {0} ({1}) during rollback."
                        ).format(secret.name, old_ref))
                    except Exception:
                        LOG.warning(_LW(
                            "Failed to delete {0} ({1}) during rollback. This "
                            "is probably not a problem."
                        ).format(secret.name, old_ref))
            with excutils.save_and_reraise_exception():
                LOG.exception(_LE("Error storing certificate data"))

    @staticmethod
    def get_cert(cert_ref, service_name='lbaas',
                 lb_id=None,
                 check_only=False, **kwargs):
        """Retrieves the specified cert and registers as a consumer.

        :param cert_ref: the UUID of the cert to retrieve
        :param service_name: Friendly name for the consuming service
        :param lb_id: Loadbalancer id for building resource consumer URL
        :param check_only: Read Certificate data without registering

        :returns: octavia.certificates.common.Cert representation of the
                 certificate data
        :raises Exception: if certificate retrieval fails
        """
        connection = BarbicanKeystoneAuth.get_barbican_client()

        LOG.info(_LI(
            "Loading certificate container {0} from Barbican."
        ).format(cert_ref))
        try:
            if check_only:
                cert_container = connection.containers.get(
                    container_ref=cert_ref
                )
            else:
                cert_container = connection.containers.register_consumer(
                    container_ref=cert_ref,
                    name=service_name,
                    url=CertManager._get_service_url(lb_id)
                )
            return Cert(cert_container)
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.exception(_LE("Error getting {0}").format(cert_ref))

    @staticmethod
    def delete_cert(cert_ref, lb_id, service_name='lbaas', **kwargs):
        """Deregister as a consumer for the specified cert.

        :param cert_ref: the UUID of the cert to retrieve
        :param service_name: Friendly name for the consuming service
        :param lb_id: Loadbalancer id for building resource consumer URL

        :raises Exception: if deregistration fails
        """
        connection = BarbicanKeystoneAuth.get_barbican_client()

        LOG.info(_LI(
            "Deregistering as a consumer of {0} in Barbican."
        ).format(cert_ref))
        try:
            connection.containers.remove_consumer(
                container_ref=cert_ref,
                name=service_name,
                url=CertManager._get_service_url(lb_id)
            )
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.exception(_LE(
                    "Error deregistering as a consumer of {0}"
                ).format(cert_ref))

    @staticmethod
    def _actually_delete_cert(cert_ref):
        """Deletes the specified cert. Very dangerous. Do not recommend.

        :param cert_ref: the UUID of the cert to delete
        :raises Exception: if certificate deletion fails
        """
        connection = BarbicanKeystoneAuth.get_barbican_client()

        LOG.info(_LI(
            "Recursively deleting certificate container {0} from Barbican."
        ).format(cert_ref))
        try:
            certificate_container = connection.containers.get(cert_ref)
            certificate_container.certificate.delete()
            if certificate_container.intermediates:
                certificate_container.intermediates.delete()
            if certificate_container.private_key_passphrase:
                certificate_container.private_key_passphrase.delete()
            certificate_container.private_key.delete()
            certificate_container.delete()
        except Exception:
            with excutils.save_and_reraise_exception():
                LOG.exception(_LE(
                    "Error recursively deleting certificate container {0}"
                ).format(cert_ref))

    @staticmethod
    def _get_service_url(lb_id):
        # Format: <servicename>://<region>/<resource>/<object_id>
        return "{0}://{1}/{2}/{3}".format(
            cfg.CONF.service_auth.service_name,
            cfg.CONF.service_auth.region,
            constants.LOADBALANCER,
            lb_id)
