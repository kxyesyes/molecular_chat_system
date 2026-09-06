from __future__ import annotations

import ctypes
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
from types import SimpleNamespace
from typing import Callable
import urllib.error

import pytest

from scripts import backup_temporal_postgres as postgres_backup
from scripts import manage_temporal_canary as manager
from scripts import observe_temporal_canary as observer
from scripts import restore_temporal_postgres as postgres_restore
from src.task_runtime.models import TaskStatus
from src.task_runtime.observation import ObservationPolicy, build_observation_report
from src.task_runtime.rollout import RolloutEvidence
from src.task_runtime.store import ReadOnlyTemporalObservationStore, TaskStore


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANAGE_SCRIPT = PROJECT_ROOT / "scripts" / "manage_temporal_canary.py"
OBSERVE_SCRIPT = PROJECT_ROOT / "scripts" / "observe_temporal_canary.py"
PYTHON = sys.executable
BACKUP_SCRIPT = PROJECT_ROOT / "scripts" / "backup_temporal_postgres.py"
RESTORE_SCRIPT = PROJECT_ROOT / "scripts" / "restore_temporal_postgres.py"
POSIX_DESCRIPTOR_IO = (
    os.name == "posix"
    and bool(getattr(os, "O_DIRECTORY", 0))
    and bool(getattr(os, "O_NOFOLLOW", 0))
    and os.open in os.supports_dir_fd
    and os.stat in os.supports_dir_fd
    and os.stat in os.supports_follow_symlinks
    and os.unlink in os.supports_dir_fd
)
requires_posix_descriptor_io = pytest.mark.skipif(
    not POSIX_DESCRIPTOR_IO,
    reason="POSIX descriptor-relative PostgreSQL operator I/O required",
)


@pytest.fixture(scope="module", autouse=True)
def restrictive_posix_umask() -> object:
    if os.name != "posix":
        yield
        return
    previous = os.umask(0o022)
    try:
        yield
    finally:
        os.umask(previous)


@pytest.fixture
def postgres_trusted_tmp_path() -> Path:
    if not POSIX_DESCRIPTOR_IO:
        pytest.skip("POSIX descriptor-relative PostgreSQL operator I/O required")
    with tempfile.TemporaryDirectory(
        prefix=".medchat-postgres-test-", dir=Path.home()
    ) as directory:
        path = Path(directory)
        os.chmod(path, 0o700)
        yield path


class _PostgresRunner:
    def __init__(
        self,
        *,
        target_exists: bool = False,
        missing_table: str | None = None,
        server_major: int = 16,
        client_major: int = 15,
        command_hook: Callable[[tuple[str, ...], int | None, int | None], None]
        | None = None,
    ) -> None:
        self.target_exists = target_exists
        self.missing_table = missing_table
        self.server_major = server_major
        self.client_major = client_major
        self.command_hook = command_hook
        self.calls: list[
            tuple[tuple[str, ...], dict[str, str], float, bool, int | None, int | None]
        ] = []
        self.restore_inputs: list[bytes] = []

    def __call__(
        self,
        argv: list[str],
        *,
        environment: dict[str, str],
        timeout: float,
        capture_output: bool = False,
        stdin_fd: int | None = None,
        stdout_fd: int | None = None,
    ) -> SimpleNamespace:
        self.calls.append(
            (
                tuple(argv),
                dict(environment),
                timeout,
                capture_output,
                stdin_fd,
                stdout_fd,
            )
        )
        executable = argv[0]
        if self.command_hook is not None:
            self.command_hook(tuple(argv), stdin_fd, stdout_fd)
        if argv[1:] == ["--version"]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"{executable} (PostgreSQL) {self.client_major}.4\n".encode(
                    "ascii"
                ),
            )
        if executable == "psql" and "server_version_num" in argv[-1]:
            return SimpleNamespace(
                returncode=0,
                stdout=f"{self.server_major}\n".encode("ascii"),
            )
        if executable == "pg_dump":
            assert stdout_fd is not None
            os.write(stdout_fd, b"PGDMP\x01temporal-logical-backup")
            return SimpleNamespace(returncode=0, stdout=b"")
        if executable == "pg_restore":
            assert stdin_fd is not None
            original_offset = os.lseek(stdin_fd, 0, os.SEEK_CUR)
            os.lseek(stdin_fd, 0, os.SEEK_SET)
            self.restore_inputs.append(os.read(stdin_fd, 1024 * 1024))
            os.lseek(stdin_fd, original_offset, os.SEEK_SET)
            return SimpleNamespace(returncode=0, stdout=b"")
        if executable == "psql" and "pg_database" in argv[-1]:
            return SimpleNamespace(
                returncode=0,
                stdout=b"1\n" if self.target_exists else b"0\n",
            )
        if executable == "psql" and "to_regclass" in argv[-1]:
            expected = (
                b"0\n"
                if self.missing_table is not None and self.missing_table in argv[-1]
                else b"1\n"
            )
            return SimpleNamespace(returncode=0, stdout=expected)
        return SimpleNamespace(returncode=0, stdout=b"")

    @property
    def argv(self) -> list[str]:
        return [
            argument
            for call, _environment, _timeout, _capture, _stdin, _stdout in self.calls
            for argument in call
        ]


def _backup_fixture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    runner: _PostgresRunner | None = None,
) -> tuple[Path, dict[str, object], _PostgresRunner]:
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    selected_runner = runner or _PostgresRunner()
    manifest_path = tmp_path / "backup-manifest.json"
    manifest = postgres_backup.create_backup(
        output_dir=tmp_path,
        manifest_output=manifest_path,
        database="temporal",
        host="127.0.0.1",
        port=5433,
        user="temporal",
        runner=selected_runner,
        now=datetime(2026, 8, 24, 12, 30, tzinfo=timezone.utc),
    )
    return manifest_path, manifest, selected_runner


@requires_posix_descriptor_io
def test_postgres_backup_uses_child_environment_and_atomic_canonical_manifest(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    manifest_path, manifest, runner = _backup_fixture(tmp_path, monkeypatch)
    password = "runtime-secret"
    dump_path = tmp_path / str(manifest["dump_file"])

    assert dump_path.is_file()
    assert manifest == {
        "database": "temporal",
        "dump_file": "temporal-20260824T123000Z.dump",
        "generated_at": "2026-08-24T12:30:00Z",
        "postgres_major": 16,
        "schema_version": 1,
        "sha256": hashlib.sha256(dump_path.read_bytes()).hexdigest(),
        "size": dump_path.stat().st_size,
        "status": "passed",
    }
    assert manifest_path.read_bytes() == postgres_backup.canonical_json(manifest)
    assert password not in "\0".join(runner.argv)
    database_calls = [
        call
        for call in runner.calls
        if call[0][0] == "pg_dump" and call[0][1:] != ("--version",)
    ]
    assert len(database_calls) == 1
    argv, environment, timeout, capture, stdin_fd, stdout_fd = database_calls[0]
    assert argv == (
        "pg_dump",
        "--format=custom",
        "--no-owner",
        "--no-acl",
        "--host",
        "127.0.0.1",
        "--port",
        "5433",
        "--username",
        "temporal",
        "temporal",
    )
    assert environment["PGPASSWORD"] == password
    assert "TEMPORAL_POSTGRES_PASSWORD" not in environment
    assert timeout == postgres_backup.BACKUP_TIMEOUT_SECONDS
    assert capture is False
    assert stdin_fd is None
    assert stdout_fd is not None
    assert not any(call[0][1:] == ("--version",) for call in runner.calls)
    server_queries = [
        call[0]
        for call in runner.calls
        if call[0][0] == "psql" and "server_version_num" in call[0][-1]
    ]
    assert len(server_queries) == 1
    assert server_queries[0][server_queries[0].index("--dbname") + 1] == "temporal"
    assert not list(tmp_path.glob("*.partial"))
    assert not list(tmp_path.glob(".*.partial"))


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("database", "témporal"),
        ("database", "-temporal"),
        ("database", "a" * 64),
        ("user", "user name"),
        ("user", "用户"),
        ("host", "host/name"),
        ("port", 0),
        ("port", 65536),
        ("port", True),
    ],
)
def test_postgres_backup_rejects_noncanonical_identifiers_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
    value: object,
) -> None:
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    runner = _PostgresRunner()
    arguments: dict[str, object] = {
        "output_dir": tmp_path,
        "manifest_output": tmp_path / "manifest.json",
        "database": "temporal",
        "host": "127.0.0.1",
        "port": 5433,
        "user": "temporal",
        "runner": runner,
        "now": datetime(2026, 8, 24, tzinfo=timezone.utc),
    }
    arguments[field] = value
    with pytest.raises(postgres_backup.PostgresBackupError):
        postgres_backup.create_backup(**arguments)
    assert runner.calls == []
    assert list(tmp_path.iterdir()) == []


def test_postgres_internal_partial_leaf_is_safe_but_traversal_is_rejected() -> None:
    assert postgres_backup._safe_leaf(".manifest.json.partial") == (
        ".manifest.json.partial"
    )
    for unsafe in (".", "..", "../manifest", "dir/manifest", "\\manifest"):
        with pytest.raises(postgres_backup.PostgresBackupError):
            postgres_backup._safe_leaf(unsafe)


def test_postgres_major_comes_from_server_query_not_client_version() -> None:
    runner = _PostgresRunner(server_major=16, client_major=14)
    major = postgres_backup.server_major(
        runner=runner,
        environment={"PATH": os.defpath, "PGPASSWORD": "child-only"},
        connection=["--host", "127.0.0.1", "--port", "5433", "--username", "temporal"],
        database="temporal",
    )
    assert major == 16
    assert len(runner.calls) == 1
    command = runner.calls[0][0]
    assert command[0] == "psql"
    assert "server_version_num" in command[-1]
    assert command[command.index("--dbname") + 1] == "temporal"
    assert command[1:] != ("--version",)


def test_postgres_backup_rejects_missing_password(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = _PostgresRunner()
    monkeypatch.delenv("TEMPORAL_POSTGRES_PASSWORD", raising=False)
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=tmp_path,
            manifest_output=tmp_path / "manifest.json",
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=runner,
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "password_missing"
    assert runner.calls == []


@requires_posix_descriptor_io
def test_postgres_backup_rejects_existing_outputs(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    runner = _PostgresRunner()

    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    existing = tmp_path / "temporal-20260824T000000Z.dump"
    existing.write_bytes(b"sentinel")
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=tmp_path,
            manifest_output=tmp_path / "manifest.json",
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=runner,
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "destination_exists"
    assert existing.read_bytes() == b"sentinel"

    existing.unlink()
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_bytes(b"sentinel-manifest")
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=tmp_path,
            manifest_output=manifest_path,
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=runner,
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "destination_exists"
    assert manifest_path.read_bytes() == b"sentinel-manifest"
    assert runner.calls == []


@requires_posix_descriptor_io
def test_postgres_backup_rejects_symlink_directory(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    runner = _PostgresRunner()

    real_directory = tmp_path / "real"
    real_directory.mkdir(mode=0o700)
    linked_directory = tmp_path / "linked"
    _create_symlink_or_skip(linked_directory, real_directory)
    with pytest.raises(postgres_backup.PostgresBackupError):
        postgres_backup.create_backup(
            output_dir=linked_directory,
            manifest_output=linked_directory / "manifest.json",
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=runner,
            now=datetime(2026, 8, 25, tzinfo=timezone.utc),
        )
    assert not list(real_directory.iterdir())


def test_postgres_cli_errors_are_stable_and_redacted(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    password = "runtime-secret"
    sensitive_path = tmp_path / password / "manifest.json"
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", password)

    result = postgres_backup.main(
        [
            "--host",
            "127.0.0.1",
            "--port",
            "5433",
            "--database",
            "temporal",
            "--user",
            "temporal",
            "--output-dir",
            str(sensitive_path.parent),
            "--manifest-output",
            str(sensitive_path),
            "--dry-run",
        ]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "temporal_postgres_backup=failed code=output_untrusted\n"
    assert password not in captured.out + captured.err
    assert str(tmp_path) not in captured.out + captured.err

    result = postgres_restore.main(
        [
            "--manifest",
            str(sensitive_path),
            "--source-database",
            "temporal",
            "--target-database",
            "temporal_verify",
            "--host",
            "127.0.0.1",
            "--port",
            "5433",
            "--user",
            "temporal",
        ]
    )
    captured = capsys.readouterr()
    assert result == 1
    assert captured.out == ""
    assert captured.err == "temporal_postgres_restore=failed code=manifest_untrusted\n"
    assert password not in captured.out + captured.err
    assert str(tmp_path) not in captured.out + captured.err


@pytest.mark.skipif(POSIX_DESCRIPTOR_IO, reason="non-POSIX fail-closed contract")
def test_postgres_backup_fails_closed_without_descriptor_relative_io(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    runner = _PostgresRunner()
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=tmp_path,
            manifest_output=tmp_path / "manifest.json",
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=runner,
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "output_untrusted"
    assert runner.calls == []
    assert list(tmp_path.iterdir()) == []


def test_postgres_restore_closes_trusted_directory_when_manifest_open_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    instances: list[SimpleNamespace] = []

    class FakeDirectory:
        def __init__(self, _path: Path) -> None:
            self.fd = 123
            self.closed = False
            instances.append(self)

        def close(self) -> None:
            self.closed = True

    def fail_manifest_open(_parent_fd: int, _name: str, code: str) -> int:
        raise postgres_restore.PostgresRestoreError(code)

    monkeypatch.setattr(postgres_backup, "TrustedDirectory", FakeDirectory)
    monkeypatch.setattr(postgres_restore, "_open_regular_at", fail_manifest_open)
    with pytest.raises(postgres_restore.PostgresRestoreError):
        postgres_restore._open_backup(tmp_path / "manifest.json")
    assert len(instances) == 1
    assert instances[0].closed is True


def test_postgres_dump_descriptor_version_rejects_in_place_mutation(
    tmp_path: Path,
) -> None:
    dump_path = tmp_path / "dump"
    dump_path.write_bytes(b"original")
    descriptor = os.open(dump_path, os.O_RDONLY | getattr(os, "O_BINARY", 0))
    try:
        expected = postgres_backup._full_identity(os.fstat(descriptor))
        dump_path.write_bytes(b"mutated-content")
        with pytest.raises(postgres_backup.PostgresBackupError) as raised:
            postgres_backup._require_descriptor_version(
                descriptor, expected, code="dump_invalid"
            )
        assert raised.value.code == "dump_invalid"
    finally:
        os.close(descriptor)


def test_postgres_trusted_directory_closes_descriptor_when_identity_read_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    monkeypatch.setattr(postgres_backup, "_open_directory_chain", lambda _path: 77)
    monkeypatch.setattr(postgres_backup.os, "fstat", lambda _fd: (_ for _ in ()).throw(OSError()))
    monkeypatch.setattr(postgres_backup.os, "close", closed.append)
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.TrustedDirectory(Path("/trusted"))
    assert raised.value.code == "output_untrusted"
    assert closed == [77]


def test_postgres_posix_metadata_policy_requires_trusted_owner_group_and_modes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(postgres_backup, "_effective_ids", lambda: (1000, 1000))

    def directory(uid: int, gid: int, mode: int) -> SimpleNamespace:
        return SimpleNamespace(
            st_mode=stat.S_IFDIR | mode,
            st_uid=uid,
            st_gid=gid,
            st_file_attributes=0,
        )

    def regular(uid: int, gid: int, mode: int) -> SimpleNamespace:
        return SimpleNamespace(
            st_mode=stat.S_IFREG | mode,
            st_uid=uid,
            st_gid=gid,
            st_file_attributes=0,
        )

    postgres_backup._require_trusted_directory_metadata(
        directory(0, 0, 0o755), final=False
    )
    postgres_backup._require_trusted_directory_metadata(
        directory(1000, 1000, 0o700), final=True
    )
    postgres_backup._require_trusted_regular_metadata(regular(1000, 1000, 0o600))
    postgres_backup._require_trusted_executable_metadata(
        regular(0, 0, 0o755)
    )
    postgres_backup._require_trusted_executable_metadata(
        regular(1000, 1000, 0o700)
    )

    for metadata, final in (
        (directory(2000, 2000, 0o755), False),
        (directory(0, 0, 0o775), False),
        (directory(1000, 1000, 0o755), True),
        (directory(1000, 2000, 0o700), True),
    ):
        with pytest.raises(postgres_backup.PostgresBackupError) as raised:
            postgres_backup._require_trusted_directory_metadata(
                metadata, final=final
            )
        assert raised.value.code == "output_untrusted"

    for metadata in (
        regular(1000, 1000, 0o640),
        regular(2000, 1000, 0o600),
        regular(1000, 2000, 0o600),
    ):
        with pytest.raises(postgres_backup.PostgresBackupError) as raised:
            postgres_backup._require_trusted_regular_metadata(metadata)
        assert raised.value.code == "output_untrusted"

    for metadata in (
        regular(2000, 2000, 0o755),
        regular(1000, 2000, 0o700),
        regular(1000, 1000, 0o775),
        regular(1000, 1000, 0o600),
    ):
        with pytest.raises(postgres_backup.PostgresBackupError) as raised:
            postgres_backup._require_trusted_executable_metadata(metadata)
        assert raised.value.code == "output_untrusted"


def test_postgres_safe_unlink_requires_full_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    metadata = SimpleNamespace(
        st_mode=stat.S_IFREG | 0o600,
        st_dev=7,
        st_ino=11,
        st_size=23,
        st_mtime_ns=29,
        st_ctime_ns=31,
        st_file_attributes=0,
    )
    unlinked: list[tuple[str, int | None]] = []
    monkeypatch.setattr(postgres_backup, "_stat_at", lambda _fd, _name: metadata)
    monkeypatch.setattr(
        postgres_backup.os,
        "unlink",
        lambda name, *, dir_fd=None: unlinked.append((name, dir_fd)),
    )

    assert postgres_backup._safe_unlink_at(
        17, "asset", postgres_backup._full_identity(metadata), sync=False
    )
    assert unlinked == [("asset", 17)]

    changed_ctime = SimpleNamespace(**vars(metadata))
    changed_ctime.st_ctime_ns += 1
    monkeypatch.setattr(
        postgres_backup, "_stat_at", lambda _fd, _name: changed_ctime
    )
    assert not postgres_backup._safe_unlink_at(
        17, "asset", postgres_backup._full_identity(metadata), sync=False
    )
    assert unlinked == [("asset", 17)]


def test_postgres_opened_backup_keeps_manifest_descriptor_until_close(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    closed: list[int] = []
    directory = SimpleNamespace(close=lambda: closed.append(99))
    monkeypatch.setattr(postgres_restore.os, "close", closed.append)
    opened = postgres_restore._OpenedBackup(
        directory=directory,
        manifest_name="manifest.json",
        manifest={"database": "temporal"},
        manifest_fd=41,
        manifest_version=(1, 2, 3, 4, 5),
        dump_name="temporal.dump",
        dump_fd=42,
        dump_version=(6, 7, 8, 9, 10),
    )

    opened.close()

    assert closed == [42, 41, 99]


def test_postgres_joint_revalidation_reports_manifest_identity_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened = SimpleNamespace(
        directory=SimpleNamespace(fd=17, revalidate=lambda: None),
        manifest_fd=41,
        manifest_version=(1, 2, 3, 4, 5),
        manifest_name="manifest.json",
        manifest={"database": "temporal"},
        dump_fd=42,
        dump_version=(6, 7, 8, 9, 10),
        dump_name="temporal.dump",
    )

    def reject_changed_manifest(
        descriptor: int,
        _expected: tuple[int, int, int, int, int],
        *,
        code: str,
    ) -> None:
        if descriptor == opened.manifest_fd:
            raise postgres_backup.PostgresBackupError(code)

    monkeypatch.setattr(
        postgres_backup, "_require_descriptor_version", reject_changed_manifest
    )
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore._revalidate_opened_backup(opened)
    assert raised.value.code == "manifest_untrusted"


def test_postgres_password_environment_never_adopts_ambient_path(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    monkeypatch.setenv("PATH", "CREDENTIAL_STEALER_PATH")
    environment = postgres_backup._password_environment(
        Path("/trusted/postgresql/bin")
    )
    assert environment == {
        "PATH": os.fspath(Path("/trusted/postgresql/bin")),
        "PGPASSWORD": "runtime-secret",
        "PGCONNECT_TIMEOUT": "10",
    }


def test_postgres_timeout_cleanup_failure_preserves_tool_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class TimedOutProcess:
        pid = 424242

        def wait(self, *, timeout: float) -> int:
            raise subprocess.TimeoutExpired("postgres-tool", timeout)

    popen_options: dict[str, object] = {}

    def fake_popen(_argv: list[str], **kwargs: object) -> TimedOutProcess:
        popen_options.update(kwargs)
        return TimedOutProcess()

    monkeypatch.setattr(
        postgres_backup, "_process_groups_available", lambda: True, raising=False
    )
    monkeypatch.setattr(postgres_backup.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(
        postgres_backup,
        "_terminate_process_group",
        lambda _process: (_ for _ in ()).throw(RuntimeError("cleanup failed")),
    )

    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.run_command(
            [sys.executable],
            environment={"PATH": "/trusted/postgresql/bin"},
            timeout=0.1,
        )

    assert raised.value.code == "tool_timeout"
    assert popen_options["start_new_session"] is True
    assert popen_options["shell"] is False


def test_postgres_real_runner_executes_fixed_tool_from_validated_bin_directory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned: list[tuple[str, ...]] = []
    spawn_options: list[dict[str, object]] = []
    opened_tools: list[object] = []

    class CompletedProcess:
        pid = 424243

        def wait(self, *, timeout: float) -> int:
            return 0

    class FakeTrustedBin:
        def __init__(self, path: Path) -> None:
            self.path = path

        def revalidate(self) -> None:
            pass

        def open_tool(self, _name: str) -> object:
            tool = FakeTrustedTool()
            opened_tools.append(tool)
            return tool

        def close(self) -> None:
            pass

    class FakeTrustedTool:
        fd = 73
        executable_path = "/proc/self/fd/73"

        def __init__(self) -> None:
            self.revalidations = 0

        def revalidate(self) -> None:
            self.revalidations += 1

        def close(self) -> None:
            pass

    def fake_popen(argv: list[str], **kwargs: object) -> CompletedProcess:
        spawned.append(tuple(argv))
        spawn_options.append(kwargs)
        return CompletedProcess()

    monkeypatch.setattr(
        postgres_backup, "_process_groups_available", lambda: True, raising=False
    )
    monkeypatch.setattr(
        postgres_backup, "TrustedPostgresBinDirectory", FakeTrustedBin
    )
    monkeypatch.setattr(postgres_backup.subprocess, "Popen", fake_popen)

    postgres_backup.run_command(
        ["psql", "--version"],
        environment={"PATH": "/trusted/postgresql/bin"},
        timeout=1.0,
    )

    assert spawned == [("/proc/self/fd/73", "--version")]
    assert spawn_options[0]["pass_fds"] == (73,)
    assert len(opened_tools) == 1
    assert opened_tools[0].revalidations == 2


def test_postgres_tool_replacement_after_spawn_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    process_finished = False

    class CompletedProcess:
        pid = 424244

        def wait(self, *, timeout: float) -> int:
            nonlocal process_finished
            process_finished = True
            return 0

    class FakeTrustedTool:
        fd = 74
        executable_path = "/proc/self/fd/74"

        def revalidate(self) -> None:
            if process_finished:
                raise postgres_backup.PostgresBackupError("output_untrusted")

        def close(self) -> None:
            pass

    class FakeTrustedBin:
        path = Path("/trusted/postgresql/bin")

        def __init__(self, _path: Path) -> None:
            pass

        def revalidate(self) -> None:
            pass

        def open_tool(self, _name: str) -> FakeTrustedTool:
            return FakeTrustedTool()

        def close(self) -> None:
            pass

    monkeypatch.setattr(
        postgres_backup, "_process_groups_available", lambda: True
    )
    monkeypatch.setattr(
        postgres_backup, "TrustedPostgresBinDirectory", FakeTrustedBin
    )
    monkeypatch.setattr(
        postgres_backup.subprocess,
        "Popen",
        lambda _argv, **_kwargs: CompletedProcess(),
    )

    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.run_command(
            ["psql"],
            environment={"PATH": "/trusted/postgresql/bin"},
            timeout=1.0,
        )
    assert raised.value.code == "output_untrusted"


def test_postgres_tool_leaf_open_is_descriptor_relative_and_nofollow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[tuple[str, int, int | None]] = []

    def reject_symlink(
        name: str,
        flags: int,
        *,
        dir_fd: int | None = None,
    ) -> int:
        opened.append((name, flags, dir_fd))
        raise OSError(errno.ELOOP, "redacted")

    trusted = object.__new__(postgres_backup.TrustedPostgresBinDirectory)
    trusted.fd = 91
    nofollow = getattr(os, "O_NOFOLLOW", 0) or 0x20000
    path_only = getattr(os, "O_PATH", 0) or 0x400000
    monkeypatch.setattr(postgres_backup, "_NOFOLLOW", nofollow)
    monkeypatch.setattr(postgres_backup, "_PATH", path_only, raising=False)
    monkeypatch.setattr(postgres_backup.os, "open", reject_symlink)

    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        trusted.open_tool("psql")

    assert raised.value.code == "output_untrusted"
    assert opened[0][0] == "psql"
    assert opened[0][2] == 91
    assert opened[0][1] & nofollow
    assert opened[0][1] & path_only


def test_postgres_tool_leaf_open_fallback_is_nonblocking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    opened: list[int] = []
    nonblock = getattr(os, "O_NONBLOCK", 0) or 0x4000
    noctty = getattr(os, "O_NOCTTY", 0) or 0x8000
    nofollow = getattr(os, "O_NOFOLLOW", 0) or 0x20000

    def reject_leaf(
        _name: str,
        flags: int,
        *,
        dir_fd: int | None = None,
    ) -> int:
        assert dir_fd == 92
        opened.append(flags)
        raise OSError(errno.ENXIO, "redacted")

    trusted = object.__new__(postgres_backup.TrustedPostgresBinDirectory)
    trusted.fd = 92
    monkeypatch.setattr(postgres_backup, "_PATH", 0)
    monkeypatch.setattr(postgres_backup, "_NONBLOCK", nonblock)
    monkeypatch.setattr(postgres_backup, "_NOCTTY", noctty)
    monkeypatch.setattr(postgres_backup, "_NOFOLLOW", nofollow)
    monkeypatch.setattr(postgres_backup.os, "open", reject_leaf)

    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        trusted.open_tool("psql")

    assert raised.value.code == "output_untrusted"
    assert opened[0] & nonblock
    assert opened[0] & noctty
    assert opened[0] & nofollow


@pytest.mark.skipif(os.name == "posix", reason="non-POSIX fail-closed contract")
def test_postgres_runner_fails_closed_before_spawn_without_process_groups(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spawned = False

    def unexpected_spawn(*_args: object, **_kwargs: object) -> None:
        nonlocal spawned
        spawned = True

    monkeypatch.setattr(postgres_backup.subprocess, "Popen", unexpected_spawn)
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.run_command(
            ["psql"], environment={"PATH": "ignored"}, timeout=0.1
        )
    assert raised.value.code == "tool_failed"
    assert not spawned


@requires_posix_descriptor_io
def test_postgres_backup_rejects_separate_manifest_directory_before_pg_command(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = postgres_trusted_tmp_path / "output"
    manifests = postgres_trusted_tmp_path / "manifests"
    output.mkdir(mode=0o700)
    manifests.mkdir(mode=0o700)
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    runner = _PostgresRunner()

    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=output,
            manifest_output=manifests / "manifest.json",
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=runner,
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )

    assert raised.value.code == "output_untrusted"
    assert runner.calls == []


@requires_posix_descriptor_io
def test_postgres_trust_boundary_rejects_writable_parent_and_bad_leaf_mode(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = postgres_trusted_tmp_path / "parent"
    output = parent / "backups"
    parent.mkdir(mode=0o700)
    output.mkdir(mode=0o700)
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    os.chmod(parent, 0o770)
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=output,
            manifest_output=output / "manifest.json",
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "output_untrusted"

    os.chmod(parent, 0o700)
    os.chmod(output, 0o750)
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=output,
            manifest_output=output / "manifest.json",
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "output_untrusted"

    os.chmod(output, 0o700)
    manifest_path, manifest, _runner = _backup_fixture(output, monkeypatch)
    os.chmod(manifest_path, 0o640)
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
        )
    assert raised.value.code == "manifest_untrusted"

    os.chmod(manifest_path, 0o600)
    os.chmod(output / str(manifest["dump_file"]), 0o640)
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
        )
    assert raised.value.code == "dump_untrusted"


@requires_posix_descriptor_io
def test_postgres_trust_boundary_rejects_wrong_owner_and_group_when_root(
    postgres_trusted_tmp_path: Path,
) -> None:
    if os.geteuid() != 0:
        pytest.skip("root required to manufacture wrong PostgreSQL asset ownership")
    output = postgres_trusted_tmp_path / "backups"
    output.mkdir(mode=0o700)
    os.chown(output, 1, 1)
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.TrustedDirectory(output)
    assert raised.value.code == "output_untrusted"


@requires_posix_descriptor_io
def test_postgres_bin_directory_is_nofollow_owned_and_not_group_writable(
    postgres_trusted_tmp_path: Path,
) -> None:
    postgres_bin = postgres_trusted_tmp_path / "postgres-bin"
    postgres_bin.mkdir(mode=0o700)
    trusted = postgres_backup.TrustedPostgresBinDirectory(postgres_bin)
    trusted.close()

    os.chmod(postgres_bin, 0o770)
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.TrustedPostgresBinDirectory(postgres_bin)
    assert raised.value.code == "output_untrusted"


@requires_posix_descriptor_io
def test_postgres_tool_leaf_rejects_symlink_and_name_replacement(
    postgres_trusted_tmp_path: Path,
) -> None:
    postgres_bin = postgres_trusted_tmp_path / "postgres-bin"
    postgres_bin.mkdir(mode=0o700)
    real_tool = postgres_trusted_tmp_path / "real-psql"
    real_tool.write_bytes(b"tool")
    os.chmod(real_tool, 0o700)
    linked_tool = postgres_bin / "psql"
    _create_symlink_or_skip(linked_tool, real_tool)
    trusted = postgres_backup.TrustedPostgresBinDirectory(postgres_bin)
    try:
        with pytest.raises(postgres_backup.PostgresBackupError) as raised:
            trusted.open_tool("psql")
        assert raised.value.code == "output_untrusted"

        linked_tool.unlink()
        linked_tool.write_bytes(b"original-tool")
        os.chmod(linked_tool, 0o700)
        tool = trusted.open_tool("psql")
        try:
            linked_tool.unlink()
            linked_tool.write_bytes(b"replacement-tool")
            os.chmod(linked_tool, 0o700)
            with pytest.raises(postgres_backup.PostgresBackupError) as raised:
                tool.revalidate()
            assert raised.value.code == "output_untrusted"
        finally:
            tool.close()
    finally:
        trusted.close()


@requires_posix_descriptor_io
def test_postgres_tool_fifo_without_writer_fails_quickly_before_spawn(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if not hasattr(os, "mkfifo") or not hasattr(signal, "setitimer"):
        pytest.skip("POSIX FIFO and interval timer support required")
    postgres_bin = postgres_trusted_tmp_path / "postgres-bin"
    postgres_bin.mkdir(mode=0o700)
    tool = postgres_bin / "psql"
    os.mkfifo(tool, mode=0o700)
    spawned = False

    def unexpected_spawn(*_args: object, **_kwargs: object) -> None:
        nonlocal spawned
        spawned = True

    def interrupt_blocked_open(_signal: int, _frame: object) -> None:
        raise TimeoutError("FIFO tool open blocked")

    monkeypatch.setattr(postgres_backup.subprocess, "Popen", unexpected_spawn)
    previous_handler = signal.signal(signal.SIGALRM, interrupt_blocked_open)
    signal.setitimer(signal.ITIMER_REAL, 2.0)
    try:
        started = time.monotonic()
        with pytest.raises(postgres_backup.PostgresBackupError) as raised:
            postgres_backup.run_command(
                ["psql"],
                environment={"PATH": os.fspath(postgres_bin)},
                timeout=1.0,
            )
        elapsed = time.monotonic() - started
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0.0)
        signal.signal(signal.SIGALRM, previous_handler)

    assert raised.value.code == "output_untrusted"
    assert elapsed < 1.0
    assert not spawned


@requires_posix_descriptor_io
def test_postgres_runner_executes_held_verified_tool_descriptor(
    postgres_trusted_tmp_path: Path,
) -> None:
    if not sys.platform.startswith("linux") or not Path("/proc/self/fd").is_dir():
        pytest.skip("Linux /proc/self/fd executable descriptors required")
    if " " in sys.executable:
        pytest.skip("test interpreter path cannot be represented in a shebang")

    postgres_bin = postgres_trusted_tmp_path / "postgres-bin"
    postgres_bin.mkdir(mode=0o700)
    tool = postgres_bin / "psql"
    tool.write_text(
        f"#!{sys.executable}\nraise SystemExit(0)\n",
        encoding="utf-8",
    )
    os.chmod(tool, 0o700)

    result = postgres_backup.run_command(
        ["psql"],
        environment={"PATH": os.fspath(postgres_bin)},
        timeout=5.0,
    )

    assert result.returncode == 0


def test_postgres_backup_success_path_uses_defined_partial_version() -> None:
    source = BACKUP_SCRIPT.read_text(encoding="utf-8")
    create_backup_source = source[
        source.index("def create_backup(") : source.index("\ndef _parser()")
    ]
    assert "_partial_version" not in create_backup_source
    assert "partial_version" in create_backup_source


@requires_posix_descriptor_io
def test_postgres_restore_rejects_wrong_manifest_and_dump_ownership_when_root(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.geteuid() != 0:
        pytest.skip("root required to manufacture wrong PostgreSQL asset ownership")
    manifest_path, manifest, _runner = _backup_fixture(
        postgres_trusted_tmp_path, monkeypatch
    )
    dump_path = postgres_trusted_tmp_path / str(manifest["dump_file"])

    os.chown(manifest_path, 1, 0)
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore._open_backup(manifest_path)
    assert raised.value.code == "manifest_untrusted"

    os.chown(manifest_path, 0, 0)
    os.chown(dump_path, 0, 1)
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore._open_backup(manifest_path)
    assert raised.value.code == "dump_untrusted"


@requires_posix_descriptor_io
def test_postgres_timeout_terminates_child_and_grandchild_process_group(
    postgres_trusted_tmp_path: Path,
) -> None:
    state = postgres_trusted_tmp_path / "process-group.txt"
    child_code = "\n".join(
        (
            "import os, signal, subprocess, sys, time",
            "try:",
            "    os.setsid()",
            "except PermissionError:",
            "    pass",
            "grandchild = subprocess.Popen([sys.executable, '-c', "
            "'import signal,time;signal.signal(signal.SIGTERM,signal.SIG_IGN);time.sleep(60)'])",
            "with open(sys.argv[1], 'w', encoding='ascii') as stream:",
            "    stream.write(f'{os.getpgrp()} {grandchild.pid}')",
            "signal.signal(signal.SIGTERM, signal.SIG_DFL)",
            "time.sleep(60)",
        )
    )
    process_group: int | None = None
    try:
        with pytest.raises(postgres_backup.PostgresBackupError) as raised:
            postgres_backup.run_command(
                [sys.executable, "-c", child_code, str(state)],
                environment={"PATH": os.defpath},
                timeout=1.0,
            )
        assert raised.value.code == "tool_timeout"
        deadline = time.monotonic() + 3.0
        while not state.exists() and time.monotonic() < deadline:
            time.sleep(0.02)
        process_group = int(state.read_text(encoding="ascii").split()[0])
        assert process_group != os.getpgrp()
        with pytest.raises(ProcessLookupError):
            os.killpg(process_group, 0)
    finally:
        if process_group is not None and process_group != os.getpgrp():
            try:
                os.killpg(process_group, signal.SIGKILL)
            except ProcessLookupError:
                pass


@requires_posix_descriptor_io
def test_postgres_restore_final_drop_failure_never_publishes_passed_marker(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path, _manifest, _backup_runner = _backup_fixture(
        postgres_trusted_tmp_path, monkeypatch
    )

    class FinalDropFailingRunner(_PostgresRunner):
        def __call__(self, argv: list[str], **kwargs: object) -> SimpleNamespace:
            result = super().__call__(argv, **kwargs)
            if Path(argv[0]).name == "dropdb":
                return SimpleNamespace(returncode=1, stdout=b"")
            return result

    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            drop_verification_database=True,
            runner=FinalDropFailingRunner(),
        )

    assert raised.value.code == "tool_failed"
    assert not (postgres_trusted_tmp_path / "latest-verified.json").exists()


@requires_posix_descriptor_io
def test_postgres_backup_rejects_parent_replacement_without_writing_replacement(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    output = tmp_path / "output"
    output.mkdir(mode=0o700)
    displaced = tmp_path / "displaced"
    attacked = False

    def replace_parent(
        argv: tuple[str, ...], _stdin_fd: int | None, _stdout_fd: int | None
    ) -> None:
        nonlocal attacked
        if not attacked and argv[0] == "psql" and "server_version_num" in argv[-1]:
            output.rename(displaced)
            output.mkdir(mode=0o700)
            attacked = True

    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=output,
            manifest_output=output / "manifest.json",
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(command_hook=replace_parent),
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "output_untrusted"
    assert attacked
    assert list(output.iterdir()) == []
    assert list(displaced.iterdir()) == []


@requires_posix_descriptor_io
def test_postgres_backup_parent_replaced_after_manifest_publish_rolls_back_both(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    output = tmp_path / "output"
    displaced = tmp_path / "displaced"
    output.mkdir(mode=0o700)
    real_atomic_write = postgres_backup._atomic_write_new_at

    def publish_then_replace_output(
        directory: object,
        destination_name: str,
        payload: bytes,
        *,
        mode: int = 0o600,
    ) -> tuple[int, int]:
        identity = real_atomic_write(
            directory, destination_name, payload, mode=mode
        )
        if destination_name == "manifest.json":
            output.rename(displaced)
            output.mkdir(mode=0o700)
        return identity

    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    monkeypatch.setattr(
        postgres_backup, "_atomic_write_new_at", publish_then_replace_output
    )
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=output,
            manifest_output=output / "manifest.json",
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "output_untrusted"
    assert list(output.iterdir()) == []
    assert list(displaced.iterdir()) == []


@requires_posix_descriptor_io
def test_postgres_backup_dump_mutated_during_manifest_publish_rolls_back_both(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    real_atomic_write = postgres_backup._atomic_write_new_at
    dump_path = tmp_path / "temporal-20260824T000000Z.dump"

    def publish_then_mutate_dump(
        directory: object,
        destination_name: str,
        payload: bytes,
        *,
        mode: int = 0o600,
    ) -> tuple[int, int]:
        identity = real_atomic_write(
            directory, destination_name, payload, mode=mode
        )
        if destination_name == "manifest.json":
            dump_path.write_bytes(b"same-inode-mutated-dump")
        return identity

    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    monkeypatch.setattr(
        postgres_backup, "_atomic_write_new_at", publish_then_mutate_dump
    )
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=tmp_path,
            manifest_output=tmp_path / "manifest.json",
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "dump_invalid"
    assert list(tmp_path.iterdir()) == []


@requires_posix_descriptor_io
def test_postgres_backup_partial_replacement_never_becomes_dump(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    replacement = b"attacker-controlled-replacement"

    def replace_partial(
        argv: tuple[str, ...], _stdin_fd: int | None, stdout_fd: int | None
    ) -> None:
        if argv[0] != "pg_dump":
            return
        assert stdout_fd is not None
        partial = next(tmp_path.glob(".*.partial"))
        partial.unlink()
        partial.write_bytes(replacement)

    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=tmp_path,
            manifest_output=tmp_path / "manifest.json",
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(command_hook=replace_partial),
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "dump_invalid"
    assert next(tmp_path.glob(".*.partial")).read_bytes() == replacement
    assert not (tmp_path / "temporal-20260824T000000Z.dump").exists()
    assert not (tmp_path / "manifest.json").exists()


@requires_posix_descriptor_io
@pytest.mark.parametrize("attack_phase", ["before_restore", "during_restore"])
def test_postgres_restore_rejects_dump_replaced_after_hash(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    attack_phase: str,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    manifest_path, manifest, _backup_runner = _backup_fixture(tmp_path, monkeypatch)
    dump_path = tmp_path / str(manifest["dump_file"])
    original = dump_path.read_bytes()
    replacement = b"attacker-dump"
    attacked = False

    def replace_dump(
        argv: tuple[str, ...], _stdin_fd: int | None, _stdout_fd: int | None
    ) -> None:
        nonlocal attacked
        selected = (
            argv[0] == "pg_restore"
            if attack_phase == "during_restore"
            else argv[0] == "psql"
        )
        if not attacked and selected:
            dump_path.unlink()
            dump_path.write_bytes(replacement)
            attacked = True

    runner = _PostgresRunner(command_hook=replace_dump)
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=runner,
        )
    assert raised.value.code == "dump_untrusted"
    assert attacked
    if attack_phase == "during_restore":
        assert runner.restore_inputs == [original]
    else:
        assert runner.restore_inputs == []
    assert dump_path.read_bytes() == replacement
    assert original != replacement
    assert not (tmp_path / "latest-verified.json").exists()


@requires_posix_descriptor_io
def test_postgres_restore_validates_and_uses_fixed_isolated_commands(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    manifest_path, manifest, _backup_runner = _backup_fixture(tmp_path, monkeypatch)
    runner = _PostgresRunner()
    marker = postgres_restore.verify_restore(
        manifest_path=manifest_path,
        source_database="temporal",
        target_database="temporal_verify",
        host="127.0.0.1",
        port=5433,
        user="temporal",
        drop_verification_database=False,
        runner=runner,
        now=datetime(2026, 8, 24, 13, tzinfo=timezone.utc),
    )

    assert marker == {
        "postgres_major": 16,
        "sha256": manifest["sha256"],
        "source_database": "temporal",
        "status": "passed",
        "verification_database": "temporal_verify",
        "verified_at": "2026-08-24T13:00:00Z",
    }
    marker_path = tmp_path / "latest-verified.json"
    assert marker_path.read_bytes() == postgres_backup.canonical_json(marker)
    commands = [call[0] for call in runner.calls]
    assert (
        "pg_restore",
        "--exit-on-error",
        "--single-transaction",
        "--no-owner",
        "--no-acl",
        "--host",
        "127.0.0.1",
        "--port",
        "5433",
        "--username",
        "temporal",
        "--dbname",
        "temporal_verify",
    ) in commands
    assert any(command[0] == "createdb" and command[-1] == "temporal_verify" for command in commands)
    checked_tables = {
        table
        for table in postgres_restore.REQUIRED_TEMPORAL_TABLES
        if any(table in argument for command in commands for argument in command)
    }
    assert checked_tables == set(postgres_restore.REQUIRED_TEMPORAL_TABLES)
    assert not any(command[0] == "dropdb" for command in commands)
    assert "runtime-secret" not in "\0".join(runner.argv)
    assert all(call[1]["PGPASSWORD"] == "runtime-secret" for call in runner.calls)
    assert runner.restore_inputs == [(tmp_path / str(manifest["dump_file"])).read_bytes()]
    assert not any(call[0][1:] == ("--version",) for call in runner.calls)
    server_query = next(
        command
        for command in commands
        if command[0] == "psql" and "server_version_num" in command[-1]
    )
    assert server_query[server_query.index("--dbname") + 1] == "postgres"
    assert commands.index(server_query) < next(
        index
        for index, command in enumerate(commands)
        if command[0] in {"createdb", "dropdb"}
    )


@pytest.mark.parametrize(
    ("source", "target", "code"),
    [
        ("temporal", "temporal", "verification_database_must_differ"),
        ("temporal", "temporal_prod", "verification_database_invalid"),
        ("temporal", "temporal_verify2", "verification_database_invalid"),
        ("témporal", "temporal_verify", "identifier_invalid"),
    ],
)
def test_postgres_restore_rejects_unsafe_database_targets_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    source: str,
    target: str,
    code: str,
) -> None:
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    manifest_path = tmp_path / "not-opened.json"
    runner = _PostgresRunner()
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database=source,
            target_database=target,
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=runner,
        )
    assert raised.value.code == code
    assert runner.calls == []


def test_postgres_restore_maps_invalid_manifest_path_to_stable_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    runner = _PostgresRunner()
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=Path("invalid\x00manifest.json"),
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=runner,
        )
    assert raised.value.code == "manifest_untrusted"
    assert runner.calls == []


@pytest.mark.parametrize(
    ("drop_request", "checked_at"),
    [
        ("yes", datetime(2026, 8, 24, tzinfo=timezone.utc)),
        (False, datetime(2026, 8, 24)),
    ],
)
def test_postgres_restore_rejects_ambiguous_drop_and_time_before_subprocess(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    drop_request: object,
    checked_at: datetime,
) -> None:
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    manifest_path = tmp_path / "not-opened.json"
    runner = _PostgresRunner()
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            drop_verification_database=drop_request,
            runner=runner,
            now=checked_at,
        )
    assert raised.value.code == "arguments_invalid"
    assert runner.calls == []


def test_postgres_restore_checks_server_major_before_database_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeOpenedBackup:
        manifest = {"database": "temporal", "postgres_major": 15}

        def __init__(self) -> None:
            self.closed = False

        def close(self) -> None:
            self.closed = True

    opened = FakeOpenedBackup()
    monkeypatch.setattr(postgres_restore, "_open_backup", lambda _path: opened)
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    runner = _PostgresRunner(target_exists=True, server_major=16)

    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=tmp_path / "backup-manifest.json",
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            drop_verification_database=True,
            runner=runner,
        )

    assert raised.value.code == "postgres_major_mismatch"
    commands = [call[0] for call in runner.calls]
    assert not any(command[0] in {"createdb", "dropdb"} for command in commands)
    assert len(commands) == 1
    assert commands[0][0] == "psql"
    assert commands[0][commands[0].index("--dbname") + 1] == "postgres"
    assert all(call[1]["PGPASSWORD"] == "runtime-secret" for call in runner.calls)
    assert "runtime-secret" not in "\0".join(runner.argv)
    assert opened.closed


def test_postgres_restore_revalidates_manifest_and_dump_after_final_drop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dump_path = tmp_path / "held.dump"
    dump_path.write_bytes(b"PGDMP-held")
    dump_fd = os.open(dump_path, os.O_RDONLY | getattr(os, "O_BINARY", 0))

    class FakeOpenedBackup:
        manifest = {
            "database": "temporal",
            "postgres_major": 16,
            "sha256": "a" * 64,
        }
        dump_fd = -1
        directory = SimpleNamespace()

        def __init__(self) -> None:
            self.dump_fd = dump_fd

        def close(self) -> None:
            if self.dump_fd >= 0:
                os.close(self.dump_fd)
                self.dump_fd = -1

    state = {"dropped": False, "marker_writes": 0}

    class DropRecordingRunner(_PostgresRunner):
        def __call__(self, argv: list[str], **kwargs: object) -> SimpleNamespace:
            result = super().__call__(argv, **kwargs)
            if Path(argv[0]).name == "dropdb":
                state["dropped"] = True
            return result

    def revalidate(_opened: object) -> None:
        if state["dropped"]:
            raise postgres_restore.PostgresRestoreError("manifest_untrusted")

    monkeypatch.setattr(
        postgres_restore, "_open_backup", lambda _path: FakeOpenedBackup()
    )
    monkeypatch.setattr(postgres_restore, "_revalidate_opened_backup", revalidate)
    monkeypatch.setattr(
        postgres_restore,
        "_write_marker_at",
        lambda _directory, _marker: state.__setitem__(
            "marker_writes", state["marker_writes"] + 1
        ),
    )
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")

    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=tmp_path / "manifest.json",
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            drop_verification_database=True,
            runner=DropRecordingRunner(),
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )

    assert raised.value.code == "manifest_untrusted"
    assert state == {"dropped": True, "marker_writes": 0}


@requires_posix_descriptor_io
def test_postgres_restore_rejects_hash_size_major_and_noncanonical_manifest(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    manifest_path, manifest, _backup_runner = _backup_fixture(tmp_path, monkeypatch)
    for field, value, code in (
        ("sha256", "0" * 64, "dump_hash_mismatch"),
        ("size", int(manifest["size"]) + 1, "dump_size_mismatch"),
        ("postgres_major", 15, "postgres_major_mismatch"),
    ):
        mutated = dict(manifest)
        mutated[field] = value
        manifest_path.write_bytes(postgres_backup.canonical_json(mutated))
        runner = _PostgresRunner(server_major=16)
        with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
            postgres_restore.verify_restore(
                manifest_path=manifest_path,
                source_database="temporal",
                target_database="temporal_verify",
                host="127.0.0.1",
                port=5433,
                user="temporal",
                runner=runner,
            )
        assert raised.value.code == code
        assert not any(call[0][0] == "pg_restore" for call in runner.calls)
        assert not any(call[0][0] == "createdb" for call in runner.calls)
        if code == "postgres_major_mismatch":
            assert not any(call[0][0] == "dropdb" for call in runner.calls)

    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
        )
    assert raised.value.code == "manifest_invalid"

    nan_manifest = dict(manifest)
    nan_manifest["postgres_major"] = float("nan")
    manifest_path.write_text(
        json.dumps(nan_manifest, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="ascii",
    )
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
        )
    assert raised.value.code == "manifest_invalid"


@requires_posix_descriptor_io
def test_postgres_restore_existing_target_and_drop_are_fail_closed(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    manifest_path, _manifest, _backup_runner = _backup_fixture(tmp_path, monkeypatch)
    existing_runner = _PostgresRunner(target_exists=True)
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=existing_runner,
        )
    assert raised.value.code == "verification_database_exists"
    assert not any(
        call[0][0] in {"dropdb", "createdb"}
        or (call[0][0] == "pg_restore" and call[0][1:] != ("--version",))
        for call in existing_runner.calls
    )

    drop_runner = _PostgresRunner(target_exists=True)
    postgres_restore.verify_restore(
        manifest_path=manifest_path,
        source_database="temporal",
        target_database="temporal_verify",
        host="127.0.0.1",
        port=5433,
        user="temporal",
        drop_verification_database=True,
        runner=drop_runner,
    )
    drops = [call[0] for call in drop_runner.calls if call[0][0] == "dropdb"]
    assert len(drops) == 2
    assert all(command[-1] == "temporal_verify" for command in drops)
    assert not any(command[-1] == "temporal" for command in drops)


@requires_posix_descriptor_io
def test_postgres_restore_missing_required_table_never_writes_verified_marker(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    manifest_path, _manifest, _backup_runner = _backup_fixture(tmp_path, monkeypatch)
    runner = _PostgresRunner(missing_table="history_node")
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=runner,
        )
    assert raised.value.code == "required_table_missing"
    assert not (tmp_path / "latest-verified.json").exists()


@requires_posix_descriptor_io
@pytest.mark.parametrize("published_name", ["dump", "manifest"])
def test_postgres_backup_removes_published_assets_after_directory_fsync_failure(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    published_name: str,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    dump_name = "temporal-20260824T000000Z.dump"
    manifest_name = "manifest.json"
    selected = dump_name if published_name == "dump" else manifest_name
    real_fsync = postgres_backup._fsync_directory_fd
    failed = False

    def fail_after_replace(parent_fd: int) -> None:
        nonlocal failed
        try:
            os.stat(selected, dir_fd=parent_fd, follow_symlinks=False)
        except FileNotFoundError:
            real_fsync(parent_fd)
            return
        if not failed:
            failed = True
            raise OSError("injected post-replace fsync failure")
        real_fsync(parent_fd)

    monkeypatch.setattr(postgres_backup, "_fsync_directory_fd", fail_after_replace)
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=tmp_path,
            manifest_output=tmp_path / manifest_name,
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "atomic_write_failed"
    assert failed
    assert not (tmp_path / dump_name).exists()
    assert not (tmp_path / manifest_name).exists()


@requires_posix_descriptor_io
@pytest.mark.parametrize("published_name", ["dump", "manifest"])
def test_postgres_backup_final_identity_race_preserves_replacement_and_fails(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    published_name: str,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    dump_name = "temporal-20260824T000000Z.dump"
    manifest_name = "manifest.json"
    selected = dump_name if published_name == "dump" else manifest_name
    replacement = b"attacker-replacement"
    real_stat_at = postgres_backup._stat_at
    attacked = False

    def replace_first_published(parent_fd: int, name: str) -> os.stat_result:
        nonlocal attacked
        if name == selected and not attacked:
            try:
                real_stat_at(parent_fd, name)
            except FileNotFoundError:
                pass
            else:
                os.unlink(name, dir_fd=parent_fd)
                descriptor = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_fd,
                )
                try:
                    os.write(descriptor, replacement)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
                attacked = True
        return real_stat_at(parent_fd, name)

    monkeypatch.setattr(postgres_backup, "_stat_at", replace_first_published)
    with pytest.raises(postgres_backup.PostgresBackupError) as raised:
        postgres_backup.create_backup(
            output_dir=tmp_path,
            manifest_output=tmp_path / manifest_name,
            database="temporal",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
            now=datetime(2026, 8, 24, tzinfo=timezone.utc),
        )
    assert raised.value.code == "atomic_write_failed"
    assert attacked
    assert (tmp_path / selected).read_bytes() == replacement
    if published_name == "manifest":
        assert not (tmp_path / dump_name).exists()
    else:
        assert not (tmp_path / manifest_name).exists()


@requires_posix_descriptor_io
def test_postgres_restore_restores_old_marker_after_post_replace_fsync_failure(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    manifest_path, _manifest, _backup_runner = _backup_fixture(tmp_path, monkeypatch)
    marker_path = tmp_path / "latest-verified.json"
    previous = b'{"status":"previous"}\n'
    marker_path.write_bytes(previous)
    os.chmod(marker_path, 0o600)
    real_fsync = postgres_backup._fsync_directory_fd
    failed = False

    def fail_once_after_marker_replace(parent_fd: int) -> None:
        nonlocal failed
        try:
            current = os.stat(
                marker_path.name, dir_fd=parent_fd, follow_symlinks=False
            )
        except FileNotFoundError:
            real_fsync(parent_fd)
            return
        if current.st_size != len(previous) and not failed:
            failed = True
            raise OSError("injected marker fsync failure")
        real_fsync(parent_fd)

    monkeypatch.setattr(
        postgres_backup, "_fsync_directory_fd", fail_once_after_marker_replace
    )
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
        )
    assert raised.value.code == "marker_write_failed"
    assert failed
    assert marker_path.read_bytes() == previous


@requires_posix_descriptor_io
def test_postgres_restore_final_marker_identity_race_preserves_replacement(
    postgres_trusted_tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tmp_path = postgres_trusted_tmp_path
    manifest_path, _manifest, _backup_runner = _backup_fixture(tmp_path, monkeypatch)
    marker_path = tmp_path / "latest-verified.json"
    marker_path.write_bytes(b'{"status":"previous"}\n')
    os.chmod(marker_path, 0o600)
    replacement = b'{"status":"attacker"}\n'
    real_stat_at = postgres_backup._stat_at
    marker_stats = 0

    def replace_before_final_identity(parent_fd: int, name: str) -> os.stat_result:
        nonlocal marker_stats
        if name == marker_path.name:
            marker_stats += 1
            if marker_stats == 3:
                os.unlink(name, dir_fd=parent_fd)
                descriptor = os.open(
                    name,
                    os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=parent_fd,
                )
                try:
                    os.write(descriptor, replacement)
                    os.fsync(descriptor)
                finally:
                    os.close(descriptor)
        return real_stat_at(parent_fd, name)

    monkeypatch.setattr(postgres_backup, "_stat_at", replace_before_final_identity)
    with pytest.raises(postgres_restore.PostgresRestoreError) as raised:
        postgres_restore.verify_restore(
            manifest_path=manifest_path,
            source_database="temporal",
            target_database="temporal_verify",
            host="127.0.0.1",
            port=5433,
            user="temporal",
            runner=_PostgresRunner(),
        )
    assert raised.value.code == "marker_write_failed"
    assert marker_path.read_bytes() == replacement


def test_postgres_scripts_are_bounded_redacted_and_never_use_shell_true() -> None:
    backup_source = BACKUP_SCRIPT.read_text(encoding="utf-8")
    restore_source = RESTORE_SCRIPT.read_text(encoding="utf-8")
    for source in (backup_source, restore_source):
        assert "shell=True" not in source
        assert "timeout=" in source
        assert "Traceback" not in source
    assert "TEMPORAL_POSTGRES_PASSWORD" in backup_source
    assert 'os.environ.get("PATH"' not in backup_source
    assert "start_new_session=True" in backup_source
    assert "os.killpg" in backup_source
    assert "--postgres-bin-dir" in backup_source
    assert "--postgres-bin-dir" in restore_source
    assert "backup._password_environment(" in restore_source
    assert "--exit-on-error" in restore_source
    assert "--single-transaction" in restore_source
    assert "dropdb" in restore_source
    assert "server_version_num" in backup_source
    assert "backup.server_major(" in restore_source
    assert "stdout_fd" in backup_source
    assert "stdin_fd" in restore_source
    for source in (backup_source, restore_source):
        assert "dir_fd=" in source
        assert "src_dir_fd=" in source
        assert "dst_dir_fd=" in source
    assert 'postgres_major("pg_dump"' not in backup_source
    assert 'postgres_major("pg_restore"' not in restore_source


class _StatProxy:
    def __init__(self, metadata: object, **overrides: int):
        self._metadata = metadata
        self._overrides = overrides

    def __getattr__(self, name: str):
        if name in self._overrides:
            return self._overrides[name]
        return getattr(self._metadata, name)


def _shift_windows_handle_ctime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_fstat = os.fstat

    def shifted(file_descriptor: int):
        metadata = original_fstat(file_descriptor)
        return _StatProxy(
            metadata,
            st_ctime_ns=metadata.st_ctime_ns + 1_000_000_000,
        )

    monkeypatch.setattr(os, "fstat", shifted)


def _create_symlink_or_skip(link: Path, target: Path) -> None:
    try:
        link.symlink_to(target)
    except NotImplementedError:
        if os.name == "nt":
            pytest.skip("Windows symlinks unsupported")
        raise
    except OSError as exc:
        if os.name == "nt" and (
            exc.errno in {errno.EPERM, errno.EACCES}
            or getattr(exc, "winerror", None) == 1314
        ):
            pytest.skip("Windows symlink privilege unavailable")
        raise


def _write_env(tmp_path: Path, level: int = 5, *, newline: str = "\n") -> Path:
    path = tmp_path / "medchat.env"
    path.write_bytes(
        (
            f"# keep this comment{newline}"
            f"MEDCHAT_TASK_BACKEND=temporal_canary{newline}"
            f"MEDCHAT_TEMPORAL_CANARY_PERCENT={level}{newline}"
            f"SAFE_UNRELATED=secret-canary{newline}"
        ).encode("utf-8")
    )
    return path


def _sample(index: int) -> dict[str, object]:
    return {
        "task_fingerprint": f"task-{index}",
        "workflow_fingerprint": f"workflow-{index}",
        "status": "succeeded",
        "attempt": 1,
        "terminal_event_count": 1,
        "pose_count": 1,
        "binding_energy": -7.0,
        "binding_energy_valid": True,
        "artifact_exists": True,
        "artifact_hash_matches": True,
        "provenance_complete": True,
        "demo_mode": False,
        "fallback_used": False,
        "latency_seconds": 10.0,
        "error_code": None,
    }


def _write_evidence(
    tmp_path: Path,
    *,
    current_level: int = 5,
    generated_at: datetime | None = None,
) -> Path:
    now = generated_at or datetime.now(timezone.utc)
    report = build_observation_report(
        [_sample(index) for index in range(20)],
        current_level=current_level,
        window_start=now - timedelta(hours=2),
        window_end=now - timedelta(minutes=1),
        worker_health={"available": True, "age_seconds": 1.0},
        infrastructure={"temporal": True, "namespace": True, "queue": True},
        blocking_alerts=[],
        backup_verified=True,
        policy=ObservationPolicy(40.0),
        generated_at=now,
    )
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(report), encoding="utf-8")
    return path


def _run_manage(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [PYTHON, str(MANAGE_SCRIPT), *args],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def test_promote_updates_only_percent_atomically_and_preserves_crlf(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path, newline="\r\n")
    evidence = _write_evidence(tmp_path)

    result = _run_manage(
        "promote",
        "--env-file",
        str(env_file),
        "--to",
        "10",
        "--evidence",
        str(evidence),
        "--reason",
        "observed twenty tasks",
    )

    assert result.returncode == 0
    assert env_file.read_bytes() == (
        b"# keep this comment\r\n"
        b"MEDCHAT_TASK_BACKEND=temporal_canary\r\n"
        b"MEDCHAT_TEMPORAL_CANARY_PERCENT=10\r\n"
        b"SAFE_UNRELATED=secret-canary\r\n"
    )
    assert "SAFE_UNRELATED" not in result.stdout
    assert "secret-canary" not in result.stdout + result.stderr
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows metadata regression")
def test_windows_export_promotion_accepts_handle_path_ctime_difference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = tmp_path / "export.env"
    env_file.write_bytes(
        b"# retained\r\n"
        b"export MEDCHAT_TEMPORAL_CANARY_PERCENT=5\r\n"
    )
    evidence = _write_evidence(tmp_path)
    _shift_windows_handle_ctime(monkeypatch)

    code = manager.main(
        [
            "promote",
            "--env-file",
            str(env_file),
            "--to",
            "10",
            "--evidence",
            str(evidence),
            "--reason",
            "stable windows identity",
        ]
    )

    assert code == 0
    assert env_file.read_bytes() == (
        b"# retained\r\n"
        b"export MEDCHAT_TEMPORAL_CANARY_PERCENT=10\r\n"
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows metadata regression")
def test_windows_repeated_real_file_reads_keep_valid_evidence_and_artifacts(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    env_file = tmp_path / "export.env"
    env_file.write_text(
        "export MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n",
        encoding="utf-8",
    )
    evidence = _write_evidence(tmp_path)
    store, staging, _marker = _observer_fixture(tmp_path, now)
    record = store.records[0]

    for _ in range(100):
        assert manager.read_rollout_config(env_file)[1] == 5
        assert manager._load_evidence(evidence)[0].status == "passed"
        assert observer._artifact_evidence(
            record,
            record.result,
            staging_root=staging,
        ) == (True, True)


@pytest.mark.parametrize(
    "content",
    [
        b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5\nMEDCHAT_TEMPORAL_CANARY_PERCENT=10\n",
        b"SAFE=1\n",
        b"MEDCHAT_TEMPORAL_CANARY_PERCENT=15\n",
        b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5\x00\n",
        b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5\nmalformed-line\n",
        b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n\xff",
    ],
)
def test_manager_rejects_duplicate_missing_unsupported_or_malformed_env(
    tmp_path: Path, content: bytes
) -> None:
    env_file = tmp_path / "bad.env"
    env_file.write_bytes(content)

    result = _run_manage("status", "--env-file", str(env_file))

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert str(env_file) not in result.stdout + result.stderr


@pytest.mark.parametrize(
    "content",
    [
        (
            b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n"
            b"export MEDCHAT_TEMPORAL_CANARY_PERCENT=10\n"
        ),
        (
            b"export MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n"
            b"MEDCHAT_TEMPORAL_CANARY_PERCENT=10\n"
        ),
        (
            b"export MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n"
            b"export MEDCHAT_TEMPORAL_CANARY_PERCENT=10\n"
        ),
    ],
)
def test_manager_rejects_semantic_direct_and_export_duplicates(
    tmp_path: Path, content: bytes
) -> None:
    env_file = tmp_path / "duplicate.env"
    env_file.write_bytes(content)

    result = _run_manage("status", "--env-file", str(env_file))

    assert result.returncode != 0
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "content",
    [
        b"SAFE_UNRELATED=one\nSAFE_UNRELATED=two\nMEDCHAT_TEMPORAL_CANARY_PERCENT=5\n",
        b"SAFE_UNRELATED=one\nexport SAFE_UNRELATED=two\nMEDCHAT_TEMPORAL_CANARY_PERCENT=5\n",
        b"export SAFE_UNRELATED=one\nSAFE_UNRELATED=two\nMEDCHAT_TEMPORAL_CANARY_PERCENT=5\n",
        b"export SAFE_UNRELATED=one\nexport SAFE_UNRELATED=two\nMEDCHAT_TEMPORAL_CANARY_PERCENT=5\n",
    ],
)
def test_manager_rejects_duplicate_unrelated_assignment_names(
    tmp_path: Path,
    content: bytes,
) -> None:
    env_file = tmp_path / "duplicate-unrelated.env"
    env_file.write_bytes(content)

    result = _run_manage("status", "--env-file", str(env_file))

    assert result.returncode != 0
    assert "one" not in result.stdout + result.stderr
    assert "two" not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_manager_promotes_single_export_assignment_without_corrupting_layout(
    tmp_path: Path,
) -> None:
    env_file = tmp_path / "export.env"
    env_file.write_bytes(
        b"# retained comment\r\n"
        b"export MEDCHAT_TEMPORAL_CANARY_PERCENT=5\r\n"
        b"SAFE_UNRELATED=retained\r\n"
    )
    evidence = _write_evidence(tmp_path)

    result = _run_manage(
        "promote", "--env-file", str(env_file), "--to", "10",
        "--evidence", str(evidence), "--reason", "export rollout",
    )

    assert result.returncode == 0
    assert env_file.read_bytes() == (
        b"# retained comment\r\n"
        b"export MEDCHAT_TEMPORAL_CANARY_PERCENT=10\r\n"
        b"SAFE_UNRELATED=retained\r\n"
    )


@pytest.mark.parametrize(
    "assignment",
    [
        b" MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n",
        b"\tMEDCHAT_TEMPORAL_CANARY_PERCENT=5\n",
        b" export MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n",
        b"export  MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n",
        b"export\tMEDCHAT_TEMPORAL_CANARY_PERCENT=5\n",
        b"export MEDCHAT_TEMPORAL_CANARY_PERCENT=5 # inline comments unsupported\n",
    ],
)
def test_manager_rejects_ambiguous_rollout_assignment_whitespace(
    tmp_path: Path, assignment: bytes
) -> None:
    env_file = tmp_path / "ambiguous.env"
    env_file.write_bytes(assignment)

    result = _run_manage("status", "--env-file", str(env_file))

    assert result.returncode != 0
    assert "Traceback" not in result.stderr


@pytest.mark.parametrize(
    "separator",
    [
        "\u2028",
        "\u2029",
        "\u0085",
        "\u000b",
        "\u000c",
        "\u001c",
        "\u001d",
        "\u001e",
        "\r",
    ],
)
def test_manager_rejects_noncanonical_line_separators_without_mutation(
    tmp_path: Path,
    separator: str,
) -> None:
    env_file = tmp_path / "separators.env"
    original = (
        "MEDCHAT_TEMPORAL_CANARY_PERCENT=5"
        f"{separator}SAFE_UNRELATED=retained\n"
    ).encode("utf-8")
    env_file.write_bytes(original)

    result = _run_manage("status", "--env-file", str(env_file))

    assert result.returncode != 0
    assert env_file.read_bytes() == original
    assert not env_file.with_name(env_file.name + ".lock").exists()
    assert "Traceback" not in result.stderr


def test_manager_rejects_oversized_env_and_symlink(tmp_path: Path) -> None:
    oversized = tmp_path / "oversized.env"
    oversized.write_bytes(
        b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n" + b"SAFE=" + b"x" * (1024 * 1024)
    )
    target = _write_env(tmp_path)
    link = tmp_path / "linked.env"
    _create_symlink_or_skip(link, target)

    assert _run_manage("status", "--env-file", str(oversized)).returncode != 0
    assert _run_manage("status", "--env-file", str(link)).returncode != 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission policy")
def test_manager_rejects_group_or_world_writable_env_and_lock(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path)
    env_file.chmod(0o666)
    assert _run_manage("status", "--env-file", str(env_file)).returncode != 0

    env_file.chmod(0o600)
    lock = env_file.with_name(env_file.name + ".lock")
    lock.write_bytes(b"")
    lock.chmod(0o666)
    evidence = _write_evidence(tmp_path)
    result = _run_manage(
        "promote",
        "--env-file",
        str(env_file),
        "--to",
        "10",
        "--evidence",
        str(evidence),
        "--reason",
        "unsafe lock",
    )
    assert result.returncode != 0


@pytest.mark.skipif(os.name != "posix", reason="POSIX trust boundary")
def test_promotion_rejects_trusted_file_in_writable_evidence_parent(
    tmp_path: Path,
) -> None:
    env_parent = tmp_path / "env"
    evidence_parent = tmp_path / "evidence"
    env_parent.mkdir(mode=0o700)
    evidence_parent.mkdir(mode=0o700)
    env_file = _write_env(env_parent)
    evidence = _write_evidence(evidence_parent)
    evidence.chmod(0o600)
    evidence_parent.chmod(0o777)
    try:
        result = _run_manage(
            "promote",
            "--env-file",
            str(env_file),
            "--to",
            "10",
            "--evidence",
            str(evidence),
            "--reason",
            "trusted evidence",
        )
    finally:
        evidence_parent.chmod(0o700)

    assert result.returncode != 0
    assert b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5" in env_file.read_bytes()


@pytest.mark.skipif(os.name != "posix", reason="POSIX trust boundary")
def test_backup_and_output_reject_writable_parent(tmp_path: Path) -> None:
    parent = tmp_path / "hostile"
    parent.mkdir(mode=0o700)
    marker = parent / "latest-verified.json"
    now = datetime.now(timezone.utc)
    marker.write_text(
        json.dumps(
            {
                "status": "passed",
                "sha256": "a" * 64,
                "verified_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    parent.chmod(0o777)
    try:
        assert observer.read_backup_verification(marker, now=now) is None
        with pytest.raises(ValueError):
            observer.write_report_atomic(parent / "report.json", {"status": "partial"})
    finally:
        parent.chmod(0o700)


def test_evidence_boundary_change_after_safe_read_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = _write_env(tmp_path)
    evidence = _write_evidence(tmp_path)
    boundary = manager._capture_trusted_path_boundary(evidence)
    changed = replace(
        boundary,
        file_version=(*boundary.file_version, 1),
    )
    captures = iter((boundary, changed))
    monkeypatch.setattr(
        manager,
        "_capture_trusted_path_boundary",
        lambda _path: next(captures),
    )

    code = manager.main(
        [
            "promote",
            "--env-file",
            str(env_file),
            "--to",
            "10",
            "--evidence",
            str(evidence),
            "--reason",
            "evidence race",
        ]
    )

    assert code == 1
    assert b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5" in env_file.read_bytes()


def test_backup_boundary_change_after_safe_read_is_unknown(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    marker = tmp_path / "latest-verified.json"
    marker.write_text(
        json.dumps(
            {
                "status": "passed",
                "sha256": "a" * 64,
                "verified_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    boundary = observer._capture_trusted_path_boundary(marker)
    changed = replace(
        boundary,
        file_version=(*boundary.file_version, 1),
    )
    captures = iter((boundary, changed))
    monkeypatch.setattr(
        observer,
        "_capture_trusted_path_boundary",
        lambda _path: next(captures),
    )

    assert observer.read_backup_verification(marker, now=now) is None


def test_output_final_boundary_change_is_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    output = tmp_path / "report.json"
    output.write_text("{}", encoding="utf-8")
    boundary = observer._capture_trusted_path_boundary(
        output,
        require_file=False,
    )
    changed = replace(
        boundary,
        parent_identity=(
            *boundary.parent_identity[:-1],
            boundary.parent_identity[-1] + 1,
        ),
    )
    captures = iter((boundary, boundary, boundary, changed))
    monkeypatch.setattr(
        observer,
        "_capture_trusted_path_boundary",
        lambda _path, **_kwargs: next(captures),
    )

    with pytest.raises(ValueError):
        observer.write_report_atomic(output, {"status": "partial"})
    assert not list(tmp_path.glob("*.tmp"))


def _allow_ace(
    ace_type: int,
    mask: int,
    *,
    inherited: bool = False,
    object_flags: int = 0,
) -> bytes:
    class AceHeader(ctypes.LittleEndianStructure):
        _pack_ = 1
        _fields_ = [
            ("ace_type", ctypes.c_ubyte),
            ("ace_flags", ctypes.c_ubyte),
            ("ace_size", ctypes.c_uint16),
        ]

    class AccessMask(ctypes.LittleEndianStructure):
        _pack_ = 1
        _fields_ = [("mask", ctypes.c_uint32)]

    body = bytes(AccessMask(mask))
    if ace_type in {5, 11}:
        body += bytes(AccessMask(object_flags))
        if object_flags & 1:
            body += b"O" * 16
        if object_flags & 2:
            body += b"I" * 16
    body += b"SID"
    size = 4 + len(body)
    return bytes(AceHeader(ace_type, 0x10 if inherited else 0, size)) + body


@pytest.mark.parametrize("ace_type", [0, 5, 9, 11])
@pytest.mark.parametrize("inherited", [False, True])
def test_windows_allow_ace_rejects_untrusted_writer_layouts(
    ace_type: int,
    inherited: bool,
) -> None:
    ace = _allow_ace(
        ace_type,
        0x40000000,
        inherited=inherited,
        object_flags=3 if ace_type in {5, 11} else 0,
    )

    assert not manager._windows_allow_ace_is_trusted(
        ace,
        {"S-1-5-18"},
        sid_decoder=lambda _ace, _offset: "S-1-1-0",
    )
    assert manager._windows_allow_ace_is_trusted(
        ace,
        {"S-1-5-18"},
        sid_decoder=lambda _ace, _offset: "S-1-5-18",
    )


def test_windows_unknown_allow_ace_and_malformed_object_ace_fail_closed() -> None:
    compound = _allow_ace(4, 0x00040000)
    malformed_object = _allow_ace(5, 0x00000002, object_flags=3)[:-10]

    assert not manager._windows_allow_ace_is_trusted(
        compound,
        {"S-1-5-18"},
        sid_decoder=lambda _ace, _offset: "S-1-5-18",
    )
    assert not manager._windows_allow_ace_is_trusted(
        malformed_object,
        {"S-1-5-18"},
        sid_decoder=lambda _ace, _offset: "S-1-5-18",
    )


@pytest.mark.parametrize(
    "mask",
    [
        0x00000002,
        0x00010000,
        0x00040000,
        0x00080000,
        0x10000000,
        0x40000000,
    ],
)
def test_windows_dacl_rejects_every_sensitive_write_mask(mask: int) -> None:
    ace = _allow_ace(0, mask)

    assert not manager._windows_allow_ace_is_trusted(
        ace,
        {"S-1-5-18"},
        sid_decoder=lambda _ace, _offset: "S-1-5-32-545",
    )


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL boundary")
def test_windows_live_security_descriptor_roundtrip(tmp_path: Path) -> None:
    source = tmp_path / "source.txt"
    replacement = tmp_path / "replacement.txt"
    source.write_text("source", encoding="utf-8")
    replacement.write_text("replacement", encoding="utf-8")
    security = manager._capture_windows_security(source)

    assert security.trusted_dacl
    manager._apply_windows_security(replacement, security)
    applied = manager._capture_windows_security(replacement)
    assert applied.owner_sid == security.owner_sid
    assert applied.dacl == security.dacl
    assert applied.dacl_protected == security.dacl_protected




def test_manager_rejects_force_and_has_stable_status_output(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path)
    status_result = _run_manage("status", "--env-file", str(env_file))
    force_result = _run_manage("promote", "--force")

    assert status_result.returncode == 0
    assert status_result.stdout == "status=passed current_level=5\n"
    assert force_result.returncode == 2
    assert "Traceback" not in force_result.stderr


def test_manager_status_never_creates_or_modifies_lock_or_temp_files(
    tmp_path: Path,
) -> None:
    env_file = _write_env(tmp_path)
    lock = env_file.with_name(env_file.name + ".lock")
    before = _database_files_state(env_file)[:3]

    result = _run_manage("status", "--env-file", str(env_file))

    assert result.returncode == 0
    assert _database_files_state(env_file)[:3] == before
    assert not lock.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_manager_status_does_not_open_existing_lock(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path)
    lock = env_file.with_name(env_file.name + ".lock")
    lock.write_bytes(b"LOCK_SENTINEL")
    before = _database_files_state(lock)[:3]

    result = _run_manage("status", "--env-file", str(env_file))

    assert result.returncode == 0
    assert _database_files_state(lock)[:3] == before
    assert lock.read_bytes() == b"LOCK_SENTINEL"


@pytest.mark.skipif(os.name != "posix", reason="POSIX trust boundary")
@pytest.mark.parametrize("mode", [0o777, 0o1777])
def test_manager_mutation_rejects_writable_or_sticky_parent(
    tmp_path: Path,
    mode: int,
) -> None:
    parent = tmp_path / "hostile"
    parent.mkdir()
    env_file = _write_env(parent)
    evidence = _write_evidence(parent)
    parent.chmod(mode)
    try:
        result = _run_manage(
            "promote",
            "--env-file",
            str(env_file),
            "--to",
            "10",
            "--evidence",
            str(evidence),
            "--reason",
            "trusted boundary",
        )
    finally:
        parent.chmod(0o700)

    assert result.returncode != 0
    assert b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5" in env_file.read_bytes()
    assert not env_file.with_name(env_file.name + ".lock").exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX ownership boundary")
def test_manager_mutation_rejects_unexpected_file_owner_where_permitted(
    tmp_path: Path,
) -> None:
    if os.geteuid() != 0:
        pytest.skip("changing file ownership requires root")
    env_file = _write_env(tmp_path)
    evidence = _write_evidence(tmp_path)
    os.chown(env_file, 1, env_file.stat().st_gid)

    result = _run_manage(
        "promote",
        "--env-file",
        str(env_file),
        "--to",
        "10",
        "--evidence",
        str(evidence),
        "--reason",
        "trusted owner",
    )

    assert result.returncode != 0


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL boundary")
def test_windows_promotion_preserves_validated_security_descriptor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = _write_env(tmp_path)
    evidence = _write_evidence(tmp_path)
    security = object()
    applied: list[tuple[Path, object]] = []
    monkeypatch.setattr(
        manager,
        "_validate_windows_mutation_boundary",
        lambda _path: security,
        raising=False,
    )
    monkeypatch.setattr(
        manager,
        "_apply_windows_security",
        lambda path, value: applied.append((path, value)),
        raising=False,
    )

    code = manager.main(
        [
            "promote",
            "--env-file",
            str(env_file),
            "--to",
            "10",
            "--evidence",
            str(evidence),
            "--reason",
            "preserve security",
        ]
    )

    assert code == 0
    assert len(applied) == 1
    assert applied[0][1] is security


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL boundary")
def test_windows_promotion_fails_closed_when_security_cannot_be_preserved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = _write_env(tmp_path)
    evidence = _write_evidence(tmp_path)
    monkeypatch.setattr(
        manager,
        "_validate_windows_mutation_boundary",
        lambda _path: object(),
        raising=False,
    )

    def unavailable(*_args, **_kwargs):
        raise ValueError("PRIVATE_ACL_CANARY")

    monkeypatch.setattr(
        manager,
        "_apply_windows_security",
        unavailable,
        raising=False,
    )

    code = manager.main(
        [
            "promote",
            "--env-file",
            str(env_file),
            "--to",
            "10",
            "--evidence",
            str(evidence),
            "--reason",
            "preserve security",
        ]
    )

    assert code == 1
    assert b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5" in env_file.read_bytes()
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL boundary")
def test_windows_promotion_rejects_untrusted_owner_or_parent_before_lock(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = _write_env(tmp_path)
    evidence = _write_evidence(tmp_path)

    def untrusted(_path):
        raise ValueError("PRIVATE_OWNER_CANARY")

    monkeypatch.setattr(
        manager,
        "_validate_windows_mutation_boundary",
        untrusted,
    )

    code = manager.main(
        [
            "promote",
            "--env-file",
            str(env_file),
            "--to",
            "10",
            "--evidence",
            str(evidence),
            "--reason",
            "trusted owner",
        ]
    )

    assert code == 1
    assert not env_file.with_name(env_file.name + ".lock").exists()
    assert b"MEDCHAT_TEMPORAL_CANARY_PERCENT=5" in env_file.read_bytes()


@pytest.mark.skipif(os.name != "nt", reason="Windows ACL boundary")
@pytest.mark.parametrize("stage", ["validation", "application"])
def test_windows_acl_unexpected_errors_are_normalized_without_leakage(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    stage: str,
) -> None:
    env_file = _write_env(tmp_path)
    evidence = _write_evidence(tmp_path)
    secret = "PRIVATE_WINDOWS_ACL_ERROR_CANARY"

    def unexpected(*_args, **_kwargs):
        raise Exception(secret)

    if stage == "validation":
        monkeypatch.setattr(
            manager,
            "_validate_windows_mutation_boundary",
            unexpected,
        )
    else:
        monkeypatch.setattr(
            manager,
            "_validate_windows_mutation_boundary",
            lambda _path: object(),
        )
        monkeypatch.setattr(manager, "_apply_windows_security", unexpected)

    code = manager.main(
        [
            "promote",
            "--env-file",
            str(env_file),
            "--to",
            "10",
            "--evidence",
            str(evidence),
            "--reason",
            "normalized acl error",
        ]
    )
    output = capsys.readouterr()

    assert code == 1
    assert secret not in output.out + output.err
    assert "Traceback" not in output.err


def test_manager_rejects_secret_like_reason_without_reflecting_it(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path)
    evidence = _write_evidence(tmp_path)
    secret = "sk-" + "operatorsecret" * 4

    result = _run_manage(
        "promote", "--env-file", str(env_file), "--to", "10",
        "--evidence", str(evidence), "--reason", secret,
    )

    assert result.returncode != 0
    assert secret not in result.stdout + result.stderr
    assert "MEDCHAT_TEMPORAL_CANARY_PERCENT=5" in env_file.read_text(encoding="utf-8")


@pytest.mark.parametrize("mutation", ["hash", "level", "stage", "stale", "future"])
def test_promotion_rejects_bad_or_stale_evidence(tmp_path: Path, mutation: str) -> None:
    env_file = _write_env(tmp_path)
    evidence = _write_evidence(tmp_path)
    report = json.loads(evidence.read_text(encoding="utf-8"))
    if mutation == "hash":
        report["sha256"] = "0" * 64
    elif mutation == "level":
        report["current_level"] = 10
    elif mutation == "stage":
        report["stage"] = "preflight"
    elif mutation == "stale":
        evidence = _write_evidence(
            tmp_path, generated_at=datetime.now(timezone.utc) - timedelta(hours=2)
        )
        report = json.loads(evidence.read_text(encoding="utf-8"))
    else:
        evidence = _write_evidence(
            tmp_path, generated_at=datetime.now(timezone.utc) + timedelta(minutes=5)
        )
        report = json.loads(evidence.read_text(encoding="utf-8"))
    if mutation in {"level", "stage"}:
        report.pop("sha256")
        report["sha256"] = hashlib.sha256(
            json.dumps(report, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
    evidence.write_text(json.dumps(report), encoding="utf-8")

    result = _run_manage(
        "promote", "--env-file", str(env_file), "--to", "10",
        "--evidence", str(evidence), "--reason", "release evidence",
    )

    assert result.returncode != 0
    assert "MEDCHAT_TEMPORAL_CANARY_PERCENT=5" in env_file.read_text(encoding="utf-8")
    assert "Traceback" not in result.stderr


def test_promotion_rejects_malformed_oversized_and_linked_evidence(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path)
    malformed = tmp_path / "malformed.json"
    malformed.write_text('{"sha256":"' + "a" * 64 + '","sha256":"' + "b" * 64 + '"}', encoding="utf-8")
    oversized = tmp_path / "oversized.json"
    oversized.write_bytes(b"{" + b"x" * (manager.MAX_EVIDENCE_FILE_BYTES + 1))
    target = _write_evidence(tmp_path)
    linked = tmp_path / "linked-evidence.json"
    _create_symlink_or_skip(linked, target)

    paths = [malformed, oversized, linked]
    for evidence in paths:
        result = _run_manage(
            "promote", "--env-file", str(env_file), "--to", "10",
            "--evidence", str(evidence), "--reason", "invalid evidence test",
        )
        assert result.returncode != 0
        assert "Traceback" not in result.stderr
        assert str(evidence) not in result.stdout + result.stderr
    assert "MEDCHAT_TEMPORAL_CANARY_PERCENT=5" in env_file.read_text(encoding="utf-8")


def test_concurrent_promotions_serialize_and_stale_writer_fails(tmp_path: Path) -> None:
    env_file = _write_env(tmp_path)
    evidence = _write_evidence(tmp_path)
    argv = [
        PYTHON, str(MANAGE_SCRIPT), "promote", "--env-file", str(env_file),
        "--to", "10", "--evidence", str(evidence), "--reason", "parallel release",
    ]

    first = subprocess.Popen(argv, cwd=PROJECT_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    second = subprocess.Popen(argv, cwd=PROJECT_ROOT, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    first_output = first.communicate(timeout=15)
    second_output = second.communicate(timeout=15)

    assert sorted((first.returncode, second.returncode)) == [0, 1]
    assert "MEDCHAT_TEMPORAL_CANARY_PERCENT=10" in env_file.read_text(encoding="utf-8")
    assert "Traceback" not in "".join(first_output + second_output)


def test_identity_change_before_replace_fails_closed_and_cleans_temp(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = _write_env(tmp_path)
    evidence = _write_evidence(tmp_path)
    original = manager._assert_unchanged

    def changed(*args, **kwargs):
        original(*args, **kwargs)
        raise ValueError("unsafe rollout config")

    monkeypatch.setattr(manager, "_assert_unchanged", changed)
    code = manager.main([
        "promote", "--env-file", str(env_file), "--to", "10",
        "--evidence", str(evidence), "--reason", "race detected",
    ])

    assert code == 1
    assert "MEDCHAT_TEMPORAL_CANARY_PERCENT=5" in env_file.read_text(encoding="utf-8")
    assert not list(tmp_path.glob("*.tmp"))


def test_manager_detects_final_swap_after_atomic_replace(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    env_file = _write_env(tmp_path)
    evidence = _write_evidence(tmp_path)
    original_replace = os.replace

    def swapped(source, destination, *args, **kwargs):
        original_replace(source, destination, *args, **kwargs)
        intruder = tmp_path / "intruder.env"
        intruder.write_bytes(b"MEDCHAT_TEMPORAL_CANARY_PERCENT=25\n")
        original_replace(intruder, env_file)

    monkeypatch.setattr(manager.os, "replace", swapped)

    code = manager.main(
        [
            "promote",
            "--env-file",
            str(env_file),
            "--to",
            "10",
            "--evidence",
            str(evidence),
            "--reason",
            "swap race",
        ]
    )

    assert code == 1
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("mutation", ["size", "mtime", "replacement"])
def test_rollout_snapshot_rejects_file_version_or_identity_mutation(
    tmp_path: Path,
    mutation: str,
) -> None:
    env_file = _write_env(tmp_path)
    snapshot = manager._safe_read_snapshot(env_file, manager.MAX_ROLLOUT_BYTES)

    if mutation == "size":
        env_file.write_bytes(env_file.read_bytes() + b"# changed\n")
    elif mutation == "mtime":
        metadata = env_file.stat()
        os.utime(
            env_file,
            ns=(metadata.st_atime_ns, metadata.st_mtime_ns + 1_000_000_000),
        )
    else:
        replacement = tmp_path / "replacement.env"
        replacement.write_bytes(env_file.read_bytes())
        os.replace(replacement, env_file)

    with pytest.raises(ValueError, match="identity changed"):
        manager._assert_unchanged(env_file, snapshot)


@pytest.mark.skipif(os.name != "nt", reason="Windows metadata regression")
def test_windows_rollout_snapshot_rejects_same_size_content_mutation(
    tmp_path: Path,
) -> None:
    env_file = _write_env(tmp_path)
    snapshot = manager._safe_read_snapshot(env_file, manager.MAX_ROLLOUT_BYTES)
    metadata = env_file.stat()
    changed = env_file.read_bytes().replace(b"secret-canary", b"secret-canaru")
    assert len(changed) == len(snapshot.content)
    env_file.write_bytes(changed)
    os.utime(env_file, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))

    with pytest.raises(ValueError, match="identity changed"):
        manager._assert_unchanged(env_file, snapshot)


@pytest.mark.parametrize("field", ["st_size", "st_mtime_ns"])
def test_safe_reader_rejects_handle_version_change_during_read(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    field: str,
) -> None:
    env_file = _write_env(tmp_path)
    original_fstat = os.fstat
    calls = 0

    def changed(file_descriptor: int):
        nonlocal calls
        metadata = original_fstat(file_descriptor)
        calls += 1
        if calls == 2:
            return _StatProxy(
                metadata,
                **{field: getattr(metadata, field) + 1},
            )
        return metadata

    monkeypatch.setattr(os, "fstat", changed)

    with pytest.raises(ValueError, match="changed while reading"):
        manager._safe_read_snapshot(env_file, manager.MAX_ROLLOUT_BYTES)


def test_rollback_never_loads_evidence_or_task_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = _write_env(tmp_path, level=25)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("rollback crossed its boundary")

    monkeypatch.setattr(manager, "_load_evidence", forbidden)
    code = manager.main([
        "rollback", "--env-file", str(env_file), "--reason", "artifact alert",
    ])

    assert code == 0
    assert "MEDCHAT_TEMPORAL_CANARY_PERCENT=0" in env_file.read_text(encoding="utf-8")


def _prom_scalar(value: int | float) -> dict[str, object]:
    return {
        "status": "success",
        "data": {
            "resultType": "vector",
            "result": [{"metric": {}, "value": [1_787_300_000, str(value)]}],
        },
    }


def _prom_alerts(*names: str) -> dict[str, object]:
    return {
        "status": "success",
        "data": {
            "alerts": [
                {"labels": {"alertname": name}, "state": "firing"} for name in names
            ]
        },
    }


class _FakeStore:
    def __init__(self, records: list[SimpleNamespace]):
        self.records = records
        self.list_calls = 0

    def list_temporal_observation(self, *_args, **_kwargs):
        self.list_calls += 1
        return self.records

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        return None

    def terminal_event_count(self, task_id: str):
        return 1


def _observer_fixture(tmp_path: Path, now: datetime) -> tuple[_FakeStore, Path, Path]:
    staging = tmp_path / "staging"
    records = []
    for index in range(20):
        task_id = f"task-{index:02d}"
        relative = "artifacts/docking_pose.pdbqt"
        pose = staging / task_id / relative
        pose.parent.mkdir(parents=True)
        pose.write_bytes(b"REMARK VINA RESULT: -7.0 0.0 0.0\n")
        digest = hashlib.sha256(pose.read_bytes()).hexdigest()
        records.append(
            SimpleNamespace(
                task_id=task_id,
                external_workflow_id=f"workflow-{index:02d}",
                status=TaskStatus.SUCCEEDED,
                attempt=1,
                started_at=(now - timedelta(minutes=3)).isoformat(),
                finished_at=(now - timedelta(minutes=2)).isoformat(),
                error_code=None,
                input={"smiles": "DB_SMILES_CANARY", "prompt": "DB_PROMPT_CANARY"},
                result={
                    "pose_count": 1,
                    "best_energy": -7.0,
                    "completion": {"pose_sha256": digest},
                },
                provenance={
                    "tool_name": "molecular_docking",
                    "tool_version": "vina-1.2.5",
                    "demo_mode": False,
                    "fallback_used": False,
                    "dsn": "postgres://private-dsn",
                    "environment": "ENV_SECRET_CANARY",
                },
                artifacts=[
                    {
                        "artifact_type": "docking_pose",
                        "path": relative,
                        "sha256": digest,
                    }
                ],
                error="RAW_TASK_ERROR_CANARY C:/private/path",
            )
        )
    marker = tmp_path / "latest-verified.json"
    marker.write_text(
        json.dumps({"status": "passed", "sha256": "a" * 64, "verified_at": now.isoformat()}),
        encoding="utf-8",
    )
    return _FakeStore(records), staging, marker


def _passed_prometheus(endpoint: str, query: str | None = None) -> dict[str, object]:
    if endpoint == "alerts":
        return _prom_alerts()
    if query == observer.PROMETHEUS_QUERIES["worker_age_seconds"]:
        return _prom_scalar(5)
    return _prom_scalar(1)


def test_observer_accepts_strict_temporal_result_with_artifact_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.task_runtime.temporal.activities import _summarize_docking_result

    now = datetime.now(timezone.utc)
    started = now - timedelta(minutes=2)
    task_id = "strict-temporal-task"
    workflow_id = "strict-temporal-workflow"
    relative = "artifacts/docking_pose.pdbqt"
    staging = tmp_path / "staging"
    pose = staging / task_id / relative
    pose.parent.mkdir(parents=True)
    content = b"REMARK VINA RESULT: -7.0 0.0 0.0\n"
    pose.write_bytes(content)
    digest = hashlib.sha256(content).hexdigest()
    database = tmp_path / "tasks.sqlite"
    store = TaskStore(database)
    store.create(
        task_id,
        "docking",
        {},
        backend="temporal",
        external_workflow_id=workflow_id,
        now=started - timedelta(seconds=1),
    )
    assert store.claim_running(task_id, attempt=1, now=started)
    activity_terminal = _summarize_docking_result(
        {
            "success": True,
            "status": "succeeded",
            "data": {
                "total_poses": 1,
                "pose_file": relative,
                "best_pose": {
                    "binding_energy": -7.0,
                    "pose_file": relative,
                },
            },
            "artifacts": [
                {
                    "artifact_type": "docking_pose",
                    "path": relative,
                    "metadata": {"sha256": digest},
                }
            ],
            "provenance": {
                "tool_name": "molecular_docking",
                "tool_version": "vina-1.2.5",
                "demo_mode": False,
                "fallback_used": False,
            },
        },
        task_id=task_id,
        attempt=1,
    )
    assert activity_terminal["artifacts"] == [
        {
            "artifact_type": "docking_pose",
            "path": relative,
            "sha256": digest,
        }
    ]
    assert "size" not in activity_terminal["artifacts"][0]
    assert store.project_temporal_terminal(
        task_id,
        TaskStatus.SUCCEEDED,
        result=activity_terminal["result"],
        artifacts=activity_terminal["artifacts"],
        provenance=activity_terminal["provenance"],
        now=now - timedelta(minutes=1),
    )

    persisted = store.get(task_id)
    assert persisted.artifacts == [
        {
            "artifact_type": "docking_pose",
            "path": relative,
            "sha256": digest,
        }
    ]
    assert persisted.result == {
        "pose_count": 1,
        "best_energy": -7.0,
    }
    with ReadOnlyTemporalObservationStore(database) as reader:
        observed = reader.list_temporal_observation(
            now - timedelta(hours=1), now
        )
    assert len(observed) == 1
    assert "pose_file" not in observed[0].result

    marker = tmp_path / "latest-verified.json"
    marker.write_text(
        json.dumps(
            {
                "status": "passed",
                "sha256": "a" * 64,
                "verified_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(observer, "_task_staging_root", lambda: staging)
    report = observer.collect_observation(
        db_path=database,
        current_level=5,
        window_start=now - timedelta(hours=1),
        window_end=now,
        prometheus_url="http://127.0.0.1:9090",
        backup_state=marker,
        baseline_p95_seconds=40.0,
        now=now,
        prometheus_fetcher=_passed_prometheus,
    )

    assert report["sample_count"] == 1
    assert report["gates"]["artifact_integrity"] == "passed"
    encoded = json.dumps(report)
    assert relative not in encoded
    assert str(pose) not in encoded


def test_observer_builds_passed_rollout_evidence_from_safe_projections(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    store, staging, marker = _observer_fixture(tmp_path, now)
    monkeypatch.setattr(observer, "_task_staging_root", lambda: staging)

    report = observer.collect_observation(
        db_path=tmp_path / "DB_PATH_CANARY.sqlite",
        current_level=5,
        window_start=now - timedelta(hours=2),
        window_end=now - timedelta(minutes=1),
        prometheus_url="http://127.0.0.1:9090",
        backup_state=marker,
        baseline_p95_seconds=40.0,
        now=now,
        observation_store_factory=lambda _path: store,
        prometheus_fetcher=_passed_prometheus,
    )

    assert report["status"] == "passed"
    assert report["sample_count"] == 20
    payload = dict(report)
    digest = payload.pop("sha256")
    assert RolloutEvidence.from_payload(payload, digest).status == "passed"
    encoded = json.dumps(report)
    for canary in (
        "DB_PATH_CANARY", "DB_SMILES_CANARY", "DB_PROMPT_CANARY",
        "postgres://private-dsn", "ENV_SECRET_CANARY", "RAW_TASK_ERROR_CANARY",
        "C:/private/path", str(tmp_path),
    ):
        assert canary not in encoded


@pytest.mark.skipif(os.name != "nt", reason="Windows metadata regression")
def test_windows_observer_accepts_valid_artifact_with_handle_path_ctime_difference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = datetime.now(timezone.utc)
    store, staging, marker = _observer_fixture(tmp_path, now)
    monkeypatch.setattr(observer, "_task_staging_root", lambda: staging)
    _shift_windows_handle_ctime(monkeypatch)

    report = observer.collect_observation(
        db_path=tmp_path / "tasks.sqlite",
        current_level=5,
        window_start=now - timedelta(hours=2),
        window_end=now - timedelta(minutes=1),
        prometheus_url="http://127.0.0.1:9090",
        backup_state=marker,
        baseline_p95_seconds=40.0,
        now=now,
        observation_store_factory=lambda _path: store,
        prometheus_fetcher=_passed_prometheus,
    )

    assert report["status"] == "passed"
    assert report["sample_count"] == 20
    assert report["gates"]["artifact_integrity"] == "passed"


@pytest.mark.parametrize(
    "status",
    [TaskStatus.FAILED, TaskStatus.CANCELED, TaskStatus.TIMED_OUT],
)
@pytest.mark.parametrize(
    "provenance",
    [
        None,
        "RAW_PROVENANCE_ERROR_CANARY",
        {"demo_mode": "RAW_PROVENANCE_ERROR_CANARY"},
    ],
)
def test_observer_writes_failed_terminal_samples_with_unknown_provenance(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    status: TaskStatus,
    provenance: object,
) -> None:
    now = datetime.now(timezone.utc)
    store, staging, marker = _observer_fixture(tmp_path, now)
    for record in store.records:
        record.status = status
        record.provenance = provenance
        record.result = {}
        record.artifacts = []
    monkeypatch.setattr(observer, "_task_staging_root", lambda: staging)

    report = observer.collect_observation(
        db_path=tmp_path / "tasks.sqlite",
        current_level=5,
        window_start=now - timedelta(hours=2),
        window_end=now - timedelta(minutes=1),
        prometheus_url="http://127.0.0.1:9090",
        backup_state=marker,
        baseline_p95_seconds=40.0,
        now=now,
        observation_store_factory=lambda _path: store,
        prometheus_fetcher=_passed_prometheus,
    )

    assert report["status"] == "failed"
    assert report["sample_count"] == 20
    assert report["gates"]["provenance_complete"] == "failed"
    assert report["gates"]["real_execution"] == "failed"
    assert "RAW_PROVENANCE_ERROR_CANARY" not in json.dumps(report)


@pytest.mark.parametrize(
    "mutation",
    [
        "extra_pose_artifact",
        "type_alias_conflict",
        "hash_alias_conflict",
        "completion_digest_conflict",
        "size_mismatch",
        "negative_size",
        "boolean_size",
        "float_size",
        "malformed_size",
        "unsafe_path",
    ],
)
def test_observer_succeeded_artifact_requires_authoritative_matching_metadata(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: str,
) -> None:
    now = datetime.now(timezone.utc)
    store, staging, marker = _observer_fixture(tmp_path, now)
    record = store.records[0]
    artifact = record.artifacts[0]
    pose_path = staging / record.task_id / artifact["path"]
    if mutation == "extra_pose_artifact":
        record.artifacts.append(dict(artifact))
    elif mutation == "type_alias_conflict":
        artifact["type"] = "log"
    elif mutation == "hash_alias_conflict":
        artifact["hash"] = "b" * 64
    elif mutation == "completion_digest_conflict":
        record.result["completion"] = {"pose_sha256": "b" * 64}
    elif mutation == "size_mismatch":
        artifact["size"] = pose_path.stat().st_size + 1
    elif mutation == "negative_size":
        artifact["size"] = -1
    elif mutation == "boolean_size":
        artifact["size"] = True
    elif mutation == "float_size":
        artifact["size"] = float(pose_path.stat().st_size)
    elif mutation == "malformed_size":
        artifact["size"] = "RAW_SIZE_CANARY"
    else:
        artifact["path"] = "../RAW_PATH_CANARY/pose.pdbqt"
    monkeypatch.setattr(observer, "_task_staging_root", lambda: staging)

    report = observer.collect_observation(
        db_path=tmp_path / "tasks.sqlite",
        current_level=5,
        window_start=now - timedelta(hours=2),
        window_end=now - timedelta(minutes=1),
        prometheus_url="http://127.0.0.1:9090",
        backup_state=marker,
        baseline_p95_seconds=40.0,
        now=now,
        observation_store_factory=lambda _path: store,
        prometheus_fetcher=_passed_prometheus,
    )

    assert report["status"] == "failed"
    assert report["sample_count"] == 20
    assert report["gates"]["artifact_integrity"] == "failed"
    assert "RAW_SIZE_CANARY" not in json.dumps(report)


def test_observer_accepts_optional_exact_artifact_size(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    store, staging, _marker = _observer_fixture(tmp_path, now)
    record = store.records[0]
    pose = staging / record.task_id / record.artifacts[0]["path"]
    record.artifacts[0]["size"] = pose.stat().st_size

    assert observer._artifact_evidence(
        record,
        record.result,
        staging_root=staging,
    ) == (True, True)


def test_observer_artifact_hash_rejects_same_size_content_mutation(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    store, staging, _marker = _observer_fixture(tmp_path, now)
    record = store.records[0]
    artifact = staging / record.task_id / record.artifacts[0]["path"]
    metadata = artifact.stat()
    original = artifact.read_bytes()
    artifact.write_bytes(b"X" * len(original))
    os.utime(artifact, ns=(metadata.st_atime_ns, metadata.st_mtime_ns))

    assert observer._artifact_evidence(
        record,
        record.result,
        staging_root=staging,
    ) == (True, False)


def test_observer_prometheus_unavailable_is_partial_not_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    store, staging, marker = _observer_fixture(tmp_path, now)
    monkeypatch.setattr(observer, "_task_staging_root", lambda: staging)

    def unavailable(*_args, **_kwargs):
        raise observer.PrometheusUnavailable("RAW_PROM_ERROR_CANARY postgres://private-dsn")

    report = observer.collect_observation(
        db_path=tmp_path / "tasks.sqlite", current_level=5,
        window_start=now - timedelta(hours=2), window_end=now - timedelta(minutes=1),
        prometheus_url="http://127.0.0.1:9090", backup_state=marker,
        baseline_p95_seconds=40.0, now=now,
        observation_store_factory=lambda _path: store, prometheus_fetcher=unavailable,
    )

    assert report["status"] == "partial"
    assert "failed" not in {
        report["gates"]["worker_health"],
        report["gates"]["temporal_available"],
        report["gates"]["blocking_alerts"],
    }
    assert "RAW_PROM_ERROR_CANARY" not in json.dumps(report)


@pytest.mark.parametrize(
    "alert_name",
    [
        "TemporalWorkerHeartbeatStale",
        "TemporalQueueBacklogGrowing",
        "TemporalWorkflowStartUnexpectedErrors",
        "TemporalDuplicateVinaExecution",
        "TemporalMultipleTerminalEvents",
        "TemporalArtifactValidationFailure",
        "TemporalDockingP95TooHigh",
        "TemporalDockingP95BaselineMissing",
        "TemporalRuntimeFailureRateHigh",
        "TemporalPostgresUnavailable",
        "TemporalBackupVerificationStale",
    ],
)
def test_observer_every_release_blocker_alert_is_failed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    alert_name: str,
) -> None:
    now = datetime.now(timezone.utc)
    store, staging, marker = _observer_fixture(tmp_path, now)
    monkeypatch.setattr(observer, "_task_staging_root", lambda: staging)

    def firing(endpoint: str, query: str | None = None):
        if endpoint == "alerts":
            return _prom_alerts(alert_name, "PrivateAlertCanary")
        return _passed_prometheus(endpoint, query)

    report = observer.collect_observation(
        db_path=tmp_path / "tasks.sqlite", current_level=5,
        window_start=now - timedelta(hours=2), window_end=now - timedelta(minutes=1),
        prometheus_url="http://127.0.0.1:9090", backup_state=marker,
        baseline_p95_seconds=40.0, now=now,
        observation_store_factory=lambda _path: store, prometheus_fetcher=firing,
    )

    assert report["status"] == "failed"
    assert report["blocking_alerts"] == [alert_name]
    assert "PrivateAlertCanary" not in json.dumps(report)


def test_observer_explicit_worker_unhealthy_is_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    store, staging, marker = _observer_fixture(tmp_path, now)
    monkeypatch.setattr(observer, "_task_staging_root", lambda: staging)

    def unhealthy(endpoint: str, query: str | None = None):
        if query == observer.PROMETHEUS_QUERIES["worker_ready"]:
            return _prom_scalar(0)
        return _passed_prometheus(endpoint, query)

    report = observer.collect_observation(
        db_path=tmp_path / "tasks.sqlite", current_level=5,
        window_start=now - timedelta(hours=2), window_end=now - timedelta(minutes=1),
        prometheus_url="http://127.0.0.1:9090", backup_state=marker,
        baseline_p95_seconds=40.0, now=now,
        observation_store_factory=lambda _path: store, prometheus_fetcher=unhealthy,
    )

    assert report["status"] == "failed"
    assert report["gates"]["worker_health"] == "failed"


@pytest.mark.parametrize(
    "url",
    [
        "https://127.0.0.1:9090", "http://localhost:9090",
        "http://127.0.0.2:9090", "http://user@127.0.0.1:9090",
        "http://127.0.0.1:9090/unsafe", "http://127.0.0.1:9090#fragment",
    ],
)
def test_observer_rejects_nonliteral_or_unsafe_prometheus_urls(url: str) -> None:
    with pytest.raises(ValueError, match="Prometheus URL"):
        observer.validate_prometheus_url(url)


def test_prometheus_decoder_rejects_malformed_oversized_and_redirects() -> None:
    with pytest.raises(observer.PrometheusUnavailable):
        observer._decode_prometheus_json(b"not-json")
    with pytest.raises(observer.PrometheusUnavailable):
        observer._decode_prometheus_json(b"{}" * (observer.MAX_PROMETHEUS_BYTES + 1))
    class RedirectingOpener:
        def open(self, request, timeout):
            raise urllib.error.HTTPError(
                request.full_url, 302, "redirect", {}, None
            )

    with pytest.raises(observer.PrometheusUnavailable):
        observer._request_prometheus_json(
            "http://127.0.0.1:9090", "alerts", opener=RedirectingOpener()
        )


def test_prometheus_rejects_non_json_content_type() -> None:
    class Response:
        status = 200
        headers = {"Content-Type": "text/html"}

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def getcode(self):
            return 200

        def geturl(self):
            return "http://127.0.0.1:9090/api/v1/alerts"

        def read(self, _limit):
            return json.dumps(_prom_alerts()).encode("utf-8")

    class Opener:
        def open(self, _request, timeout):
            assert timeout == 5.0
            return Response()

    with pytest.raises(observer.PrometheusUnavailable):
        observer._request_prometheus_json(
            "http://127.0.0.1:9090", "alerts", opener=Opener()
        )


def test_injected_reparse_metadata_is_rejected_for_manager_and_output(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_file = _write_env(tmp_path)
    original_manager = manager._is_reparse_point
    monkeypatch.setattr(
        manager,
        "_is_reparse_point",
        lambda metadata: stat.S_ISREG(getattr(metadata, "st_mode", 0))
        or original_manager(metadata),
    )
    with pytest.raises(ValueError):
        manager.read_rollout_config(env_file)

    monkeypatch.undo()
    output = tmp_path / "report.json"
    output.write_text("{}", encoding="utf-8")
    original_observer = observer._is_reparse_point
    monkeypatch.setattr(
        observer,
        "_is_reparse_point",
        lambda metadata: stat.S_ISREG(getattr(metadata, "st_mode", 0))
        or original_observer(metadata),
    )
    with pytest.raises(ValueError):
        observer.write_report_atomic(output, {"status": "partial"})


@pytest.mark.parametrize(
    ("marker", "expected"),
    [
        (
            {"status": "failed", "sha256": "a" * 64, "verified_at": "2026-08-22T00:00:00+00:00"},
            False,
        ),
        (
            {"status": "passed", "sha256": "A" * 64, "verified_at": "2026-08-22T00:00:00+00:00"},
            None,
        ),
        (
            {"status": "passed", "sha256": "a" * 64, "verified_at": "not-a-time"},
            None,
        ),
    ],
)
def test_backup_marker_malformed_state_is_explicitly_unknown(
    tmp_path: Path, marker: dict[str, object], expected: bool | None
) -> None:
    path = tmp_path / "latest-verified.json"
    path.write_text(json.dumps(marker), encoding="utf-8")

    assert observer.read_backup_verification(
        path, now=datetime(2026, 8, 22, 1, tzinfo=timezone.utc)
    ) is expected


def test_backup_marker_rejects_stale_future_and_symlink(tmp_path: Path) -> None:
    now = datetime.now(timezone.utc)
    path = tmp_path / "latest-verified.json"
    for verified_at in (now - timedelta(hours=24, microseconds=1), now + timedelta(microseconds=1)):
        path.write_text(json.dumps({"status": "passed", "sha256": "a" * 64, "verified_at": verified_at.isoformat()}), encoding="utf-8")
        assert observer.read_backup_verification(path, now=now) is None
    target_dir = tmp_path / "target"
    target_dir.mkdir()
    target = target_dir / "latest-verified.json"
    target.write_text("{}", encoding="utf-8")
    link_dir = tmp_path / "links"
    link_dir.mkdir()
    link = link_dir / "latest-verified.json"
    _create_symlink_or_skip(link, target)
    assert observer.read_backup_verification(link, now=now) is None


def test_unavailable_or_truncated_taskstore_cannot_pass(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    _store, staging, marker = _observer_fixture(tmp_path, now)
    monkeypatch.setattr(observer, "_task_staging_root", lambda: staging)

    def broken(_path):
        raise ValueError("PRIVATE_DB_ERROR_CANARY")

    report = observer.collect_observation(
        db_path=tmp_path / "tasks.sqlite", current_level=5,
        window_start=now - timedelta(hours=24), window_end=now - timedelta(minutes=1),
        prometheus_url="http://127.0.0.1:9090", backup_state=marker,
        baseline_p95_seconds=40.0, now=now,
        observation_store_factory=broken, prometheus_fetcher=_passed_prometheus,
    )

    assert report["status"] == "partial"
    assert report["sample_count"] == 0
    assert "PRIVATE_DB_ERROR_CANARY" not in json.dumps(report)


def _database_files_state(path: Path) -> tuple[bytes, int, int, tuple[str, ...]]:
    metadata = path.stat()
    sidecars = tuple(
        suffix
        for suffix in ("-wal", "-shm", "-journal")
        if Path(str(path) + suffix).exists()
    )
    return path.read_bytes(), metadata.st_size, metadata.st_mtime_ns, sidecars


def test_observer_does_not_initialize_empty_non_medchat_database(
    tmp_path: Path,
) -> None:
    db_path = tmp_path / "PRIVATE_EMPTY_DB_CANARY.sqlite"
    sqlite3.connect(db_path).close()
    before = _database_files_state(db_path)
    now = datetime.now(timezone.utc)
    output = tmp_path / "report.json"

    result = subprocess.run(
        [
            PYTHON, str(OBSERVE_SCRIPT), "--db-path", str(db_path),
            "--level", "5",
            "--window-start", (now - timedelta(hours=24)).isoformat(),
            "--window-end", (now - timedelta(minutes=1)).isoformat(),
            "--prometheus-url", "http://127.0.0.1:1",
            "--backup-state", str(tmp_path / "latest-verified.json"),
            "--baseline-p95-seconds", "40", "--output", str(output),
        ],
        cwd=PROJECT_ROOT,
        text=True,
        capture_output=True,
        timeout=20,
        check=False,
    )

    assert result.returncode in {0, 1}
    assert _database_files_state(db_path) == before
    assert "PRIVATE_EMPTY_DB_CANARY" not in result.stdout + result.stderr
    assert str(db_path) not in result.stdout + result.stderr
    assert "Traceback" not in result.stderr


def test_observer_leaves_idle_initialized_database_files_unchanged(
    tmp_path: Path,
) -> None:
    from src.task_runtime.store import TaskStore

    db_path = tmp_path / "tasks.sqlite"
    TaskStore(db_path)
    before = _database_files_state(db_path)
    now = datetime.now(timezone.utc)
    marker = tmp_path / "latest-verified.json"
    marker.write_text(
        json.dumps(
            {
                "status": "passed",
                "sha256": "a" * 64,
                "verified_at": now.isoformat(),
            }
        ),
        encoding="utf-8",
    )

    report = observer.collect_observation(
        db_path=db_path,
        current_level=5,
        window_start=now - timedelta(hours=24),
        window_end=now - timedelta(minutes=1),
        prometheus_url="http://127.0.0.1:9090",
        backup_state=marker,
        baseline_p95_seconds=40.0,
        now=now,
        prometheus_fetcher=_passed_prometheus,
    )

    assert report["status"] == "partial"
    assert _database_files_state(db_path) == before


def test_taskstore_slice_overflow_is_partial(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    now = datetime.now(timezone.utc)
    _store, staging, marker = _observer_fixture(tmp_path, now)
    monkeypatch.setattr(observer, "_task_staging_root", lambda: staging)

    class OverflowStore:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            return None

        def list_temporal_observation(self, *_args, **_kwargs):
            raise ValueError("observation result exceeds limit PRIVATE_TASK_CANARY")

    report = observer.collect_observation(
        db_path=tmp_path / "tasks.sqlite", current_level=5,
        window_start=now - timedelta(hours=24), window_end=now - timedelta(minutes=1),
        prometheus_url="http://127.0.0.1:9090", backup_state=marker,
        baseline_p95_seconds=40.0, now=now,
        observation_store_factory=lambda _path: OverflowStore(),
        prometheus_fetcher=_passed_prometheus,
    )

    assert report["status"] == "partial"
    assert report["sample_count"] == 0
    assert "PRIVATE_TASK_CANARY" not in json.dumps(report)


def test_observer_atomic_output_rejects_symlink_and_stdout_is_sanitized(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    report = {
        "status": "partial", "sample_count": 0, "current_level": 5,
        "sha256": "a" * 64,
    }
    target = tmp_path / "target.json"
    target.write_text("do not replace", encoding="utf-8")
    linked = tmp_path / "report.json"
    _create_symlink_or_skip(linked, target)
    with pytest.raises(ValueError):
        observer.write_report_atomic(linked, report)
    assert target.read_text(encoding="utf-8") == "do not replace"
    assert not list(tmp_path.glob("*.tmp"))

    monkeypatch.setattr(observer, "collect_observation", lambda **_kwargs: report)
    output = tmp_path / "safe-report.json"
    code = observer.main([
        "--db-path", "PRIVATE_DB_PATH_CANARY", "--level", "5",
        "--window-start", "2026-08-21T00:00:00+00:00",
        "--window-end", "2026-08-22T00:00:00+00:00",
        "--prometheus-url", "http://127.0.0.1:9090",
        "--backup-state", str(tmp_path / "latest-verified.json"),
        "--baseline-p95-seconds", "40", "--output", str(output),
    ])
    captured = capsys.readouterr()
    assert code == 0
    assert captured.out == f"status=partial sample_count=0 current_level=5 sha256={'a' * 64}\n"
    assert "PRIVATE_DB_PATH_CANARY" not in captured.out + captured.err


def test_observer_atomic_output_cleans_temp_when_replace_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    output = tmp_path / "report.json"

    def fail_replace(*_args, **_kwargs):
        raise OSError("RAW_REPLACE_ERROR_CANARY")

    monkeypatch.setattr(observer.os, "replace", fail_replace)
    with pytest.raises(ValueError, match="atomic report output failed") as error:
        observer.write_report_atomic(
            output,
            {"status": "partial", "sha256": "a" * 64},
        )

    assert "RAW_REPLACE_ERROR_CANARY" not in str(error.value)
    assert not output.exists()
    assert not list(tmp_path.glob("*.tmp"))


def test_subprocess_cli_errors_never_leak_tracebacks_or_raw_arguments(tmp_path: Path) -> None:
    secret = "postgres://operator-secret@127.0.0.1/db"
    result = subprocess.run(
        [
            PYTHON, str(OBSERVE_SCRIPT), "--db-path", secret, "--level", "5",
            "--window-start", "bad", "--window-end", "bad",
            "--prometheus-url", "http://example.test:9090",
            "--backup-state", secret, "--baseline-p95-seconds", "40",
            "--output", str(tmp_path / "out.json"),
        ],
        cwd=PROJECT_ROOT, text=True, capture_output=True, timeout=15, check=False,
    )

    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert secret not in result.stdout + result.stderr


class _MissingDeploymentTools:
    def available(self, _tool: str) -> bool:
        return False

    def run(self, *_args: object, **_kwargs: object) -> object:
        raise AssertionError("missing deployment tools must not execute")


def _task10_acceptance_report(mode: str) -> dict[str, object]:
    real = mode == "real"
    report: dict[str, object] = {
        "status": "passed",
        "mode": mode,
        "run_count": 3,
        "scientific_execution": real,
        "provenance_gate": {
            "status": "passed",
            "evidence_type": "real_temporal_vina" if real else "contract_replay",
        },
        "source_report_sha256": "b" * 64,
        "source_schema_valid": True,
    }
    canonical = json.dumps(
        report,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    report["sha256"] = hashlib.sha256(canonical).hexdigest()
    return report


def _task10_real_source_report(artifact_root: Path) -> dict[str, object]:
    runs: list[dict[str, object]] = []
    latencies = [100.0, 200.0, 300.0]
    for index, latency in enumerate(latencies, start=1):
        task_id = f"00000000-0000-4000-8000-{index:012d}"
        relative = "artifacts/docking_pose.pdbqt"
        pose = artifact_root / task_id / relative
        pose.parent.mkdir(parents=True)
        pose.write_bytes(
            f"REMARK VINA RESULT: {-7.0 - index / 10:.3f} 0.0 0.0\n".encode()
        )
        digest = hashlib.sha256(pose.read_bytes()).hexdigest()
        runs.append(
            {
                "run": index,
                "status": "passed",
                "backend": "temporal",
                "workflow_id": f"medchat-docking-{task_id}",
                "vina_attempts": 1,
                "terminal_event_count": 1,
                "pose_exists": True,
                "pose_count": 1,
                "binding_energy": -7.0 - index / 10,
                "artifact_path": relative,
                "artifact_sha256": digest,
                "provenance_complete": True,
                "provenance": {
                    "tool_name": "molecular_docking",
                    "tool_version": "vina-1.2.5",
                    "model_name": "AutoDock Vina",
                    "model_version": "1.2.5",
                },
                "demo_mode": False,
                "fallback_used": False,
                "artifact_integrity_valid": True,
                "terminal_event_valid": True,
                "latency_ms": latency,
                "warnings": [],
                "failures": [],
            }
        )
    report: dict[str, object] = {
        "status": "passed",
        "run_count": 3,
        "passed_count": 3,
        "pass_rate": 1.0,
        "duplicate_vina_count": 0,
        "terminal_event_violation_count": 0,
        "latency_ms": {"p50": 200.0, "p95": 300.0},
        "failure_type_distribution": {},
        "runs": runs,
        "mode": "real",
        "scientific_execution": True,
        "provenance_gate": {
            "status": "passed",
            "evidence_type": "real_temporal_vina",
        },
    }
    canonical = json.dumps(
        report, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode()
    report["sha256"] = hashlib.sha256(canonical).hexdigest()
    return report


def _task10_preflight_inputs() -> dict[str, object]:
    return {
        "deployment": {"status": "passed"},
        "contract": _task10_acceptance_report("contract"),
        "real": _task10_acceptance_report("real"),
        "infrastructure": {
            "temporal": True,
            "namespace": True,
            "queue": True,
            "worker": True,
        },
        "blocking_alerts": [],
        "backup": {"status": "passed", "restore_verified": True},
        "current_level": 0,
        "now": datetime(2026, 8, 25, 12, tzinfo=timezone.utc),
    }


def _assert_canonical_report_hash(report: dict[str, object]) -> None:
    projected = dict(report)
    digest = projected.pop("sha256")
    canonical = json.dumps(
        projected,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    assert digest == hashlib.sha256(canonical).hexdigest()


def test_deployment_validator_has_explicit_pass_fail_skip_results() -> None:
    from scripts.validate_temporal_deployment import validate_deployment

    report = validate_deployment(
        repo_root=PROJECT_ROOT,
        command_runner=_MissingDeploymentTools(),
        platform_name="Windows",
        now=datetime(2026, 8, 25, 12, tzinfo=timezone.utc),
    )

    assert report["status"] == "partial"
    assert report["checks"]["python_static"]["status"] == "passed"
    assert report["checks"]["docker_compose"] == {
        "status": "skipped",
        "code": "docker_unavailable",
    }
    assert report["checks"]["promtool"] == {
        "status": "skipped",
        "code": "promtool_unavailable",
    }
    assert report["checks"]["systemd_analyze"] == {
        "status": "skipped",
        "code": "linux_systemd_required",
    }
    assert {
        check["status"] for check in report["checks"].values()
    } <= {"passed", "failed", "skipped"}
    _assert_canonical_report_hash(report)


def test_preflight_requires_repeat_three_real_science_and_backup() -> None:
    from scripts.run_temporal_production_preflight import build_preflight_report

    report = build_preflight_report(**_task10_preflight_inputs())

    assert report["status"] == "passed"
    assert report["stage"] == "preflight"
    assert report["current_level"] == 0
    assert report["all_required_gates"] is True
    assert all(
        gate["status"] == "passed" for gate in report["checks"].values()
    )
    _assert_canonical_report_hash(report)


@pytest.mark.parametrize(
    "field",
    ["deployment", "contract", "real", "infrastructure", "backup"],
)
def test_preflight_never_passes_when_required_evidence_is_missing(field: str) -> None:
    from scripts.run_temporal_production_preflight import build_preflight_report

    inputs = _task10_preflight_inputs()
    inputs[field] = {"status": "skipped"}

    report = build_preflight_report(**inputs)

    assert report["status"] != "passed"
    assert report["all_required_gates"] is False


def test_preflight_rejects_contract_or_fake_runner_as_real_evidence() -> None:
    from scripts.run_temporal_production_preflight import build_preflight_report

    inputs = _task10_preflight_inputs()
    inputs["real"] = _task10_acceptance_report("contract")

    report = build_preflight_report(**inputs)

    assert report["status"] == "failed"
    assert report["checks"]["real_science"] == {
        "status": "failed",
        "code": "real_science_invalid",
    }


@pytest.mark.parametrize(
    "alert_name",
    [
        "TemporalWorkerHeartbeatStale",
        "TemporalQueueBacklogGrowing",
        "TemporalWorkflowStartUnexpectedErrors",
        "TemporalDuplicateVinaExecution",
        "TemporalMultipleTerminalEvents",
        "TemporalArtifactValidationFailure",
        "TemporalDockingP95TooHigh",
        "TemporalDockingP95BaselineMissing",
        "TemporalRuntimeFailureRateHigh",
        "TemporalPostgresUnavailable",
        "TemporalBackupVerificationStale",
    ],
)
def test_preflight_never_passes_with_any_release_blocker_firing(
    alert_name: str,
) -> None:
    from scripts.run_temporal_production_preflight import build_preflight_report

    inputs = _task10_preflight_inputs()
    inputs["blocking_alerts"] = [alert_name]

    report = build_preflight_report(**inputs)

    assert report["status"] == "failed"
    assert report["checks"]["blocking_alerts"] == {
        "status": "failed",
        "code": "blocking_alerts_firing",
    }


def test_real_acceptance_subprocess_uses_isolated_canary_override_and_redacts(
    tmp_path: Path,
) -> None:
    from scripts.run_temporal_production_preflight import run_acceptance_evidence

    secret = "PRIVATE_PREFLIGHT_SECRET_CANARY"
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
    source_digests: list[str] = []
    timeouts: list[float] = []

    def runner(
        argv: list[str],
        *,
        cwd: Path,
        environment: dict[str, str],
        timeout: float,
    ) -> object:
        del cwd
        timeouts.append(timeout)
        calls.append((tuple(argv), dict(environment)))
        output = Path(argv[argv.index("--output") + 1])
        payload = _task10_real_source_report(tmp_path / "artifacts")
        source_digests.append(str(payload["sha256"]))
        output.write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    evidence = run_acceptance_evidence(
        mode="real",
        repo_root=PROJECT_ROOT,
        output_path=tmp_path / "real.json",
        command_runner=runner,
        artifact_root=tmp_path / "artifacts",
        base_environment={
            "PATH": "C:/malicious-path",
            "PYTHONPATH": "C:/injected-python",
            "LD_PRELOAD": "C:/injected-loader",
            "OPENAI_API_KEY": secret,
            "TEMPORAL_POSTGRES_PASSWORD": secret,
            "MEDCHAT_TEMPORAL_CANARY_PERCENT": "5",
            "MEDCHAT_TEMPORAL_ADDRESS": "127.0.0.1:7233",
        },
    )

    argv, child_environment = calls[0]
    assert Path(argv[0]).is_absolute()
    assert argv[1] == "-I"
    assert "--mode" in argv and "real" in argv
    assert "--repeat" in argv and "3" in argv
    assert secret not in " ".join(argv)
    assert child_environment["MEDCHAT_TASK_BACKEND"] == "temporal_canary"
    assert child_environment["MEDCHAT_TEMPORAL_CANARY_PERCENT"] == "100"
    assert child_environment["MEDCHAT_TEMPORAL_ADDRESS"] == "127.0.0.1:7233"
    assert "PATH" not in child_environment
    assert "PYTHONPATH" not in child_environment
    assert "LD_PRELOAD" not in child_environment
    assert "OPENAI_API_KEY" not in child_environment
    assert "TEMPORAL_POSTGRES_PASSWORD" not in child_environment
    assert secret not in json.dumps(evidence)
    assert evidence["provenance_gate"]["evidence_type"] == "real_temporal_vina"
    assert "runs" not in evidence
    assert evidence["source_report_sha256"] == source_digests[0]
    assert timeouts == [7360.0]
    _assert_canonical_report_hash(evidence)


def test_acceptance_parent_watchdog_soft_terminates_then_waits_cleanup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_temporal_production_preflight as preflight

    events: list[object] = []

    class FakeProcess:
        pid = 4321
        returncode = 1

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            events.append(("communicate", timeout))
            if len([item for item in events if isinstance(item, tuple)]) == 1:
                raise subprocess.TimeoutExpired("acceptance", timeout)
            return b"", b""

        def send_signal(self, selected: int) -> None:
            events.append(("signal", selected))

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")

    monkeypatch.setattr(preflight.subprocess, "Popen", lambda *_args, **_kwargs: FakeProcess())
    result = preflight._subprocess_runner(
        ["/trusted/python", "-I", "acceptance.py"],
        cwd=PROJECT_ROOT,
        environment={},
        timeout=7360.0,
        cleanup_grace=130.0,
    )

    assert result.returncode == 1
    assert events[0] == ("communicate", 7230.0)
    expected_signal = (
        signal.CTRL_BREAK_EVENT
        if os.name == "nt" and hasattr(signal, "CTRL_BREAK_EVENT")
        else signal.SIGTERM
    )
    assert events[1] == ("signal", expected_signal)
    assert events[2] == ("communicate", 130.0)
    assert "kill" not in events


def test_acceptance_parent_watchdog_kills_only_after_cleanup_grace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_temporal_production_preflight as preflight

    events: list[object] = []

    class HungProcess:
        pid = 4322
        returncode = -9
        killed = False

        def communicate(self, *, timeout: float | None = None) -> tuple[bytes, bytes]:
            events.append(("communicate", timeout))
            if timeout is not None and not self.killed:
                raise subprocess.TimeoutExpired("acceptance", timeout)
            return b"", b""

        def send_signal(self, selected: int) -> None:
            events.append(("signal", selected))

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            self.killed = True
            events.append("kill")

    monkeypatch.setattr(preflight.subprocess, "Popen", lambda *_args, **_kwargs: HungProcess())
    result = preflight._subprocess_runner(
        ["/trusted/python", "-I", "acceptance.py"],
        cwd=PROJECT_ROOT,
        environment={},
        timeout=7360.0,
        cleanup_grace=130.0,
    )

    assert result.returncode == -9
    assert events[-2:] == [
        "kill",
        ("communicate", preflight.ACCEPTANCE_CLOSE_BUDGET_SECONDS),
    ]
    assert ("communicate", 130.0) in events


def test_acceptance_parent_watchdog_signal_failure_still_reaps_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.run_temporal_production_preflight as preflight

    events: list[object] = []

    class SignalFailureProcess:
        pid = 4323
        returncode = 1

        def communicate(self, *, timeout: float) -> tuple[bytes, bytes]:
            events.append(("communicate", timeout))
            if len([item for item in events if isinstance(item, tuple)]) == 1:
                raise subprocess.TimeoutExpired("acceptance", timeout)
            return b"", b""

        def send_signal(self, _selected: int) -> None:
            events.append("signal_failed")
            raise OSError("no console")

        def terminate(self) -> None:
            events.append("terminate")

        def kill(self) -> None:
            events.append("kill")

    monkeypatch.setattr(
        preflight.subprocess,
        "Popen",
        lambda *_args, **_kwargs: SignalFailureProcess(),
    )
    result = preflight._subprocess_runner(
        ["/trusted/python", "-I", "acceptance.py"],
        cwd=PROJECT_ROOT,
        environment={},
        timeout=7360.0,
        cleanup_grace=130.0,
    )

    assert result.returncode == 1
    assert events[1:4] == [
        "signal_failed",
        "terminate",
        ("communicate", 130.0),
    ]


def test_real_acceptance_fake_runner_cannot_forge_shallow_pass(
    tmp_path: Path,
) -> None:
    from scripts.run_temporal_production_preflight import run_acceptance_evidence

    def runner(argv: list[str], **_kwargs: object) -> object:
        output = Path(argv[argv.index("--output") + 1])
        output.write_text(json.dumps(_task10_acceptance_report("real")))
        return SimpleNamespace(returncode=0)

    evidence = run_acceptance_evidence(
        mode="real",
        repo_root=PROJECT_ROOT,
        output_path=tmp_path / "real.json",
        artifact_root=tmp_path / "artifacts",
        command_runner=runner,
        base_environment={},
    )

    assert evidence["status"] == "failed"
    assert evidence["source_schema_valid"] is False


def test_failed_acceptance_child_projection_remains_canonical(
    tmp_path: Path,
) -> None:
    from scripts.run_temporal_production_preflight import run_acceptance_evidence

    artifact_root = tmp_path / "artifacts"

    def runner(argv: list[str], **_kwargs: object) -> object:
        output = Path(argv[argv.index("--output") + 1])
        output.write_text(
            json.dumps(_task10_real_source_report(artifact_root)), encoding="utf-8"
        )
        return SimpleNamespace(returncode=1)

    evidence = run_acceptance_evidence(
        mode="real",
        repo_root=PROJECT_ROOT,
        output_path=tmp_path / "real.json",
        artifact_root=artifact_root,
        command_runner=runner,
        base_environment={},
    )

    assert evidence["status"] == "failed"
    _assert_canonical_report_hash(evidence)


@pytest.mark.parametrize(
    "mutation",
    [
        "contract_label",
        "failed_run",
        "missing_artifact",
        "demo_provenance",
        "fallback_provenance",
        "non_numeric_energy",
        "multiple_terminal_events",
        "missing_run",
        "boolean_run_index",
        "boolean_attempt",
        "boolean_terminal_count",
        "boolean_run_count",
        "boolean_passed_count",
        "boolean_pass_rate",
        "wrong_aggregate",
    ],
)
def test_real_acceptance_rejects_incomplete_or_relabelled_evidence(
    tmp_path: Path,
    mutation: str,
) -> None:
    from scripts.run_temporal_production_preflight import run_acceptance_evidence

    artifact_root = tmp_path / "artifacts"

    def runner(argv: list[str], **_kwargs: object) -> object:
        output = Path(argv[argv.index("--output") + 1])
        payload = _task10_real_source_report(artifact_root)
        runs = payload["runs"]
        assert isinstance(runs, list)
        if mutation == "contract_label":
            payload["mode"] = "contract"
        elif mutation == "failed_run":
            runs[0]["status"] = "failed"
        elif mutation == "missing_artifact":
            Path(artifact_root / runs[0]["workflow_id"].removeprefix("medchat-docking-") / runs[0]["artifact_path"]).unlink()
        elif mutation == "demo_provenance":
            runs[0]["demo_mode"] = True
        elif mutation == "fallback_provenance":
            runs[0]["fallback_used"] = True
        elif mutation == "non_numeric_energy":
            runs[0]["binding_energy"] = "-7.1"
        elif mutation == "multiple_terminal_events":
            runs[0]["terminal_event_count"] = 2
        elif mutation == "missing_run":
            runs.pop()
        elif mutation == "boolean_run_index":
            runs[0]["run"] = True
        elif mutation == "boolean_attempt":
            runs[0]["vina_attempts"] = True
        elif mutation == "boolean_terminal_count":
            runs[0]["terminal_event_count"] = True
        elif mutation == "boolean_run_count":
            payload["run_count"] = True
        elif mutation == "boolean_passed_count":
            payload["passed_count"] = True
        elif mutation == "boolean_pass_rate":
            payload["pass_rate"] = True
        elif mutation == "wrong_aggregate":
            payload["passed_count"] = 2
        payload.pop("sha256")
        payload["sha256"] = hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"), allow_nan=False
            ).encode()
        ).hexdigest()
        output.write_text(json.dumps(payload), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    evidence = run_acceptance_evidence(
        mode="real",
        repo_root=PROJECT_ROOT,
        output_path=tmp_path / "real.json",
        artifact_root=artifact_root,
        command_runner=runner,
        base_environment={},
    )

    assert evidence["status"] == "failed"
    assert evidence["source_schema_valid"] is False


def test_deployment_validator_passes_only_executed_host_checks(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from scripts.validate_temporal_deployment import validate_deployment

    secret = "PRIVATE_DEPLOYMENT_ENV_CANARY"
    calls: list[tuple[tuple[str, ...], dict[str, str]]] = []
    call_directories: list[Path] = []

    class PassingTools:
        def available(self, _tool: str) -> bool:
            return True

        def run(
            self,
            argv: list[str],
            *,
            cwd: Path,
            environment: dict[str, str],
            timeout: float,
        ) -> object:
            del timeout
            call_directories.append(cwd)
            calls.append((tuple(argv), dict(environment)))
            return SimpleNamespace(returncode=0)

    monkeypatch.setenv("PRIVATE_DEPLOYMENT_TOKEN", secret)
    report = validate_deployment(
        repo_root=PROJECT_ROOT,
        command_runner=PassingTools(),
        platform_name="Linux",
        now=datetime(2026, 8, 25, 12, tzinfo=timezone.utc),
    )

    assert report["status"] == "passed"
    assert all(check["status"] == "passed" for check in report["checks"].values())
    assert len(calls) == 5
    assert calls[0][0][:2] == ("docker", "compose")
    assert calls[3][0] == (
        "promtool",
        "test",
        "rules",
        "deployment/temporal/prometheus/tests/medchat-temporal.test.yml",
    )
    assert calls[-1][0] == (
        "systemd-analyze",
        "verify",
        "deployment/medchat.service",
        "deployment/medchat-temporal-worker.service",
    )
    assert call_directories == [PROJECT_ROOT] * 5
    assert all(
        environment["PATH"]
        == "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        for _argv, environment in calls
    )
    assert secret not in json.dumps(calls)


def test_deployment_validator_failed_tool_is_never_partial() -> None:
    from scripts.validate_temporal_deployment import validate_deployment

    class FailingPromtool:
        def available(self, _tool: str) -> bool:
            return True

        def run(self, argv: list[str], **_kwargs: object) -> object:
            return SimpleNamespace(returncode=1 if argv[0] == "promtool" else 0)

    report = validate_deployment(
        repo_root=PROJECT_ROOT,
        command_runner=FailingPromtool(),
        platform_name="Linux",
    )

    assert report["status"] == "failed"
    assert report["checks"]["promtool"] == {
        "status": "failed",
        "code": "promtool_invalid",
    }


def test_deployment_tool_resolution_ignores_untrusted_path(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.validate_temporal_deployment as deployment_validator

    marker = tmp_path / "promtool"
    marker.write_text("malicious", encoding="utf-8")
    monkeypatch.setenv("PATH", str(tmp_path))
    attempted: list[Path] = []

    def reject(path: Path) -> object:
        attempted.append(path)
        raise ValueError("trusted tool unavailable")

    monkeypatch.setattr(deployment_validator, "open_trusted_executable", reject)
    runner = deployment_validator.DeploymentCommandRunner()

    assert runner.available("promtool") is False
    assert marker not in attempted
    assert attempted == [Path("/usr/bin/promtool"), Path("/usr/local/bin/promtool")]


def test_deployment_runner_close_is_idempotent_and_closes_cached_tools() -> None:
    from scripts.validate_temporal_deployment import DeploymentCommandRunner

    closed: list[str] = []
    runner = DeploymentCommandRunner()
    runner._tools = {
        "docker": SimpleNamespace(close=lambda: closed.append("docker")),
        "promtool": SimpleNamespace(close=lambda: closed.append("promtool")),
    }

    runner.close()
    runner.close()

    assert sorted(closed) == ["docker", "promtool"]
    assert runner._tools == {}


def test_trusted_executable_context_closes_both_descriptors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from src.task_runtime.trusted_process import TrustedExecutable

    closed: list[int] = []
    monkeypatch.setattr(os, "close", lambda descriptor: closed.append(descriptor))
    tool = TrustedExecutable(10, (1, 2, 3, 4, 5), 11, "tool")

    with tool as selected:
        assert selected is tool
    tool.close()

    assert closed == [10, 11]
    assert tool.descriptor == -1
    assert tool.parent_descriptor == -1


def test_validate_deployment_closes_only_internally_created_runner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.validate_temporal_deployment as deployment_validator

    closed: list[str] = []

    class FakeRunner(_MissingDeploymentTools):
        def close(self) -> None:
            closed.append("closed")

    monkeypatch.setattr(deployment_validator, "DeploymentCommandRunner", FakeRunner)
    deployment_validator.validate_deployment(
        repo_root=PROJECT_ROOT,
        platform_name="Windows",
    )
    assert closed == ["closed"]

    external = FakeRunner()
    deployment_validator.validate_deployment(
        repo_root=PROJECT_ROOT,
        command_runner=external,
        platform_name="Windows",
    )
    assert closed == ["closed"]


@requires_posix_descriptor_io
def test_trusted_tool_rejects_symlink_and_named_leaf_replacement(
    postgres_trusted_tmp_path: Path,
) -> None:
    from src.task_runtime.trusted_process import open_trusted_executable

    binary_directory = postgres_trusted_tmp_path / "bin"
    binary_directory.mkdir(mode=0o700)
    tool = binary_directory / "tool"
    tool.write_bytes(b"#!/bin/sh\nexit 0\n")
    tool.chmod(0o700)
    linked = binary_directory / "linked"
    linked.symlink_to(tool)
    with pytest.raises(ValueError, match="trusted tool unavailable"):
        open_trusted_executable(linked)

    trusted = open_trusted_executable(tool)
    replacement = binary_directory / "replacement"
    replacement.write_bytes(b"#!/bin/sh\nexit 1\n")
    replacement.chmod(0o700)
    os.replace(replacement, tool)
    try:
        with pytest.raises(ValueError, match="trusted tool changed"):
            trusted.revalidate()
    finally:
        trusted.close()


def test_preflight_windows_host_gaps_remain_partial() -> None:
    from scripts.run_temporal_production_preflight import build_preflight_report

    inputs = _task10_preflight_inputs()
    inputs["deployment"] = {"status": "partial"}
    inputs["infrastructure"] = {
        "temporal": None,
        "namespace": None,
        "queue": None,
        "worker": None,
    }
    inputs["blocking_alerts"] = None
    inputs["backup"] = {"status": "skipped", "restore_verified": False}

    report = build_preflight_report(**inputs)

    assert report["status"] == "partial"
    assert report["all_required_gates"] is False


def test_task10_atomic_report_writer_rejects_symlink(
    tmp_path: Path,
) -> None:
    from scripts.validate_temporal_deployment import write_report_atomic

    target = tmp_path / "target.json"
    target.write_text("sentinel", encoding="utf-8")
    linked = tmp_path / "report.json"
    _create_symlink_or_skip(linked, target)

    with pytest.raises(ValueError, match="unsafe report output"):
        write_report_atomic(linked, {"status": "passed"})

    assert target.read_text(encoding="utf-8") == "sentinel"
    assert not list(tmp_path.glob("*.tmp"))


@pytest.mark.parametrize("fail_at", [2, 3, 4])
def test_atomic_report_writer_normalizes_every_boundary_capture_failure(
    tmp_path: Path,
    fail_at: int,
) -> None:
    import src.task_runtime.secure_io as secure_io

    output = tmp_path / "report.json"
    calls = 0

    def flaky_capture(path: Path, *, require_file: bool = True) -> object:
        nonlocal calls
        calls += 1
        if calls == fail_at:
            raise ValueError("RAW_TRUST_BOUNDARY_ERROR_CANARY")
        return secure_io.capture_trusted_path_boundary(
            path, require_file=require_file
        )

    with pytest.raises(ValueError, match="^unsafe report output$") as error:
        secure_io.write_json_atomic(
            output,
            {"status": "passed"},
            maximum_bytes=4096,
            capture_boundary=flaky_capture,
        )

    assert "RAW_TRUST_BOUNDARY_ERROR_CANARY" not in str(error.value)


@requires_posix_descriptor_io
def test_atomic_report_writer_enforces_mode_under_permissive_umask(
    tmp_path: Path,
) -> None:
    import src.task_runtime.secure_io as secure_io

    output = tmp_path / "report.json"
    previous = os.umask(0o002)
    try:
        secure_io.write_json_atomic(
            output,
            {"status": "passed"},
            maximum_bytes=4096,
        )
    finally:
        os.umask(previous)

    assert stat.S_IMODE(output.stat().st_mode) == 0o600


def test_deployment_validator_cli_requires_existing_output_parent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import scripts.validate_temporal_deployment as deployment_validator

    monkeypatch.setattr(
        deployment_validator,
        "validate_deployment",
        lambda: {"status": "partial", "sha256": "a" * 64},
    )
    missing_parent = tmp_path / "missing" / "report.json"

    assert deployment_validator.main(["--output", str(missing_parent)]) == 1
    assert not missing_parent.parent.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows handle/path ctime semantics")
def test_windows_snapshot_allows_handle_path_ctime_difference(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.secure_io as secure_io

    source = tmp_path / "asset.yml"
    source.write_bytes(b"stable")
    original_fstat = secure_io.os.fstat

    def shifted_ctime(descriptor: int) -> object:
        metadata = original_fstat(descriptor)
        return SimpleNamespace(
            st_dev=metadata.st_dev,
            st_ino=metadata.st_ino,
            st_mode=metadata.st_mode,
            st_file_attributes=getattr(metadata, "st_file_attributes", 0),
            st_size=metadata.st_size,
            st_mtime_ns=metadata.st_mtime_ns,
            st_ctime_ns=metadata.st_ctime_ns + 10_000,
        )

    monkeypatch.setattr(secure_io.os, "fstat", shifted_ctime)
    snapshot = secure_io.read_file_snapshot(source.resolve(), 1024)

    assert snapshot.content == b"stable"
    assert snapshot.sha256 == hashlib.sha256(b"stable").hexdigest()


def test_task10_atomic_report_writer_rejects_symlink_ancestor(
    tmp_path: Path,
) -> None:
    from scripts.validate_temporal_deployment import write_report_atomic

    real_parent = tmp_path / "real"
    real_parent.mkdir()
    linked_parent = tmp_path / "linked"
    _create_symlink_or_skip(linked_parent, real_parent)

    with pytest.raises(ValueError, match="unsafe|atomic"):
        write_report_atomic(linked_parent / "report.json", {"status": "passed"})

    assert not (real_parent / "report.json").exists()


def test_bounded_asset_snapshot_rejects_same_size_replacement(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import src.task_runtime.secure_io as secure_io

    source = tmp_path / "asset.yml"
    replacement = tmp_path / "replacement.yml"
    source.write_bytes(b"safe")
    replacement.write_bytes(b"evil")
    original = secure_io._bounded_read

    def replace_after_read(descriptor: int, maximum_bytes: int) -> bytes:
        content = original(descriptor, maximum_bytes)
        os.replace(replacement, source)
        return content

    monkeypatch.setattr(secure_io, "_bounded_read", replace_after_read)

    with pytest.raises(ValueError, match="unsafe file snapshot"):
        secure_io.read_file_snapshot(source.resolve(), 1024)


def _task10_static_fixture(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    shutil.copytree(PROJECT_ROOT / "deployment", root / "deployment")
    (root / "scripts").mkdir()
    for name in (
        "run_temporal_docking_acceptance.py",
        "backup_temporal_postgres.py",
        "restore_temporal_postgres.py",
    ):
        shutil.copy2(PROJECT_ROOT / "scripts" / name, root / "scripts" / name)
    return root


@pytest.mark.parametrize(
    "mutation",
    [
        "compose_service",
        "compose_service_shape",
        "compose_network",
        "compose_privilege",
        "compose_schema_entrypoint",
        "compose_entrypoint",
        "compose_namespace_entrypoint",
        "compose_role_entrypoint",
        "compose_command",
        "systemd_contract",
        "prometheus_job",
        "prometheus_rule",
        "prometheus_other_rule",
        "prometheus_fixture",
        "grafana_query",
        "schema_script",
        "schema_command",
        "namespace_script",
        "server_wrapper",
        "role_sync_script",
        "role_sync_command",
        "role_sync_no_psql",
        "rotate_script",
        "missing_operator_script",
        "nonregular_operator_script",
    ],
)
def test_deployment_python_static_rejects_semantic_asset_tamper(
    tmp_path: Path,
    mutation: str,
) -> None:
    from scripts import validate_temporal_deployment as validator

    root = _task10_static_fixture(tmp_path)
    if mutation == "compose_service":
        path = root / "deployment/temporal/docker-compose.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(content.replace("  temporal-ui:\n", "  temporal-ui-rogue:\n", 1), encoding="utf-8")
    elif mutation == "compose_service_shape":
        path = root / "deployment/temporal/docker-compose.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            re.sub(
                r"(?ms)^  temporal-ui:\n.*?(?=^  prometheus:)",
                "  temporal-ui: malformed\n",
                content,
                count=1,
            ),
            encoding="utf-8",
        )
    elif mutation == "compose_network":
        path = root / "deployment/temporal/docker-compose.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(content.replace("  database:\n    internal: true", "  database-rogue:\n    internal: true", 1), encoding="utf-8")
    elif mutation == "compose_privilege":
        path = root / "deployment/temporal/docker-compose.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace("    image: postgres:16.14-alpine3.23", "    image: postgres:16.14-alpine3.23\n    privileged: true", 1),
            encoding="utf-8",
        )
    elif mutation == "compose_schema_entrypoint":
        path = root / "deployment/temporal/docker-compose.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace(
                "      - /opt/medchat/temporal-schema-setup.sh",
                "      - /bin/false",
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "compose_entrypoint":
        path = root / "deployment/temporal/docker-compose.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace(
                "      - /opt/medchat/temporal-server-entrypoint.sh",
                "      - /bin/false",
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "compose_namespace_entrypoint":
        path = root / "deployment/temporal/docker-compose.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace(
                "      - /opt/medchat/temporal-namespace-setup.sh",
                "      - /bin/false",
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "compose_role_entrypoint":
        path = root / "deployment/temporal/docker-compose.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace(
                "      - /opt/medchat/010-exporter.sh",
                "      - /bin/false",
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "compose_command":
        path = root / "deployment/temporal/docker-compose.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace(
                "      - /opt/medchat/temporal-server-entrypoint.sh\n",
                "      - /opt/medchat/temporal-server-entrypoint.sh\n"
                "    command: [/bin/false]\n",
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "systemd_contract":
        path = root / "deployment/medchat-temporal-worker.service"
        content = path.read_text(encoding="utf-8")
        path.write_text(content.replace("KillMode=mixed", "KillMode=process", 1), encoding="utf-8")
    elif mutation == "prometheus_job":
        path = root / "deployment/temporal/prometheus/prometheus.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(content.replace("job_name: temporal-server", "job_name: temporal-rogue", 1), encoding="utf-8")
    elif mutation == "prometheus_rule":
        path = root / "deployment/temporal/prometheus/rules/medchat-temporal.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(content.replace('taskqueue="medchat-docking"', 'taskqueue="other-queue"', 1), encoding="utf-8")
    elif mutation == "prometheus_other_rule":
        path = root / "deployment/temporal/prometheus/rules/medchat-temporal.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace(
                "medchat_temporal_duplicate_vina_execution_total[15m]",
                "medchat_temporal_duplicate_vina_execution_total[1h]",
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "prometheus_fixture":
        path = root / "deployment/temporal/prometheus/tests/medchat-temporal.test.yml"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace("labeled baseline p95 fires", "unrecognized fixture", 1),
            encoding="utf-8",
        )
    elif mutation == "grafana_query":
        path = root / "deployment/temporal/grafana/dashboards/medchat-temporal-docking.json"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace(
                'service_requests{service_name=\\"frontend\\",namespace=\\"default\\",operation=\\"StartWorkflowExecution\\"}',
                'service_requests{service_name=\\"frontend\\",namespace=\\"other\\",operation=\\"StartWorkflowExecution\\"}',
                1,
            ),
            encoding="utf-8",
        )
    elif mutation == "schema_script":
        path = root / "deployment/temporal/scripts/temporal-schema-setup.sh"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace("setup-schema -v 0.0", "setup-schema -v unsafe", 1),
            encoding="utf-8",
            newline="\n",
        )
    elif mutation == "schema_command":
        path = root / "deployment/temporal/scripts/temporal-schema-setup.sh"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace("if ! psql \\", "if ! /bin/false \\", 1),
            encoding="utf-8",
            newline="\n",
        )
    elif mutation == "namespace_script":
        path = root / "deployment/temporal/scripts/temporal-namespace-setup.sh"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace(
                "temporal operator namespace update",
                "/bin/false",
                1,
            ),
            encoding="utf-8",
            newline="\n",
        )
    elif mutation == "server_wrapper":
        path = root / "deployment/temporal/scripts/temporal-server-entrypoint.sh"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace(
                'exec /etc/temporal/entrypoint.sh "$@"',
                "exec /bin/false",
                1,
            ),
            encoding="utf-8",
            newline="\n",
        )
    elif mutation == "role_sync_script":
        path = root / "deployment/temporal/postgres-init/010-exporter.sh"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace("NOREPLICATION NOBYPASSRLS", "NOREPLICATION BYPASSRLS", 1),
            encoding="utf-8",
            newline="\n",
        )
    elif mutation == "role_sync_command":
        path = root / "deployment/temporal/postgres-init/010-exporter.sh"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace("if ! psql \\", "if ! /bin/false \\", 1),
            encoding="utf-8",
            newline="\n",
        )
    elif mutation == "role_sync_no_psql":
        path = root / "deployment/temporal/postgres-init/010-exporter.sh"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace("psql", "disabled-tool"),
            encoding="utf-8",
            newline="\n",
        )
    elif mutation == "rotate_script":
        path = root / "deployment/temporal/scripts/rotate-exporter-password.sh"
        content = path.read_text(encoding="utf-8")
        path.write_text(
            content.replace("--wait-timeout 120", "--wait-timeout 12", 1),
            encoding="utf-8",
            newline="\n",
        )
    elif mutation == "missing_operator_script":
        (root / "deployment/temporal/scripts/rotate-exporter-password.sh").unlink()
    else:
        path = root / "deployment/temporal/scripts/rotate-exporter-password.sh"
        path.unlink()
        path.mkdir()

    assert validator._python_static(root) == {
        "status": "failed",
        "code": "python_static_invalid",
    }


def test_deployment_python_static_rejects_symlink_operator_script(
    tmp_path: Path,
) -> None:
    from scripts import validate_temporal_deployment as validator

    root = _task10_static_fixture(tmp_path)
    path = root / "deployment/temporal/scripts/rotate-exporter-password.sh"
    path.unlink()
    try:
        path.symlink_to("temporal-schema-setup.sh")
    except OSError as error:
        if os.name == "nt" and (
            error.errno in {errno.EPERM, errno.EACCES}
            or getattr(error, "winerror", None) == 1314
        ):
            pytest.skip("Windows symlink privilege unavailable")
        raise

    assert validator._python_static(root) == {
        "status": "failed",
        "code": "python_static_invalid",
    }


@pytest.mark.parametrize(
    ("queue_value", "expected"),
    [(1, True), (0, False)],
    ids=("target-queue", "non-target-queue-only"),
)
def test_observer_prometheus_queue_query_is_exactly_scoped(
    queue_value: int,
    expected: bool,
) -> None:
    assert observer.PROMETHEUS_QUERIES["namespace"] == (
        'count(service_requests{namespace="default"}) > bool 0'
    )
    assert observer.PROMETHEUS_QUERIES["queue"] == (
        'count(poll_success{namespace="default",taskqueue="medchat-docking"}) '
        '> bool 0'
    )
    queries: list[str] = []

    def fetcher(endpoint: str, query: str | None) -> dict[str, object]:
        if endpoint == "alerts":
            return _prom_alerts()
        assert query is not None
        queries.append(query)
        if query == observer.PROMETHEUS_QUERIES["worker_age_seconds"]:
            return _prom_scalar(5)
        if query == observer.PROMETHEUS_QUERIES["queue"]:
            return _prom_scalar(queue_value)
        return _prom_scalar(1)

    _worker, infrastructure, _alerts = observer._prometheus_state(fetcher)

    assert infrastructure["queue"] is expected
    assert all("task_queue" not in query for query in queries)
    assert all('namespace!=""' not in query for query in queries)
