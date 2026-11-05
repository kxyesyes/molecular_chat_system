"""Direct HTTPS transport pinned to an already validated public address."""

from urllib3.connection import HTTPSConnection
from urllib3.connectionpool import HTTPSConnectionPool
from urllib3.util import connection
import requests


def pinned_connection_class(address):
    class PinnedConnection(HTTPSConnection):
        def _new_conn(self):
            return connection.create_connection(
                (address, self.port), self.timeout,
                source_address=self.source_address, socket_options=self.socket_options)
    return PinnedConnection


def get_pinned(url, address, **kwargs):
    """Keep the URL host for TLS and HTTP while connecting only to address."""
    class PinnedPool(HTTPSConnectionPool):
        ConnectionCls = pinned_connection_class(address)

    class PinnedAdapter(requests.adapters.HTTPAdapter):
        def init_poolmanager(self, *args, **options):
            super().init_poolmanager(*args, **options)
            self.poolmanager.pool_classes_by_scheme = {
                **self.poolmanager.pool_classes_by_scheme, 'https': PinnedPool}

    session = requests.Session()
    session.trust_env = False
    session.mount('https://', PinnedAdapter())
    try:
        response = session.get(url, **kwargs)
    except BaseException:
        session.close()
        raise
    original_close = response.close

    def close():
        try:
            original_close()
        finally:
            session.close()
    response.close = close
    return response
