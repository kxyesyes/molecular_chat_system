"""Narrow, fail-closed boundary around the optional OpenSandbox SDK."""

from __future__ import annotations

import asyncio
import base64
import json
import math
import random
import re
import unicodedata
from collections import OrderedDict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from fnmatch import fnmatchcase
from pathlib import PurePosixPath
from typing import Any, Protocol

from .config import BrokerConfig
from .telemetry import FailureClass


_SANDBOX_PYTHON = "/opt/conda/bin/python"
_FIXED_COMMAND = (
    f"{_SANDBOX_PYTHON} /opt/medchat/run_docking.py "
    "--request /workspace/input/request.json --output /workspace/output"
)
_FIXED_ENTRYPOINT = ("sleep", "infinity")
_INPUT_ROOT_PARTS = ("workspace", "input")
_OUTPUT_ROOT_PARTS = ("workspace", "output")
_REQUEST_MAX_BYTES = 64 * 1024
_READ_MAX_BYTES = 16 * 1024 * 1024
_COMMAND_OUTPUT_MAX_BYTES = 16 * 1024
_LIST_MAX_FILES = 256
_TRUNCATED_MARKER = "\n[TRUNCATED]"
_PATH_MAX_CHARACTERS = 512
_LIST_OUTPUT_MAX_BYTES = (_PATH_MAX_CHARACTERS + 4) * (_LIST_MAX_FILES + 1)
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_PATH_COMPONENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_ALLOWED_LIST_PATTERNS = frozenset({"*", "*.json", "*.pdbqt"})
_SDK_REQUEST_TIMEOUT_SECONDS = 20
_SDK_READY_TIMEOUT_SECONDS = 60
_CLEANUP_COMPLETED_LIMIT = 4096
_CLEANUP_DRAIN_MARGIN_SECONDS = 1.0
_CLEANUP_QUARANTINE_LIMIT = 16
_RECONCILE_PAGE_SIZE = 100
_ANSI_OSC_PATTERN = re.compile(r"\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)")
_ANSI_CSI_PATTERN = re.compile(r"\x1b(?:\[[0-?]*[ -/]*[@-~]|[@-_])")
_AUTHORIZATION_HEADER_PATTERN = re.compile(
    r"(?i)\bauthorization[ \t]*:[^\r\n]*"
)
_SECRET_ASSIGNMENT_PATTERN = re.compile(
    r"(?i)\b(?:x[-_])?(?:api[-_ ]?key|api[-_ ]?token|access[-_ ]?token|"
    r"token|password|secret)\b[ \t]*[:=][ \t]*[^\s,;]+"
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._~+/=-]+")
_OPENAI_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_-]{8,}\b")
_WINDOWS_HOST_PATH_PATTERN = re.compile(r"(?i)\b[A-Z]:[\\/][^\s]*")
_POSIX_HOST_PATH_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_])/(?!workspace(?:/|$)|opt/medchat(?:/|$))[^\s]*"
)
_LIST_HELPER_SOURCE = r'''
import fnmatch
import json
import os
import re
import sys

root = sys.argv[1]
pattern = sys.argv[2]
allowed = os.path.realpath("/workspace/output")
resolved_root = os.path.realpath(root)
component = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")

def inside_output(candidate):
    try:
        return os.path.commonpath((allowed, candidate)) == allowed
    except ValueError:
        return False

if not inside_output(resolved_root):
    raise SystemExit(76)

matches = []
for current, directories, files in os.walk(root, topdown=True, followlinks=False):
    directories[:] = sorted(
        name
        for name in directories
        if not os.path.islink(os.path.join(current, name))
    )
    for name in sorted(files):
        candidate = os.path.join(current, name)
        parts = candidate.split("/")[1:]
        if (
            len(candidate) > 512
            or "\\" in candidate
            or "\0" in candidate
            or not parts
            or any(not component.fullmatch(part) for part in parts)
        ):
            raise SystemExit(76)
        if os.path.islink(candidate):
            continue
        if not inside_output(os.path.realpath(candidate)):
            raise SystemExit(76)
        if fnmatch.fnmatchcase(name, pattern):
            matches.append(candidate)
            if len(matches) > 256:
                raise SystemExit(75)
sys.stdout.write(
    json.dumps(matches, separators=(",", ":"), ensure_ascii=True) + "\n"
)
'''.strip()
_LIST_HELPER_B64 = base64.b64encode(_LIST_HELPER_SOURCE.encode("utf-8")).decode(
    "ascii"
)
_CONTROL_PLANE_OPERATIONS = frozenset(
    {"create", "readiness", "metadata", "upload", "command", "destroy"}
)
_RETRYABLE_METADATA_FAILURES = frozenset(
    {
        FailureClass.CONNECTION_FAILED,
        FailureClass.SERVER_500,
        FailureClass.PROXY_502,
        FailureClass.RESOURCE_LIMIT,
        FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
    }
)


def _default_metadata_retry_backoff(_attempt: int) -> float:
    return random.uniform(0.05, 0.15)


def _http_status(failure: object) -> int | None:
    try:
        direct = getattr(failure, "status_code", None)
    except Exception:
        direct = None
    if type(direct) is int:
        return direct
    try:
        response = getattr(failure, "response", None)
        nested = getattr(response, "status_code", None)
    except Exception:
        return None
    return nested if type(nested) is int else None


def classify_control_plane_failure(
    operation: str,
    failure: object,
) -> FailureClass:
    """Classify bounded exception metadata without inspecting raw text."""

    if type(operation) is not str or operation not in _CONTROL_PLANE_OPERATIONS:
        return FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE
    status = _http_status(failure)
    if status == 500:
        return FailureClass.SERVER_500
    if status == 502:
        return FailureClass.PROXY_502
    if status in {413, 429}:
        return FailureClass.RESOURCE_LIMIT
    if isinstance(
        failure,
        (ConnectionError, ConnectionRefusedError, ConnectionResetError),
    ):
        return FailureClass.CONNECTION_FAILED
    if isinstance(failure, (asyncio.TimeoutError, TimeoutError)):
        return {
            "create": FailureClass.CREATE_TIMEOUT,
            "readiness": FailureClass.READINESS_TIMEOUT,
            "command": FailureClass.COMMAND_TIMEOUT,
        }.get(operation, FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE)
    if operation == "command":
        return FailureClass.COMMAND_TRANSPORT_FAILED
    if operation == "destroy":
        return FailureClass.DESTROY_FAILED
    return FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE


class SandboxClientError(Exception):
    """Base class for sanitized OpenSandbox adapter failures."""

    def __init__(
        self,
        message: str,
        *,
        operation: str = "metadata",
        failure_class: FailureClass = FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
    ) -> None:
        if type(operation) is not str or operation not in _CONTROL_PLANE_OPERATIONS:
            raise ValueError("invalid sandbox operation")
        if type(failure_class) is not FailureClass:
            raise ValueError("invalid sandbox failure class")
        super().__init__(message)
        self.operation = operation
        self.failure_class = failure_class


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

    def __init__(
        self,
        message: str,
        *,
        operation: str = "destroy",
        failure_class: FailureClass = FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        completion_known: bool = False,
    ) -> None:
        if type(completion_known) is not bool:
            raise ValueError("invalid sandbox destroy completion certainty")
        super().__init__(
            message,
            operation=operation,
            failure_class=failure_class,
        )
        self._completion_known = completion_known

    @property
    def completion_known(self) -> bool:
        return self._completion_known


class SandboxProtocolError(SandboxClientError):
    """The SDK or server returned data outside the expected contract."""

    def __init__(
        self,
        message: str,
        *,
        operation: str = "metadata",
        failure_class: FailureClass = FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        cleanup_confirmed: bool = True,
    ) -> None:
        if type(cleanup_confirmed) is not bool:
            raise ValueError("invalid sandbox cleanup certainty")
        super().__init__(
            message,
            operation=operation,
            failure_class=failure_class,
        )
        self._cleanup_confirmed = cleanup_confirmed

    @property
    def cleanup_confirmed(self) -> bool:
        return self._cleanup_confirmed


class _MetadataObservationFailure(Exception):
    """Carry only classified list failure data between official lifecycle layers."""

    def __init__(self, failure_class: FailureClass) -> None:
        self.failure_class = failure_class
        super().__init__("sandbox metadata observation failed")


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
            raise SandboxProtocolError(
                "the command service returned an invalid result",
                operation="command",
            )
        if type(self.stdout) is not str or type(self.stderr) is not str:
            raise SandboxProtocolError(
                "the command service returned invalid logs",
                operation="command",
            )
        if (
            _utf8_size(self.stdout, SandboxProtocolError, operation="command")
            > _COMMAND_OUTPUT_MAX_BYTES
            or _utf8_size(
                self.stderr,
                SandboxProtocolError,
                operation="command",
            )
            > _COMMAND_OUTPUT_MAX_BYTES
        ):
            raise SandboxProtocolError(
                "the command service returned oversized logs",
                operation="command",
            )


class SandboxClient(Protocol):
    async def create(self, job_id: str) -> SandboxHandle:
        raise NotImplementedError

    async def upload_text(
        self,
        handle: SandboxHandle,
        path: str,
        data: str | bytes,
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

    async def destroy_by_job_id(self, job_id: str) -> int:
        raise NotImplementedError

    async def drain_cleanup(self) -> None:
        raise NotImplementedError


class _SandboxFactory(Protocol):
    async def create(
        self,
        *,
        image: str,
        timeout_seconds: int,
        resource: dict[str, str],
        entrypoint: list[str],
        metadata: dict[str, str],
    ) -> Any:
        raise NotImplementedError

    async def upload_text(self, raw: Any, path: str, data: str | bytes) -> None:
        raise NotImplementedError

    async def read_bytes_stream(
        self,
        raw: Any,
        path: str,
        max_bytes: int,
    ) -> object:
        raise NotImplementedError

    async def run(
        self,
        raw: Any,
        command: str,
        *,
        on_stdout: Callable[[Any], Awaitable[None]],
        on_stderr: Callable[[Any], Awaitable[None]],
        skip_accumulation: bool,
    ) -> object:
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
    ExecutionHandlers: Any
    SandboxFilter: Any = None
    RunCommandOpts: Any = None


def _load_official_sdk() -> _OfficialSDK:
    """Load OpenSandbox 0.1.15 only when a production SDK call is made."""

    try:
        from opensandbox import Sandbox, SandboxManager
        from opensandbox.config import ConnectionConfig
        from opensandbox.models import SandboxFilter, SearchEntry, WriteEntry
        from opensandbox.models.execd import ExecutionHandlers, RunCommandOpts
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
        ExecutionHandlers=ExecutionHandlers,
        SandboxFilter=SandboxFilter,
        RunCommandOpts=RunCommandOpts,
    )


class _OfficialSandboxFactory:
    """Translate fixed primitive policy into OpenSandbox 0.1.15 objects."""

    def __init__(
        self,
        config: BrokerConfig,
        *,
        telemetry: object | None = None,
        metadata_retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        metadata_retry_backoff: Callable[[int], float] = (
            _default_metadata_retry_backoff
        ),
    ) -> None:
        self._config = config
        self._telemetry = telemetry
        self._metadata_retry_sleep = metadata_retry_sleep
        self._metadata_retry_backoff = metadata_retry_backoff
        self._metadata_lifecycle_tasks: set[asyncio.Task[Any]] = set()

    def _record_metadata_retry(self, outcome: str) -> None:
        if self._telemetry is None:
            return
        try:
            self._telemetry.record_retry("metadata", outcome)
        except BaseException:
            pass

    @staticmethod
    def _consume_metadata_lifecycle_task(task: asyncio.Task[Any]) -> None:
        try:
            task.exception()
        except BaseException:
            pass

    def _metadata_lifecycle_finished(self, task: asyncio.Task[Any]) -> None:
        self._consume_metadata_lifecycle_task(task)
        self._metadata_lifecycle_tasks.discard(task)

    def metadata_lifecycle_tasks(self) -> tuple[asyncio.Task[Any], ...]:
        return tuple(self._metadata_lifecycle_tasks)

    def _track_metadata_lifecycle(
        self,
        operation: Awaitable[Any],
    ) -> asyncio.Task[Any]:
        task = asyncio.create_task(operation)
        self._metadata_lifecycle_tasks.add(task)
        task.add_done_callback(self._metadata_lifecycle_finished)
        return task

    async def _cancel_metadata_lifecycle(self, task: asyncio.Task[Any]) -> None:
        task.cancel()
        await asyncio.sleep(0)

    async def _metadata_lifecycle(self, sdk: _OfficialSDK, job_id: str) -> int:
        manager: Any = None
        result = 0
        pending: BaseException | None = None
        try:
            manager = await sdk.SandboxManager.create(
                connection_config=self._connection(sdk)
            )
            try:
                page = await manager.list_sandbox_infos(
                    sdk.SandboxFilter(
                        metadata={
                            "medchat.operation": "molecular_docking",
                            "medchat.job_id": job_id,
                        },
                        page=1,
                        page_size=_RECONCILE_PAGE_SIZE,
                    )
                )
            except (
                SandboxInputError,
                SandboxDependencyUnavailableError,
                SandboxProtocolError,
                asyncio.CancelledError,
            ):
                raise
            except Exception as failure:
                raise _MetadataObservationFailure(
                    classify_control_plane_failure("metadata", failure)
                ) from None
            infos = getattr(page, "sandbox_infos", None)
            if type(infos) is not list or len(infos) > _RECONCILE_PAGE_SIZE:
                raise SandboxProtocolError(
                    "the sandbox service returned an invalid reconciliation result",
                    operation="destroy",
                )
            sandbox_ids: list[str] = []
            for info in infos:
                metadata = getattr(info, "metadata", None)
                if (
                    type(metadata) is not dict
                    or metadata.get("medchat.operation") != "molecular_docking"
                    or metadata.get("medchat.job_id") != job_id
                ):
                    raise SandboxProtocolError(
                        "the sandbox service returned an invalid reconciliation result",
                        operation="destroy",
                    )
                sandbox_ids.append(
                    _validate_id(
                        getattr(info, "id", None),
                        server_value=True,
                        operation="destroy",
                    )
                )
            for sandbox_id in sandbox_ids:
                await manager.kill_sandbox(sandbox_id)
                result += 1
        except BaseException as failure:
            pending = failure
        if manager is not None:
            try:
                await manager.close()
            except BaseException as close_failure:
                if pending is None:
                    pending = close_failure
        if pending is not None:
            pending.__traceback__ = None
            raise pending
        return result

    async def _await_metadata_lifecycle(
        self,
        sdk: _OfficialSDK,
        job_id: str,
        deadline: float,
    ) -> int:
        loop = asyncio.get_running_loop()
        if any(not task.done() for task in self._metadata_lifecycle_tasks):
            raise SandboxListError(
                "sandbox metadata observation failed",
                operation="metadata",
            )
        remaining = deadline - loop.time()
        if remaining <= 0:
            raise SandboxListError(
                "sandbox metadata observation failed",
                operation="metadata",
            )
        task = self._track_metadata_lifecycle(
            self._metadata_lifecycle(sdk, job_id)
        )
        try:
            done, _ = await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            await self._cancel_metadata_lifecycle(task)
            raise
        if task not in done:
            await self._cancel_metadata_lifecycle(task)
            raise SandboxListError(
                "sandbox metadata observation failed",
                operation="metadata",
            ) from None
        try:
            return task.result()
        except (
            _MetadataObservationFailure,
            SandboxDependencyUnavailableError,
            SandboxProtocolError,
        ):
            raise
        except SandboxInputError:
            raise SandboxDestroyError(
                "sandbox cleanup failed",
                operation="destroy",
                failure_class=FailureClass.DESTROY_FAILED,
                completion_known=True,
            ) from None
        except Exception as failure:
            raise SandboxDestroyError(
                "sandbox cleanup failed",
                operation="destroy",
                failure_class=classify_control_plane_failure(
                    "destroy", failure
                ),
                completion_known=True,
            ) from None

    async def _sleep_for_metadata_retry(
        self,
        delay: float,
        deadline: float,
    ) -> None:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= delay:
            raise SandboxListError(
                "sandbox metadata observation failed",
                operation="metadata",
            )
        task = self._track_metadata_lifecycle(
            self._metadata_retry_sleep(delay)
        )
        try:
            done, _ = await asyncio.wait({task}, timeout=remaining)
        except asyncio.CancelledError:
            await self._cancel_metadata_lifecycle(task)
            raise
        if task not in done:
            await self._cancel_metadata_lifecycle(task)
            raise SandboxListError(
                "sandbox metadata observation failed",
                operation="metadata",
            ) from None
        task.result()

    def _sdk(self) -> _OfficialSDK:
        try:
            return _load_official_sdk()
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable",
                operation="metadata",
            ) from None
        except Exception:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable"
            ) from None

    def _connection(self, sdk: _OfficialSDK) -> Any:
        return sdk.ConnectionConfig(
            api_key=self._config.opensandbox_api_key,
            domain=self._config.opensandbox_domain,
            request_timeout=timedelta(seconds=_SDK_REQUEST_TIMEOUT_SECONDS),
            use_server_proxy=True,
        )

    async def create(
        self,
        *,
        image: str,
        timeout_seconds: int,
        resource: dict[str, str],
        entrypoint: list[str],
        metadata: dict[str, str],
    ) -> Any:
        sdk = self._sdk()
        return await sdk.Sandbox.create(
            image,
            timeout=timedelta(seconds=timeout_seconds),
            resource=resource,
            entrypoint=entrypoint,
            metadata=metadata,
            connection_config=self._connection(sdk),
            ready_timeout=timedelta(seconds=_SDK_READY_TIMEOUT_SECONDS),
        )

    async def upload_text(self, raw: Any, path: str, data: str | bytes) -> None:
        sdk = self._sdk()
        entry = sdk.WriteEntry(path=path, data=data, mode=600, encoding="utf-8")
        await raw.files.write_files([entry])

    async def read_bytes_stream(
        self,
        raw: Any,
        path: str,
        max_bytes: int,
    ) -> object:
        self._sdk()
        return await raw.files.read_bytes_stream(
            path,
            chunk_size=64 * 1024,
            range_header=f"bytes=0-{max_bytes}",
        )

    async def run(
        self,
        raw: Any,
        command: str,
        *,
        on_stdout: Callable[[Any], Awaitable[None]],
        on_stderr: Callable[[Any], Awaitable[None]],
        skip_accumulation: bool,
    ) -> object:
        sdk = self._sdk()
        handlers = sdk.ExecutionHandlers(
            on_stdout=on_stdout,
            on_stderr=on_stderr,
            skip_accumulation=skip_accumulation,
        )
        if sdk.RunCommandOpts is None:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK command timeout is unavailable",
                operation="command",
            )
        opts = sdk.RunCommandOpts(
            timeout=timedelta(seconds=self._config.execution_timeout_seconds)
        )
        return await raw.commands.run(command, opts=opts, handlers=handlers)

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

    async def destroy_by_job_id(self, job_id: str) -> int:
        sdk = self._sdk()
        if sdk.SandboxFilter is None:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK metadata filter is unavailable",
                operation="destroy",
            )
        deadline = (
            asyncio.get_running_loop().time() + _SDK_REQUEST_TIMEOUT_SECONDS
        )
        retried = False
        for attempt in (1, 2):
            try:
                result = await self._await_metadata_lifecycle(
                    sdk, job_id, deadline
                )
            except _MetadataObservationFailure as failure:
                failure_class = failure.failure_class
                if (
                    attempt == 2
                    or failure_class not in _RETRYABLE_METADATA_FAILURES
                ):
                    if retried:
                        self._record_metadata_retry("exhausted")
                    raise SandboxListError(
                        "sandbox metadata observation failed",
                        operation="metadata",
                        failure_class=failure_class,
                    ) from None
                retried = True
                self._record_metadata_retry("attempted")
                try:
                    delay = self._metadata_retry_backoff(attempt)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._record_metadata_retry("exhausted")
                    raise SandboxListError(
                        "sandbox metadata observation failed",
                        operation="metadata",
                        failure_class=failure_class,
                    ) from None
                if (
                    type(delay) not in {int, float}
                    or not math.isfinite(delay)
                    or not 0 <= delay <= 0.15
                ):
                    self._record_metadata_retry("exhausted")
                    raise SandboxListError(
                        "sandbox metadata observation failed",
                        operation="metadata",
                        failure_class=failure_class,
                    ) from None
                try:
                    await self._sleep_for_metadata_retry(
                        float(delay), deadline
                    )
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._record_metadata_retry("exhausted")
                    raise SandboxListError(
                        "sandbox metadata observation failed",
                        operation="metadata",
                        failure_class=failure_class,
                    ) from None
                continue
            except SandboxListError:
                self._record_metadata_retry("exhausted")
                raise
            if retried:
                self._record_metadata_retry("succeeded")
            return result
        raise AssertionError("unreachable metadata retry state")


def _validate_id(
    value: object,
    *,
    server_value: bool,
    operation: str = "metadata",
) -> str:
    if type(value) is not str or _ID_PATTERN.fullmatch(value) is None:
        if server_value:
            raise SandboxProtocolError(
                "the sandbox service returned an invalid identifier",
                operation=operation,
            )
        raise SandboxInputError(
            "the identifier is outside the fixed contract",
            operation=operation,
        )
    return value


def _path_parts(
    path: object,
    *,
    operation: str = "metadata",
) -> tuple[str, ...]:
    if type(path) is not str:
        raise SandboxInputError(
            "the sandbox path is outside the fixed contract",
            operation=operation,
        )
    if (
        not path.startswith("/")
        or len(path) > _PATH_MAX_CHARACTERS
        or path.endswith("/")
        or "\\" in path
        or "\x00" in path
        or "//" in path
    ):
        raise SandboxInputError(
            "the sandbox path is outside the fixed contract",
            operation=operation,
        )
    components = tuple(path.split("/")[1:])
    if not components or any(
        component in {"", ".", ".."}
        or _PATH_COMPONENT_PATTERN.fullmatch(component) is None
        for component in components
    ):
        raise SandboxInputError(
            "the sandbox path is outside the fixed contract",
            operation=operation,
        )
    if str(PurePosixPath(path)) != path:
        raise SandboxInputError(
            "the sandbox path is outside the fixed contract",
            operation=operation,
        )
    return components


def _validate_output_path(
    path: object,
    *,
    allow_root: bool,
    operation: str = "metadata",
) -> tuple[str, ...]:
    parts = _path_parts(path, operation=operation)
    if parts[:2] != _OUTPUT_ROOT_PARTS or (not allow_root and len(parts) == 2):
        raise SandboxInputError(
            "the sandbox path is outside the output root",
            operation=operation,
        )
    return parts


def _utf8_size(
    value: str,
    error: type[SandboxClientError],
    *,
    operation: str = "metadata",
) -> int:
    try:
        return len(value.encode("utf-8", errors="strict"))
    except UnicodeError:
        raise error("text is not valid UTF-8", operation=operation) from None


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


def _normalize_output(value: str) -> str:
    normalized = _ANSI_OSC_PATTERN.sub("", value)
    normalized = _ANSI_CSI_PATTERN.sub("", normalized)
    return _remove_control_characters(normalized)


def _redact_normalized_output(value: str, config: BrokerConfig) -> str:
    sanitized = _AUTHORIZATION_HEADER_PATTERN.sub("[REDACTED]", value)
    sensitive_values = {
        config.opensandbox_api_key,
        config.opensandbox_domain,
        str(config.state_root),
        str(config.socket_path),
        str(config.state_root).replace("\\", "/"),
        str(config.socket_path).replace("\\", "/"),
    }
    for sensitive in sorted(sensitive_values, key=len, reverse=True):
        normalized_sensitive = _normalize_output(sensitive)
        if normalized_sensitive:
            sanitized = sanitized.replace(normalized_sensitive, "[REDACTED]")
    sanitized = _SECRET_ASSIGNMENT_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _BEARER_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _OPENAI_KEY_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _WINDOWS_HOST_PATH_PATTERN.sub("[REDACTED]", sanitized)
    sanitized = _POSIX_HOST_PATH_PATTERN.sub("[REDACTED]", sanitized)
    return sanitized


def _sanitize_output(value: str, config: BrokerConfig) -> str:
    normalized = _normalize_output(value)
    redacted = _redact_normalized_output(normalized, config)
    truncated = _truncate_utf8(redacted, _COMMAND_OUTPUT_MAX_BYTES)
    return _redact_normalized_output(truncated, config)


class _BoundedUtf8Buffer:
    """Keep at most ``capacity`` bytes without encoding an unbounded chunk."""

    def __init__(self, capacity: int) -> None:
        self._capacity = capacity
        self._data = bytearray()
        self.truncated = False

    def feed_message(self, message: object) -> None:
        text = getattr(message, "text", None)
        if type(text) is not str:
            raise SandboxProtocolError("the command service returned invalid logs")
        self.feed_text(text)

    def feed_text(self, value: str) -> None:
        remaining = self._capacity - len(self._data)
        if remaining <= 0:
            if value:
                self.truncated = True
            return

        # A Unicode code point takes at most four UTF-8 bytes. Inspecting only
        # this prefix prevents a malicious callback chunk from causing another
        # unbounded allocation while still proving whether data was omitted.
        prefix = value[: remaining + 1]
        try:
            encoded = prefix.encode("utf-8", errors="strict")
        except UnicodeError:
            raise SandboxProtocolError(
                "the command service returned invalid UTF-8 logs"
            ) from None
        if len(encoded) > remaining:
            self._data.extend(encoded[:remaining])
            self.truncated = True
        else:
            self._data.extend(encoded)
        if len(prefix) < len(value):
            self.truncated = True

    def text(self) -> str:
        return bytes(self._data).decode("utf-8", errors="ignore")


def _finalize_command_output(
    buffer: _BoundedUtf8Buffer,
    config: BrokerConfig,
) -> str:
    value = buffer.text()
    if buffer.truncated:
        # Omit a boundary guard so a credential split exactly at the transport
        # cap can never be returned as an apparently harmless partial secret.
        known_secret_bytes = len(
            _normalize_output(config.opensandbox_api_key).encode(
                "utf-8", errors="strict"
            )
        )
        guard = max(512, known_secret_bytes)
        value = _truncate_utf8(
            value,
            max(
                0,
                _utf8_size(
                    value,
                    SandboxProtocolError,
                    operation="command",
                )
                - guard,
            ),
        )
    sanitized = _sanitize_output(value, config)
    if buffer.truncated:
        marker_bytes = len(_TRUNCATED_MARKER.encode("utf-8"))
        sanitized = (
            _truncate_utf8(
                sanitized,
                _COMMAND_OUTPUT_MAX_BYTES - marker_bytes,
            )
            + _TRUNCATED_MARKER
        )
    return _redact_normalized_output(sanitized, config)


def _fixed_list_command(path: str, pattern: str) -> str:
    """Build the one internal discovery command from already-validated tokens."""

    return (
        f"{_SANDBOX_PYTHON} -c \"import base64;"
        f"exec(base64.b64decode('{_LIST_HELPER_B64}'))\" "
        f"'{path}' '{pattern}'"
    )


async def _consume_bounded_read_stream(stream: object) -> str:
    if not hasattr(stream, "__aiter__"):
        raise SandboxProtocolError("the sandbox service returned invalid output")
    data = bytearray()
    try:
        async for chunk in stream:  # type: ignore[union-attr]
            if type(chunk) is not bytes:
                raise SandboxProtocolError(
                    "the sandbox service returned non-bytes output"
                )
            remaining = _READ_MAX_BYTES + 1 - len(data)
            if remaining <= 0:
                raise SandboxReadError("sandbox output exceeded its byte limit")
            data.extend(chunk[:remaining])
            if len(chunk) > remaining or len(data) > _READ_MAX_BYTES:
                raise SandboxReadError("sandbox output exceeded its byte limit")
    finally:
        close = getattr(stream, "aclose", None)
        if callable(close):
            await close()
    try:
        return bytes(data).decode("utf-8", errors="strict")
    except UnicodeError:
        raise SandboxProtocolError(
            "the sandbox service returned invalid UTF-8 output"
        ) from None


class OpenSandboxClient:
    """Expose only the broker's fixed OpenSandbox operations."""

    def __init__(
        self,
        config: BrokerConfig,
        *,
        sandbox_factory: _SandboxFactory | None = None,
        telemetry: object | None = None,
        metadata_retry_sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        metadata_retry_backoff: Callable[[int], float] = (
            _default_metadata_retry_backoff
        ),
    ) -> None:
        if type(config) is not BrokerConfig:
            raise SandboxInputError("config must be a validated BrokerConfig")
        self._config = config
        self._factory: _SandboxFactory = (
            _OfficialSandboxFactory(
                config,
                telemetry=telemetry,
                metadata_retry_sleep=metadata_retry_sleep,
                metadata_retry_backoff=metadata_retry_backoff,
            )
            if sandbox_factory is None
            else sandbox_factory
        )
        self._cleanup_in_flight: dict[tuple[str, str], asyncio.Task[Any]] = {}
        self._cleanup_waiters: dict[asyncio.Task[Any], int] = {}
        self._cleanup_completed: OrderedDict[str, None] = OrderedDict()
        self._cleanup_completed_limit = _CLEANUP_COMPLETED_LIMIT
        self._cleanup_hard_timeout_seconds = float(_SDK_REQUEST_TIMEOUT_SECONDS)
        self._cleanup_drain_margin_seconds = _CLEANUP_DRAIN_MARGIN_SECONDS
        self._cleanup_quarantine_limit = _CLEANUP_QUARANTINE_LIMIT
        self._cleanup_active_operations: set[asyncio.Task[Any]] = set()
        self._cleanup_quarantine: set[asyncio.Task[Any]] = set()
        self._cleanup_generation = 0
        self._cleanup_failure_generation = 0
        self._cleanup_reported_failure_generation = 0
        self._cleanup_breaker_generation = 0
        self._cleanup_breaker_resolved_generation = 0
        self._cleanup_lock = asyncio.Lock()

    def _handle(self, handle: object, *, operation: str) -> SandboxHandle:
        if type(handle) is not SandboxHandle or handle.raw is None:
            raise SandboxInputError(
                "the sandbox handle is outside the fixed contract",
                operation=operation,
            )
        _validate_id(
            handle.sandbox_id,
            server_value=False,
            operation=operation,
        )
        return handle

    async def create(self, job_id: str) -> SandboxHandle:
        validated_job_id = _validate_id(
            job_id,
            server_value=False,
            operation="create",
        )
        try:
            raw = await self._factory.create(
                image=(
                    f"{self._config.image_uri}@sha256:"
                    f"{self._config.image_digest}"
                ),
                timeout_seconds=self._config.sandbox_timeout_seconds,
                resource={"cpu": self._config.cpu, "memory": self._config.memory},
                entrypoint=list(_FIXED_ENTRYPOINT),
                metadata={
                    "medchat.operation": "molecular_docking",
                    "medchat.job_id": validated_job_id,
                },
            )
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable",
                operation="create",
            ) from None
        except Exception as failure:
            raise SandboxCreateError(
                "sandbox provisioning failed",
                operation="create",
                failure_class=classify_control_plane_failure("create", failure),
            ) from None

        raw_id = getattr(raw, "id", None)
        try:
            sandbox_id = _validate_id(
                raw_id,
                server_value=True,
                operation="create",
            )
        except SandboxProtocolError:
            cleanup_confirmed = True
            if raw is not None:
                try:
                    await self._perform_cleanup(lambda: self._factory.destroy(raw))
                except BaseException:
                    cleanup_confirmed = False
            raise SandboxProtocolError(
                "the sandbox service returned an invalid identifier",
                operation="create",
                cleanup_confirmed=cleanup_confirmed,
            ) from None
        return SandboxHandle(sandbox_id=sandbox_id, raw=raw)

    async def upload_text(
        self,
        handle: SandboxHandle,
        path: str,
        data: str | bytes,
    ) -> None:
        validated_handle = self._handle(handle, operation="upload")
        parts = _path_parts(path, operation="upload")
        if parts[:2] != _INPUT_ROOT_PARTS or len(parts) != 3:
            raise SandboxInputError(
                "only fixed broker inputs may be uploaded",
                operation="upload",
            )
        limits = {
            "receptor.pdb": self._config.receptor_max_bytes,
            "receptor.pdbqt": self._config.receptor_max_bytes,
            "ligand.sdf": self._config.ligand_max_bytes,
            "ligand.mol": self._config.ligand_max_bytes,
            "ligand.pdb": self._config.ligand_max_bytes,
            "ligand.pdbqt": self._config.ligand_max_bytes,
            "request.json": _REQUEST_MAX_BYTES,
        }
        limit = limits.get(parts[-1])
        if limit is None or type(data) not in {str, bytes} or not data:
            raise SandboxInputError(
                "only fixed non-empty inputs may be uploaded",
                operation="upload",
            )
        if parts[-1] == "request.json" and type(data) is not str:
            raise SandboxInputError(
                "the fixed request must be UTF-8 text",
                operation="upload",
            )
        size = (
            _utf8_size(data, SandboxInputError, operation="upload")
            if type(data) is str
            else len(data)
        )
        if size > limit:
            raise SandboxInputError(
                "the fixed input exceeds its byte limit",
                operation="upload",
            )
        try:
            await self._factory.upload_text(validated_handle.raw, path, data)
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable",
                operation="upload",
            ) from None
        except Exception as failure:
            raise SandboxUploadError(
                "sandbox input upload failed",
                operation="upload",
                failure_class=classify_control_plane_failure("upload", failure),
            ) from None

    async def read_text(self, handle: SandboxHandle, path: str) -> str:
        validated_handle = self._handle(handle, operation="metadata")
        _validate_output_path(path, allow_root=False)
        try:
            stream = await self._factory.read_bytes_stream(
                validated_handle.raw,
                path,
                _READ_MAX_BYTES,
            )
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable",
                operation="metadata",
            ) from None
        except Exception as failure:
            raise SandboxReadError(
                "sandbox output read failed",
                operation="metadata",
                failure_class=classify_control_plane_failure("metadata", failure),
            ) from None
        try:
            return await _consume_bounded_read_stream(stream)
        except (SandboxProtocolError, SandboxReadError):
            raise
        except Exception as failure:
            raise SandboxReadError(
                "sandbox output read failed",
                operation="metadata",
                failure_class=classify_control_plane_failure("metadata", failure),
            ) from None

    async def list_files(
        self,
        handle: SandboxHandle,
        path: str,
        pattern: str,
    ) -> list[str]:
        validated_handle = self._handle(handle, operation="metadata")
        requested_parts = _validate_output_path(path, allow_root=True)
        if type(pattern) is not str or pattern not in _ALLOWED_LIST_PATTERNS:
            raise SandboxInputError("the search pattern is outside the fixed contract")
        stdout_buffer = _BoundedUtf8Buffer(_LIST_OUTPUT_MAX_BYTES)
        stderr_buffer = _BoundedUtf8Buffer(_COMMAND_OUTPUT_MAX_BYTES)

        async def on_stdout(message: object) -> None:
            stdout_buffer.feed_message(message)

        async def on_stderr(message: object) -> None:
            stderr_buffer.feed_message(message)

        try:
            execution = await self._factory.run(
                validated_handle.raw,
                _fixed_list_command(path, pattern),
                on_stdout=on_stdout,
                on_stderr=on_stderr,
                skip_accumulation=True,
            )
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable",
                operation="metadata",
            ) from None
        except Exception as failure:
            raise SandboxListError(
                "sandbox output listing failed",
                operation="metadata",
                failure_class=classify_control_plane_failure("metadata", failure),
            ) from None

        exit_code = getattr(execution, "exit_code", None)
        if type(exit_code) is not int or not -(2**31) <= exit_code < 2**31:
            raise SandboxProtocolError("the command service returned an invalid result")
        if stdout_buffer.truncated:
            raise SandboxListError("sandbox output listing exceeded its byte limit")
        if exit_code != 0:
            raise SandboxListError("sandbox output listing failed")

        serialized = stdout_buffer.text()
        try:
            candidates = json.loads(serialized)
        except (json.JSONDecodeError, UnicodeError):
            raise SandboxProtocolError("the sandbox service returned an invalid file list")
        if type(candidates) is not list:
            raise SandboxProtocolError("the sandbox service returned an invalid file list")
        if len(candidates) > _LIST_MAX_FILES:
            raise SandboxListError("sandbox output listing exceeded its file limit")

        paths: list[str] = []
        seen: set[str] = set()
        for candidate in candidates:
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
        validated_handle = self._handle(handle, operation="command")
        stdout_buffer = _BoundedUtf8Buffer(_COMMAND_OUTPUT_MAX_BYTES)
        stderr_buffer = _BoundedUtf8Buffer(_COMMAND_OUTPUT_MAX_BYTES)

        async def on_stdout(message: object) -> None:
            stdout_buffer.feed_message(message)

        async def on_stderr(message: object) -> None:
            stderr_buffer.feed_message(message)

        try:
            execution = await self._factory.run(
                validated_handle.raw,
                _FIXED_COMMAND,
                on_stdout=on_stdout,
                on_stderr=on_stderr,
                skip_accumulation=True,
            )
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable",
                operation="command",
            ) from None
        except Exception as failure:
            raise SandboxRunError(
                "sandbox command execution failed",
                operation="command",
                failure_class=classify_control_plane_failure("command", failure),
            ) from None

        exit_code = getattr(execution, "exit_code", None)
        if type(exit_code) is not int or not -(2**31) <= exit_code < 2**31:
            raise SandboxProtocolError(
                "the command service returned an invalid result",
                operation="command",
            )
        return SandboxCommandResult(
            exit_code=exit_code,
            stdout=_finalize_command_output(stdout_buffer, self._config),
            stderr=_finalize_command_output(stderr_buffer, self._config),
        )

    async def _perform_cleanup(
        self,
        cleanup: Any,
        *,
        retain_uncertain_operation: Callable[[asyncio.Task[Any]], None] | None = None,
    ) -> Any:
        async with self._cleanup_lock:
            breaker_open = (
                self._cleanup_breaker_generation
                != self._cleanup_breaker_resolved_generation
            )
            if (
                breaker_open
                or len(self._cleanup_active_operations)
                >= self._cleanup_quarantine_limit
            ):
                if not breaker_open:
                    self._cleanup_breaker_generation += 1
                raise SandboxDestroyError(
                    "sandbox cleanup failed",
                    operation="destroy",
                    failure_class=FailureClass.DESTROY_FAILED,
                )
            operation = asyncio.create_task(cleanup())
            self._cleanup_generation += 1
            self._cleanup_active_operations.add(operation)
            operation.add_done_callback(self._cleanup_operation_finished)
        try:
            done, _ = await asyncio.wait(
                {operation},
                timeout=self._cleanup_hard_timeout_seconds,
            )
            if operation not in done:
                if retain_uncertain_operation is not None:
                    retain_uncertain_operation(operation)
                await self._cancel_or_quarantine_cleanup(operation)
                raise SandboxDestroyError(
                    "sandbox cleanup failed",
                    operation="destroy",
                    failure_class=FailureClass.DESTROY_FAILED,
                )
            return operation.result()
        except asyncio.CancelledError:
            if not operation.done() and retain_uncertain_operation is not None:
                retain_uncertain_operation(operation)
            await self._cancel_or_quarantine_cleanup(operation)
            raise
        except SandboxDependencyUnavailableError:
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK is unavailable",
                operation="destroy",
            ) from None
        except SandboxListError as failure:
            failure.__cause__ = None
            failure.__context__ = None
            failure.__traceback__ = None
            raise
        except SandboxProtocolError:
            raise SandboxProtocolError(
                "the sandbox service returned an invalid reconciliation result",
                operation="destroy",
            ) from None
        except SandboxDestroyError as failure:
            raise SandboxDestroyError(
                "sandbox cleanup failed",
                operation="destroy",
                failure_class=FailureClass.DESTROY_FAILED,
                completion_known=failure.completion_known,
            ) from None
        except Exception as failure:
            raise SandboxDestroyError(
                "sandbox cleanup failed",
                operation="destroy",
                failure_class=classify_control_plane_failure("destroy", failure),
                completion_known=operation.done() and not operation.cancelled(),
            ) from None

    @staticmethod
    def _consume_cleanup_task(task: asyncio.Task[Any]) -> None:
        try:
            task.exception()
        except BaseException:
            pass

    def _cleanup_operation_finished(self, task: asyncio.Task[Any]) -> None:
        """Consume and forget one real SDK operation on its event-loop thread."""

        self._consume_cleanup_task(task)
        self._cleanup_quarantine.discard(task)
        self._cleanup_active_operations.discard(task)
        if not self._cleanup_active_operations:
            self._cleanup_breaker_resolved_generation = (
                self._cleanup_breaker_generation
            )

    def _release_uncertain_cleanup_identity(
        self,
        cleanup_key: tuple[str, str],
        owner: asyncio.Task[Any],
        _operation: asyncio.Task[Any],
    ) -> None:
        """Release identity ownership only after the raw SDK operation terminates."""

        if self._cleanup_in_flight.get(cleanup_key) is owner:
            del self._cleanup_in_flight[cleanup_key]

    async def _cancel_or_quarantine_cleanup(
        self,
        operation: asyncio.Task[Any],
    ) -> None:
        operation.cancel()
        # Give cancellable SDK/httpx operations one turn to honor cancellation.
        await asyncio.sleep(0)
        if operation.done():
            return
        async with self._cleanup_lock:
            if operation in self._cleanup_active_operations and not operation.done():
                self._cleanup_quarantine.add(operation)

    async def _perform_tracked_cleanup(
        self,
        sandbox_id: str,
        cleanup: Any,
    ) -> None:
        succeeded = False
        cleanup_key = ("sandbox", sandbox_id)
        task = asyncio.current_task()
        uncertain_operation: asyncio.Task[Any] | None = None

        def retain_identity(operation: asyncio.Task[Any]) -> None:
            nonlocal uncertain_operation
            if uncertain_operation is not None:
                return
            uncertain_operation = operation
            operation.add_done_callback(
                lambda completed: self._release_uncertain_cleanup_identity(
                    cleanup_key,
                    task,
                    completed,
                )
            )

        try:
            await self._perform_cleanup(
                cleanup,
                retain_uncertain_operation=retain_identity,
            )
            succeeded = True
        finally:
            async with self._cleanup_lock:
                if (
                    uncertain_operation is None
                    and self._cleanup_in_flight.get(cleanup_key) is task
                ):
                    del self._cleanup_in_flight[cleanup_key]
                if succeeded:
                    self._cleanup_completed[sandbox_id] = None
                    self._cleanup_completed.move_to_end(sandbox_id)
                    while len(self._cleanup_completed) > self._cleanup_completed_limit:
                        self._cleanup_completed.popitem(last=False)
                else:
                    self._cleanup_failure_generation += 1

    async def _cleanup_once(self, sandbox_id: str, cleanup: Any) -> None:
        cleanup_key = ("sandbox", sandbox_id)
        async with self._cleanup_lock:
            if sandbox_id in self._cleanup_completed:
                self._cleanup_completed.move_to_end(sandbox_id)
                return
            task = self._cleanup_in_flight.get(cleanup_key)
            if task is None:
                task = asyncio.create_task(
                    self._perform_tracked_cleanup(sandbox_id, cleanup)
                )
                self._cleanup_generation += 1
                self._cleanup_in_flight[cleanup_key] = task
                task.add_done_callback(self._consume_cleanup_task)
        await asyncio.shield(task)

    async def _perform_tracked_reconciliation(
        self,
        cleanup_key: tuple[str, str],
        cleanup: Any,
    ) -> Any:
        succeeded = False
        task = asyncio.current_task()
        uncertain_operation: asyncio.Task[Any] | None = None

        def retain_identity(operation: asyncio.Task[Any]) -> None:
            nonlocal uncertain_operation
            if uncertain_operation is not None:
                return
            uncertain_operation = operation
            operation.add_done_callback(
                lambda completed: self._release_uncertain_cleanup_identity(
                    cleanup_key,
                    task,
                    completed,
                )
            )

        try:
            if isinstance(self._factory, _OfficialSandboxFactory):
                result = await cleanup()
            else:
                result = await self._perform_cleanup(
                    cleanup,
                    retain_uncertain_operation=retain_identity,
                )
            if (
                type(result) is not int
                or not 0 <= result <= _RECONCILE_PAGE_SIZE
            ):
                raise SandboxProtocolError(
                    "the sandbox service returned an invalid reconciliation result",
                    operation="destroy",
                )
            succeeded = True
            return result
        except (SandboxListError, SandboxDestroyError) as failure:
            failure.__cause__ = None
            failure.__context__ = None
            failure.__traceback__ = None
            raise
        finally:
            if (
                uncertain_operation is None
                and isinstance(self._factory, _OfficialSandboxFactory)
            ):
                for operation in self._factory_metadata_lifecycle_tasks():
                    if not operation.done():
                        retain_identity(operation)
                        break
            async with self._cleanup_lock:
                if (
                    uncertain_operation is None
                    and self._cleanup_in_flight.get(cleanup_key) is task
                ):
                    del self._cleanup_in_flight[cleanup_key]
                if not succeeded:
                    self._cleanup_failure_generation += 1

    def _factory_metadata_lifecycle_tasks(self) -> tuple[asyncio.Task[Any], ...]:
        snapshot = getattr(self._factory, "metadata_lifecycle_tasks", None)
        if not callable(snapshot):
            return ()
        try:
            tasks = snapshot()
        except BaseException:
            return ()
        if type(tasks) is not tuple:
            return ()
        return tuple(task for task in tasks if isinstance(task, asyncio.Task))

    async def drain_cleanup(self) -> None:
        """Wait for a stable empty cleanup set within one fixed deadline."""

        loop = asyncio.get_running_loop()
        total_timeout = max(
            0.001,
            float(self._cleanup_hard_timeout_seconds)
            + float(self._cleanup_drain_margin_seconds),
        )
        deadline = loop.time() + total_timeout
        while True:
            async with self._cleanup_lock:
                snapshot = tuple(
                    {
                        *self._cleanup_in_flight.values(),
                        *self._cleanup_active_operations,
                        *self._cleanup_quarantine,
                        *self._factory_metadata_lifecycle_tasks(),
                    }
                )
                generation = self._cleanup_generation
                breaker_open = (
                    self._cleanup_breaker_generation
                    != self._cleanup_breaker_resolved_generation
                )
                if not snapshot:
                    if breaker_open:
                        self._cleanup_reported_failure_generation = (
                            self._cleanup_failure_generation
                        )
                        raise SandboxDestroyError(
                            "sandbox cleanup failed",
                            operation="destroy",
                            failure_class=FailureClass.DESTROY_FAILED,
                        )
                    failed = (
                        self._cleanup_failure_generation
                        > self._cleanup_reported_failure_generation
                    )
                    if failed:
                        self._cleanup_reported_failure_generation = (
                            self._cleanup_failure_generation
                        )
                        raise SandboxDestroyError(
                            "sandbox cleanup failed",
                            operation="destroy",
                            failure_class=FailureClass.DESTROY_FAILED,
                        )
            if snapshot:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    async with self._cleanup_lock:
                        self._cleanup_reported_failure_generation = (
                            self._cleanup_failure_generation
                        )
                    raise SandboxDestroyError(
                        "sandbox cleanup failed",
                        operation="destroy",
                        failure_class=FailureClass.DESTROY_FAILED,
                    )
                done, _ = await asyncio.wait(snapshot, timeout=remaining)
                if len(done) != len(snapshot):
                    async with self._cleanup_lock:
                        self._cleanup_reported_failure_generation = (
                            self._cleanup_failure_generation
                        )
                    raise SandboxDestroyError(
                        "sandbox cleanup failed",
                        operation="destroy",
                        failure_class=FailureClass.DESTROY_FAILED,
                    )
                await asyncio.sleep(0)
                continue

            # A cleanup can start immediately after the first empty check.
            # Yield once and require both emptiness and generation stability.
            await asyncio.sleep(0)
            async with self._cleanup_lock:
                breaker_open = (
                    self._cleanup_breaker_generation
                    != self._cleanup_breaker_resolved_generation
                )
                if (
                    self._cleanup_in_flight
                    or self._cleanup_active_operations
                    or self._cleanup_quarantine
                    or self._factory_metadata_lifecycle_tasks()
                    or self._cleanup_generation != generation
                    or breaker_open
                ):
                    if loop.time() >= deadline:
                        self._cleanup_reported_failure_generation = (
                            self._cleanup_failure_generation
                        )
                        raise SandboxDestroyError(
                            "sandbox cleanup failed",
                            operation="destroy",
                            failure_class=FailureClass.DESTROY_FAILED,
                        )
                    continue
                if (
                    self._cleanup_failure_generation
                    > self._cleanup_reported_failure_generation
                ):
                    self._cleanup_reported_failure_generation = (
                        self._cleanup_failure_generation
                    )
                    raise SandboxDestroyError(
                        "sandbox cleanup failed",
                        operation="destroy",
                        failure_class=FailureClass.DESTROY_FAILED,
                    )
                return

    async def destroy(self, handle: SandboxHandle) -> None:
        validated_handle = self._handle(handle, operation="destroy")

        async def cleanup() -> None:
            await self._factory.destroy(validated_handle.raw)

        await self._cleanup_once(validated_handle.sandbox_id, cleanup)

    async def destroy_by_id(self, sandbox_id: str) -> None:
        validated_id = _validate_id(
            sandbox_id,
            server_value=False,
            operation="destroy",
        )

        async def cleanup() -> None:
            await self._factory.destroy_by_id(validated_id)

        await self._cleanup_once(validated_id, cleanup)

    async def destroy_by_job_id(self, job_id: str) -> int:
        """Compensate only sandboxes carrying this broker's fixed job metadata."""

        validated_job_id = _validate_id(
            job_id,
            server_value=False,
            operation="destroy",
        )
        operation = getattr(self._factory, "destroy_by_job_id", None)
        if not callable(operation):
            raise SandboxDependencyUnavailableError(
                "the pinned OpenSandbox SDK metadata filter is unavailable",
                operation="destroy",
            )
        cleanup_key = ("job", validated_job_id)

        async def cleanup() -> int:
            return await operation(validated_job_id)

        async with self._cleanup_lock:
            task = self._cleanup_in_flight.get(cleanup_key)
            if task is None:
                task = asyncio.create_task(
                    self._perform_tracked_reconciliation(cleanup_key, cleanup)
                )
                self._cleanup_generation += 1
                self._cleanup_in_flight[cleanup_key] = task
                task.add_done_callback(self._consume_cleanup_task)
            self._cleanup_waiters[task] = self._cleanup_waiters.get(task, 0) + 1
        cancelled = False
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            self._release_cleanup_waiter(task, cancelled=cancelled)

    def _release_cleanup_waiter(
        self,
        task: asyncio.Task[Any],
        *,
        cancelled: bool,
    ) -> None:
        """Release one waiter without introducing a cancellation point."""

        remaining_waiters = self._cleanup_waiters.get(task, 1) - 1
        if remaining_waiters > 0:
            self._cleanup_waiters[task] = remaining_waiters
            return
        self._cleanup_waiters.pop(task, None)
        if cancelled and not task.done():
            task.cancel()
