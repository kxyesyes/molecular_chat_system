"""Runtime LLM connection configuration helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


DEFAULT_PROVIDER = "ollama"


def normalize_llm_config(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    data = raw or {}
    provider = str(data.get("provider") or DEFAULT_PROVIDER).strip().lower()
    provider = provider.replace("-", "_")
    if provider not in {"ollama", "modelscope", "openai_compatible", "custom"}:
        provider = DEFAULT_PROVIDER

    return {
        "provider": provider,
        "base_url": str(data.get("base_url") or "").strip(),
        "model_name": str(data.get("model_name") or data.get("model") or "").strip(),
        "api_key": str(data.get("api_key") or "").strip(),
        "stream": bool(data.get("stream", True)),
    }


def api_key_hint(api_key: str) -> str:
    if not api_key:
        return ""
    if len(api_key) <= 8:
        return "*" * len(api_key)
    return f"{api_key[:4]}...{api_key[-4:]}"


def public_llm_config(raw: Dict[str, Any] | None) -> Dict[str, Any]:
    config = normalize_llm_config(raw)
    api_key = config.pop("api_key", "")
    config["api_key_configured"] = bool(api_key)
    config["api_key_hint"] = api_key_hint(api_key)
    return config


def load_runtime_config(path: str | Path) -> Dict[str, Any]:
    config_path = Path(path)
    if not config_path.exists():
        return {}
    with config_path.open("r", encoding="utf-8") as f:
        return normalize_llm_config(json.load(f))


def save_runtime_config(path: str | Path, raw: Dict[str, Any]) -> Dict[str, Any]:
    config = normalize_llm_config(raw)
    config_path = Path(path)
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with config_path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
    return config
