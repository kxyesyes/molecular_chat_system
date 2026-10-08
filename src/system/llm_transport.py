"""Pinned HTTP transport used by model clients and Agent decision requests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar

import httpx
import httpcore

from src.system.network_policy import resolve_llm_host


_PINNED_ADDRESSES: ContextVar[dict[str, tuple[str, ...]] | None] = ContextVar(
    "llm_pinned_addresses", default=None,
)


class PinnedNetworkBackend(httpcore.AsyncNetworkBackend):
    """Resolve an approved hostname once and use that address for the socket."""

    def __init__(self, backend: httpcore.AsyncNetworkBackend | None = None):
        backend_type = getattr(httpcore, "AutoBackend", None)
        if backend_type is None:
            backend_type = httpcore.AnyIOBackend
        self._backend = backend or backend_type()

    @asynccontextmanager
    async def pin(self, host: str, addresses):
        current = dict(_PINNED_ADDRESSES.get() or {})
        current[host] = tuple(addresses)
        token = _PINNED_ADDRESSES.set(current)
        try:
            yield
        finally:
            _PINNED_ADDRESSES.reset(token)

    async def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        pinned = (_PINNED_ADDRESSES.get() or {}).get(host)
        if not pinned:
            raise httpcore.ConnectError("unbound outbound hostname")
        return await self._backend.connect_tcp(
            pinned[0], port, timeout=timeout, local_address=local_address,
            socket_options=socket_options,
        )

    async def connect_unix_socket(self, *args, **kwargs):
        raise httpcore.ConnectError("unix sockets are not allowed for outbound LLM transport")

    async def sleep(self, seconds):
        return await self._backend.sleep(seconds)


class PinnedAsyncHTTPTransport(httpx.AsyncHTTPTransport):
    """HTTPX transport with connect-time DNS pinning and normal TLS checks."""

    def __init__(self, **kwargs):
        kwargs["trust_env"] = False
        super().__init__(**kwargs)
        self._pinned_backend = PinnedNetworkBackend()
        self._pool._network_backend = self._pinned_backend

    async def handle_async_request(self, request):
        host = request.url.host
        port = request.url.port
        addresses = resolve_llm_host(host, port)
        async with self._pinned_backend.pin(host, addresses):
            return await super().handle_async_request(request)


class PinnedClientTransport(httpx.AsyncBaseTransport):
    """Pin direct HTTPX client connections while preserving its pool settings."""

    def __init__(self, transport):
        pool = getattr(transport, "_pool", None)
        if pool is None or not hasattr(pool, "_network_backend"):
            raise TypeError("unsupported client transport for DNS pinning")
        self._transport = transport
        self._backend = PinnedNetworkBackend()
        pool._network_backend = self._backend

    async def handle_async_request(self, request):
        addresses = resolve_llm_host(request.url.host, request.url.port)
        async with self._backend.pin(request.url.host, addresses):
            return await self._transport.handle_async_request(request)

    async def aclose(self):
        await self._transport.aclose()


def pin_supplied_async_client(client):
    """Apply connect-time pinning to a direct HTTPX client, if supported."""
    transport = getattr(client, "_transport", None)
    if isinstance(transport, httpx.MockTransport):
        return client
    if isinstance(transport, httpx.AsyncHTTPTransport):
        client._trust_env = False
        if isinstance(getattr(client, "_mounts", None), dict):
            client._mounts = {}
        client._transport = PinnedClientTransport(transport)
    return client


def create_pinned_async_client(timeout, *, event_hooks=None, httpx_module=httpx):
    """Create an HTTPX client with direct, pinned outbound connections."""
    client = httpx_module.AsyncClient(
        timeout=timeout,
        follow_redirects=False,
        trust_env=False,
        event_hooks=event_hooks,
    )
    if not isinstance(getattr(client, "_transport", None), httpx_module.MockTransport):
        client._transport = PinnedAsyncHTTPTransport()
    return client


__all__ = [
    "PinnedAsyncHTTPTransport",
    "PinnedClientTransport",
    "PinnedNetworkBackend",
    "create_pinned_async_client",
    "pin_supplied_async_client",
]
