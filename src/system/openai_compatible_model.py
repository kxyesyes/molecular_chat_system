#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI-compatible chat-completions model adapter."""

from __future__ import annotations

import json
import logging
from contextlib import aclosing
from contextvars import ContextVar
from typing import AsyncIterator, Optional
from urllib.parse import urlsplit

import httpx

from src.system.llm_transport import create_pinned_async_client, pin_supplied_async_client
from src.system.network_policy import validate_llm_url


# Preserve the public logging category used by deployment filters and audits.
logger = logging.getLogger("src.agent.openai_compatible_model")
_HTTPX_ASYNC_CLIENT_TYPE = httpx.AsyncClient
_DEFAULT_DECISION_TRANSPORT = None


def configure_default_decision_transport(transport) -> None:
    """Install an optional Agent protocol adapter from the composition root."""

    global _DEFAULT_DECISION_TRANSPORT
    _DEFAULT_DECISION_TRANSPORT = transport


class OpenAICompatibleModel:
    """Small adapter for OpenAI-compatible /v1/chat/completions APIs."""

    STREAM_CHUNK_CHARS = 32

    def __init__(
        self,
        api_key: str,
        model_name: str,
        base_url: str,
        provider_name: str = "OpenAI-compatible",
        client: Optional[httpx.AsyncClient] = None,
        provider: str = "openai_compatible",
        enforce_url_policy: bool | None = None,
        decision_transport=None,
    ):
        self.api_key = (api_key or "").strip()
        self.model_name = (model_name or "").strip()
        self.base_url = self._normalize_chat_url(base_url)
        self.provider_name = provider_name
        self.provider = provider
        # Agent-specific protocol handling is injected by the composition
        # root.  The shared client must remain usable without importing Agent.
        self._decision_transport = (
            decision_transport
            if decision_transport is not None
            else _DEFAULT_DECISION_TRANSPORT
        )
        self._implicit_url_policy = enforce_url_policy is None
        if enforce_url_policy is None:
            # MockTransport and small fake clients are test seams, not network
            # adapters. Real owned/HTTPX clients remain fail-closed by default.
            transport = getattr(client, "_transport", None)
            is_mock = isinstance(transport, httpx.MockTransport)
            self.enforce_url_policy = client is None or not is_mock and isinstance(
                client, httpx.AsyncClient,
            )
        else:
            self.enforce_url_policy = bool(enforce_url_policy)
        if self.enforce_url_policy:
            try:
                self.base_url = validate_llm_url(
                    self.base_url, provider=self.provider, resolve_host=False
                )
            except ValueError:
                # Existing offline lifecycle probes use the reserved
                # example.invalid domain with an owned client substituted by
                # the test. Explicit production enforcement never takes this
                # compatibility path.
                if not (
                    self._implicit_url_policy and client is None
                    and (urlsplit(self.base_url).hostname or "").lower().endswith(".invalid")
                ):
                    raise
        self.client = (
            pin_supplied_async_client(client)
            if self.enforce_url_policy and isinstance(client, _HTTPX_ASYNC_CLIENT_TYPE)
            else client
        )
        self._response_metadata_var: ContextVar[dict | None] = ContextVar(
            f"openai_response_metadata_{id(self)}",
            default=None,
        )
        self._last_response_metadata: dict = {}

    @property
    def last_response_metadata(self) -> dict:
        """Return metadata for the current async request context only."""
        contextual = self._response_metadata_var.get()
        return contextual if contextual is not None else self._last_response_metadata

    @last_response_metadata.setter
    def last_response_metadata(self, value: dict) -> None:
        copied = dict(value or {})
        self._last_response_metadata = copied
        self._response_metadata_var.set(copied)

    @staticmethod
    def _normalize_chat_url(base_url: str) -> str:
        url = (base_url or "").strip().rstrip("/")
        if not url:
            return ""
        if url.endswith("/chat/completions"):
            return url
        if url.endswith("/v1"):
            return f"{url}/chat/completions"
        return f"{url}/v1/chat/completions"

    def _unavailable_message(self) -> str:
        if not self.api_key:
            return f"{self.provider_name} API Key 未配置，请填写 API Key 或切换到本地 Ollama。"
        if not self.base_url:
            return f"{self.provider_name} Base URL 未配置，请填写接口地址。"
        if not self.model_name:
            return f"{self.provider_name} 模型名称未配置，请填写模型名称。"
        return ""

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }

    def _payload(self, prompt: str, temperature: float, max_tokens: int, stream: bool) -> dict:
        payload = {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if self.model_name.lower().startswith("deepseek-v4-"):
            payload["thinking"] = {"type": "disabled"}
        return payload

    def _validate_request_endpoint(self) -> None:
        if self.enforce_url_policy:
            try:
                self.base_url = validate_llm_url(
                    self.base_url, provider=self.provider, resolve_host=True
                )
            except ValueError:
                if not (
                    self._implicit_url_policy and self.client is None
                    and (urlsplit(self.base_url).hostname or "").lower().endswith(".invalid")
                ):
                    raise

    def _empty_response_message(self) -> str:
        finish_reason = self.last_response_metadata.get("finish_reason")
        suffix = f"（finish_reason={finish_reason}）" if finish_reason else ""
        return f"模型调用失败：未收到有效正文{suffix}"

    async def generate(self, prompt: str, temperature: float = 0.7, max_tokens: int = 1500) -> str:
        self.last_response_metadata = {}
        unavailable = self._unavailable_message()
        if unavailable:
            logger.warning(unavailable)
            return unavailable

        try:
            self._validate_request_endpoint()
            logger.info("Calling %s model: %s", self.provider_name, self.model_name)
            if self.client is not None:
                post_kwargs = {
                    "headers": self._headers(),
                    "json": self._payload(prompt, temperature, max_tokens, stream=False),
                }
                if isinstance(self.client, _HTTPX_ASYNC_CLIENT_TYPE):
                    post_kwargs["follow_redirects"] = False
                response = await self.client.post(
                    self.base_url,
                    **post_kwargs,
                )
            else:
                async with create_pinned_async_client(timeout=150.0, httpx_module=httpx) as client:
                    response = await client.post(
                        self.base_url,
                        headers=self._headers(),
                        json=self._payload(prompt, temperature, max_tokens, stream=False),
                        follow_redirects=False,
                    )
            if response.status_code != 200:
                logger.error(
                    "%s API error: HTTP %s",
                    self.provider_name,
                    response.status_code,
                )
                return f"模型调用失败：HTTP {response.status_code}"

            result = response.json()
            choice = result.get("choices", [{}])[0]
            message = choice.get("message", {})
            content = message.get("content") or ""
            reasoning = message.get("reasoning_content") or ""
            self.last_response_metadata = {
                "model": result.get("model") or self.model_name,
                "finish_reason": choice.get("finish_reason"),
                "usage": result.get("usage") or {},
                "reasoning_chars": len(reasoning),
                "content_chars": len(content),
            }
            return content if content.strip() else self._empty_response_message()
        except Exception as exc:
            logger.error(
                "%s API call failed (%s)",
                self.provider_name,
                type(exc).__name__,
            )
            return "模型调用失败：上游服务不可用"

    async def stream_generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1500,
    ) -> AsyncIterator[str]:
        self.last_response_metadata = {}
        unavailable = self._unavailable_message()
        if unavailable:
            logger.warning(unavailable)
            yield unavailable
            return

        try:
            self._validate_request_endpoint()
            if self.client is not None:
                async with aclosing(self._stream_with_client(self.client, prompt, temperature, max_tokens)) as stream:
                    async for content in stream:
                        yield content
            else:
                async with create_pinned_async_client(timeout=150.0, httpx_module=httpx) as client:
                    async with aclosing(self._stream_with_client(client, prompt, temperature, max_tokens)) as stream:
                        async for content in stream:
                            yield content
        except Exception as exc:
            logger.error(
                "%s stream call failed (%s)",
                self.provider_name,
                type(exc).__name__,
            )
            yield "模型调用失败：上游服务不可用"

    async def _stream_with_client(
        self,
        client,
        prompt: str,
        temperature: float,
        max_tokens: int,
    ) -> AsyncIterator[str]:
        stream_kwargs = {
            "headers": self._headers(),
            "json": self._payload(prompt, temperature, max_tokens, stream=True),
            "timeout": 150.0,
        }
        if isinstance(client, _HTTPX_ASYNC_CLIENT_TYPE):
            stream_kwargs["follow_redirects"] = False
        async with client.stream("POST", self.base_url, **stream_kwargs) as response:
            if response.status_code != 200:
                logger.error(
                    "%s stream error: HTTP %s",
                    self.provider_name,
                    response.status_code,
                )
                yield f"模型调用失败：HTTP {response.status_code}"
                return

            buffer = ""
            pending_content = ""
            content_chars = 0
            meaningful_content = False
            reasoning_chars = 0
            finish_reason = None
            usage = {}
            async for chunk in response.aiter_bytes():
                buffer += chunk.decode("utf-8", errors="ignore")
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    if line.startswith("data: "):
                        line = line[6:].strip()
                    if line == "[DONE]":
                        continue
                    try:
                        data = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    choices = data.get("choices") or [{}]
                    choice = choices[0]
                    if choice.get("finish_reason") is not None:
                        finish_reason = choice.get("finish_reason")
                    if isinstance(data.get("usage"), dict):
                        usage = data["usage"]
                    content = choice.get("delta", {}).get("content")
                    if content is None:
                        content = choice.get("message", {}).get("content")
                    reasoning = choice.get("delta", {}).get("reasoning_content")
                    if reasoning is None:
                        reasoning = choice.get("message", {}).get("reasoning_content")
                    if reasoning:
                        reasoning_chars += len(reasoning)
                    if content:
                        content_chars += len(content)
                        meaningful_content = meaningful_content or bool(content.strip())
                        pending_content += content
                        if (
                            len(pending_content) >= self.STREAM_CHUNK_CHARS
                            or "\n" in pending_content
                        ):
                            yield pending_content
                            pending_content = ""

            if pending_content:
                yield pending_content
            self.last_response_metadata = {
                "model": self.model_name,
                "finish_reason": finish_reason,
                "usage": usage,
                "reasoning_chars": reasoning_chars,
                "content_chars": content_chars,
            }
            if not meaningful_content:
                yield self._empty_response_message()

    async def decide(
        self, messages, *, mode="native", max_tokens=1500, timeout_seconds=60.0,
    ):
        """Return a validated proposal, never execute a tool or a workflow."""
        transport = self._require_decision_transport("request_decision")
        return await transport.request_decision(
            self, messages, mode=mode, max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

    async def propose_ordinary_intent(
        self, messages, *, mode="native", max_tokens=256, timeout_seconds=30.0, _journal=None,
    ):
        """Return one strict intent proposal using the same bounded transport."""
        transport = self._require_decision_transport("request_ordinary_intent")
        return await transport.request_ordinary_intent(
            self, messages, mode=mode, max_tokens=max_tokens,
            timeout_seconds=timeout_seconds, _journal=_journal,
        )

    async def propose_docking_preparation(
        self, messages, *, mode="native", max_tokens=256, timeout_seconds=30.0, _journal=None,
    ):
        """Return a preparation proposal, never admission, consent or execution."""
        transport = self._require_decision_transport("request_docking_preparation")
        return await transport.request_docking_preparation(
            self, messages, mode=mode, max_tokens=max_tokens,
            timeout_seconds=timeout_seconds, _journal=_journal,
        )

    def _require_decision_transport(self, method):
        transport = self._decision_transport
        if transport is None:
            raise RuntimeError("Agent decision transport is not configured")
        if not callable(getattr(transport, method, None)):
            raise RuntimeError("Agent decision transport is incomplete")
        return transport

    async def close(self) -> None:
        close = getattr(self.client, "aclose", None) if self.client is not None else None
        if close:
            await close()
