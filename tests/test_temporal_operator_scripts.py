from __future__ import annotations

from datetime import datetime, timedelta, timezone
import errno
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import stat
import subprocess
import sys
from types import SimpleNamespace
import urllib.error

import pytest

from scripts import manage_temporal_canary as manager
from scripts import observe_temporal_canary as observer
from src.task_runtime.models import TaskStatus
from src.task_runtime.observation import ObservationPolicy, build_observation_report
from src.task_runtime.rollout import RolloutEvidence


PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANAGE_SCRIPT = PROJECT_ROOT / "scripts" / "manage_temporal_canary.py"
OBSERVE_SCRIPT = PROJECT_ROOT / "scripts" / "observe_temporal_canary.py"
PYTHON = sys.executable


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
    assert _run_manage("status", "--env-file", str(env_file)).returncode != 0


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
                result={"pose_count": 1, "best_energy": -7.0, "pose_file": relative},
                provenance={
                    "tool_name": "molecular_docking",
                    "tool_version": "vina-1.2.5",
                    "demo_mode": False,
                    "fallback_used": False,
                    "dsn": "postgres://private-dsn",
                    "environment": "ENV_SECRET_CANARY",
                },
                artifacts=[{"artifact_type": "docking_pose", "path": relative, "sha256": digest}],
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
        "missing_pose_file",
        "path_mismatch",
        "hash_alias_conflict",
        "result_digest_conflict",
        "completion_digest_conflict",
        "size_mismatch",
        "malformed_size",
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
    pose_path = staging / record.task_id / record.result["pose_file"]
    if mutation == "missing_pose_file":
        record.result.pop("pose_file")
    elif mutation == "path_mismatch":
        record.result["pose_file"] = "artifacts/other_pose.pdbqt"
    elif mutation == "hash_alias_conflict":
        artifact["hash"] = "b" * 64
    elif mutation == "result_digest_conflict":
        record.result["pose_sha256"] = "b" * 64
    elif mutation == "completion_digest_conflict":
        record.result["completion"] = {"pose_sha256": "b" * 64}
    elif mutation == "size_mismatch":
        artifact["size"] = pose_path.stat().st_size + 1
    else:
        artifact["size"] = "RAW_SIZE_CANARY"
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


def test_observer_artifact_hash_rejects_same_size_content_mutation(
    tmp_path: Path,
) -> None:
    now = datetime.now(timezone.utc)
    store, staging, _marker = _observer_fixture(tmp_path, now)
    record = store.records[0]
    artifact = staging / record.task_id / record.result["pose_file"]
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


def test_observer_firing_allowlisted_alert_is_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    now = datetime.now(timezone.utc)
    store, staging, marker = _observer_fixture(tmp_path, now)
    monkeypatch.setattr(observer, "_task_staging_root", lambda: staging)

    def firing(endpoint: str, query: str | None = None):
        if endpoint == "alerts":
            return _prom_alerts("TemporalArtifactValidationFailure", "PrivateAlertCanary")
        return _passed_prometheus(endpoint, query)

    report = observer.collect_observation(
        db_path=tmp_path / "tasks.sqlite", current_level=5,
        window_start=now - timedelta(hours=2), window_end=now - timedelta(minutes=1),
        prometheus_url="http://127.0.0.1:9090", backup_state=marker,
        baseline_p95_seconds=40.0, now=now,
        observation_store_factory=lambda _path: store, prometheus_fetcher=firing,
    )

    assert report["status"] == "failed"
    assert report["blocking_alerts"] == ["TemporalArtifactValidationFailure"]
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
