#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""OpenAI-compatible chat-completions model adapter."""

from __future__ import annotations

import json
import logging
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
        return {
            "model": self.model_name,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }

    async def generate(self, prompt: str, temperature: float = 0.7, max_tokens: int = 1500) -> str:
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
                    "%s API error: %s - %s",
                    self.provider_name,
                    response.status_code,
                    response.text,
                )
                return f"模型调用失败：HTTP {response.status_code}"

            result = response.json()
            return result.get("choices", [{}])[0].get("message", {}).get("content", "")
        except Exception as exc:
            logger.error("%s API call failed: %s", self.provider_name, exc, exc_info=True)
            return f"模型调用失败：{exc}"

    async def stream_generate(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1500,
    ) -> AsyncIterator[str]:
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
            logger.error("%s stream call failed: %s", self.provider_name, exc, exc_info=True)
            yield f"模型调用失败：{exc}"

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
                error_content = await response.aread()
                detail = error_content.decode("utf-8", errors="ignore")
                logger.error("%s stream error: %s - %s", self.provider_name, response.status_code, detail)
                yield f"模型调用失败：HTTP {response.status_code}"
                return

            buffer = ""
            pending_content = ""
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
                    choice = data.get("choices", [{}])[0]
                    content = choice.get("delta", {}).get("content")
                    if content is None:
                        content = choice.get("message", {}).get("content")
                    if content:
                        pending_content += content
                        if (
                            len(pending_content) >= self.STREAM_CHUNK_CHARS
                            or "\n" in pending_content
                        ):
                            yield pending_content
                            pending_content = ""

            if pending_content:
                yield pending_content

    async def close(self) -> None:
        close = getattr(self.client, "aclose", None) if self.client is not None else None
        if close:
            await close()
