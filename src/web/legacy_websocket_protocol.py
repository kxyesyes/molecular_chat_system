"""Strict decoding for the legacy browser chat WebSocket protocol."""

from __future__ import annotations

import json
import math
from typing import Any


MAX_FRAME_CHARS = 24 * 1024
_ALLOWED_KEYS = frozenset({
    "type", "message", "enable_rag", "enable_tools", "rag_count",
    "temperature", "mol_count", "reference", "selection", "timestamp",
})


class LegacyFrameError(ValueError):
    """A browser frame failed the protocol contract."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _finite_number(value: Any, *, name: str) -> float:
    if type(value) not in (int, float) or not math.isfinite(value):
        raise LegacyFrameError("invalid_frame", f"{name} must be a finite number")
    return float(value)


def decode_legacy_frame(raw: str) -> dict[str, Any]:
    """Decode and normalize one legacy frame without invoking application logic."""
    if not isinstance(raw, str) or len(raw) > MAX_FRAME_CHARS:
        raise LegacyFrameError("invalid_frame", "WebSocket frame is too large")
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise LegacyFrameError("invalid_frame", "WebSocket frame is not valid JSON") from exc
    if not isinstance(payload, dict):
        raise LegacyFrameError("invalid_frame", "WebSocket frame must be an object")
    unknown = set(payload).difference(_ALLOWED_KEYS)
    if unknown:
        raise LegacyFrameError("invalid_frame", "WebSocket frame contains unknown fields")

    frame_type = payload.get("type", "chat")
    if type(frame_type) is not str or frame_type not in {"chat", "ping"}:
        raise LegacyFrameError("invalid_frame", "Unsupported WebSocket message type")
    if frame_type == "ping":
        timestamp = payload.get("timestamp", 0)
        # Keep the legacy echo behavior, but never echo an attacker-controlled object/string.
        if type(timestamp) not in (int, float) or not math.isfinite(timestamp):
            raise LegacyFrameError("invalid_frame", "ping timestamp must be finite")
        return {"type": "ping", "timestamp": timestamp}

    message = payload.get("message", "")
    if not isinstance(message, str) or len(message) > 8192:
        raise LegacyFrameError("invalid_frame", "message must be a bounded string")

    normalized = {
        "type": "chat",
        "message": message,
        "enable_rag": payload.get("enable_rag", True),
        "enable_tools": payload.get("enable_tools", True),
        "rag_count": payload.get("rag_count", 5),
        "temperature": payload.get("temperature", 0.7),
        "mol_count": payload.get("mol_count"),
        "reference": payload.get("reference"),
        "selection": payload.get("selection"),
    }
    for name in ("enable_rag", "enable_tools"):
        if type(normalized[name]) is not bool:
            raise LegacyFrameError("invalid_frame", f"{name} must be boolean")
    rag_count = normalized["rag_count"]
    if type(rag_count) is not int or not 0 <= rag_count <= 50:
        raise LegacyFrameError("invalid_frame", "rag_count must be an integer from 0 to 50")
    normalized["temperature"] = _finite_number(normalized["temperature"], name="temperature")
    if not 0 <= normalized["temperature"] <= 2:
        raise LegacyFrameError("invalid_frame", "temperature must be between 0 and 2")
    mol_count = normalized["mol_count"]
    if mol_count is not None and (type(mol_count) is not int or not 1 <= mol_count <= 100):
        raise LegacyFrameError("invalid_frame", "mol_count must be an integer from 1 to 100")
    return normalized
