#!/usr/bin/env python3
"""Verify a Temporal dump through descriptor-confined isolated restore."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import datetime, timezone
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
MAX_MARKER_BYTES = 16 * 1024
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
    stdin_fd: int | None = None,
) -> object:
    try:
        result = runner(
            argv,
            environment=environment,
            timeout=timeout,
            capture_output=capture_output,
            stdin_fd=stdin_fd,
            stdout_fd=None,
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


def _open_regular_at(parent_fd: int, name: str, code: str) -> int:
    try:
        backup._safe_leaf(name, code)
        descriptor = os.open(
            name,
            os.O_RDONLY | backup._NOFOLLOW | backup._CLOEXEC | backup._BINARY,
            dir_fd=parent_fd,
        )
    except (OSError, backup.PostgresBackupError) as exc:
        raise PostgresRestoreError(code) from exc
    try:
        opened = os.fstat(descriptor)
        entry = backup._stat_at(parent_fd, name)
        try:
            backup._require_trusted_regular_metadata(opened)
        except backup.PostgresBackupError as exc:
            raise PostgresRestoreError(code) from exc
        if (
            backup._full_identity(opened) != backup._full_identity(entry)
            or backup._is_reparse_point(entry)
        ):
            raise PostgresRestoreError(code)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


def _read_descriptor_bounded(descriptor: int, limit: int, code: str) -> bytes:
    try:
        os.lseek(descriptor, 0, os.SEEK_SET)
        before = os.fstat(descriptor)
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
        os.lseek(descriptor, 0, os.SEEK_SET)
        return payload
    except PostgresRestoreError:
        raise
    except OSError as exc:
        raise PostgresRestoreError(code) from exc


def _validate_manifest(payload: bytes) -> dict[str, object]:
    try:
        decoded = json.loads(payload.decode("ascii"))
    except (UnicodeError, json.JSONDecodeError) as exc:
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
    return manifest


@dataclass
class _OpenedBackup:
    directory: backup.TrustedDirectory
    manifest_name: str
    manifest: dict[str, object]
    manifest_fd: int
    manifest_version: tuple[int, int, int, int, int]
    dump_name: str
    dump_fd: int
    dump_version: tuple[int, int, int, int, int]

    def close(self) -> None:
        if self.dump_fd >= 0:
            os.close(self.dump_fd)
            self.dump_fd = -1
        if self.manifest_fd >= 0:
            os.close(self.manifest_fd)
            self.manifest_fd = -1
        self.directory.close()


def _open_backup(manifest_path: Path) -> _OpenedBackup:
    try:
        lexical = backup._absolute_lexical(Path(manifest_path))
        manifest_name = backup._safe_leaf(lexical.name, "manifest_untrusted")
        directory = backup.TrustedDirectory(lexical.parent)
    except backup.PostgresBackupError as exc:
        raise PostgresRestoreError("manifest_untrusted") from exc
    manifest_fd = -1
    dump_fd = -1
    ownership_transferred = False
    try:
        manifest_fd = _open_regular_at(
            directory.fd, manifest_name, "manifest_untrusted"
        )
        payload = _read_descriptor_bounded(
            manifest_fd, backup.MAX_MANIFEST_BYTES, "manifest_untrusted"
        )
        manifest = _validate_manifest(payload)
        manifest_version = backup._full_identity(os.fstat(manifest_fd))
        directory.revalidate()
        dump_name = str(manifest["dump_file"])
        dump_fd = _open_regular_at(directory.fd, dump_name, "dump_untrusted")
        try:
            digest, size, _dump_identity, dump_version = backup._hash_descriptor(
                dump_fd, code="dump_invalid", sync=False
            )
        except backup.PostgresBackupError as exc:
            raise PostgresRestoreError("dump_untrusted") from exc
        try:
            backup._require_name_matches_fd(
                directory.fd, dump_name, dump_fd, code="dump_invalid"
            )
        except backup.PostgresBackupError as exc:
            raise PostgresRestoreError("dump_untrusted") from exc
        if size != manifest["size"]:
            raise PostgresRestoreError("dump_size_mismatch")
        if digest != manifest["sha256"]:
            raise PostgresRestoreError("dump_hash_mismatch")
        opened = _OpenedBackup(
            directory=directory,
            manifest_name=manifest_name,
            manifest=manifest,
            manifest_fd=manifest_fd,
            manifest_version=manifest_version,
            dump_name=dump_name,
            dump_fd=dump_fd,
            dump_version=dump_version,
        )
        manifest_fd = -1
        dump_fd = -1
        ownership_transferred = True
        return opened
    except backup.PostgresBackupError as exc:
        raise PostgresRestoreError("manifest_untrusted") from exc
    finally:
        if manifest_fd >= 0:
            os.close(manifest_fd)
        if dump_fd >= 0:
            os.close(dump_fd)
        if not ownership_transferred:
            directory.close()


def _connection_arguments(host: str, port: int, user: str) -> list[str]:
    return ["--host", host, "--port", str(port), "--username", user]


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


def _revalidate_opened_backup(opened: _OpenedBackup) -> None:
    try:
        opened.directory.revalidate()
        backup._require_descriptor_version(
            opened.manifest_fd,
            opened.manifest_version,
            code="manifest_invalid",
        )
        backup._require_name_matches_fd(
            opened.directory.fd,
            opened.manifest_name,
            opened.manifest_fd,
            code="manifest_invalid",
        )
        manifest_payload = _read_descriptor_bounded(
            opened.manifest_fd,
            backup.MAX_MANIFEST_BYTES,
            "manifest_untrusted",
        )
        if manifest_payload != backup.canonical_json(opened.manifest):
            raise PostgresRestoreError("manifest_untrusted")
    except PostgresRestoreError:
        raise
    except (backup.PostgresBackupError, OSError) as exc:
        raise PostgresRestoreError("manifest_untrusted") from exc
    try:
        backup._require_descriptor_version(
            opened.dump_fd,
            opened.dump_version,
            code="dump_invalid",
        )
        backup._require_name_matches_fd(
            opened.directory.fd,
            opened.dump_name,
            opened.dump_fd,
            code="dump_invalid",
        )
        os.lseek(opened.dump_fd, 0, os.SEEK_SET)
    except (backup.PostgresBackupError, OSError) as exc:
        raise PostgresRestoreError("dump_untrusted") from exc


@dataclass(frozen=True)
class _MarkerSnapshot:
    existed: bool
    payload: bytes
    mode: int
    identity: tuple[int, int, int, int, int] | None


def _snapshot_marker(directory: backup.TrustedDirectory, name: str) -> _MarkerSnapshot:
    try:
        descriptor = _open_regular_at(directory.fd, name, "marker_write_failed")
    except PostgresRestoreError:
        try:
            backup._stat_at(directory.fd, name)
        except FileNotFoundError:
            return _MarkerSnapshot(False, b"", 0o600, None)
        raise
    try:
        metadata = os.fstat(descriptor)
        payload = _read_descriptor_bounded(
            descriptor, MAX_MARKER_BYTES, "marker_write_failed"
        )
        return _MarkerSnapshot(
            True,
            payload,
            stat.S_IMODE(metadata.st_mode),
            backup._full_identity(metadata),
        )
    finally:
        os.close(descriptor)


def _rollback_marker(
    directory: backup.TrustedDirectory,
    name: str,
    published_identity: tuple[int, int, int, int, int] | None,
    previous: _MarkerSnapshot,
) -> None:
    if not backup._safe_unlink_at(directory.fd, name, published_identity):
        return
    if not previous.existed:
        return
    try:
        backup._atomic_write_new_at(
            directory,
            name,
            previous.payload,
            mode=previous.mode,
        )
    except backup.PostgresBackupError:
        return


def _write_marker_at(
    directory: backup.TrustedDirectory,
    marker: dict[str, object],
) -> None:
    name = "latest-verified.json"
    temporary_name = f".{name}.partial"
    try:
        previous = _snapshot_marker(directory, name)
        backup._ensure_absent_at(directory.fd, temporary_name)
        descriptor = backup._open_new_regular_at(directory.fd, temporary_name)
    except (backup.PostgresBackupError, PostgresRestoreError) as exc:
        raise PostgresRestoreError("marker_write_failed") from exc
    temporary_identity = backup._full_identity(os.fstat(descriptor))
    published_identity: tuple[int, int, int, int, int] | None = None
    replaced = False
    try:
        payload = backup.canonical_json(marker)
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise PostgresRestoreError("marker_write_failed")
            view = view[written:]
        os.fsync(descriptor)
        if os.fstat(descriptor).st_size != len(payload):
            raise PostgresRestoreError("marker_write_failed")
        if previous.existed:
            current = backup._stat_at(directory.fd, name)
            if backup._full_identity(current) != previous.identity:
                raise PostgresRestoreError("marker_write_failed")
        else:
            backup._ensure_absent_at(directory.fd, name)
        directory.revalidate()
        os.replace(
            temporary_name,
            name,
            src_dir_fd=directory.fd,
            dst_dir_fd=directory.fd,
        )
        replaced = True
        published_identity = temporary_identity
        published_descriptor = os.fstat(descriptor)
        if backup._identity(published_descriptor) != temporary_identity[:2]:
            raise PostgresRestoreError("marker_write_failed")
        published_identity = backup._full_identity(published_descriptor)
        backup._fsync_directory_fd(directory.fd)
        published = backup._stat_at(directory.fd, name)
        if (
            backup._full_identity(published)
            != backup._full_identity(published_descriptor)
            or not stat.S_ISREG(published.st_mode)
            or backup._is_reparse_point(published)
        ):
            raise PostgresRestoreError("marker_write_failed")
        directory.revalidate()
    except (backup.PostgresBackupError, PostgresRestoreError, OSError) as exc:
        if replaced:
            _rollback_marker(directory, name, published_identity, previous)
        raise PostgresRestoreError("marker_write_failed") from exc
    finally:
        try:
            temporary_identity = backup._full_identity(os.fstat(descriptor))
        except OSError:
            pass
        backup._safe_unlink_at(
            directory.fd, temporary_name, temporary_identity, sync=False
        )
        os.close(descriptor)


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
    postgres_bin_dir: Path | None = None,
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
    opened = _open_backup(Path(manifest_path))
    trusted_bin: backup.TrustedPostgresBinDirectory | None = None
    try:
        manifest = opened.manifest
        if source_database != manifest["database"]:
            raise PostgresRestoreError("manifest_invalid")
        try:
            selected_bin_dir = backup._selected_postgres_bin_dir(postgres_bin_dir)
            environment = backup._password_environment(selected_bin_dir)
        except backup.PostgresBackupError as exc:
            raise PostgresRestoreError("arguments_invalid") from exc
        if runner is backup.run_command:
            try:
                trusted_bin = backup.TrustedPostgresBinDirectory(selected_bin_dir)
            except backup.PostgresBackupError as exc:
                raise PostgresRestoreError("tool_failed") from exc
        connection = _connection_arguments(host, port, user)
        try:
            current_major = backup.server_major(
                runner=runner,
                environment=environment,
                connection=connection,
                database="postgres",
            )
        except backup.PostgresBackupError as exc:
            raise _map_backup_error(exc) from exc
        if trusted_bin is not None:
            try:
                trusted_bin.revalidate()
            except backup.PostgresBackupError as exc:
                raise PostgresRestoreError("tool_failed") from exc
        if current_major != manifest["postgres_major"]:
            raise PostgresRestoreError("postgres_major_mismatch")
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
            _revalidate_opened_backup(opened)
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
                ],
                environment,
                RESTORE_TIMEOUT_SECONDS,
                stdin_fd=opened.dump_fd,
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
            _revalidate_opened_backup(opened)
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
            if created and drop_verification_database:
                _drop_target(runner, environment, connection)
                created = False
                _revalidate_opened_backup(opened)
            _write_marker_at(opened.directory, marker)
            return marker
        finally:
            if created and drop_verification_database:
                _drop_target(runner, environment, connection)
    finally:
        if trusted_bin is not None:
            trusted_bin.close()
        opened.close()


def _parser() -> argparse.ArgumentParser:
    parser = _SafeArgumentParser(add_help=True)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source-database", required=True)
    parser.add_argument("--target-database", required=True)
    parser.add_argument("--host", required=True)
    parser.add_argument("--port", required=True, type=int)
    parser.add_argument("--user", required=True)
    parser.add_argument("--postgres-bin-dir", type=Path)
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
            postgres_bin_dir=arguments.postgres_bin_dir,
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
