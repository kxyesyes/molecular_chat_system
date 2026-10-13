#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Web模型模块
包含与AI模型交互的类
"""

import asyncio
import inspect
from typing import Any

from .ollama_model import OllamaModel


def _strict_options(method, enabled):
    known = (OllamaModel.generate, OllamaModel.generate_async,
             OllamaModel.stream_generate)
    if (enabled and isinstance(getattr(method, "__self__", None), OllamaModel)
            and getattr(method, "__func__", None) in known):
        return {"strict_errors": True}
    return {}


def stream_for_chat(model, prompt, temperature=0.7, max_tokens=1500):
    method = model.stream_generate
    return method(prompt, temperature=temperature, max_tokens=max_tokens,
                  **_strict_options(method, True))


async def generate_for_chat(
    model: Any,
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 1500,
    *,
    strict_errors=False,
) -> str:
    """Call sync or async model APIs without blocking the event loop."""
    method = getattr(model, "generate_async", None)
    preferred_async = callable(method)
    if not preferred_async:
        method = getattr(model, "generate")
    kwargs = {
        "temperature": temperature,
        "max_tokens": max_tokens,
        **_strict_options(method, strict_errors),
    }
    if preferred_async or inspect.iscoroutinefunction(method):
        result = method(prompt, **kwargs)
    else:
        result = await asyncio.to_thread(method, prompt, **kwargs)

    if inspect.isawaitable(result):
        result = await result
    return str(result)


__all__ = ["OllamaModel", "generate_for_chat", "stream_for_chat"]

