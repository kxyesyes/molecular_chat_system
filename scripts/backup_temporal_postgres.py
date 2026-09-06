#!/usr/bin/env python3
"""Create one atomic, descriptor-confined Temporal PostgreSQL backup."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import stat
import subprocess
import sys
import tempfile
import time
from typing import Callable


SCHEMA_VERSION = 1
BACKUP_TIMEOUT_SECONDS = 300.0
QUERY_TIMEOUT_SECONDS = 15.0
MAX_TOOL_OUTPUT_BYTES = 4096
MAX_MANIFEST_BYTES = 16 * 1024
MAX_PASSWORD_BYTES = 1024
DEFAULT_POSTGRES_BIN_DIR = Path("/usr/bin")
POSTGRES_BIN_DIR_ENV = "TEMPORAL_POSTGRES_BIN_DIR"
PROCESS_TERM_GRACE_SECONDS = 0.5
PROCESS_REAP_TIMEOUT_SECONDS = 2.0
POSTGRES_TOOL_NAMES = frozenset({"createdb", "dropdb", "pg_dump", "pg_restore", "psql"})
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z")
_LEAF = re.compile(r"[A-Za-z0-9.][A-Za-z0-9._-]{0,127}\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_SERVER_MAJOR = re.compile(rb"([1-9][0-9]?)\r?\n?\Z")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_BINARY = getattr(os, "O_BINARY", 0)
_PATH = getattr(os, "O_PATH", 0)
_NONBLOCK = getattr(os, "O_NONBLOCK", 0)
_NOCTTY = getattr(os, "O_NOCTTY", 0)


class PostgresBackupError(RuntimeError):
    _CODES = frozenset(
        {
            "arguments_invalid",
            "password_missing",
            "output_untrusted",
            "destination_exists",
            "tool_failed",
            "tool_timeout",
            "tool_output_invalid",
            "dump_invalid",
            "atomic_write_failed",
            "manifest_invalid",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if code in self._CODES else "tool_failed"
        super().__init__(self.code)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise PostgresBackupError("arguments_invalid")


def _descriptor_io_available() -> bool:
    return (
        os.name == "posix"
        and bool(_DIRECTORY)
        and bool(_NOFOLLOW)
        and os.open in os.supports_dir_fd
        and os.stat in os.supports_dir_fd
        and os.stat in os.supports_follow_symlinks
        and os.unlink in os.supports_dir_fd
    )


def _is_reparse_point(metadata: os.stat_result) -> bool:
    return bool(
        getattr(metadata, "st_file_attributes", 0)
        & _FILE_ATTRIBUTE_REPARSE_POINT
    )


def _identity(metadata: os.stat_result) -> tuple[int, int]:
    return metadata.st_dev, metadata.st_ino


def _full_identity(metadata: os.stat_result) -> tuple[int, int, int, int, int]:
    return (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
        metadata.st_ctime_ns,
    )


def _absolute_lexical(path: Path) -> Path:
    raw = os.fspath(path)
    if "\x00" in raw:
        raise PostgresBackupError("output_untrusted")
    return Path(os.path.abspath(raw))


def _safe_leaf(name: str, code: str = "output_untrusted") -> str:
    if (
        type(name) is not str
        or name in {".", ".."}
        or _LEAF.fullmatch(name) is None
    ):
        raise PostgresBackupError(code)
    return name


def _directory_flags() -> int:
    return os.O_RDONLY | _DIRECTORY | _NOFOLLOW | _CLOEXEC


def _effective_ids() -> tuple[int, int]:
    if os.name != "posix" or not hasattr(os, "geteuid") or not hasattr(os, "getegid"):
        raise PostgresBackupError("output_untrusted")
    return os.geteuid(), os.getegid()


def _require_trusted_directory_metadata(
    metadata: os.stat_result,
    *,
    final: bool,
) -> None:
    effective_uid, effective_gid = _effective_ids()
    owner = (metadata.st_uid, metadata.st_gid)
    trusted_owner = owner in {(0, 0), (effective_uid, effective_gid)}
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISDIR(metadata.st_mode)
        or _is_reparse_point(metadata)
        or not trusted_owner
        or (final and (owner != (effective_uid, effective_gid) or mode != 0o700))
        or (not final and mode & 0o022)
    ):
        raise PostgresBackupError("output_untrusted")


def _require_trusted_regular_metadata(metadata: os.stat_result) -> None:
    effective_uid, effective_gid = _effective_ids()
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _is_reparse_point(metadata)
        or (metadata.st_uid, metadata.st_gid) != (effective_uid, effective_gid)
        or stat.S_IMODE(metadata.st_mode) != 0o600
    ):
        raise PostgresBackupError("output_untrusted")


def _require_trusted_executable_metadata(metadata: os.stat_result) -> None:
    effective_uid, effective_gid = _effective_ids()
    owner = (metadata.st_uid, metadata.st_gid)
    mode = stat.S_IMODE(metadata.st_mode)
    if (
        not stat.S_ISREG(metadata.st_mode)
        or _is_reparse_point(metadata)
        or owner not in {(0, 0), (effective_uid, effective_gid)}
        or mode & 0o022
        or not mode & 0o111
    ):
        raise PostgresBackupError("output_untrusted")


def _open_directory_chain(path: Path, *, final_exact: bool = True) -> int:
    if not _descriptor_io_available():
        raise PostgresBackupError("output_untrusted")
    lexical = _absolute_lexical(path)
    if lexical.anchor != "/":
        raise PostgresBackupError("output_untrusted")
    descriptor = -1
    try:
        descriptor = os.open("/", _directory_flags())
        root_is_final = final_exact and len(lexical.parts) == 1
        _require_trusted_directory_metadata(os.fstat(descriptor), final=root_is_final)
        components = lexical.parts[1:]
        for index, component in enumerate(components):
            if component in {"", ".", ".."} or "/" in component:
                raise PostgresBackupError("output_untrusted")
            child = os.open(component, _directory_flags(), dir_fd=descriptor)
            metadata = os.fstat(child)
            try:
                _require_trusted_directory_metadata(
                    metadata,
                    final=final_exact and index == len(components) - 1,
                )
            except PostgresBackupError:
                os.close(child)
                raise
            os.close(descriptor)
            descriptor = child
        return descriptor
    except PostgresBackupError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PostgresBackupError("output_untrusted") from exc


class TrustedDirectory:
    """A directory identity held open for the complete transaction."""

    def __init__(self, path: Path) -> None:
        self.path = _absolute_lexical(path)
        self.fd = _open_directory_chain(self.path)
        try:
            self.identity = _identity(os.fstat(self.fd))
        except OSError as exc:
            os.close(self.fd)
            self.fd = -1
            raise PostgresBackupError("output_untrusted") from exc

    def revalidate(self) -> None:
        replacement = _open_directory_chain(self.path)
        try:
            if _identity(os.fstat(replacement)) != self.identity:
                raise PostgresBackupError("output_untrusted")
        finally:
            os.close(replacement)

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> "TrustedDirectory":
        return self

    def __exit__(self, _type: object, _value: object, _traceback: object) -> None:
        self.close()


def _selected_postgres_bin_dir(value: Path | None = None) -> Path:
    selected: object = value
    if selected is None:
        selected = os.environ.get(POSTGRES_BIN_DIR_ENV, DEFAULT_POSTGRES_BIN_DIR)
    if not isinstance(selected, (str, os.PathLike)):
        raise PostgresBackupError("output_untrusted")
    raw = os.fspath(selected)
    if not raw or "\x00" in raw:
        raise PostgresBackupError("output_untrusted")
    return Path(raw)


class TrustedPostgresTool:
    def __init__(
        self,
        directory: "TrustedPostgresBinDirectory",
        name: str,
        descriptor: int,
        identity: tuple[int, int, int, int, int],
    ) -> None:
        self.directory = directory
        self.name = name
        self.fd = descriptor
        self.identity = identity

    @property
    def executable_path(self) -> str:
        if os.name != "posix" or not sys.platform.startswith("linux"):
            raise PostgresBackupError("tool_failed")
        return f"/proc/self/fd/{self.fd}"

    def revalidate(self) -> None:
        try:
            opened = os.fstat(self.fd)
            entry = _stat_at(self.directory.fd, self.name)
            _require_trusted_executable_metadata(opened)
            if (
                _full_identity(opened) != self.identity
                or _full_identity(entry) != self.identity
                or _is_reparse_point(entry)
            ):
                raise PostgresBackupError("output_untrusted")
        except PostgresBackupError:
            raise
        except OSError as exc:
            raise PostgresBackupError("output_untrusted") from exc

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


class TrustedPostgresBinDirectory:
    def __init__(self, path: Path) -> None:
        selected = _selected_postgres_bin_dir(path)
        if not selected.is_absolute():
            raise PostgresBackupError("output_untrusted")
        self.path = _absolute_lexical(selected)
        self.fd = _open_directory_chain(self.path, final_exact=False)
        try:
            self.identity = _identity(os.fstat(self.fd))
        except OSError as exc:
            os.close(self.fd)
            self.fd = -1
            raise PostgresBackupError("output_untrusted") from exc

    def revalidate(self) -> None:
        replacement = _open_directory_chain(self.path, final_exact=False)
        try:
            if _identity(os.fstat(replacement)) != self.identity:
                raise PostgresBackupError("output_untrusted")
        finally:
            os.close(replacement)

    def open_tool(self, name: str) -> TrustedPostgresTool:
        if name not in POSTGRES_TOOL_NAMES:
            raise PostgresBackupError("tool_failed")
        descriptor = -1
        try:
            if _PATH:
                open_flags = _PATH | _NOFOLLOW | _CLOEXEC
            else:
                if not _NONBLOCK or not _NOCTTY or not _NOFOLLOW:
                    raise PostgresBackupError("output_untrusted")
                open_flags = (
                    os.O_RDONLY
                    | _NONBLOCK
                    | _NOCTTY
                    | _NOFOLLOW
                    | _CLOEXEC
                    | _BINARY
                )
            descriptor = os.open(
                name,
                open_flags,
                dir_fd=self.fd,
            )
            opened = os.fstat(descriptor)
            entry = _stat_at(self.fd, name)
            _require_trusted_executable_metadata(opened)
            identity = _full_identity(opened)
            if _full_identity(entry) != identity or _is_reparse_point(entry):
                raise PostgresBackupError("output_untrusted")
            return TrustedPostgresTool(self, name, descriptor, identity)
        except PostgresBackupError:
            if descriptor >= 0:
                os.close(descriptor)
            raise
        except OSError as exc:
            if descriptor >= 0:
                os.close(descriptor)
            raise PostgresBackupError("output_untrusted") from exc

    def close(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1


def _stat_at(parent_fd: int, name: str) -> os.stat_result:
    return os.stat(name, dir_fd=parent_fd, follow_symlinks=False)


def _ensure_absent_at(parent_fd: int, name: str) -> None:
    _safe_leaf(name)
    try:
        _stat_at(parent_fd, name)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PostgresBackupError("output_untrusted") from exc
    raise PostgresBackupError("destination_exists")


def _fsync_directory_fd(parent_fd: int) -> None:
    os.fsync(parent_fd)


def _safe_unlink_at(
    parent_fd: int,
    name: str,
    expected_identity: tuple[int, int, int, int, int] | None,
    *,
    sync: bool = True,
) -> bool:
    if expected_identity is None:
        return False
    try:
        metadata = _stat_at(parent_fd, name)
        if (
            _full_identity(metadata) != expected_identity
            or not stat.S_ISREG(metadata.st_mode)
            or _is_reparse_point(metadata)
        ):
            return False
        os.unlink(name, dir_fd=parent_fd)
        if sync:
            _fsync_directory_fd(parent_fd)
        return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def _safe_unlink_pinned_at(
    parent_fd: int,
    name: str,
    descriptor: int,
    *,
    sync: bool = True,
) -> bool:
    if descriptor < 0:
        return False
    try:
        opened = os.fstat(descriptor)
        named = _stat_at(parent_fd, name)
        if (
            not stat.S_ISREG(opened.st_mode)
            or not stat.S_ISREG(named.st_mode)
            or _is_reparse_point(named)
            or _identity(opened) != _identity(named)
        ):
            return False
        os.unlink(name, dir_fd=parent_fd)
        if sync:
            _fsync_directory_fd(parent_fd)
        return True
    except (FileNotFoundError, OSError):
        return False


def validate_identifier(value: object) -> str:
    if type(value) is not str or _IDENTIFIER.fullmatch(value) is None:
        raise PostgresBackupError("arguments_invalid")
    return value


def validate_host(value: object) -> str:
    if type(value) is not str or _HOST.fullmatch(value) is None:
        raise PostgresBackupError("arguments_invalid")
    return value


def validate_port(value: object) -> int:
    if type(value) is not int or not 1 <= value <= 65535:
        raise PostgresBackupError("arguments_invalid")
    return value


def _utc_timestamp(now: datetime) -> tuple[str, str]:
    if not isinstance(now, datetime) or now.tzinfo is None:
        raise PostgresBackupError("arguments_invalid")
    utc = now.astimezone(timezone.utc).replace(microsecond=0)
    return utc.strftime("%Y%m%dT%H%M%SZ"), utc.strftime("%Y-%m-%dT%H:%M:%SZ")


def canonical_json(payload: dict[str, object]) -> bytes:
    try:
        return (
            json.dumps(
                payload,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
                allow_nan=False,
            )
            + "\n"
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError) as exc:
        raise PostgresBackupError("manifest_invalid") from exc


def _password_environment(postgres_bin_dir: Path | None = None) -> dict[str, str]:
    password = os.environ.get("TEMPORAL_POSTGRES_PASSWORD")
    try:
        encoded = password.encode("utf-8", "strict") if type(password) is str else b""
    except UnicodeError as exc:
        raise PostgresBackupError("password_missing") from exc
    if (
        type(password) is not str
        or not password
        or len(encoded) > MAX_PASSWORD_BYTES
        or any(character in password for character in ("\x00", "\r", "\n"))
    ):
        raise PostgresBackupError("password_missing")
    return {
        "PATH": os.fspath(postgres_bin_dir or DEFAULT_POSTGRES_BIN_DIR),
        "PGPASSWORD": password,
        "PGCONNECT_TIMEOUT": "10",
    }


def _terminate_process_group(process: subprocess.Popen[bytes]) -> None:
    process_group = process.pid
    try:
        os.killpg(process_group, signal.SIGTERM)
    except ProcessLookupError:
        pass
    deadline = time.monotonic() + PROCESS_TERM_GRACE_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)
    try:
        os.killpg(process_group, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=PROCESS_REAP_TIMEOUT_SECONDS)
    except (subprocess.TimeoutExpired, OSError):
        pass
    deadline = time.monotonic() + PROCESS_REAP_TIMEOUT_SECONDS
    while time.monotonic() < deadline:
        try:
            os.killpg(process_group, 0)
        except ProcessLookupError:
            break
        time.sleep(0.02)


def _process_groups_available() -> bool:
    return os.name == "posix" and hasattr(os, "killpg")


def run_command(
    argv: list[str],
    *,
    environment: dict[str, str],
    timeout: float,
    capture_output: bool = False,
    stdin_fd: int | None = None,
    stdout_fd: int | None = None,
) -> object:
    if not _process_groups_available():
        raise PostgresBackupError("tool_failed")
    if capture_output and stdout_fd is not None:
        raise PostgresBackupError("tool_failed")
    command = list(argv)
    trusted_bin: TrustedPostgresBinDirectory | None = None
    trusted_tool: TrustedPostgresTool | None = None
    if not command:
        raise PostgresBackupError("tool_failed")
    tool_name = Path(command[0]).name
    output = None
    try:
        if tool_name in POSTGRES_TOOL_NAMES:
            path_value = environment.get("PATH")
            if type(path_value) is not str:
                raise PostgresBackupError("tool_failed")
            trusted_bin = TrustedPostgresBinDirectory(Path(path_value))
            trusted_bin.revalidate()
            trusted_tool = trusted_bin.open_tool(tool_name)
            trusted_tool.revalidate()
            command[0] = trusted_tool.executable_path
        output = tempfile.TemporaryFile() if capture_output else None
        process = subprocess.Popen(
            command,
            stdin=stdin_fd if stdin_fd is not None else subprocess.DEVNULL,
            stdout=(
                stdout_fd
                if stdout_fd is not None
                else output if output is not None else subprocess.DEVNULL
            ),
            stderr=subprocess.DEVNULL,
            env=environment,
            shell=False,
            close_fds=True,
            pass_fds=(trusted_tool.fd,) if trusted_tool is not None else (),
            start_new_session=True,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            try:
                _terminate_process_group(process)
            except BaseException:
                pass
            raise PostgresBackupError("tool_timeout") from exc
        if trusted_tool is not None:
            trusted_tool.revalidate()
            trusted_bin.revalidate()
        payload = b""
        if output is not None:
            output.seek(0)
            payload = output.read(MAX_TOOL_OUTPUT_BYTES + 1)
            if len(payload) > MAX_TOOL_OUTPUT_BYTES:
                raise PostgresBackupError("tool_output_invalid")
        if returncode != 0:
            raise PostgresBackupError("tool_failed")
        return type("CommandResult", (), {"returncode": returncode, "stdout": payload})()
    except PostgresBackupError:
        raise
    except (OSError, ValueError) as exc:
        raise PostgresBackupError("tool_failed") from exc
    finally:
        if output is not None:
            output.close()
        if trusted_tool is not None:
            trusted_tool.close()
        if trusted_bin is not None:
            trusted_bin.close()


Runner = Callable[..., object]


def _invoke(
    runner: Runner,
    argv: list[str],
    environment: dict[str, str],
    timeout: float,
    *,
    capture_output: bool = False,
    stdin_fd: int | None = None,
    stdout_fd: int | None = None,
) -> object:
    try:
        result = runner(
            argv,
            environment=environment,
            timeout=timeout,
            capture_output=capture_output,
            stdin_fd=stdin_fd,
            stdout_fd=stdout_fd,
        )
    except PostgresBackupError:
        raise
    except Exception as exc:
        raise PostgresBackupError("tool_failed") from exc
    if getattr(result, "returncode", None) != 0:
        raise PostgresBackupError("tool_failed")
    return result


def server_major(
    *,
    runner: Runner,
    environment: dict[str, str],
    connection: list[str],
    database: str,
) -> int:
    query = "SELECT current_setting('server_version_num')::integer / 10000;"
    result = _invoke(
        runner,
        [
            "psql",
            *connection,
            "--dbname",
            database,
            "--no-align",
            "--tuples-only",
            "--set",
            "ON_ERROR_STOP=1",
            "--command",
            query,
        ],
        environment,
        QUERY_TIMEOUT_SECONDS,
        capture_output=True,
    )
    output = getattr(result, "stdout", b"")
    if not isinstance(output, bytes) or len(output) > MAX_TOOL_OUTPUT_BYTES:
        raise PostgresBackupError("tool_output_invalid")
    match = _SERVER_MAJOR.fullmatch(output)
    if match is None:
        raise PostgresBackupError("tool_output_invalid")
    return int(match.group(1))


def _open_new_regular_at(parent_fd: int, name: str, mode: int = 0o600) -> int:
    _safe_leaf(name)
    flags = os.O_RDWR | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC | _BINARY
    descriptor = -1
    try:
        descriptor = os.open(name, flags, mode, dir_fd=parent_fd)
        os.fchmod(descriptor, mode)
        metadata = os.fstat(descriptor)
        entry = _stat_at(parent_fd, name)
        _require_trusted_regular_metadata(metadata)
        if (
            _full_identity(metadata) != _full_identity(entry)
            or _is_reparse_point(entry)
            or mode != 0o600
        ):
            raise PostgresBackupError("output_untrusted")
        return descriptor
    except FileExistsError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PostgresBackupError("destination_exists") from exc
    except PostgresBackupError:
        if descriptor >= 0:
            os.close(descriptor)
        raise
    except OSError as exc:
        if descriptor >= 0:
            os.close(descriptor)
        raise PostgresBackupError("atomic_write_failed") from exc


def _hash_descriptor(
    descriptor: int,
    *,
    code: str,
    sync: bool,
) -> tuple[str, int, tuple[int, int], tuple[int, int, int, int, int]]:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode) or _is_reparse_point(before):
            raise PostgresBackupError(code)
        digest = hashlib.sha256()
        size = 0
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(descriptor)
        if _full_identity(before) != _full_identity(after) or size != after.st_size:
            raise PostgresBackupError(code)
        if sync:
            os.fsync(descriptor)
        os.lseek(descriptor, 0, os.SEEK_SET)
        return digest.hexdigest(), size, _identity(after), _full_identity(after)
    except PostgresBackupError:
        raise
    except OSError as exc:
        raise PostgresBackupError(code) from exc


def _require_descriptor_version(
    descriptor: int,
    expected: tuple[int, int, int, int, int],
    *,
    code: str,
) -> os.stat_result:
    try:
        current = os.fstat(descriptor)
    except OSError as exc:
        raise PostgresBackupError(code) from exc
    if (
        not stat.S_ISREG(current.st_mode)
        or _is_reparse_point(current)
        or _full_identity(current) != expected
    ):
        raise PostgresBackupError(code)
    return current


def _require_name_matches_fd(
    parent_fd: int,
    name: str,
    descriptor: int,
    *,
    code: str,
) -> os.stat_result:
    try:
        opened = os.fstat(descriptor)
        entry = _stat_at(parent_fd, name)
    except OSError as exc:
        raise PostgresBackupError(code) from exc
    if (
        not stat.S_ISREG(opened.st_mode)
        or not stat.S_ISREG(entry.st_mode)
        or _full_identity(opened) != _full_identity(entry)
        or _is_reparse_point(entry)
    ):
        raise PostgresBackupError(code)
    return opened


def _require_published_identity_at(
    parent_fd: int,
    name: str,
    expected_identity: tuple[int, int, int, int, int],
    *,
    code: str,
) -> os.stat_result:
    try:
        current = _stat_at(parent_fd, name)
    except OSError as exc:
        raise PostgresBackupError(code) from exc
    if (
        _full_identity(current) != expected_identity
        or not stat.S_ISREG(current.st_mode)
        or _is_reparse_point(current)
    ):
        raise PostgresBackupError(code)
    return current


def _publish_new_at(
    directory: TrustedDirectory,
    temporary_name: str,
    destination_name: str,
    descriptor: int,
) -> tuple[int, int, int, int, int]:
    _safe_leaf(temporary_name)
    _safe_leaf(destination_name)
    temporary = _require_name_matches_fd(
        directory.fd, temporary_name, descriptor, code="dump_invalid"
    )
    published_identity: tuple[int, int, int, int, int] | None = None
    replaced = False
    try:
        directory.revalidate()
        _ensure_absent_at(directory.fd, destination_name)
        os.replace(
            temporary_name,
            destination_name,
            src_dir_fd=directory.fd,
            dst_dir_fd=directory.fd,
        )
        replaced = True
        published_descriptor = os.fstat(descriptor)
        if _identity(published_descriptor) != _identity(temporary):
            raise PostgresBackupError("atomic_write_failed")
        published_identity = _full_identity(published_descriptor)
        _fsync_directory_fd(directory.fd)
        published = _stat_at(directory.fd, destination_name)
        if (
            _full_identity(published) != _full_identity(published_descriptor)
            or not stat.S_ISREG(published.st_mode)
            or _is_reparse_point(published)
        ):
            raise PostgresBackupError("atomic_write_failed")
        directory.revalidate()
        return published_identity
    except PostgresBackupError:
        if replaced:
            _safe_unlink_at(directory.fd, destination_name, published_identity)
        raise
    except OSError as exc:
        if replaced:
            _safe_unlink_at(directory.fd, destination_name, published_identity)
        raise PostgresBackupError("atomic_write_failed") from exc


def _atomic_write_new_at(
    directory: TrustedDirectory,
    destination_name: str,
    payload: bytes,
    *,
    mode: int = 0o600,
) -> tuple[int, int, int, int, int]:
    destination_name = _safe_leaf(destination_name)
    temporary_name = _safe_leaf(f".{destination_name}.partial")
    _ensure_absent_at(directory.fd, destination_name)
    _ensure_absent_at(directory.fd, temporary_name)
    descriptor = -1
    temporary_identity: tuple[int, int, int, int, int] | None = None
    try:
        descriptor = _open_new_regular_at(directory.fd, temporary_name, mode)
        temporary_identity = _full_identity(os.fstat(descriptor))
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PostgresBackupError("atomic_write_failed")
            view = view[written:]
        os.fsync(descriptor)
        if os.fstat(descriptor).st_size != len(payload):
            raise PostgresBackupError("atomic_write_failed")
        return _publish_new_at(
            directory, temporary_name, destination_name, descriptor
        )
    except PostgresBackupError:
        raise
    except OSError as exc:
        raise PostgresBackupError("atomic_write_failed") from exc
    finally:
        if descriptor >= 0:
            try:
                temporary_identity = _full_identity(os.fstat(descriptor))
            except OSError:
                pass
        _safe_unlink_at(
            directory.fd, temporary_name, temporary_identity, sync=False
        )
        if descriptor >= 0:
            os.close(descriptor)


def _connection_arguments(host: str, port: int, user: str) -> list[str]:
    return ["--host", host, "--port", str(port), "--username", user]


def create_backup(
    *,
    output_dir: Path,
    manifest_output: Path,
    database: str,
    host: str,
    port: int,
    user: str,
    runner: Runner = run_command,
    now: datetime | None = None,
    postgres_bin_dir: Path | None = None,
) -> dict[str, object]:
    database = validate_identifier(database)
    user = validate_identifier(user)
    host = validate_host(host)
    port = validate_port(port)
    stamp, generated_at = _utc_timestamp(now or datetime.now(timezone.utc))
    selected_bin_dir = _selected_postgres_bin_dir(postgres_bin_dir)
    environment = _password_environment(selected_bin_dir)
    manifest_path = _absolute_lexical(Path(manifest_output))
    manifest_name = _safe_leaf(manifest_path.name)
    dump_name = _safe_leaf(f"{database}-{stamp}.dump")
    partial_name = _safe_leaf(f".{dump_name}.partial")
    output = TrustedDirectory(Path(output_dir))
    trusted_bin: TrustedPostgresBinDirectory | None = None
    partial_descriptor = -1
    partial_identity: tuple[int, int, int, int, int] | None = None
    published_identity: tuple[int, int, int, int, int] | None = None
    manifest_identity: tuple[int, int, int, int, int] | None = None
    try:
        manifest_directory = TrustedDirectory(manifest_path.parent)
        try:
            if manifest_directory.identity != output.identity:
                raise PostgresBackupError("output_untrusted")
        finally:
            manifest_directory.close()
        if runner is run_command:
            trusted_bin = TrustedPostgresBinDirectory(selected_bin_dir)
        _ensure_absent_at(output.fd, dump_name)
        _ensure_absent_at(output.fd, partial_name)
        _ensure_absent_at(output.fd, manifest_name)
        _ensure_absent_at(output.fd, f".{manifest_name}.partial")
        connection = _connection_arguments(host, port, user)
        major = server_major(
            runner=runner,
            environment=environment,
            connection=connection,
            database=database,
        )
        if trusted_bin is not None:
            trusted_bin.revalidate()
        output.revalidate()
        partial_descriptor = _open_new_regular_at(output.fd, partial_name)
        partial_identity = _full_identity(os.fstat(partial_descriptor))
        _invoke(
            runner,
            [
                "pg_dump",
                "--format=custom",
                "--no-owner",
                "--no-acl",
                *connection,
                database,
            ],
            environment,
            BACKUP_TIMEOUT_SECONDS,
            stdout_fd=partial_descriptor,
        )
        digest, size, _partial_identity, partial_version = _hash_descriptor(
            partial_descriptor, code="dump_invalid", sync=True
        )
        partial_identity = partial_version
        if size <= 0 or _SHA256.fullmatch(digest) is None:
            raise PostgresBackupError("dump_invalid")
        _require_descriptor_version(
            partial_descriptor,
            partial_version,
            code="dump_invalid",
        )
        _require_name_matches_fd(
            output.fd, partial_name, partial_descriptor, code="dump_invalid"
        )
        published_identity = _publish_new_at(
            output, partial_name, dump_name, partial_descriptor
        )
        manifest: dict[str, object] = {
            "database": database,
            "dump_file": dump_name,
            "generated_at": generated_at,
            "postgres_major": major,
            "schema_version": SCHEMA_VERSION,
            "sha256": digest,
            "size": size,
            "status": "passed",
        }
        manifest_identity = _atomic_write_new_at(
            output,
            manifest_name,
            canonical_json(manifest),
        )
        output.revalidate()
        (
            final_digest,
            final_size,
            _final_identity,
            _final_version,
        ) = _hash_descriptor(partial_descriptor, code="dump_invalid", sync=False)
        if final_digest != digest or final_size != size:
            raise PostgresBackupError("dump_invalid")
        _require_name_matches_fd(
            output.fd,
            dump_name,
            partial_descriptor,
            code="dump_invalid",
        )
        _require_published_identity_at(
            output.fd,
            dump_name,
            published_identity,
            code="atomic_write_failed",
        )
        _require_published_identity_at(
            output.fd,
            manifest_name,
            manifest_identity,
            code="atomic_write_failed",
        )
        return manifest
    except PostgresBackupError:
        _safe_unlink_at(output.fd, manifest_name, manifest_identity)
        if not _safe_unlink_pinned_at(
            output.fd, dump_name, partial_descriptor
        ):
            _safe_unlink_at(output.fd, dump_name, published_identity)
        raise
    except OSError as exc:
        _safe_unlink_at(output.fd, manifest_name, manifest_identity)
        if not _safe_unlink_pinned_at(
            output.fd, dump_name, partial_descriptor
        ):
            _safe_unlink_at(output.fd, dump_name, published_identity)
        raise PostgresBackupError("atomic_write_failed") from exc
    finally:
        if partial_descriptor >= 0:
            try:
                partial_identity = _full_identity(os.fstat(partial_descriptor))
            except OSError:
                pass
        _safe_unlink_at(output.fd, partial_name, partial_identity, sync=False)
        if partial_descriptor >= 0:
            os.close(partial_descriptor)
        if trusted_bin is not None:
            trusted_bin.close()
        output.close()


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--manifest-output", required=True, type=Path)
    parser.add_argument("--postgres-bin-dir", type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        validate_identifier(arguments.database)
        validate_identifier(arguments.user)
        validate_host(arguments.host)
        validate_port(arguments.port)
        selected_bin_dir = _selected_postgres_bin_dir(arguments.postgres_bin_dir)
        _password_environment(selected_bin_dir)
        manifest_path = _absolute_lexical(arguments.manifest_output)
        _safe_leaf(manifest_path.name)
        output_directory = TrustedDirectory(arguments.output_dir)
        try:
            manifest_directory = TrustedDirectory(manifest_path.parent)
            try:
                if output_directory.identity != manifest_directory.identity:
                    raise PostgresBackupError("output_untrusted")
            finally:
                manifest_directory.close()
            trusted_bin = TrustedPostgresBinDirectory(selected_bin_dir)
            trusted_bin.close()
        finally:
            output_directory.close()
        if arguments.dry_run:
            print("temporal_postgres_backup=dry-run")
            return 0
        manifest = create_backup(
            output_dir=arguments.output_dir,
            manifest_output=arguments.manifest_output,
            database=arguments.database,
            host=arguments.host,
            port=arguments.port,
            user=arguments.user,
            postgres_bin_dir=selected_bin_dir,
        )
    except PostgresBackupError as exc:
        print(f"temporal_postgres_backup=failed code={exc.code}", file=sys.stderr)
        return 1
    except Exception:
        print("temporal_postgres_backup=failed code=tool_failed", file=sys.stderr)
        return 1
    print(f"temporal_postgres_backup=passed sha256={manifest['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
