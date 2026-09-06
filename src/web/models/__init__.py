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


async def generate_for_chat(
    model: Any,
    prompt: str,
    temperature: float = 0.7,
    max_tokens: int = 1500,
) -> str:
    """Call sync or async model APIs without blocking the event loop."""
    method = getattr(model, "generate_async", None)
    if callable(method):
        result = method(
            prompt,
            temperature=temperature,
            max_tokens=max_tokens,
        )
    else:
        method = getattr(model, "generate")
        if inspect.iscoroutinefunction(method):
            result = method(
                prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )
        else:
            result = await asyncio.to_thread(
                method,
                prompt,
                temperature=temperature,
                max_tokens=max_tokens,
            )

    if inspect.isawaitable(result):
        result = await result
    return str(result)


__all__ = ["OllamaModel", "generate_for_chat"]

