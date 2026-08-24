#!/usr/bin/env python3
"""Create one atomic, content-verified Temporal PostgreSQL logical backup."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys
import tempfile
from typing import Callable


SCHEMA_VERSION = 1
BACKUP_TIMEOUT_SECONDS = 300.0
VERSION_TIMEOUT_SECONDS = 10.0
MAX_TOOL_OUTPUT_BYTES = 4096
MAX_MANIFEST_BYTES = 16 * 1024
MAX_PASSWORD_BYTES = 1024
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,62}\Z")
_HOST = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\Z")
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_VERSION = re.compile(rb"[^\r\n]*\(PostgreSQL\) ([1-9][0-9]*)(?:\.[0-9]+)*\r?\n?\Z")
_FILE_ATTRIBUTE_REPARSE_POINT = 0x400
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_CLOEXEC = getattr(os, "O_CLOEXEC", 0)
_BINARY = getattr(os, "O_BINARY", 0)


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
    if "\x00" in os.fspath(path):
        raise PostgresBackupError("output_untrusted")
    return Path(os.path.abspath(os.fspath(path)))


def _trusted_directory(path: Path) -> tuple[Path, os.stat_result]:
    lexical = _absolute_lexical(path)
    anchor = Path(lexical.anchor)
    current = anchor
    try:
        for component in lexical.parts[1:]:
            if component in {"", ".", ".."}:
                raise PostgresBackupError("output_untrusted")
            current = current / component
            metadata = os.lstat(current)
            if (
                not stat.S_ISDIR(metadata.st_mode)
                or stat.S_ISLNK(metadata.st_mode)
                or _is_reparse_point(metadata)
            ):
                raise PostgresBackupError("output_untrusted")
        metadata = os.lstat(lexical)
    except PostgresBackupError:
        raise
    except OSError as exc:
        raise PostgresBackupError("output_untrusted") from exc
    return lexical, metadata


def _revalidate_directory(path: Path, expected: os.stat_result) -> None:
    try:
        current = os.lstat(path)
    except OSError as exc:
        raise PostgresBackupError("output_untrusted") from exc
    if (
        _identity(current) != _identity(expected)
        or not stat.S_ISDIR(current.st_mode)
        or stat.S_ISLNK(current.st_mode)
        or _is_reparse_point(current)
    ):
        raise PostgresBackupError("output_untrusted")


def _ensure_absent(path: Path) -> None:
    try:
        os.lstat(path)
    except FileNotFoundError:
        return
    except OSError as exc:
        raise PostgresBackupError("output_untrusted") from exc
    raise PostgresBackupError("destination_exists")


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | _NOFOLLOW | _CLOEXEC
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _safe_unlink(path: Path, expected_identity: tuple[int, int] | None) -> None:
    if expected_identity is None:
        return
    try:
        metadata = os.lstat(path)
    except FileNotFoundError:
        return
    except OSError:
        return
    if _identity(metadata) != expected_identity or not stat.S_ISREG(metadata.st_mode):
        return
    try:
        os.unlink(path)
    except OSError:
        return


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


def _password_environment() -> dict[str, str]:
    password = os.environ.get("TEMPORAL_POSTGRES_PASSWORD")
    if (
        type(password) is not str
        or not password
        or len(password.encode("utf-8", "strict")) > MAX_PASSWORD_BYTES
        or any(character in password for character in ("\x00", "\r", "\n"))
    ):
        raise PostgresBackupError("password_missing")
    return {
        "PATH": os.environ.get("PATH", os.defpath),
        "PGPASSWORD": password,
        "PGCONNECT_TIMEOUT": "10",
    }


def run_command(
    argv: list[str],
    *,
    environment: dict[str, str],
    timeout: float,
    capture_output: bool = False,
) -> object:
    output = tempfile.TemporaryFile() if capture_output else None
    try:
        process = subprocess.Popen(
            argv,
            stdin=subprocess.DEVNULL,
            stdout=output if output is not None else subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=environment,
            shell=False,
            close_fds=True,
        )
        try:
            returncode = process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as exc:
            process.kill()
            process.wait(timeout=5)
            raise PostgresBackupError("tool_timeout") from exc
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


Runner = Callable[..., object]


def _invoke(
    runner: Runner,
    argv: list[str],
    environment: dict[str, str],
    timeout: float,
    *,
    capture_output: bool = False,
) -> object:
    try:
        result = runner(
            argv,
            environment=environment,
            timeout=timeout,
            capture_output=capture_output,
        )
    except PostgresBackupError:
        raise
    except Exception as exc:
        raise PostgresBackupError("tool_failed") from exc
    if getattr(result, "returncode", None) != 0:
        raise PostgresBackupError("tool_failed")
    return result


def postgres_major(
    executable: str,
    *,
    runner: Runner,
    environment: dict[str, str],
) -> int:
    result = _invoke(
        runner,
        [executable, "--version"],
        environment,
        VERSION_TIMEOUT_SECONDS,
        capture_output=True,
    )
    output = getattr(result, "stdout", b"")
    if not isinstance(output, bytes) or len(output) > MAX_TOOL_OUTPUT_BYTES:
        raise PostgresBackupError("tool_output_invalid")
    match = _VERSION.fullmatch(output)
    if match is None:
        raise PostgresBackupError("tool_output_invalid")
    major = int(match.group(1))
    if not 1 <= major <= 99:
        raise PostgresBackupError("tool_output_invalid")
    return major


def _open_new_regular(path: Path) -> tuple[int, tuple[int, int]]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | _NOFOLLOW | _CLOEXEC | _BINARY
    try:
        descriptor = os.open(path, flags, 0o600)
        # The create mode is already no broader than 0600.  On POSIX, set the
        # exact mode through the opened descriptor so a path replacement can
        # never redirect chmod.  Windows has no descriptor chmod and does not
        # implement POSIX permission bits.
        if hasattr(os, "fchmod"):
            os.fchmod(descriptor, 0o600)
        metadata = os.fstat(descriptor)
        entry = os.lstat(path)
        if (
            not stat.S_ISREG(metadata.st_mode)
            or _identity(metadata) != _identity(entry)
            or _is_reparse_point(entry)
        ):
            raise PostgresBackupError("output_untrusted")
        return descriptor, _identity(metadata)
    except FileExistsError as exc:
        raise PostgresBackupError("destination_exists") from exc
    except PostgresBackupError:
        raise
    except OSError as exc:
        raise PostgresBackupError("atomic_write_failed") from exc


def _hash_open_regular(
    path: Path,
    expected_identity: tuple[int, int],
    *,
    sync: bool = False,
) -> tuple[str, int, tuple[int, int]]:
    flags = (
        (os.O_RDWR if sync else os.O_RDONLY) | _NOFOLLOW | _CLOEXEC | _BINARY
    )
    try:
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            entry = os.lstat(path)
            if (
                not stat.S_ISREG(before.st_mode)
                or _identity(before) != expected_identity
                or _identity(entry) != expected_identity
                or _is_reparse_point(entry)
            ):
                raise PostgresBackupError("dump_invalid")
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
                raise PostgresBackupError("dump_invalid")
            if sync:
                os.fsync(descriptor)
            return digest.hexdigest(), size, _identity(after)
        finally:
            os.close(descriptor)
    except PostgresBackupError:
        raise
    except OSError as exc:
        raise PostgresBackupError("dump_invalid") from exc


def _write_new_atomic(
    destination: Path,
    payload: bytes,
    *,
    parent_metadata: os.stat_result,
) -> tuple[int, int]:
    temporary = destination.with_name(f".{destination.name}.partial")
    _ensure_absent(destination)
    _ensure_absent(temporary)
    descriptor = -1
    temporary_identity: tuple[int, int] | None = None
    try:
        descriptor, temporary_identity = _open_new_regular(temporary)
        offset = 0
        while offset < len(payload):
            offset += os.write(descriptor, payload[offset:])
        os.fsync(descriptor)
        metadata = os.fstat(descriptor)
        if metadata.st_size != len(payload):
            raise PostgresBackupError("atomic_write_failed")
        os.close(descriptor)
        descriptor = -1
        _revalidate_directory(destination.parent, parent_metadata)
        _ensure_absent(destination)
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
        published = os.lstat(destination)
        if (
            _identity(published) != temporary_identity
            or not stat.S_ISREG(published.st_mode)
            or _is_reparse_point(published)
        ):
            raise PostgresBackupError("atomic_write_failed")
        return _identity(published)
    except PostgresBackupError:
        raise
    except OSError as exc:
        raise PostgresBackupError("atomic_write_failed") from exc
    finally:
        if descriptor >= 0:
            os.close(descriptor)
        _safe_unlink(temporary, temporary_identity)


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
) -> dict[str, object]:
    database = validate_identifier(database)
    user = validate_identifier(user)
    host = validate_host(host)
    port = validate_port(port)
    stamp, generated_at = _utc_timestamp(now or datetime.now(timezone.utc))
    environment = _password_environment()
    output_directory, output_metadata = _trusted_directory(Path(output_dir))
    manifest_path = _absolute_lexical(Path(manifest_output))
    manifest_directory, manifest_directory_metadata = _trusted_directory(
        manifest_path.parent
    )
    manifest_path = manifest_directory / manifest_path.name
    if manifest_path.name in {"", ".", ".."}:
        raise PostgresBackupError("output_untrusted")
    dump_name = f"{database}-{stamp}.dump"
    dump_path = output_directory / dump_name
    partial_path = output_directory / f".{dump_name}.partial"
    _ensure_absent(dump_path)
    _ensure_absent(partial_path)
    _ensure_absent(manifest_path)
    _ensure_absent(manifest_path.with_name(f".{manifest_path.name}.partial"))
    major = postgres_major("pg_dump", runner=runner, environment=environment)
    partial_descriptor = -1
    partial_identity: tuple[int, int] | None = None
    published_identity: tuple[int, int] | None = None
    try:
        partial_descriptor, partial_identity = _open_new_regular(partial_path)
        os.close(partial_descriptor)
        partial_descriptor = -1
        argv = [
            "pg_dump",
            "--format=custom",
            "--no-owner",
            "--no-acl",
            "--host",
            host,
            "--port",
            str(port),
            "--username",
            user,
            "--file",
            str(partial_path),
            database,
        ]
        _invoke(runner, argv, environment, BACKUP_TIMEOUT_SECONDS)
        digest, size, partial_identity = _hash_open_regular(
            partial_path, partial_identity, sync=True
        )
        if size <= 0 or _SHA256.fullmatch(digest) is None:
            raise PostgresBackupError("dump_invalid")
        _revalidate_directory(output_directory, output_metadata)
        _ensure_absent(dump_path)
        os.replace(partial_path, dump_path)
        _fsync_directory(output_directory)
        published = os.lstat(dump_path)
        if (
            _identity(published) != partial_identity
            or not stat.S_ISREG(published.st_mode)
            or _is_reparse_point(published)
        ):
            raise PostgresBackupError("dump_invalid")
        published_identity = _identity(published)
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
        _write_new_atomic(
            manifest_path,
            canonical_json(manifest),
            parent_metadata=manifest_directory_metadata,
        )
        return manifest
    except PostgresBackupError:
        _safe_unlink(dump_path, published_identity)
        raise
    except OSError as exc:
        _safe_unlink(dump_path, published_identity)
        raise PostgresBackupError("atomic_write_failed") from exc
    finally:
        if partial_descriptor >= 0:
            os.close(partial_descriptor)
        _safe_unlink(partial_path, partial_identity)


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--database", required=True)
    parser.add_argument("--user", required=True)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--manifest-output", required=True, type=Path)
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        validate_identifier(arguments.database)
        validate_identifier(arguments.user)
        validate_host(arguments.host)
        validate_port(arguments.port)
        _password_environment()
        _trusted_directory(arguments.output_dir)
        _trusted_directory(arguments.manifest_output.parent)
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
