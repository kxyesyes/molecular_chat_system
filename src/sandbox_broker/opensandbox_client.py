"""Narrow, fail-closed boundary around the optional OpenSandbox SDK."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import timedelta
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any, Protocol

from .config import BrokerConfig


_FIXED_COMMAND = (
    "python /opt/medchat/run_docking.py "
    "--request /workspace/input/request.json --output /workspace/output"
)
_FIXED_ENTRYPOINT = ("sleep", "infinity")
_INPUT_ROOT_PARTS = ("workspace", "input")
_OUTPUT_ROOT_PARTS = ("workspace", "output")
_REQUEST_MAX_BYTES = 64 * 1024
_READ_MAX_BYTES = 16 * 1024 * 1024
_COMMAND_OUTPUT_MAX_BYTES = 16 * 1024
_COMMAND_INSPECTION_MAX_BYTES = 64 * 1024
_LIST_MAX_FILES = 256
_PATH_MAX_CHARACTERS = 512
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PATH_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_LIST_PATTERNS = frozenset({"*", "*.json", "*.pdbqt"})
_ANSI_OSC_PATTERN = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_CSI_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:api[_ -]?key|authorization|token|password|secret)\b"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_WINDOWS_HOST_PATH_PATTERN = re.compile(r"(?i)\b[A-Z]:[\\/][^\s]*")
_POSIX_HOST_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])/(?:home|Users|tmp|var/folders)/[^\s]*"
)


class SandboxClientError(Exception):
    """Base class for sanitized OpenSandbox adapter failures."""


class SandboxInputError(SandboxClientError):
    """The caller supplied a value outside the narrow adapter contract."""


class SandboxDependencyUnavailableError(SandboxClientError):
    """The pinned OpenSandbox SDK cannot be loaded."""


class SandboxCreateError(SandboxClientError):
    """Sandbox provisioning failed."""


class SandboxUploadError(SandboxClientError):
    """A fixed sandbox input could not be uploaded."""


class SandboxReadError(SandboxClientError):
    """A bounded sandbox output could not be read."""


class SandboxListError(SandboxClientError):
    """Sandbox output discovery failed or exceeded its bound."""


class SandboxRunError(SandboxClientError):
    """The fixed docking command could not be executed."""


class SandboxDestroyError(SandboxClientError):
    """Sandbox cleanup failed after one best-effort invocation."""


class SandboxProtocolError(SandboxClientError):
    """The SDK or server returned data outside the expected contract."""


@dataclass(frozen=True)
class SandboxHandle:
    sandbox_id: str
    raw: Any = field(repr=False)

    def __post_init__(self) -> None:
        _validate_id(self.sandbox_id, server_value=False)
        if self.raw is None:
            raise SandboxInputError("the sandbox handle is outside the fixed contract")


@dataclass(frozen=True)
class SandboxCommandResult:
    exit_code: int
    stdout: str
    stderr: str

    def __post_init__(self) -> None:
        if type(self.exit_code) is not int or not -(2**31) <= self.exit_code < 2**31:
            raise SandboxProtocolError("the command service returned an invalid result")
        if type(self.stdout) is not str or type(self.stderr) is not str:
            raise SandboxProtocolError("the command service returned invalid logs")
        if (
            _utf8_size(self.stdout, SandboxProtocolError)
            > _COMMAND_OUTPUT_MAX_BYTES
            or _utf8_size(self.stderr, SandboxProtocolError)
            > _COMMAND_OUTPUT_MAX_BYTES
        ):
            raise SandboxProtocolError("the command service returned oversized logs")


class SandboxClient(Protocol):
    async def create(self, job_id: str) -> SandboxHandle:
        raise NotImplementedError

    async def upload_text(
        self,
        handle: SandboxHandle,
        path: str,
        data: str,
    ) -> None:
        raise NotImplementedError

    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        raise NotImplementedError

    async def list_files(
        self,
        handle: SandboxHandle,
        path: str,
        pattern: str,
    ) -> list[str]:
        raise NotImplementedError

    async def run(self, handle: SandboxHandle) -> SandboxCommandResult:
        raise NotImplementedError

    async def destroy(self, handle: SandboxHandle) -> None:
        raise NotImplementedError

    async def destroy_by_id(self, sandbox_id: str) -> None:
        raise NotImplementedError


class _SandboxFactory(Protocol):
    async def create(
        self,
        *,
        image: str,
        timeout_seconds: int,
        resource: dict[str, str],
        network_default: str,
        entrypoint: list[str],
        metadata: dict[str, str],
    ) -> Any:
        raise NotImplementedError

    async def upload_text(self, raw: Any, path: str, data: str) -> None:
        raise NotImplementedError

    async def read_text(self, raw: Any, path: str, max_bytes: int) -> object:
        raise NotImplementedError

    async def list_files(self, raw: Any, path: str, pattern: str) -> object:
        raise NotImplementedError

    async def run(self, raw: Any, command: str) -> object:
        raise NotImplementedError

    async def destroy(self, raw: Any) -> None:
        raise NotImplementedError

    async def destroy_by_id(self, sandbox_id: str) -> None:
        raise NotImplementedError


@dataclass(frozen=True)
class _OfficialSDK:
    ConnectionConfig: Any
    Sandbox: Any
    SandboxManager: Any
    WriteEntry: Any
    SearchEntry: Any
    NetworkPolicy: Any


def _load_official_sdk() -> _OfficialSDK:
    """Load OpenSandbox 0.1.15 only when a production SDK call is made."""

    try:
        from opensandbox import Sandbox, SandboxManager
        from opensandbox.config import ConnectionConfig
        from opensandbox.models import NetworkPolicy, SearchEntry, WriteEntry
    except (ImportError, ModuleNotFoundError):
        raise SandboxDependencyUnavailableError(
            "the pinned OpenSandbox SDK is unavailable"
        ) from None
    return _OfficialSDK(
        ConnectionConfig=ConnectionConfig,
        Sandbox=Sandbox,
        SandboxManager=SandboxManager,
        WriteEntry=WriteEntry,
        SearchEntry=SearchEntry,
        NetworkPolicy=NetworkPolicy,
    )


class _OfficialSandboxFactory:
    """Translate fixed primitive policy into OpenSandbox 0.1.15 objects."""

    def __init__(self, config: BrokerConfig) -> None:
        self._config = config

    def _sdk(self) -> _OfficialSDK:
        try:
            return _load_official_sdk()
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable"
            ) from None
        except Exception:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable"
            ) from None

    def _connection(self, sdk: _OfficialSDK) -> Any:
        return sdk.ConnectionConfig(
            api_key=self._config.opensandbox_api_key,
            domain=self._config.opensandbox_domain,
        )

    async def create(
        self,
        *,
        image: str,
        timeout_seconds: int,
        resource: dict[str, str],
        network_default: str,
        entrypoint: list[str],
        metadata: dict[str, str],
    ) -> Any:
        sdk = self._sdk()
        network_policy = sdk.NetworkPolicy(
            defaultAction=network_default,
            egress=[],
        )
        return await sdk.Sandbox.create(
            image,
            timeout=timedelta(seconds=timeout_seconds),
            resource=resource,
            network_policy=network_policy,
            entrypoint=entrypoint,
            metadata=metadata,
            connection_config=self._connection(sdk),
        )

    async def upload_text(self, raw: Any, path: str, data: str) -> None:
        sdk = self._sdk()
        entry = sdk.WriteEntry(path=path, data=data, mode=600, encoding="utf-8")
        await raw.files.write_files([entry])

    async def read_text(self, raw: Any, path: str, max_bytes: int) -> object:
        self._sdk()
        return await raw.files.read_file(
            path,
            encoding="utf-8",
            range_header=f"bytes=0-{max_bytes}",
        )

    async def list_files(self, raw: Any, path: str, pattern: str) -> object:
        sdk = self._sdk()
        return await raw.files.search(sdk.SearchEntry(path=path, pattern=pattern))

    async def run(self, raw: Any, command: str) -> object:
        self._sdk()
        return await raw.commands.run(command)

    async def destroy(self, raw: Any) -> None:
        await raw.destroy()

    async def destroy_by_id(self, sandbox_id: str) -> None:
        sdk = self._sdk()
        manager = await sdk.SandboxManager.create(
            connection_config=self._connection(sdk)
        )
        try:
            await manager.kill_sandbox(sandbox_id)
        finally:
            await manager.close()


def _validate_id(value: object, *, server_value: bool) -> str:
    if type(value) is not str or _ID_PATTERN.fullmatch(value) is None:
        if server_value:
            raise SandboxProtocolError("the sandbox service returned an invalid identifier")
        raise SandboxInputError("the identifier is outside the fixed contract")
    return value


def _path_parts(path: object) -> tuple[str, ...]:
    if type(path) is not str:
        raise SandboxInputError("the sandbox path is outside the fixed contract")
    if (
        not path.startswith("/")
        or len(path) > _PATH_MAX_CHARACTERS
        or path.endswith("/")
        or "\\" in path
        or "\x00" in path
        or "//" in path
    ):
        raise SandboxInputError("the sandbox path is outside the fixed contract")
    components = tuple(path.split("/")[1:])
    if not components or any(
        component in {"", ".", ".."}
        or _PATH_COMPONENT_PATTERN.fullmatch(component) is None
        for component in components
    ):
        raise SandboxInputError("the sandbox path is outside the fixed contract")
    if str(PurePosixPath(path)) != path:
        raise SandboxInputError("the sandbox path is outside the fixed contract")
    return components


def _validate_output_path(path: object, *, allow_root: bool) -> tuple[str, ...]:
    parts = _path_parts(path)
    if parts[:2] != _OUTPUT_ROOT_PARTS or (not allow_root and len(parts) == 2):
        raise SandboxInputError("the sandbox path is outside the output root")
    return parts


def _utf8_size(value: str, error: type[SandboxClientError]) -> int:
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeError:
        raise error("text is not valid UTF-8") from None


def _truncate_utf8(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8", errors="strict")
    if len(encoded) <= max_bytes:
        return value
    return encoded[:max_bytes].decode("utf-8", errors="ignore")


def _remove_control_characters(value: str) -> str:
    return "".join(
        character
        for character in value
        if character in "\n\r\t"
        or not unicodedata.category(character).startswith("C")
    )


def _sanitize_output(value: str, config: BrokerConfig) -> str:
    sanitized = _ANSI_OSC_PATTERN.sub("", value)
    sanitized = _ANSI_CSI_PATTERN.sub("", sanitized)
    sensitive_values = {
        config.opensandbox_api_key,
        str(config.state_root),
        str(config.socket_path),
        str(config.state_root).replace("\\", "/"),
        str(config.socket_path).replace("\\", "/"),
    }
    for sensitive in sorted(sensitive_values, key=len, reverse=True):
        if sensitive:
            sanitized = sanitized.replace(sensitive, "[REDACTED]")
    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _BEARER_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _OPENAI_KEY_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _WINDOWS_HOST_PATH_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _POSIX_HOST_PATH_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _remove_control_characters(sanitized)
    return _truncate_utf8(sanitized, _COMMAND_OUTPUT_MAX_BYTES)


def _bounded_log_text(messages: object) -> str:
    if type(messages) is not list:
        raise SandboxProtocolError("the command service returned invalid logs")
    chunks: list[str] = []
    used = 0
    for message in messages:
        text = getattr(message, "text", None)
        if type(text) is not str:
            raise SandboxProtocolError("the command service returned invalid logs")
        try:
            encoded = text.encode("utf-8", errors="strict")
        except UnicodeError:
            raise SandboxProtocolError(
                "the command service returned invalid UTF-8 logs"
            ) from None
        remaining = _COMMAND_INSPECTION_MAX_BYTES - used
        if remaining <= 0:
            break
        if len(encoded) > remaining:
            chunks.append(encoded[:remaining].decode("utf-8", errors="ignore"))
            break
        chunks.append(text)
        used += len(encoded)
    return "".join(chunks)


class OpenSandboxClient:
    """Expose only the broker's fixed OpenSandbox operations."""

    def __init__(
        self,
        config: BrokerConfig,
        *,
        sandbox_factory: _SandboxFactory | None = None,
    ) -> None:
        if type(config) is not BrokerConfig:
            raise SandboxInputError("config must be a validated BrokerConfig")
        self._config = config
        self._factory: _SandboxFactory = (
            _OfficialSandboxFactory(config)
            if sandbox_factory is None
            else sandbox_factory
        )
        self._destroy_started: set[str] = set()

    def _handle(self, handle: object) -> SandboxHandle:
        if type(handle) is not SandboxHandle or handle.raw is None:
            raise SandboxInputError("the sandbox handle is outside the fixed contract")
        _validate_id(handle.sandbox_id, server_value=False)
        return handle

    async def create(self, job_id: str) -> SandboxHandle:
        validated_job_id = _validate_id(job_id, server_value=False)
        try:
            raw = await self._factory.create(
                image=(
                    f"{self._config.image_uri}@sha256:"
                    f"{self._config.image_digest}"
                ),
                timeout_seconds=self._config.sandbox_timeout_seconds,
                resource={"cpu": self._config.cpu, "memory": self._config.memory},
                network_default="deny",
                entrypoint=list(_FIXED_ENTRYPOINT),
                metadata={
                    "medchat.operation": "molecular_docking",
                    "medchat.job_id": validated_job_id,
                },
            )
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable"
            ) from None
        except Exception:
            raise SandboxCreateError("sandbox provisioning failed") from None

        raw_id = getattr(raw, "id", None)
        try:
            sandbox_id = _validate_id(raw_id, server_value=True)
        except SandboxProtocolError:
            if raw is not None:
                try:
                    await self._factory.destroy(raw)
                except Exception:
                    raise SandboxProtocolError(
                        "the sandbox service returned an invalid identifier"
                    ) from None
            raise
        return SandboxHandle(sandbox_id=sandbox_id, raw=raw)

    async def upload_text(
        self,
        handle: SandboxHandle,
        path: str,
        data: str,
    ) -> None:
        validated_handle = self._handle(handle)
        parts = _path_parts(path)
        if parts[:2] != _INPUT_ROOT_PARTS or len(parts) != 3:
            raise SandboxInputError("only fixed broker inputs may be uploaded")
        limits = {
            "receptor.pdb": self._config.receptor_max_bytes,
            "ligand.sdf": self._config.ligand_max_bytes,
            "request.json": _REQUEST_MAX_BYTES,
        }
        limit = limits.get(parts[-1])
        if limit is None or type(data) is not str or not data:
            raise SandboxInputError("only fixed non-empty text inputs may be uploaded")
        if _utf8_size(data, SandboxInputError) > limit:
            raise SandboxInputError("the fixed input exceeds its byte limit")
        try:
            await self._factory.upload_text(validated_handle.raw, path, data)
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable"
            ) from None
        except Exception:
            raise SandboxUploadError("sandbox input upload failed") from None

    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        validated_handle = self._handle(handle)
        _validate_output_path(path, allow_root=False)
        try:
            result = await self._factory.read_text(
                validated_handle.raw,
                path,
                _READ_MAX_BYTES,
            )
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable"
            ) from None
        except Exception:
            raise SandboxReadError("sandbox output read failed") from None
        if type(result) is not str:
            raise SandboxProtocolError("the sandbox service returned non-text output")
        if _utf8_size(result, SandboxProtocolError) > _READ_MAX_BYTES:
            raise SandboxReadError("sandbox output exceeded its byte limit")
        return result

    async def list_files(
        self,
        handle: SandboxHandle,
        path: str,
        pattern: str,
    ) -> list[str]:
        validated_handle = self._handle(handle)
        requested_parts = _validate_output_path(path, allow_root=True)
        if type(pattern) is not str or pattern not in _ALLOWED_LIST_PATTERNS:
            raise SandboxInputError("the search pattern is outside the fixed contract")
        try:
            result = await self._factory.list_files(
                validated_handle.raw,
                path,
                pattern,
            )
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable"
            ) from None
        except Exception:
            raise SandboxListError("sandbox output listing failed") from None
        if type(result) is not list:
            raise SandboxProtocolError("the sandbox service returned an invalid file list")
        if len(result) > _LIST_MAX_FILES:
            raise SandboxListError("sandbox output listing exceeded its file limit")

        paths: list[str] = []
        seen: set[str] = set()
        for entry in result:
            candidate = entry if type(entry) is str else getattr(entry, "path", None)
            if type(candidate) is not str:
                raise SandboxProtocolError(
                    "the sandbox service returned an invalid file path"
                )
            try:
                candidate_parts = _validate_output_path(candidate, allow_root=False)
            except SandboxInputError:
                raise SandboxProtocolError(
                    "the sandbox service returned an unsafe file path"
                ) from None
            if (
                len(candidate_parts) <= len(requested_parts)
                or candidate_parts[: len(requested_parts)] != requested_parts
                or not fnmatchcase(candidate_parts[-1], pattern)
            ):
                raise SandboxProtocolError(
                    "the sandbox service returned an out-of-scope file path"
                )
            if candidate not in seen:
                seen.add(candidate)
                paths.append(candidate)
        return paths

    async def run(self, handle: SandboxHandle) -> SandboxCommandResult:
        validated_handle = self._handle(handle)
        try:
            execution = await self._factory.run(
                validated_handle.raw,
                _FIXED_COMMAND,
            )
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable"
            ) from None
        except Exception:
            raise SandboxRunError("sandbox command execution failed") from None

        exit_code = getattr(execution, "exit_code", None)
        logs = getattr(execution, "logs", None)
        if (
            type(exit_code) is not int
            or not -(2**31) <= exit_code < 2**31
            or logs is None
        ):
            raise SandboxProtocolError("the command service returned an invalid result")
        stdout = _bounded_log_text(getattr(logs, "stdout", None))
        stderr = _bounded_log_text(getattr(logs, "stderr", None))
        return SandboxCommandResult(
            exit_code=exit_code,
            stdout=_sanitize_output(stdout, self._config),
            stderr=_sanitize_output(stderr, self._config),
        )

    async def destroy(self, handle: SandboxHandle) -> None:
        validated_handle = self._handle(handle)
        claimed = False
        try:
            if validated_handle.sandbox_id not in self._destroy_started:
                self._destroy_started.add(validated_handle.sandbox_id)
                claimed = True
        finally:
            if claimed:
                try:
                    await self._factory.destroy(validated_handle.raw)
                except SandboxDependencyUnavailableError:
                    raise SandboxDependencyUnavailableError(
                        "the pinned OpenSandbox SDK is unavailable"
                    ) from None
                except Exception:
                    raise SandboxDestroyError("sandbox cleanup failed") from None

    async def destroy_by_id(self, sandbox_id: str) -> None:
        validated_id = _validate_id(sandbox_id, server_value=False)
        claimed = False
        try:
            if validated_id not in self._destroy_started:
                self._destroy_started.add(validated_id)
                claimed = True
        finally:
            if claimed:
                try:
                    await self._factory.destroy_by_id(validated_id)
                except SandboxDependencyUnavailableError:
                    raise SandboxDependencyUnavailableError(
                        "the pinned OpenSandbox SDK is unavailable"
                    ) from None
                except Exception:
                    raise SandboxDestroyError("sandbox cleanup failed") from None
