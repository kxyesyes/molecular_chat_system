"""Compatibility export for the canonical OpenAI-compatible client."""

# Retain the shared HTTPX module for existing transport-injection callers.
from src.system.openai_compatible_model import OpenAICompatibleModel, httpx

__all__ = ["OpenAICompatibleModel"]
