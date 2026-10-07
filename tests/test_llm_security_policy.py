from __future__ import annotations

import pytest

from src.agent.decision_transport import (
    PinnedClientTransport,
    PinnedNetworkBackend,
    create_pinned_async_client,
)
from src.web.security.url_policy import validate_llm_url
from src.web.user_llm_config import _endpoint, _request_config


@pytest.mark.parametrize("url", [
    "http://localhost:8080/v1",
    "http://127.0.0.1:8080/v1",
    "http://10.0.0.5/v1",
    "http://192.168.1.20/v1",
    "http://169.254.169.254/latest/meta-data",
    "https://user:password@api.deepseek.com/v1",
    "ftp://api.deepseek.com/v1",
])
def test_external_llm_endpoint_rejects_internal_or_unsafe_urls(url):
    with pytest.raises(ValueError):
        validate_llm_url(url, provider="openai_compatible", resolve_host=False)


def test_external_llm_endpoint_allows_official_deepseek_host():
    assert validate_llm_url(
        "https://api.deepseek.com/chat/completions",
        provider="openai_compatible", resolve_host=False,
    ).startswith("https://api.deepseek.com/")


def test_operator_can_allow_one_exact_external_host(monkeypatch):
    monkeypatch.setenv("MEDCHAT_LLM_ALLOWED_HOSTS", "api.supxh.xin,api.deepseek.com")
    assert validate_llm_url(
        "https://api.supxh.xin/v1", provider="custom", resolve_host=False,
    ).endswith("/v1/chat/completions")
    with pytest.raises(ValueError):
        validate_llm_url(
            "https://evil.supxh.xin/v1", provider="custom", resolve_host=False,
        )


def test_ollama_loopback_is_explicit_provider_only():
    assert validate_llm_url(
        "http://127.0.0.1:11434", provider="ollama", resolve_host=False,
    ).startswith("http://127.0.0.1:11434")
    with pytest.raises(ValueError):
        validate_llm_url(
            "http://127.0.0.1:11434", provider="openai_compatible", resolve_host=False,
        )


def test_user_config_endpoint_uses_the_same_policy():
    with pytest.raises(ValueError):
        _endpoint({"provider": "openai_compatible", "base_url": "http://127.0.0.1:6001", "model_name": "demo"})
    with pytest.raises(ValueError):
        _request_config({
            "provider": "openai_compatible",
            "base_url": "https://api.deepseek.com/v1?next=http://127.0.0.1",
            "model_name": "demo", "stream": True,
        })


def test_external_resolution_rejects_any_restricted_answer(monkeypatch):
    import src.web.security.url_policy as policy

    monkeypatch.setattr(
        policy.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [
            (2, 1, 6, "", ("203.0.113.10", 443)),
            (2, 1, 6, "", ("169.254.169.254", 443)),
        ],
    )
    with pytest.raises(ValueError, match="受限网络"):
        validate_llm_url(
            "https://api.deepseek.com/v1",
            provider="openai_compatible",
            resolve_host=True,
        )


def test_external_resolution_rejects_non_ip_answer(monkeypatch):
    import src.web.security.url_policy as policy

    monkeypatch.setattr(
        policy.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("unexpected-hostname", 443))],
    )
    with pytest.raises(ValueError, match="受限网络"):
        validate_llm_url(
            "https://api.deepseek.com/v1",
            provider="openai_compatible",
            resolve_host=True,
        )


def test_actual_connect_uses_pinned_address_without_second_dns_lookup():
    class Backend:
        def __init__(self):
            self.hosts = []

        async def connect_tcp(self, host, port, **kwargs):
            self.hosts.append((host, port))
            return "stream"

        async def connect_unix_socket(self, *args, **kwargs):
            raise AssertionError("unix sockets are not part of external LLM transport")

        async def sleep(self, seconds):
            return None

    async def exercise():
        backend = Backend()
        pinned = PinnedNetworkBackend(backend)
        async with pinned.pin("api.deepseek.com", ("203.0.113.10",)):
            assert await pinned.connect_tcp("api.deepseek.com", 443) == "stream"
        return backend.hosts

    import asyncio

    assert asyncio.run(exercise()) == [("203.0.113.10", 443)]


@pytest.fixture
def _safe_provider_dns(monkeypatch):
    import src.web.security.url_policy as policy

    monkeypatch.setattr(
        policy.socket,
        "getaddrinfo",
        lambda *_args, **_kwargs: [(2, 1, 6, "", ("93.184.216.34", 443))],
    )


@pytest.mark.parametrize("operation", ["generate", "stream", "decide", "ordinary", "preparation"])
def test_supplied_client_cannot_enable_redirects(_safe_provider_dns, operation):
    import asyncio
    import httpx

    from src.agent.decision_transport import docking_preparation_response_guard
    from src.agent.openai_compatible_model import OpenAICompatibleModel

    seen = []

    def redirect(request):
        seen.append(request)
        return httpx.Response(307, headers={"location": "https://api.deepseek.com/private"})

    hooks = {"response": [docking_preparation_response_guard]} if operation == "preparation" else None
    kwargs = {"transport": httpx.MockTransport(redirect), "follow_redirects": True}
    if hooks is not None:
        kwargs["event_hooks"] = hooks
    client = httpx.AsyncClient(**kwargs)
    model = OpenAICompatibleModel(
        "test-key", "test-model", "https://api.deepseek.com/v1",
        client=client, enforce_url_policy=True,
    )

    async def run():
        try:
            if operation == "generate":
                return await model.generate("hello")
            if operation == "stream":
                return [part async for part in model.stream_generate("hello")]
            if operation == "decide":
                return await model.decide([{"role": "user", "content": "hello"}], mode="json")
            if operation == "ordinary":
                return await model.propose_ordinary_intent(
                    [{"role": "user", "content": "hello"}], mode="json",
                )
            return await model.propose_docking_preparation(
                [{"role": "user", "content": "hello"}], mode="json",
            )
        finally:
            await client.aclose()

    result = asyncio.run(run())
    assert len(seen) == 1
    if operation == "generate":
        assert "HTTP 307" in result
    elif operation == "stream":
        assert result == ["模型调用失败：HTTP 307"]
    else:
        assert not result.success


def test_modelscope_explicit_provider_options_are_not_duplicated():
    from src.agent.modelscope_model import ModelScopeModel

    model = ModelScopeModel(
        api_key="test-key",
        provider="modelscope",
        enforce_url_policy=True,
    )
    assert model.provider == "modelscope"
    assert model.enforce_url_policy is True


def test_owned_openai_adapter_enforces_policy_by_default():
    from src.agent.openai_compatible_model import OpenAICompatibleModel

    model = OpenAICompatibleModel(
        api_key="test-key",
        model_name="test-model",
        base_url="https://api.deepseek.com/v1",
    )
    assert model.enforce_url_policy is True
    with pytest.raises(ValueError):
        OpenAICompatibleModel(
            api_key="test-key",
            model_name="test-model",
            base_url="https://evil.example/v1",
            enforce_url_policy=True,
        )


def test_supplied_direct_httpx_client_is_pinned_per_client():
    import asyncio
    import httpx

    from src.agent.openai_compatible_model import OpenAICompatibleModel

    client = httpx.AsyncClient()
    model = OpenAICompatibleModel(
        api_key="test-key",
        model_name="test-model",
        base_url="https://api.deepseek.com/v1",
        client=client,
        enforce_url_policy=True,
    )
    assert isinstance(model.client._transport, PinnedClientTransport)
    assert model.client.trust_env is False
    assert model.client._mounts == {}
    asyncio.run(model.close())


def test_owned_llm_client_does_not_inherit_proxy_environment():
    import asyncio

    async def exercise():
        client = create_pinned_async_client(5)
        try:
            assert client.trust_env is False
            assert client._mounts == {}
        finally:
            await client.aclose()

    asyncio.run(exercise())
