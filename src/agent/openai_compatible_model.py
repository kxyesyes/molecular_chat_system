#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI-compatible chat-completions model adapter."""

from __future__ import annotations

import json
import logging
from contextvars import ContextVar
from typing import AsyncIterator, Optional

import httpx


logger = logging.getLogger(__name__)


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
    ):
        self.api_key = (api_key or "").strip()
        self.model_name = (model_name or "").strip()
        self.base_url = self._normalize_chat_url(base_url)
        self.provider_name = provider_name
        self.client = client
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
            logger.info("Calling %s model: %s", self.provider_name, self.model_name)
            if self.client is not None:
                response = await self.client.post(
                    self.base_url,
                    headers=self._headers(),
                    json=self._payload(prompt, temperature, max_tokens, stream=False),
                )
            else:
                async with httpx.AsyncClient(timeout=150.0) as client:
                    response = await client.post(
                        self.base_url,
                        headers=self._headers(),
                        json=self._payload(prompt, temperature, max_tokens, stream=False),
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
            if self.client is not None:
                async for content in self._stream_with_client(self.client, prompt, temperature, max_tokens):
                    yield content
            else:
                async with httpx.AsyncClient(timeout=150.0) as client:
                    async for content in self._stream_with_client(client, prompt, temperature, max_tokens):
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
        async with client.stream(
            "POST",
            self.base_url,
            headers=self._headers(),
            json=self._payload(prompt, temperature, max_tokens, stream=True),
            timeout=150.0,
        ) as response:
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
        from src.agent.decision_transport import request_decision

        return await request_decision(
            self, messages, mode=mode, max_tokens=max_tokens,
            timeout_seconds=timeout_seconds,
        )

    async def close(self) -> None:
        close = getattr(self.client, "aclose", None) if self.client is not None else None
        if close:
            await close()
