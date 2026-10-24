"""Pinned HTTP transport used by model clients and Agent decision requests."""

from __future__ import annotations

from contextlib import asynccontextmanager
from contextvars import ContextVar
import asyncio
import ipaddress
import socket
from urllib.parse import urlsplit

import httpx
import httpcore

from src.system.network_policy import resolve_llm_host
from src.system.model_lifecycle import finish_on_cancel


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


def validate_ollama_endpoint(value):
    """Explicit local-only policy, separate from public cloud DNS validation."""
    from src.system.network_policy import validate_llm_url
    if (type(value) is not str or not value
            or any(char.isspace() or ord(char) < 32 or char in '\\?#' for char in value)):
        raise ValueError('unsafe_ollama_endpoint')
    if urlsplit(value).port == 0:
        raise ValueError('unsafe_ollama_endpoint')
    return validate_llm_url(value, provider='ollama', resolve_host=False)


class OllamaLoopbackTransport(httpx.AsyncBaseTransport):
    """Resolve only approved local names, validate all answers, pin the socket."""

    def __init__(self):
        # Keep normal TLS verification. This wrapper must not be rewrapped by
        # pin_supplied_async_client's distinct public-network-only policy.
        self._transport = httpx.AsyncHTTPTransport(trust_env=False)
        self._backend = PinnedNetworkBackend()
        self._transport._pool._network_backend = self._backend
        self._resolutions = set()

    async def handle_async_request(self, request):
        validate_ollama_endpoint(str(request.url))
        host = request.url.host
        port = request.url.port or (443 if request.url.scheme == 'https' else 80)
        # DNS is blocking even for localhost. Keep its worker owned through
        # cancellation so a cancelled resolution can never proceed to HTTP.
        resolution = asyncio.create_task(asyncio.to_thread(
            socket.getaddrinfo, host, port, type=socket.SOCK_STREAM))
        self._resolutions.add(resolution)
        try:
            infos = await finish_on_cancel(resolution)
        finally:
            self._resolutions.discard(resolution)
        addresses = sorted({str(info[4][0]) for info in infos})
        if not addresses or any(not ipaddress.ip_address(address).is_loopback for address in addresses):
            raise ValueError('unsafe_ollama_resolution')
        async with self._backend.pin(host, addresses):
            return await self._transport.handle_async_request(request)

    async def aclose(self):
        # Repeated cancellation can interrupt an outer wait_for's child drain.
        # Retain physical DNS ownership here as well, before closing the pool.
        try:
            await finish_on_cancel(asyncio.gather(
                *tuple(self._resolutions), return_exceptions=True))
        finally:
            await finish_on_cancel(self._transport.aclose())


def create_ollama_transport():
    return OllamaLoopbackTransport()


def create_ollama_async_client(timeout, *, event_hooks=None):
    """Own one local protocol client's connection pool and supplied hook order."""
    return httpx.AsyncClient(timeout=timeout, follow_redirects=False, trust_env=False,
                             event_hooks=event_hooks, transport=create_ollama_transport())


__all__ = [
    "PinnedAsyncHTTPTransport",
    "PinnedClientTransport",
    "PinnedNetworkBackend",
    "create_pinned_async_client",
    "pin_supplied_async_client",
    "validate_ollama_endpoint",
    "OllamaLoopbackTransport",
    "create_ollama_transport",
    "create_ollama_async_client",
]
