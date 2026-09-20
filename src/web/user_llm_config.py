"""Authoritative per-user UI settings; never inherit credentials from a checkout."""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit, urlunsplit

from src.task_runtime.secure_io import read_file_snapshot
from src.task_runtime.trusted_files import (
    capture_trusted_path_boundary, windows_allow_ace_is_trusted, windows_trusted_sids,
)
from .llm_runtime_config import (
    PROVIDER_ENV_FIELDS, _atomic_replace_text, _exclusive_file_lock,
    _validated_env_value, normalize_llm_config,
)

_ERROR = "本机模型配置不可用；请检查用户配置目录权限或文件格式。"
_DEFAULTS = {
    "openai_compatible": ("https://api.deepseek.com/chat/completions", "deepseek-v4-pro"),
    "custom": ("https://api.deepseek.com/chat/completions", "deepseek-v4-pro"),
    "ollama": ("http://127.0.0.1:11434", "gmm-llama:latest"),
    "modelscope": ("https://api-inference.modelscope.cn/v1/chat/completions", "ZhipuAI/GLM-5.1"),
}


def default_user_llm_config() -> dict:
    url, model = _DEFAULTS["openai_compatible"]
    return dict(provider="openai_compatible", base_url=url, model_name=model, api_key="", stream=True)


def _safe_path(value: Path | str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError(_ERROR)
    for part in (*reversed(path.parents), path):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError(_ERROR)
        if (part / ".git").exists():
            raise ValueError(_ERROR)
    return path


def user_llm_config_path() -> Path:
    explicit = os.environ.get("MEDCHAT_USER_CONFIG_DIR", "").strip()
    if explicit:
        root = Path(explicit)
    elif os.name == "nt":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData/Local") / "MedChat/config"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "medchat"
    return _safe_path(root / "llm.env")


def _private_windows_acl(acl: bytes, trusted: frozenset | set, *, sid_decoder=None) -> bool:
    """Unlike public report files, credential files must also reject public readers."""
    if len(acl) < 8 or int.from_bytes(acl[2:4], "little") < len(acl):
        return False
    offset = 8
    for _ in range(int.from_bytes(acl[4:6], "little")):
        size = int.from_bytes(acl[offset + 2:offset + 4], "little")
        ace = acl[offset:offset + size]
        if size < 8 or len(ace) != size:
            return False
        # Force trustee validation for read-only ALLOW entries using the shared
        # ACE/SID parser. This changes no OS permissions or security descriptor.
        mask = int.from_bytes(ace[4:8], "little") | 2
        checked = ace[:4] + mask.to_bytes(4, "little") + ace[8:]
        if not windows_allow_ace_is_trusted(checked, trusted, sid_decoder=sid_decoder):
            return False
        offset += size
    return offset <= len(acl)


def _check_boundary(path: Path, *, create: bool = False) -> None:
    _safe_path(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not path.parent.exists():
        return
    boundary = capture_trusted_path_boundary(path, require_file=False)
    if os.name == "nt":
        for security in (boundary.parent_security, boundary.file_security):
            if security and not _private_windows_acl(security.dacl, windows_trusted_sids()):
                raise ValueError(_ERROR)
    else:
        if path.parent.stat().st_mode & 0o077:
            raise ValueError(_ERROR)
        if path.exists() and path.stat().st_mode & 0o077:
            raise ValueError(_ERROR)


def _read(path: Path) -> bytes | None:
    _check_boundary(path)
    try:
        path.lstat()
    except FileNotFoundError:
        return None
    content = read_file_snapshot(path, maximum_bytes=65536).content
    _check_boundary(path)
    return content


def user_llm_signature(path: Path | str) -> str | None:
    try:
        content = _read(Path(path))
        return hashlib.sha256(content).hexdigest() if content is not None else None
    except (OSError, ValueError, RuntimeError):
        raise ValueError(_ERROR) from None


def _request_config(raw: dict) -> dict:
    if not isinstance(raw, dict):
        raise ValueError(_ERROR)
    # Validate before strip/normalization, using every separator splitlines reads.
    for field in ("provider", "base_url", "model_name", "model", "api_key"):
        value = _validated_env_value(raw.get(field, ""))
        if any(marker in value for marker in "\v\f\x1c\x1d\x1e\x85\u2028\u2029"):
            raise ValueError(_ERROR)
    provider = str(raw.get("provider") or "openai_compatible").strip().lower().replace("-", "_")
    if provider not in _DEFAULTS or not isinstance(raw.get("stream", True), bool):
        raise ValueError(_ERROR)
    url, model = _DEFAULTS[provider]
    config = normalize_llm_config(dict(raw, provider=provider,
        base_url=raw.get("base_url") or url, model_name=raw.get("model_name") or raw.get("model") or model))
    if not config["base_url"] or not config["model_name"]:
        raise ValueError(_ERROR)
    for value in config.values():
        _validated_env_value(value)
    _endpoint(config)
    if config["provider"] == "ollama":
        config["api_key"] = ""
    return config


def _endpoint(config: dict) -> tuple[str, str]:
    from src.agent.openai_compatible_model import OpenAICompatibleModel
    url = config["base_url"]
    if config["provider"] != "ollama":
        url = OpenAICompatibleModel._normalize_chat_url(url)
    parts = urlsplit(url)
    if parts.scheme not in {"http", "https"} or not parts.hostname or parts.username or parts.password or parts.query or parts.fragment:
        raise ValueError(_ERROR)
    port = parts.port
    host = parts.hostname.lower()
    if ":" in host:
        host = f"[{host}]"
    if port and port != (443 if parts.scheme == "https" else 80):
        host += f":{port}"
    return config["provider"], urlunsplit((parts.scheme, host, parts.path.rstrip("/"), "", ""))


def resolve_user_llm_request(raw: dict, current: dict, *, clear_api_key: bool = False) -> dict:
    """Blank means keep only on the same provider and actual request endpoint."""
    try:
        config = _request_config(raw)
        if clear_api_key:
            config["api_key"] = ""
        elif not config["api_key"] and config["provider"] != "ollama" and _endpoint(config) == _endpoint(current):
            config["api_key"] = current.get("api_key", "")
        return config
    except (OSError, ValueError, RuntimeError, TypeError):
        raise ValueError(_ERROR) from None


def load_user_llm_config(path: Path | str) -> dict:
    try:
        content = _read(Path(path))
        if content is None:
            return default_user_llm_config()
        values = {}
        for line in content.decode("utf-8").splitlines():
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                raise ValueError(_ERROR)
            key, value = line.split("=", 1)
            if key in values:
                raise ValueError(_ERROR)
            values[key] = value
        provider = values["MEDCHAT_LLM_PROVIDER"]
        fields = PROVIDER_ENV_FIELDS[provider]
        required = {"MEDCHAT_LLM_PROVIDER", "MEDCHAT_LLM_STREAM", *fields.values()}
        if set(values) != required or values["MEDCHAT_LLM_STREAM"] not in {"true", "false"}:
            raise ValueError(_ERROR)
        raw = {field: values[name] for field, name in fields.items()}
        if not raw["base_url"] or not raw["model_name"]:
            raise ValueError(_ERROR)
        return _request_config(dict(raw, provider=provider, stream=values["MEDCHAT_LLM_STREAM"] == "true"))
    except (OSError, ValueError, RuntimeError, KeyError, TypeError):
        raise ValueError(_ERROR) from None


def save_user_llm_config(path: Path | str, raw: dict, *, clear_api_key: bool = False) -> tuple[dict, str]:
    """Read/modify/write under the existing cross-process lock, with atomic replace."""
    try:
        path = _safe_path(path)
        _check_boundary(path, create=True)
        with _exclusive_file_lock(path):
            current = load_user_llm_config(path)
            config = resolve_user_llm_request(raw, current, clear_api_key=clear_api_key)
            fields = PROVIDER_ENV_FIELDS[config["provider"]]
            values = {"MEDCHAT_LLM_PROVIDER": config["provider"], "MEDCHAT_LLM_STREAM": "true" if config["stream"] else "false"}
            values.update({name: config[field] for field, name in fields.items()})
            content = "".join(f"{key}={value}\n" for key, value in values.items())
            if len(content.encode("utf-8")) > 65536:
                raise ValueError(_ERROR)
            _check_boundary(path)
            _atomic_replace_text(path, content, private=True)
            return config, hashlib.sha256(content.encode("utf-8")).hexdigest()
    except (OSError, ValueError, RuntimeError, TypeError):
        raise ValueError(_ERROR) from None
