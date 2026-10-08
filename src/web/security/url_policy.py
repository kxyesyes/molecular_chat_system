"""Compatibility exports for the model endpoint policy."""

import socket

from src.system.network_policy import (
    _resolved_addresses,
    resolve_llm_host,
    validate_llm_url,
)

__all__ = ["resolve_llm_host", "validate_llm_url"]
