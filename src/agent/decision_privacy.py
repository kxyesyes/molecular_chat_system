"""Mute raw dependency network logs only inside a decision request context."""
from contextlib import contextmanager
from contextvars import ContextVar
import logging


_PRIVATE_REQUEST = ContextVar("medchat_private_decision_request", default=False)
# Logger names used by the installed HTTPX/HTTPCore async and sync transports.
# Parent logger filters do not filter child records, so list the emitters.
_NETWORK_LOGGERS = (
    "httpx", "httpcore.connection", "httpcore.http11", "httpcore.http2",
    "httpcore.proxy", "httpcore.socks",
)


class _DecisionNetworkFilter(logging.Filter):
    def filter(self, record):
        return not _PRIVATE_REQUEST.get()


_FILTER = _DecisionNetworkFilter()
for _name in _NETWORK_LOGGERS:
    logging.getLogger(_name).addFilter(_FILTER)


@contextmanager
def private_decision_request():
    """Keep unrelated requests' logger levels and records unchanged."""
    token = _PRIVATE_REQUEST.set(True)
    try:
        yield
    finally:
        _PRIVATE_REQUEST.reset(token)
