"""Compatibility export for the canonical OpenAI-compatible client."""

# Retain the shared HTTPX module for existing transport-injection callers.
from src.agent import decision_transport as _decision_transport
from src.system.openai_compatible_model import (
    OpenAICompatibleModel,
    configure_default_decision_transport,
    httpx,
)

# Existing Agent callers construct the canonical class through this module.
# Configure its protocol adapter without making the shared system client import
# the Agent package in the opposite direction.
configure_default_decision_transport(_decision_transport)

__all__ = ["OpenAICompatibleModel"]
