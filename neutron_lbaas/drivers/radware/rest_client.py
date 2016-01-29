# Copyright 2015, Radware LTD. All rights reserved
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

import base64
import httplib

from oslo_log import helpers as log_helpers
from oslo_log import log as logging
from oslo_serialization import jsonutils

from neutron_lbaas._i18n import _LE, _LW
from neutron_lbaas.drivers.radware import exceptions as r_exc

LOG = logging.getLogger(__name__)

RESP_STATUS = 0
RESP_REASON = 1
RESP_STR = 2
RESP_DATA = 3


class vDirectRESTClient(object):
    """REST server proxy to Radware vDirect."""
    @log_helpers.log_method_call
    def __init__(self,
                 server='localhost',
                 secondary_server=None,
                 user=None,
                 password=None,
                 port=2189,
                 ssl=True,
                 timeout=5000,
                 base_uri=''):
        self.server = server
        self.secondary_server = secondary_server
        self.port = port
        self.ssl = ssl
        self.base_uri = base_uri
        self.timeout = timeout
        if user and password:
            self.auth = base64.encodestring('%s:%s' % (user, password))
            self.auth = self.auth.replace('\n', '')
        else:
            raise r_exc.AuthenticationMissing()

        debug_params = {'server': self.server,
                        'sec_server': self.secondary_server,
                        'port': self.port,
                        'ssl': self.ssl}
        LOG.debug('vDirectRESTClient:init server=%(server)s, '
                  'secondary server=%(sec_server)s, '
                  'port=%(port)d, '
                  'ssl=%(ssl)r', debug_params)

    def _flip_servers(self):
        LOG.warning(_LW('Fliping servers. Current is: %(server)s, '
                 'switching to %(secondary)s'),
                 {'server': self.server,
                 'secondary': self.secondary_server})
        self.server, self.secondary_server = self.secondary_server, self.server

    def _recover(self, action, resource, data, headers, binary=False):
        if self.server and self.secondary_server:
            self._flip_servers()
            resp = self._call(action, resource, data,
                              headers, binary)
            return resp
        else:
            LOG.error(_LE('REST client is not able to recover '
                          'since only one vDirect server is '
                          'configured.'))
            return -1, None, None, None

    def call(self, action, resource, data, headers, binary=False):
        resp = self._call(action, resource, data, headers, binary)
        if resp[RESP_STATUS] == -1:
            LOG.warning(_LW('vDirect server is not responding (%s).'),
                        self.server)
            return self._recover(action, resource, data, headers, binary)
        elif resp[RESP_STATUS] in (301, 307):
            LOG.warning(_LW('vDirect server is not active (%s).'),
                        self.server)
            return self._recover(action, resource, data, headers, binary)
        else:
            return resp

    @log_helpers.log_method_call
    def _call(self, action, resource, data, headers, binary=False):
        if resource.startswith('http'):
            uri = resource
        else:
            uri = self.base_uri + resource
        if binary:
            body = data
        else:
            body = jsonutils.dumps(data)

        debug_data = 'binary' if binary else body
        debug_data = debug_data if debug_data else 'EMPTY'
        if not headers:
            headers = {'Authorization': 'Basic %s' % self.auth}
        else:
            headers['Authorization'] = 'Basic %s' % self.auth
        conn = None
        if self.ssl:
            conn = httplib.HTTPSConnection(
                self.server, self.port, timeout=self.timeout)
            if conn is None:
                LOG.error(_LE('vdirectRESTClient: Could not establish HTTPS '
                          'connection'))
                return 0, None, None, None
        else:
            conn = httplib.HTTPConnection(
                self.server, self.port, timeout=self.timeout)
            if conn is None:
                LOG.error(_LE('vdirectRESTClient: Could not establish HTTP '
                          'connection'))
                return 0, None, None, None

        try:
            conn.request(action, uri, body, headers)
            response = conn.getresponse()
            respstr = response.read()
            respdata = respstr
            try:
                respdata = jsonutils.loads(respstr)
            except ValueError:
                # response was not JSON, ignore the exception
                pass
            ret = (response.status, response.reason, respstr, respdata)
        except Exception as e:
            log_dict = {'action': action, 'e': e}
            LOG.error(_LE('vdirectRESTClient: %(action)s failure, %(e)r'),
                      log_dict)
            ret = -1, None, None, None
        conn.close()
        return ret
