"""Runtime LLM connection configuration helpers."""

from __future__ import annotations

import json
import hashlib
import os
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator


DEFAULT_PROVIDER = "ollama"
PERSISTED_FIELDS = ("provider", "base_url", "model_name", "stream")
ENV_KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
PROVIDER_ENV_FIELDS = {
    "openai_compatible": {
        "api_key": "OPENAI_COMPATIBLE_API_KEY",
        "base_url": "OPENAI_COMPATIBLE_BASE_URL",
        "model_name": "OPENAI_COMPATIBLE_MODEL",
    },
    "custom": {
        "api_key": "OPENAI_COMPATIBLE_API_KEY",
        "base_url": "OPENAI_COMPATIBLE_BASE_URL",
        "model_name": "OPENAI_COMPATIBLE_MODEL",
    },
    "modelscope": {
        "api_key": "MODELSCOPE_API_KEY",
        "base_url": "MODELSCOPE_BASE_URL",
        "model_name": "MODELSCOPE_MODEL",
    },
    "ollama": {
        "base_url": "OLLAMA_BASE_URL",
        "model_name": "OLLAMA_MODEL",
    },
}
_LOCKS_GUARD = threading.Lock()
_PATH_LOCKS: Dict[str, threading.Lock] = {}


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
        raw = json.load(f)
    config = normalize_llm_config(raw)
    config["api_key"] = ""
    if isinstance(raw, dict) and "api_key" in raw:
        persisted = {key: config[key] for key in PERSISTED_FIELDS}
        _atomic_write_text(
            config_path,
            json.dumps(persisted, ensure_ascii=False, indent=2) + "\n",
        )
    return config


def save_runtime_config(path: str | Path, raw: Dict[str, Any]) -> Dict[str, Any]:
    config = normalize_llm_config(raw)
    persisted = {key: config[key] for key in PERSISTED_FIELDS}
    config_path = Path(path)
    _atomic_write_text(
        config_path,
        json.dumps(persisted, ensure_ascii=False, indent=2) + "\n",
    )
    return config


def _thread_lock_for(path: Path) -> threading.Lock:
    key = str(path.resolve())
    with _LOCKS_GUARD:
        return _PATH_LOCKS.setdefault(key, threading.Lock())


def _lock_path_for(path: str | Path) -> Path:
    target = Path(path).resolve()
    configured = str(os.environ.get("MEDCHAT_LLM_LOCK_DIR") or "").strip()
    if configured:
        lock_dir = Path(configured).expanduser()
    elif os.name == "nt":
        lock_dir = Path(
            os.environ.get("LOCALAPPDATA")
            or (Path.home() / "AppData" / "Local")
        ) / "MedChat" / "locks"
    else:  # pragma: no cover - exercised on Linux deployments
        runtime_dir = str(os.environ.get("XDG_RUNTIME_DIR") or "").strip()
        lock_dir = (
            Path(runtime_dir) / "medchat"
            if runtime_dir
            else Path.home() / ".cache" / "medchat" / "locks"
        )
    if lock_dir.is_symlink():
        raise RuntimeError("LLM lock directory must not be a symbolic link")
    lock_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":  # pragma: no cover - exercised on Linux deployments
        os.chmod(lock_dir, 0o700)
    digest = hashlib.sha256(str(target).encode("utf-8")).hexdigest()[:24]
    return lock_dir / f"llm-{digest}.lock"


@contextmanager
def _exclusive_file_lock(path: str | Path) -> Iterator[None]:
    """Serialize writers in this process and across local worker processes."""
    target = Path(path).resolve()
    lock_path = _lock_path_for(target)
    thread_lock = _thread_lock_for(target)
    with thread_lock:
        if lock_path.is_symlink():
            raise RuntimeError("LLM lock file must not be a symbolic link")
        flags = os.O_CREAT | os.O_RDWR
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(lock_path, flags, 0o600)
        with os.fdopen(descriptor, "r+b") as lock_stream:
            lock_stream.seek(0, os.SEEK_END)
            if lock_stream.tell() == 0:
                lock_stream.write(b"\0")
                lock_stream.flush()
            lock_stream.seek(0)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(lock_stream.fileno(), msvcrt.LK_LOCK, 1)
            else:  # pragma: no cover - exercised on Linux deployments
                import fcntl

                fcntl.flock(lock_stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                lock_stream.seek(0)
                if os.name == "nt":
                    msvcrt.locking(lock_stream.fileno(), msvcrt.LK_UNLCK, 1)
                else:  # pragma: no cover - exercised on Linux deployments
                    fcntl.flock(lock_stream.fileno(), fcntl.LOCK_UN)


def _atomic_replace_text(path: Path, content: str, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        if private and os.name != "nt":
            os.chmod(temporary_path, 0o600)
        os.replace(temporary_path, path)
    except Exception:
        temporary_path.unlink(missing_ok=True)
        raise


def _atomic_write_text(path: str | Path, content: str) -> None:
    target = Path(path)
    if target.is_symlink():
        raise ValueError(f"Refusing to replace symbolic-link config file: {target}")
    with _exclusive_file_lock(target):
        if target.is_symlink():
            raise ValueError(f"Refusing to replace symbolic-link config file: {target}")
        _atomic_replace_text(target, content)


def _env_line_key(line: str) -> str:
    stripped = line.strip()
    if not stripped or stripped.startswith("#") or "=" not in stripped:
        return ""
    key = stripped.split("=", 1)[0].strip()
    return key if ENV_KEY_PATTERN.fullmatch(key) else ""


def _validated_env_value(value: Any) -> str:
    text = str(value or "")
    if any(marker in text for marker in ("\r", "\n", "\x00")):
        raise ValueError("Environment values must be single-line text")
    return text


def _read_env_values(path: str | Path) -> Dict[str, str]:
    env_path = Path(path)
    if not env_path.exists():
        return {}
    values: Dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key = _env_line_key(line)
        if not key:
            continue
        value = line.strip().split("=", 1)[1].strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def load_llm_env_config(path: str | Path, provider: str) -> Dict[str, Any]:
    """Read one provider's saved values directly from a local env file."""
    normalized_provider = normalize_llm_config({"provider": provider})["provider"]
    fields = PROVIDER_ENV_FIELDS[normalized_provider]
    values = _read_env_values(path)
    stream_text = values.get("MEDCHAT_LLM_STREAM", "true").strip().lower()
    return normalize_llm_config(
        {
            "provider": normalized_provider,
            "base_url": values.get(fields["base_url"], ""),
            "model_name": values.get(fields["model_name"], ""),
            "api_key": values.get(fields.get("api_key", ""), ""),
            "stream": stream_text not in {"0", "false", "no", "off"},
        }
    )


def load_active_llm_env_config(path: str | Path) -> Dict[str, Any]:
    """Load the provider selected by the UI-managed environment file."""
    values = _read_env_values(path)
    provider = str(values.get("MEDCHAT_LLM_PROVIDER") or "").strip()
    if not provider:
        return {}
    return load_llm_env_config(path, provider)


def sync_llm_process_environment(
    raw: Dict[str, Any],
    *,
    clear_api_key: bool = False,
    clear_api_key_providers: Iterable[str] = (),
) -> Dict[str, Any]:
    """Keep managed process variables aligned with the just-saved local config."""
    config = normalize_llm_config(raw)
    for provider in clear_api_key_providers:
        normalized_provider = normalize_llm_config({"provider": provider})["provider"]
        clear_name = PROVIDER_ENV_FIELDS[normalized_provider].get("api_key")
        if clear_name:
            os.environ.pop(clear_name, None)

    fields = PROVIDER_ENV_FIELDS[config["provider"]]
    os.environ["MEDCHAT_LLM_PROVIDER"] = config["provider"]
    os.environ["MEDCHAT_LLM_STREAM"] = "true" if config["stream"] else "false"
    os.environ[fields["base_url"]] = config["base_url"]
    os.environ[fields["model_name"]] = config["model_name"]
    api_key_name = fields.get("api_key")
    if api_key_name and clear_api_key:
        os.environ.pop(api_key_name, None)
    elif api_key_name and config["api_key"]:
        os.environ[api_key_name] = config["api_key"]
    return config


def _atomic_update_env_file(
    path: str | Path,
    updates: Dict[str, str | None],
) -> str:
    env_path = Path(path)
    if env_path.is_symlink():
        raise ValueError(f"Refusing to replace symbolic-link env file: {env_path}")
    validated_updates = {
        key: None if value is None else _validated_env_value(value)
        for key, value in updates.items()
    }
    if not all(ENV_KEY_PATTERN.fullmatch(key) for key in validated_updates):
        raise ValueError("Invalid environment variable name")

    with _exclusive_file_lock(env_path):
        if env_path.is_symlink():
            raise ValueError(f"Refusing to replace symbolic-link env file: {env_path}")
        existing_lines = (
            env_path.read_text(encoding="utf-8").splitlines()
            if env_path.exists()
            else []
        )
        rendered = []
        seen = set()
        for line in existing_lines:
            key = _env_line_key(line)
            if key not in validated_updates:
                rendered.append(line)
                continue
            if key in seen:
                continue
            seen.add(key)
            replacement = validated_updates[key]
            if replacement is not None:
                rendered.append(f"{key}={replacement}")

        for key, replacement in validated_updates.items():
            if key not in seen and replacement is not None:
                rendered.append(f"{key}={replacement}")

        content = "\n".join(rendered)
        if content:
            content += "\n"
        _atomic_replace_text(env_path, content, private=True)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


def save_llm_env_config_snapshot(
    path: str | Path,
    raw: Dict[str, Any],
    *,
    clear_api_key: bool = False,
    clear_api_key_providers: Iterable[str] = (),
) -> tuple[Dict[str, Any], str]:
    """Persist config and return the fingerprint of the exact written snapshot."""
    config = normalize_llm_config(raw)
    fields = PROVIDER_ENV_FIELDS[config["provider"]]
    updates: Dict[str, str | None] = {
        "MEDCHAT_LLM_PROVIDER": config["provider"],
        "MEDCHAT_LLM_STREAM": "true" if config["stream"] else "false",
        fields["base_url"]: config["base_url"],
        fields["model_name"]: config["model_name"],
    }
    for provider in clear_api_key_providers:
        normalized_provider = normalize_llm_config({"provider": provider})["provider"]
        clear_name = PROVIDER_ENV_FIELDS[normalized_provider].get("api_key")
        if clear_name:
            updates[clear_name] = None
    api_key_name = fields.get("api_key")
    if api_key_name and clear_api_key:
        updates[api_key_name] = None
    elif api_key_name and config["api_key"]:
        updates[api_key_name] = config["api_key"]

    signature = _atomic_update_env_file(path, updates)
    return config, signature


def save_llm_env_config(
    path: str | Path,
    raw: Dict[str, Any],
    *,
    clear_api_key: bool = False,
    clear_api_key_providers: Iterable[str] = (),
) -> Dict[str, Any]:
    """Persist LLM connection values to a local plaintext environment file."""
    config, _signature = save_llm_env_config_snapshot(
        path,
        raw,
        clear_api_key=clear_api_key,
        clear_api_key_providers=clear_api_key_providers,
    )
    return config
