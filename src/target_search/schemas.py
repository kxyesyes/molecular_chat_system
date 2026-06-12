"""Small response-shaping helpers for target-search API payloads."""

from __future__ import annotations

from typing import Any


def split_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    return [item.strip() for item in text.replace(",", ";").split(";") if item.strip()]


def as_bool(value: Any) -> bool:
    return bool(int(value or 0))
