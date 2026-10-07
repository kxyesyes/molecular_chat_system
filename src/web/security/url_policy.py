"""Outbound URL policy for user-configured model endpoints."""

from __future__ import annotations

import ipaddress
import os
import socket
from urllib.parse import urlsplit, urlunsplit

_OFFICIAL_HOSTS = frozenset({
    "api.deepseek.com",
    "api.openai.com",
    "api-inference.modelscope.cn",
})
_OLLAMA_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})


def _configured_hosts() -> frozenset[str]:
    return frozenset(
        item.strip().lower().rstrip(".")
        for item in os.environ.get("MEDCHAT_LLM_ALLOWED_HOSTS", "").split(",")
        if item.strip()
    )


def _is_blocked_address(address: str) -> bool:
    try:
        value = ipaddress.ip_address(address)
    except ValueError:
        # getaddrinfo must yield a numeric destination. Treating an
        # unexpected hostname as safe would reintroduce a second DNS lookup
        # at connect time and defeat the pinning boundary.
        return True
    return any((not value.is_global, value.is_private, value.is_loopback,
                value.is_link_local, value.is_multicast, value.is_reserved,
                value.is_unspecified))


def resolve_llm_host(host: str, port: int) -> tuple[str, ...]:
    try:
        infos = socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    except OSError as exc:
        raise ValueError("无法解析模型服务域名") from exc
    addresses = {str(info[4][0]).split("%", 1)[0] for info in infos}
    if not addresses or any(_is_blocked_address(address) for address in addresses):
        raise ValueError("模型服务地址指向受限网络")
    return tuple(sorted(addresses))


# Kept as a private compatibility alias for callers from the first policy slice.
_resolved_addresses = resolve_llm_host


def validate_llm_url(value: str, *, provider: str, resolve_host: bool = True) -> str:
    """Normalize and validate one model endpoint before persistence or I/O."""
    if type(value) is not str or not value or len(value) > 2048:
        raise ValueError("模型服务地址不合法")
    if any(ord(char) < 32 or ord(char) == 127 for char in value):
        raise ValueError("模型服务地址不合法")
    raw = value.strip().rstrip("/")
    parts = urlsplit(raw)
    normalized_provider = str(provider or "").strip().lower().replace("-", "_")
    if parts.scheme not in {"http", "https"} or not parts.hostname:
        raise ValueError("模型服务地址不合法")
    if parts.username is not None or parts.password is not None:
        raise ValueError("模型服务地址不能包含凭据")
    if parts.query or parts.fragment or "\\" in raw:
        raise ValueError("模型服务地址不合法")
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError("模型服务端口不合法") from exc
    host = parts.hostname.lower().rstrip(".")
    try:
        ipaddress.ip_address(host)
    except ValueError:
        is_ip_literal = False
    else:
        is_ip_literal = True
    if normalized_provider == "ollama":
        if host not in _OLLAMA_HOSTS:
            raise ValueError("Ollama 只允许本机地址")
    else:
        if is_ip_literal or host not in (_OFFICIAL_HOSTS | _configured_hosts()):
            raise ValueError("模型服务域名不在可信名单中")
        if parts.scheme != "https":
            raise ValueError("外部模型服务必须使用 HTTPS")
    effective_port = port or (443 if parts.scheme == "https" else 80)
    if resolve_host and not (normalized_provider == "ollama" and host in _OLLAMA_HOSTS):
        _resolved_addresses(host, effective_port)
    rendered_host = f"[{host}]" if ":" in host else host
    rendered_port = "" if port is None or port == (443 if parts.scheme == "https" else 80) else f":{port}"
    path = parts.path.rstrip("/") or ""
    if normalized_provider != "ollama" and not path.endswith("/chat/completions"):
        path = f"{path}/chat/completions" if path.endswith("/v1") else f"{path}/v1/chat/completions"
    return urlunsplit((parts.scheme, rendered_host + rendered_port, path, "", ""))
