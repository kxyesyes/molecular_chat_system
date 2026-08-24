#!/usr/bin/env python3
"""Restore a Temporal dump only into the fixed isolated verification database."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
from typing import Callable

try:
    from . import backup_temporal_postgres as backup
except ImportError:  # pragma: no cover - direct script execution
    import backup_temporal_postgres as backup


VERIFICATION_DATABASE = "temporal_verify"
RESTORE_TIMEOUT_SECONDS = 300.0
DATABASE_COMMAND_TIMEOUT_SECONDS = 30.0
QUERY_TIMEOUT_SECONDS = 15.0
REQUIRED_TEMPORAL_TABLES = (
    "cluster_metadata",
    "namespaces",
    "executions",
    "current_executions",
    "history_node",
    "history_tree",
    "queue",
    "queue_metadata",
)
_MANIFEST_KEYS = frozenset(
    {
        "database",
        "dump_file",
        "generated_at",
        "postgres_major",
        "schema_version",
        "sha256",
        "size",
        "status",
    }
)
_DUMP_BASENAME = re.compile(
    r"([A-Za-z_][A-Za-z0-9_]{0,62})-[0-9]{8}T[0-9]{6}Z\.dump\Z"
)
_GENERATED_AT = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z\Z"
)
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class PostgresRestoreError(RuntimeError):
    _CODES = frozenset(
        {
            "arguments_invalid",
            "identifier_invalid",
            "verification_database_must_differ",
            "verification_database_invalid",
            "manifest_untrusted",
            "manifest_invalid",
            "dump_untrusted",
            "dump_hash_mismatch",
            "dump_size_mismatch",
            "postgres_major_mismatch",
            "verification_database_exists",
            "required_table_missing",
            "tool_failed",
            "tool_timeout",
            "marker_write_failed",
        }
    )

    def __init__(self, code: str) -> None:
        self.code = code if code in self._CODES else "tool_failed"
        super().__init__(self.code)


class _SafeArgumentParser(argparse.ArgumentParser):
    def error(self, _message: str) -> None:
        raise PostgresRestoreError("arguments_invalid")


Runner = Callable[..., object]


def _map_backup_error(exc: backup.PostgresBackupError) -> PostgresRestoreError:
    if exc.code == "tool_timeout":
        return PostgresRestoreError("tool_timeout")
    if exc.code == "password_missing":
        return PostgresRestoreError("arguments_invalid")
    return PostgresRestoreError("tool_failed")


def _call(
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
    except backup.PostgresBackupError as exc:
        raise _map_backup_error(exc) from exc
    except PostgresRestoreError:
        raise
    except Exception as exc:
        raise PostgresRestoreError("tool_failed") from exc
    if getattr(result, "returncode", None) != 0:
        raise PostgresRestoreError("tool_failed")
    output = getattr(result, "stdout", b"")
    if not isinstance(output, bytes) or len(output) > backup.MAX_TOOL_OUTPUT_BYTES:
        raise PostgresRestoreError("tool_failed")
    return result


def _read_regular_bounded(path: Path, limit: int, code: str) -> bytes:
    flags = os.O_RDONLY | backup._NOFOLLOW | backup._CLOEXEC | backup._BINARY
    try:
        descriptor = os.open(path, flags)
        try:
            before = os.fstat(descriptor)
            entry = os.lstat(path)
            if (
                not stat.S_ISREG(before.st_mode)
                or backup._identity(before) != backup._identity(entry)
                or backup._is_reparse_point(entry)
            ):
                raise PostgresRestoreError(code)
            chunks: list[bytes] = []
            remaining = limit + 1
            while remaining:
                chunk = os.read(descriptor, min(remaining, 64 * 1024))
                if not chunk:
                    break
                chunks.append(chunk)
                remaining -= len(chunk)
            payload = b"".join(chunks)
            after = os.fstat(descriptor)
            if (
                len(payload) > limit
                or backup._full_identity(before) != backup._full_identity(after)
            ):
                raise PostgresRestoreError(code)
            return payload
        finally:
            os.close(descriptor)
    except PostgresRestoreError:
        raise
    except OSError as exc:
        raise PostgresRestoreError(code) from exc


def load_manifest(path: Path) -> tuple[Path, dict[str, object]]:
    lexical = backup._absolute_lexical(Path(path))
    try:
        parent, parent_metadata = backup._trusted_directory(lexical.parent)
        manifest_path = parent / lexical.name
        payload = _read_regular_bounded(
            manifest_path, backup.MAX_MANIFEST_BYTES, "manifest_untrusted"
        )
        backup._revalidate_directory(parent, parent_metadata)
        decoded = json.loads(payload.decode("ascii"))
    except PostgresRestoreError:
        raise
    except (backup.PostgresBackupError, UnicodeError, json.JSONDecodeError) as exc:
        raise PostgresRestoreError("manifest_invalid") from exc
    if type(decoded) is not dict or set(decoded) != _MANIFEST_KEYS:
        raise PostgresRestoreError("manifest_invalid")
    manifest: dict[str, object] = decoded
    try:
        canonical = backup.canonical_json(manifest)
    except backup.PostgresBackupError as exc:
        raise PostgresRestoreError("manifest_invalid") from exc
    if payload != canonical:
        raise PostgresRestoreError("manifest_invalid")
    database = manifest.get("database")
    dump_file = manifest.get("dump_file")
    generated_at = manifest.get("generated_at")
    major = manifest.get("postgres_major")
    size = manifest.get("size")
    digest = manifest.get("sha256")
    if (
        manifest.get("schema_version") != backup.SCHEMA_VERSION
        or type(manifest.get("schema_version")) is not int
        or manifest.get("status") != "passed"
        or type(database) is not str
        or type(dump_file) is not str
        or type(generated_at) is not str
        or type(major) is not int
        or not 1 <= major <= 99
        or type(size) is not int
        or size <= 0
        or type(digest) is not str
        or _SHA256.fullmatch(digest) is None
    ):
        raise PostgresRestoreError("manifest_invalid")
    try:
        backup.validate_identifier(database)
    except backup.PostgresBackupError as exc:
        raise PostgresRestoreError("manifest_invalid") from exc
    match = _DUMP_BASENAME.fullmatch(dump_file)
    if match is None or match.group(1) != database:
        raise PostgresRestoreError("manifest_invalid")
    if _GENERATED_AT.fullmatch(generated_at) is None:
        raise PostgresRestoreError("manifest_invalid")
    try:
        datetime.strptime(generated_at, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as exc:
        raise PostgresRestoreError("manifest_invalid") from exc
    return manifest_path, manifest


def _verify_dump(
    manifest_path: Path, manifest: dict[str, object]
) -> Path:
    dump_path = manifest_path.parent / str(manifest["dump_file"])
    try:
        entry = os.lstat(dump_path)
    except OSError as exc:
        raise PostgresRestoreError("dump_untrusted") from exc
    if (
        not stat.S_ISREG(entry.st_mode)
        or stat.S_ISLNK(entry.st_mode)
        or backup._is_reparse_point(entry)
    ):
        raise PostgresRestoreError("dump_untrusted")
    try:
        digest, size, _identity = backup._hash_open_regular(
            dump_path, backup._identity(entry)
        )
    except backup.PostgresBackupError as exc:
        raise PostgresRestoreError("dump_untrusted") from exc
    if size != manifest["size"]:
        raise PostgresRestoreError("dump_size_mismatch")
    if digest != manifest["sha256"]:
        raise PostgresRestoreError("dump_hash_mismatch")
    return dump_path


def _postgres_major(
    runner: Runner,
    environment: dict[str, str],
) -> int:
    try:
        return backup.postgres_major(
            "pg_restore", runner=runner, environment=environment
        )
    except backup.PostgresBackupError as exc:
        raise _map_backup_error(exc) from exc


def _connection_arguments(host: str, port: int, user: str) -> list[str]:
    return [
        "--host",
        host,
        "--port",
        str(port),
        "--username",
        user,
    ]


def _target_exists(
    runner: Runner,
    environment: dict[str, str],
    connection: list[str],
) -> bool:
    query = (
        "SELECT CASE WHEN EXISTS (SELECT 1 FROM pg_database "
        "WHERE datname = 'temporal_verify') THEN 1 ELSE 0 END;"
    )
    result = _call(
        runner,
        [
            "psql",
            *connection,
            "--dbname",
            "postgres",
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
    output = result.stdout.strip()
    if output not in {b"0", b"1"}:
        raise PostgresRestoreError("tool_failed")
    return output == b"1"


def _drop_target(
    runner: Runner,
    environment: dict[str, str],
    connection: list[str],
) -> None:
    _call(
        runner,
        ["dropdb", *connection, "--if-exists", VERIFICATION_DATABASE],
        environment,
        DATABASE_COMMAND_TIMEOUT_SECONDS,
    )


def _write_marker(path: Path, marker: dict[str, object]) -> None:
    lexical = backup._absolute_lexical(path)
    try:
        parent, parent_metadata = backup._trusted_directory(lexical.parent)
        destination = parent / lexical.name
        temporary = destination.with_name(f".{destination.name}.partial")
        try:
            existing = os.lstat(destination)
        except FileNotFoundError:
            existing_identity: tuple[int, int] | None = None
        except OSError as exc:
            raise PostgresRestoreError("marker_write_failed") from exc
        else:
            if (
                not stat.S_ISREG(existing.st_mode)
                or stat.S_ISLNK(existing.st_mode)
                or backup._is_reparse_point(existing)
            ):
                raise PostgresRestoreError("marker_write_failed")
            existing_identity = backup._identity(existing)
        backup._ensure_absent(temporary)
        descriptor = -1
        temporary_identity: tuple[int, int] | None = None
        try:
            descriptor, temporary_identity = backup._open_new_regular(temporary)
            payload = backup.canonical_json(marker)
            offset = 0
            while offset < len(payload):
                offset += os.write(descriptor, payload[offset:])
            os.fsync(descriptor)
            os.close(descriptor)
            descriptor = -1
            backup._revalidate_directory(parent, parent_metadata)
            if existing_identity is None:
                backup._ensure_absent(destination)
            else:
                current = os.lstat(destination)
                if (
                    backup._identity(current) != existing_identity
                    or not stat.S_ISREG(current.st_mode)
                    or backup._is_reparse_point(current)
                ):
                    raise PostgresRestoreError("marker_write_failed")
            os.replace(temporary, destination)
            backup._fsync_directory(parent)
            published = os.lstat(destination)
            if (
                backup._identity(published) != temporary_identity
                or not stat.S_ISREG(published.st_mode)
                or backup._is_reparse_point(published)
            ):
                raise PostgresRestoreError("marker_write_failed")
        finally:
            if descriptor >= 0:
                os.close(descriptor)
            backup._safe_unlink(temporary, temporary_identity)
    except PostgresRestoreError:
        raise
    except (backup.PostgresBackupError, OSError) as exc:
        raise PostgresRestoreError("marker_write_failed") from exc


def verify_restore(
    *,
    manifest_path: Path,
    source_database: str,
    target_database: str,
    host: str,
    port: int,
    user: str,
    drop_verification_database: bool = False,
    runner: Runner = backup.run_command,
    now: datetime | None = None,
) -> dict[str, object]:
    if type(drop_verification_database) is not bool:
        raise PostgresRestoreError("arguments_invalid")
    if now is not None and (not isinstance(now, datetime) or now.tzinfo is None):
        raise PostgresRestoreError("arguments_invalid")
    try:
        source_database = backup.validate_identifier(source_database)
        target_database = backup.validate_identifier(target_database)
        user = backup.validate_identifier(user)
        host = backup.validate_host(host)
        port = backup.validate_port(port)
    except backup.PostgresBackupError as exc:
        raise PostgresRestoreError("identifier_invalid") from exc
    if source_database == target_database:
        raise PostgresRestoreError("verification_database_must_differ")
    if target_database != VERIFICATION_DATABASE:
        raise PostgresRestoreError("verification_database_invalid")
    manifest_path, manifest = load_manifest(Path(manifest_path))
    if source_database != manifest["database"]:
        raise PostgresRestoreError("manifest_invalid")
    dump_path = _verify_dump(manifest_path, manifest)
    try:
        environment = backup._password_environment()
    except backup.PostgresBackupError as exc:
        raise PostgresRestoreError("arguments_invalid") from exc
    current_major = _postgres_major(runner, environment)
    if current_major != manifest["postgres_major"]:
        raise PostgresRestoreError("postgres_major_mismatch")
    connection = _connection_arguments(host, port, user)
    exists = _target_exists(runner, environment, connection)
    if exists and not drop_verification_database:
        raise PostgresRestoreError("verification_database_exists")
    if exists:
        _drop_target(runner, environment, connection)
    created = False
    try:
        _call(
            runner,
            ["createdb", *connection, VERIFICATION_DATABASE],
            environment,
            DATABASE_COMMAND_TIMEOUT_SECONDS,
        )
        created = True
        _call(
            runner,
            [
                "pg_restore",
                "--exit-on-error",
                "--single-transaction",
                "--no-owner",
                "--no-acl",
                *connection,
                "--dbname",
                VERIFICATION_DATABASE,
                str(dump_path),
            ],
            environment,
            RESTORE_TIMEOUT_SECONDS,
        )
        for table in REQUIRED_TEMPORAL_TABLES:
            query = (
                "SELECT CASE WHEN to_regclass('public."
                f"{table}') IS NULL THEN 0 ELSE 1 END;"
            )
            result = _call(
                runner,
                [
                    "psql",
                    *connection,
                    "--dbname",
                    VERIFICATION_DATABASE,
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
            if result.stdout.strip() != b"1":
                raise PostgresRestoreError("required_table_missing")
        checked_at = now or datetime.now(timezone.utc)
        marker: dict[str, object] = {
            "postgres_major": current_major,
            "sha256": manifest["sha256"],
            "source_database": source_database,
            "status": "passed",
            "verification_database": VERIFICATION_DATABASE,
            "verified_at": checked_at.astimezone(timezone.utc)
            .replace(microsecond=0)
            .strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
        _write_marker(manifest_path.parent / "latest-verified.json", marker)
        return marker
    finally:
        if created and drop_verification_database:
            _drop_target(runner, environment, connection)


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-database", required=True)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--user", required=True)
    parser.add_argument("--drop-verification-database", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    try:
        arguments = _parser().parse_args(argv)
        marker = verify_restore(
            manifest_path=arguments.manifest,
            source_database=arguments.source_database,
            target_database=arguments.target_database,
            host=arguments.host,
            port=arguments.port,
            user=arguments.user,
            drop_verification_database=arguments.drop_verification_database,
        )
    except PostgresRestoreError as exc:
        print(f"temporal_postgres_restore=failed code={exc.code}", file=sys.stderr)
        return 1
    except Exception:
        print("temporal_postgres_restore=failed code=tool_failed", file=sys.stderr)
        return 1
    print(f"temporal_postgres_restore=passed sha256={marker['sha256']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
