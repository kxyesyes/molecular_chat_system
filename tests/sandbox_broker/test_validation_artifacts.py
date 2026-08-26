from __future__ import annotations

import ast
import asyncio
import hashlib
import io
import json
import math
import os
import sqlite3
from pathlib import Path

import pytest

import src.sandbox_broker.artifacts as artifacts_module
import src.sandbox_broker.validation as validation_module
from src.sandbox_broker.artifacts import ArtifactRegistry
from src.sandbox_broker.store import BrokerStore
from src.sandbox_broker.validation import (
    InputStagingError,
    ScientificOutputError,
    stage_input,
    validate_scientific_output,
)


POSE = b"REMARK VINA RESULT: -7.4 0.0 0.0\nMODEL 1\nENDMDL\n"
SHA_A = "a" * 64
SHA_B = "b" * 64


class RecordingSource:
    def __init__(self, content: bytes, *, failure: Exception | None = None) -> None:
        self._content = content
        self._offset = 0
        self._failure = failure
        self.requests: list[int] = []

    def read(self, size: int) -> bytes:
        self.requests.append(size)
        if self._failure is not None:
            raise self._failure
        result = self._content[self._offset : self._offset + size]
        self._offset += len(result)
        return result


def build_valid_output(tmp_path: Path, *, pose: bytes = POSE) -> Path:
    output = tmp_path / "output"
    poses = output / "poses"
    poses.mkdir(parents=True)
    (poses / "result.pdbqt").write_bytes(pose)
    payload = {
        "schema_version": 1,
        "status": "succeeded",
        "receptor_sha256": SHA_A,
        "ligand_sha256": SHA_B,
        "pose_count": 1,
        "best_energy": -7.4,
        "pose_files": ["poses/result.pdbqt"],
        "vina_version": "1.2.5",
        "meeko_version": "0.6.1",
        "warnings": [],
    }
    (output / "result.json").write_text(
        json.dumps(payload, allow_nan=True), encoding="utf-8"
    )
    return output


def rewrite_result(output: Path, mutation: dict[str, object]) -> None:
    result = output / "result.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    payload.update(mutation)
    result.write_text(json.dumps(payload, allow_nan=True), encoding="utf-8")


def make_store_and_job(tmp_path: Path, key: str = "idem-1") -> tuple[BrokerStore, str]:
    state_root = tmp_path / "state"
    state_root.mkdir()
    store = BrokerStore(state_root / "broker.sqlite")
    job, _ = store.create_or_get(key, SHA_A, "trace-1")
    return store, job.job_id


def test_stage_input_streams_one_mib_chunks_hashes_and_ignores_client_path(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    job_id = "1" * 32
    content = b"a" * (1024 * 1024 + 17)
    source = RecordingSource(content)

    staged = stage_input(
        state_root,
        job_id,
        "receptor",
        ".pdb",
        source,
        len(content),
        client_filename="../../host-secret.pdb",
    )

    assert source.requests == [1024 * 1024, 1024 * 1024, 1024 * 1024]
    assert staged.relative_path == f"jobs/{job_id}/input/receptor.pdb"
    assert staged.size_bytes == len(content)
    assert staged.sha256 == hashlib.sha256(content).hexdigest()
    assert (state_root / Path(*staged.relative_path.split("/"))).read_bytes() == content
    assert not list((state_root / "jobs" / job_id / "input").glob(".*.part"))
    assert str(state_root) not in repr(staged)


@pytest.mark.parametrize(
    ("role", "suffix"),
    [
        ("receptor", ".sdf"),
        ("receptor", ".pdbqt"),
        ("ligand", ".txt"),
        ("ligand", ".pdb"),
        ("ligand", ".pdbqt"),
        ("RECEPTOR", ".pdb"),
        ("ligand", ".PDBQT"),
        (True, ".pdb"),
        ("receptor", 1),
    ],
)
def test_stage_input_rejects_invalid_role_or_suffix(
    tmp_path: Path, role: object, suffix: object
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    with pytest.raises(InputStagingError):
        stage_input(state_root, "1" * 32, role, suffix, io.BytesIO(b"x"), 10)


@pytest.mark.parametrize("job_id", ["", "1" * 31, "A" * 32, "../" + "1" * 32, 3])
def test_stage_input_requires_uuid_hex(tmp_path: Path, job_id: object) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    with pytest.raises(InputStagingError):
        stage_input(state_root, job_id, "ligand", ".sdf", io.BytesIO(b"x"), 10)


@pytest.mark.parametrize(
    ("role", "suffix"),
    [("receptor", ".pdb"), ("ligand", ".sdf"), ("ligand", ".mol")],
)
def test_stage_input_accepts_only_meeko_supported_suffixes(
    tmp_path: Path, role: str, suffix: str
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    staged = stage_input(
        state_root,
        "1" * 32,
        role,
        suffix,
        io.BytesIO(b"content"),
        10,
    )
    assert staged.relative_path.endswith(f"/{role}{suffix}")


@pytest.mark.parametrize("limit", [True, "1", -1, 0])
def test_stage_input_rejects_confused_or_nonpositive_limit(
    tmp_path: Path, limit: object
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    with pytest.raises(InputStagingError):
        stage_input(state_root, "1" * 32, "ligand", ".sdf", io.BytesIO(b"x"), limit)


def test_stage_input_accepts_exact_limit_and_rejects_overflow_without_part(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    job_id = "1" * 32
    exact = stage_input(
        state_root, job_id, "ligand", ".sdf", io.BytesIO(b"1234"), 4
    )
    assert exact.size_bytes == 4

    with pytest.raises(InputStagingError):
        stage_input(
            state_root, job_id, "receptor", ".pdb", io.BytesIO(b"12345"), 4
        )
    input_root = state_root / "jobs" / job_id / "input"
    assert not (input_root / "receptor.pdb").exists()
    assert not (input_root / ".receptor.pdb.part").exists()


@pytest.mark.parametrize("content", [b"", bytearray(b"x"), "not-bytes"])
def test_stage_input_rejects_empty_or_non_bytes_and_cleans_part(
    tmp_path: Path, content: object
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    job_id = "1" * 32
    source = RecordingSource(b"") if content == b"" else type("Source", (), {"read": lambda self, size: content})()
    with pytest.raises(InputStagingError):
        stage_input(state_root, job_id, "ligand", ".sdf", source, 10)
    assert not (state_root / "jobs" / job_id / "input" / ".ligand.sdf.part").exists()


def test_stage_input_redacts_source_failure_and_preserves_existing_final(
    tmp_path: Path,
) -> None:
    state_root = tmp_path / "state-secret"
    state_root.mkdir()
    job_id = "1" * 32
    stage_input(state_root, job_id, "ligand", ".sdf", io.BytesIO(b"old"), 10)

    source = RecordingSource(b"", failure=OSError("payload-secret"))
    with pytest.raises(InputStagingError) as caught:
        stage_input(state_root, job_id, "receptor", ".pdb", source, 10)
    rendered = str(caught.value)
    assert "payload-secret" not in rendered
    assert str(state_root) not in rendered

    with pytest.raises(InputStagingError):
        stage_input(state_root, job_id, "ligand", ".sdf", io.BytesIO(b"new"), 10)
    assert (state_root / "jobs" / job_id / "input" / "ligand.sdf").read_bytes() == b"old"


def test_stage_input_removes_new_final_when_directory_fsync_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    job_id = "1" * 32

    def fail_fsync(path: Path) -> None:
        del path
        raise OSError("fsync-secret")

    monkeypatch.setattr(validation_module, "_fsync_directory", fail_fsync)
    with pytest.raises(InputStagingError) as caught:
        stage_input(state_root, job_id, "ligand", ".sdf", io.BytesIO(b"content"), 10)

    input_root = state_root / "jobs" / job_id / "input"
    assert not (input_root / "ligand.sdf").exists()
    assert not (input_root / ".ligand.sdf.part").exists()
    assert "fsync-secret" not in str(caught.value)


def test_stage_input_rejects_hardlinked_part_and_final(tmp_path: Path) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    job_id = "1" * 32
    input_root = state_root / "jobs" / job_id / "input"
    input_root.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_bytes(b"victim")
    try:
        os.link(victim, input_root / ".ligand.sdf.part")
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {type(exc).__name__}")
    with pytest.raises(InputStagingError):
        stage_input(state_root, job_id, "ligand", ".sdf", io.BytesIO(b"new"), 10)
    assert victim.read_bytes() == b"victim"
    (input_root / ".ligand.sdf.part").unlink()

    os.link(victim, input_root / "ligand.sdf")
    with pytest.raises(InputStagingError):
        stage_input(state_root, job_id, "ligand", ".sdf", io.BytesIO(b"new"), 10)
    assert victim.read_bytes() == b"victim"
    assert not (input_root / ".ligand.sdf.part").exists()


def test_stage_input_never_overwrites_final_created_during_publication(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    job_id = "1" * 32
    original_replace = os.replace
    original_link = os.link

    def competing_replace(source: object, destination: object, *args: object, **kwargs: object) -> None:
        Path(destination).write_bytes(b"competing")
        original_replace(source, destination, *args, **kwargs)

    def competing_link(source: object, destination: object, *args: object, **kwargs: object) -> None:
        Path(destination).write_bytes(b"competing")
        original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(validation_module.os, "replace", competing_replace)
    monkeypatch.setattr(validation_module.os, "link", competing_link)
    with pytest.raises(InputStagingError):
        stage_input(state_root, job_id, "ligand", ".sdf", io.BytesIO(b"ours"), 10)

    final = state_root / "jobs" / job_id / "input" / "ligand.sdf"
    assert final.read_bytes() == b"competing"
    assert not (final.parent / ".ligand.sdf.part").exists()


@pytest.mark.parametrize(
    "interrupt_type",
    [KeyboardInterrupt, SystemExit, asyncio.CancelledError],
)
def test_stage_input_read_interrupt_closes_fd_cleans_part_and_retries(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interrupt_type: type[BaseException],
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    job_id = "1" * 32
    opened_parts: list[int] = []
    original_open = os.open

    def capture_open(path: object, flags: int, mode: int = 0o777) -> int:
        descriptor = original_open(path, flags, mode)
        if str(path).endswith(".part"):
            opened_parts.append(descriptor)
        return descriptor

    class InterruptingSource:
        def read(self, size: int) -> bytes:
            del size
            raise interrupt_type()

    with monkeypatch.context() as patch:
        patch.setattr(validation_module.os, "open", capture_open)
        with pytest.raises(interrupt_type):
            stage_input(
                state_root,
                job_id,
                "ligand",
                ".sdf",
                InterruptingSource(),  # type: ignore[arg-type]
                16,
            )

    assert len(opened_parts) == 1
    descriptor_closed = False
    try:
        os.fstat(opened_parts[0])
    except OSError:
        descriptor_closed = True
    if not descriptor_closed:
        os.close(opened_parts[0])
    assert descriptor_closed
    input_root = state_root / "jobs" / job_id / "input"
    assert not (input_root / ".ligand.sdf.part").exists()

    retried = stage_input(
        state_root, job_id, "ligand", ".sdf", io.BytesIO(b"retry"), 16
    )
    assert retried.size_bytes == 5


@pytest.mark.parametrize("failure_point", ["write", "fsync", "publish"])
def test_stage_input_io_interrupt_cleans_part_and_allows_retry(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    failure_point: str,
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    job_id = "1" * 32
    opened_parts: list[int] = []
    original_open = os.open
    original_link = os.link

    def capture_open(path: object, flags: int, mode: int = 0o777) -> int:
        descriptor = original_open(path, flags, mode)
        if str(path).endswith(".part"):
            opened_parts.append(descriptor)
        return descriptor

    def interrupt_write(descriptor: int, content: object) -> int:
        del descriptor, content
        raise KeyboardInterrupt

    def interrupt_fsync(descriptor: int) -> None:
        del descriptor
        raise KeyboardInterrupt

    def publish_then_interrupt(
        source: object,
        destination: object,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        original_link(source, destination, follow_symlinks=follow_symlinks)
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(validation_module.os, "open", capture_open)
        if failure_point == "write":
            patch.setattr(validation_module.os, "write", interrupt_write)
        elif failure_point == "fsync":
            patch.setattr(validation_module.os, "fsync", interrupt_fsync)
        else:
            patch.setattr(validation_module.os, "link", publish_then_interrupt)
        with pytest.raises(KeyboardInterrupt):
            stage_input(
                state_root, job_id, "ligand", ".sdf", io.BytesIO(b"content"), 16
            )

    assert len(opened_parts) == 1
    descriptor_closed = False
    try:
        os.fstat(opened_parts[0])
    except OSError:
        descriptor_closed = True
    if not descriptor_closed:
        os.close(opened_parts[0])
    assert descriptor_closed
    input_root = state_root / "jobs" / job_id / "input"
    assert not (input_root / ".ligand.sdf.part").exists()

    retried = stage_input(
        state_root, job_id, "ligand", ".sdf", io.BytesIO(b"content"), 16
    )
    assert retried.size_bytes == 7


def test_stage_input_interrupt_never_deletes_replaced_part(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    state_root = tmp_path / "state"
    state_root.mkdir()
    job_id = "1" * 32

    def replace_part_then_interrupt(
        source: object,
        destination: object,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        del destination, follow_symlinks
        part = Path(source)
        part.unlink()
        part.write_bytes(b"attacker")
        raise KeyboardInterrupt

    with monkeypatch.context() as patch:
        patch.setattr(validation_module.os, "link", replace_part_then_interrupt)
        with pytest.raises(KeyboardInterrupt):
            stage_input(
                state_root, job_id, "ligand", ".sdf", io.BytesIO(b"content"), 16
            )

    part = state_root / "jobs" / job_id / "input" / ".ligand.sdf.part"
    assert part.read_bytes() == b"attacker"
    with pytest.raises(InputStagingError):
        stage_input(
            state_root, job_id, "ligand", ".sdf", io.BytesIO(b"content"), 16
        )
    part.unlink()
    assert stage_input(
        state_root, job_id, "ligand", ".sdf", io.BytesIO(b"content"), 16
    ).size_bytes == 7


def test_stage_input_rejects_state_and_part_symlinks(tmp_path: Path) -> None:
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    try:
        alias.symlink_to(real, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {type(exc).__name__}")

    with pytest.raises(InputStagingError):
        stage_input(alias, "1" * 32, "ligand", ".sdf", io.BytesIO(b"x"), 10)

    input_root = real / "jobs" / ("1" * 32) / "input"
    input_root.mkdir(parents=True)
    victim = tmp_path / "victim"
    victim.write_bytes(b"secret")
    (input_root / ".ligand.sdf.part").symlink_to(victim)
    with pytest.raises(InputStagingError):
        stage_input(real, "1" * 32, "ligand", ".sdf", io.BytesIO(b"x"), 10)
    assert victim.read_bytes() == b"secret"


def test_scientific_output_returns_immutable_safe_metadata(tmp_path: Path) -> None:
    output = build_valid_output(tmp_path)
    rewrite_result(output, {"warnings": ["host-secret-warning"]})
    validated = validate_scientific_output(
        output,
        receptor_sha256=SHA_A,
        ligand_sha256=SHA_B,
        max_output_bytes=1024,
    )
    assert validated.pose_count == 1
    assert validated.best_energy == -7.4
    assert validated.pose_files == ("poses/result.pdbqt",)
    assert validated.warnings == ("host-secret-warning",)
    assert str(output) not in repr(validated)
    assert "host-secret-warning" not in repr(validated)
    with pytest.raises(AttributeError):
        validated.pose_count = 2  # type: ignore[misc]


@pytest.mark.parametrize(
    "mutation",
    [
        {"ligand_sha256": "c" * 64},
        {"receptor_sha256": "A" * 64},
        {"pose_count": 0},
        {"pose_count": -1},
        {"pose_count": True},
        {"best_energy": "-7.4"},
        {"best_energy": True},
        {"best_energy": math.nan},
        {"best_energy": math.inf},
        {"best_energy": -math.inf},
        {"schema_version": True},
        {"schema_version": 2},
        {"status": "failed"},
        {"vina_version": ""},
        {"meeko_version": 1},
        {"warnings": [1]},
    ],
)
def test_scientific_output_rejects_invalid_exact_fields(
    tmp_path: Path, mutation: dict[str, object]
) -> None:
    output = build_valid_output(tmp_path)
    rewrite_result(output, mutation)
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


@pytest.mark.parametrize("change", ["missing", "extra"])
def test_scientific_output_requires_exact_json_schema(tmp_path: Path, change: str) -> None:
    output = build_valid_output(tmp_path)
    result = output / "result.json"
    payload = json.loads(result.read_text(encoding="utf-8"))
    if change == "missing":
        del payload["warnings"]
    else:
        payload["secret"] = "must not be accepted"
    result.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


@pytest.mark.parametrize(
    "pose_path",
    [
        "",
        "/poses/result.pdbqt",
        "./poses/result.pdbqt",
        "poses/../result.pdbqt",
        "poses\\result.pdbqt",
        "C:/poses/result.pdbqt",
        "poses//result.pdbqt",
        "poses/r\N{LATIN SMALL LETTER E WITH ACUTE}sult.pdbqt",
        "poses/result.pdb",
    ],
)
def test_scientific_output_rejects_pose_path_aliases(
    tmp_path: Path, pose_path: str
) -> None:
    output = build_valid_output(tmp_path)
    rewrite_result(output, {"pose_files": [pose_path]})
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


def test_scientific_output_rejects_duplicate_count_and_missing_pose(tmp_path: Path) -> None:
    output = build_valid_output(tmp_path)
    rewrite_result(
        output,
        {"pose_count": 2, "pose_files": ["poses/result.pdbqt", "poses/result.pdbqt"]},
    )
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)

    output = build_valid_output(tmp_path / "count")
    rewrite_result(output, {"pose_count": 2})
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)

    output = build_valid_output(tmp_path / "missing")
    (output / "poses" / "result.pdbqt").unlink()
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


@pytest.mark.parametrize(
    "pose",
    [
        b"",
        b"MODEL 1\nENDMDL\n",
        b"REMARK VINA RESULT: -7.3 0.0 0.0\n",
        b"REMARK VINA RESULT: nan 0.0 0.0\n",
        b"REMARK VINA RESULT: inf 0.0 0.0\n",
    ],
)
def test_scientific_output_rejects_empty_missing_or_mismatched_energy(
    tmp_path: Path, pose: bytes
) -> None:
    output = build_valid_output(tmp_path, pose=pose)
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


def test_scientific_output_requires_best_energy_to_be_global_minimum(
    tmp_path: Path,
) -> None:
    pose = (
        b"REMARK VINA RESULT: -7.4 0.0 0.0\n"
        b"REMARK VINA RESULT: -8.1 0.0 0.0\n"
        b"MODEL 1\nENDMDL\n"
    )
    output = build_valid_output(tmp_path, pose=pose)
    rewrite_result(output, {"pose_count": 2})
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


def test_scientific_output_counts_all_modes_in_one_pose_file(tmp_path: Path) -> None:
    pose = (
        b"REMARK VINA RESULT: -8.1 0.0 0.0\n"
        b"MODEL 1\nENDMDL\n"
        b"REMARK VINA RESULT: -7.2 1.0 1.5\n"
        b"MODEL 2\nENDMDL\n"
    )
    output = build_valid_output(tmp_path, pose=pose)
    rewrite_result(output, {"pose_count": 2, "best_energy": -8.1})

    validated = validate_scientific_output(output, SHA_A, SHA_B, 1024)

    assert validated.pose_count == 2
    assert validated.pose_files == ("poses/result.pdbqt",)


def test_scientific_output_rejects_mode_count_not_file_count(tmp_path: Path) -> None:
    pose = (
        b"REMARK VINA RESULT: -8.1 0.0 0.0\n"
        b"REMARK VINA RESULT: -7.2 1.0 1.5\n"
    )
    output = build_valid_output(tmp_path, pose=pose)
    rewrite_result(output, {"pose_count": 1, "best_energy": -8.1})

    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


def test_scientific_output_counts_modes_across_unique_pose_files(
    tmp_path: Path,
) -> None:
    first = (
        b"REMARK VINA RESULT: -8.1 0.0 0.0\n"
        b"REMARK VINA RESULT: -7.2 1.0 1.5\n"
    )
    output = build_valid_output(tmp_path, pose=first)
    (output / "poses" / "second.pdbqt").write_bytes(
        b"REMARK VINA RESULT: -6.5 2.0 2.5\n"
    )
    rewrite_result(
        output,
        {
            "pose_count": 3,
            "best_energy": -8.1,
            "pose_files": ["poses/result.pdbqt", "poses/second.pdbqt"],
        },
    )

    validated = validate_scientific_output(output, SHA_A, SHA_B, 2048)

    assert validated.pose_count == 3
    assert validated.pose_files == (
        "poses/result.pdbqt",
        "poses/second.pdbqt",
    )


@pytest.mark.parametrize(
    "invalid_remark",
    [
        b"REMARK VINA RESULT: nan 1.0 1.5\n",
        b"REMARK VINA RESULT: -7.0 1.0\n",
        b"REMARK VINA RESULT broken\n",
        b" REMARK VINA RESULT: -7.0 1.0 1.5\n",
        b"REMARK  VINA RESULT: -7.0 1.0 1.5\n",
    ],
)
def test_scientific_output_rejects_invalid_later_mode(
    tmp_path: Path, invalid_remark: bytes
) -> None:
    pose = b"REMARK VINA RESULT: -7.4 0.0 0.0\n" + invalid_remark
    output = build_valid_output(tmp_path, pose=pose)
    rewrite_result(output, {"pose_count": 1})

    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


def test_scientific_output_requires_first_mode_to_be_best(tmp_path: Path) -> None:
    pose = (
        b"REMARK VINA RESULT: -7.0 0.0 0.0\n"
        b"REMARK VINA RESULT: -8.0 1.0 1.5\n"
    )
    output = build_valid_output(tmp_path, pose=pose)
    rewrite_result(output, {"pose_count": 2, "best_energy": -8.0})

    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


def test_scientific_output_requires_non_decreasing_mode_energies(
    tmp_path: Path,
) -> None:
    pose = (
        b"REMARK VINA RESULT: -8.0 0.0 0.0\n"
        b"REMARK VINA RESULT: -6.0 1.0 1.5\n"
        b"REMARK VINA RESULT: -7.0 2.0 2.5\n"
    )
    output = build_valid_output(tmp_path, pose=pose)
    rewrite_result(output, {"pose_count": 3, "best_energy": -8.0})

    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


def test_scientific_output_reads_second_pose_only_with_remaining_budget(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    output = build_valid_output(tmp_path)
    second_content = b"REMARK VINA RESULT: -6.5 1.0 1.5\n"
    (output / "poses" / "second.pdbqt").write_bytes(second_content)
    rewrite_result(
        output,
        {
            "pose_count": 2,
            "pose_files": ["poses/result.pdbqt", "poses/second.pdbqt"],
        },
    )
    result_size = (output / "result.json").stat().st_size
    total_size = result_size + len(POSE) + len(second_content)
    second_descriptors: set[int] = set()
    second_requests: list[int] = []
    original_open = os.open
    original_read = os.read

    def tracking_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if dir_fd is None:
            descriptor = original_open(path, flags, mode)
        else:
            descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if str(path).endswith("second.pdbqt"):
            second_descriptors.add(descriptor)
        return descriptor

    def tracking_read(descriptor: int, size: int) -> bytes:
        if descriptor in second_descriptors:
            second_requests.append(size)
        return original_read(descriptor, size)

    with monkeypatch.context() as patch:
        patch.setattr(validation_module.os, "open", tracking_open)
        patch.setattr(validation_module.os, "read", tracking_read)
        validated = validate_scientific_output(
            output, SHA_A, SHA_B, total_size
        )

    assert validated.pose_count == 2
    assert second_requests
    assert max(second_requests) <= len(second_content) + 1
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, total_size - 1)


def test_scientific_output_allows_sibling_churn_during_incremental_pose_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pose = b"ATOM 1\n" * 12_000 + POSE
    output = build_valid_output(tmp_path, pose=pose)
    sibling = output.parent / "concurrent-sibling"
    pose_descriptors: set[int] = set()
    original_open = os.open
    original_read = os.read
    created = False

    def tracking_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if dir_fd is None:
            descriptor = original_open(path, flags, mode)
        else:
            descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if str(path).endswith("result.pdbqt"):
            pose_descriptors.add(descriptor)
        return descriptor

    def create_sibling_while_reading(descriptor: int, size: int) -> bytes:
        nonlocal created
        content = original_read(descriptor, size)
        if descriptor in pose_descriptors and content and not created:
            sibling.mkdir()
            sibling.rmdir()
            created = True
        return content

    with monkeypatch.context() as patch:
        patch.setattr(validation_module.os, "open", tracking_open)
        patch.setattr(validation_module.os, "read", create_sibling_while_reading)
        validated = validate_scientific_output(output, SHA_A, SHA_B, 256 * 1024)

    assert created
    assert validated.pose_count == 1


def test_scientific_output_rejects_path_component_replaced_during_pose_read(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    pose = b"ATOM 1\n" * 12_000 + POSE
    output = build_valid_output(tmp_path, pose=pose)
    moved = output.parent / "moved-output"
    pose_descriptors: set[int] = set()
    original_open = os.open
    original_read = os.read
    replaced = False
    unsupported = False

    def tracking_open(
        path: object,
        flags: int,
        mode: int = 0o777,
        *,
        dir_fd: int | None = None,
    ) -> int:
        if dir_fd is None:
            descriptor = original_open(path, flags, mode)
        else:
            descriptor = original_open(path, flags, mode, dir_fd=dir_fd)
        if str(path).endswith("result.pdbqt"):
            pose_descriptors.add(descriptor)
        return descriptor

    def replace_component_while_reading(descriptor: int, size: int) -> bytes:
        nonlocal replaced, unsupported
        content = original_read(descriptor, size)
        if descriptor in pose_descriptors and content and not replaced and not unsupported:
            try:
                os.replace(output, moved)
                output.mkdir()
                (output / "poses").mkdir()
                replaced = True
            except OSError:
                unsupported = True
        return content

    caught: ScientificOutputError | None = None
    with monkeypatch.context() as patch:
        patch.setattr(validation_module.os, "open", tracking_open)
        patch.setattr(validation_module.os, "read", replace_component_while_reading)
        try:
            validate_scientific_output(output, SHA_A, SHA_B, 256 * 1024)
        except ScientificOutputError as exc:
            caught = exc

    if unsupported:
        pytest.skip("open-directory replacement unavailable")
    assert replaced
    assert caught is not None


@pytest.mark.parametrize(
    "prefix",
    [
        b"A" * 4097 + b"\n",
        b"ordinary\x00binary\n",
        "non-ascii-\N{LATIN SMALL LETTER E WITH ACUTE}\n".encode("utf-8"),
        b"\xff\n",
    ],
)
def test_scientific_output_rejects_unbounded_or_non_ascii_pose_lines(
    tmp_path: Path, prefix: bytes
) -> None:
    output = build_valid_output(tmp_path, pose=prefix + POSE)
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 8192)


def test_scientific_output_rejects_more_than_64_pose_files(tmp_path: Path) -> None:
    output = build_valid_output(tmp_path)
    pose_files: list[str] = []
    for index in range(65):
        name = f"poses/mode-{index:02d}.pdbqt"
        (output / "poses" / f"mode-{index:02d}.pdbqt").write_bytes(POSE)
        pose_files.append(name)
    rewrite_result(output, {"pose_count": 65, "pose_files": pose_files})

    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 32 * 1024)


def test_scientific_output_rejects_more_than_256_modes(tmp_path: Path) -> None:
    pose = b"REMARK VINA RESULT: -7.4 0.0 0.0\n" * 257
    output = build_valid_output(tmp_path, pose=pose)
    rewrite_result(output, {"pose_count": 256})

    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 32 * 1024)


@pytest.mark.parametrize(
    "warnings",
    [
        ["warning"] * 65,
        ["w" * 1025],
    ],
)
def test_scientific_output_bounds_warning_cardinality_and_length(
    tmp_path: Path, warnings: list[str]
) -> None:
    output = build_valid_output(tmp_path)
    rewrite_result(output, {"warnings": warnings})

    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 16 * 1024)


@pytest.mark.parametrize(
    "remark",
    [
        b"REMARK VINA RESULT: -7_4 0.0 0.0\n",
        b"REMARK VINA RESULT: -7.4 0_0 0.0\n",
        "REMARK VINA RESULT: -7.4 \N{ARABIC-INDIC DIGIT ZERO}.0 0.0\n".encode("utf-8"),
        b"REMARK VINA RESULT: -7,4 0.0 0.0\n",
        b"REMARK VINA RESULT: 0x1p0 0.0 0.0\n",
        b"REMARK VINA RESULT: -7.4tail 0.0 0.0\n",
        b"REMARK VINA RESULT: nan 0.0 0.0\n",
        b"REMARK VINA RESULT: -7.4 inf 0.0\n",
        b" REMARK VINA RESULT: -7.4 0.0 0.0\n",
        b"REMARK  VINA RESULT: -7.4 0.0 0.0\n",
    ],
)
def test_scientific_output_rejects_non_ascii_decimal_vina_tokens(
    tmp_path: Path, remark: bytes
) -> None:
    output = build_valid_output(tmp_path, pose=remark)
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


def test_scientific_output_accepts_strict_decimal_and_exponent_tokens(
    tmp_path: Path,
) -> None:
    pose = (
        b"REMARK VINA RESULT: -7.4e0 +0 .5\n"
        b"REMARK VINA RESULT: -6E+0 1. 2e-3\n"
    )
    output = build_valid_output(tmp_path, pose=pose)
    rewrite_result(output, {"pose_count": 2})

    validated = validate_scientific_output(output, SHA_A, SHA_B, 1024)

    assert validated.pose_count == 2
    assert validated.best_energy == -7.4


def test_scientific_output_enforces_total_size_and_strict_limit_type(tmp_path: Path) -> None:
    output = build_valid_output(tmp_path)
    total = (output / "result.json").stat().st_size + len(POSE)
    assert validate_scientific_output(output, SHA_A, SHA_B, total).pose_count == 1
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, total - 1)
    for invalid in (True, "1024", -1, 0):
        with pytest.raises(ScientificOutputError):
            validate_scientific_output(output, SHA_A, SHA_B, invalid)


def test_scientific_output_rejects_symlink_pose_and_redacts_errors(tmp_path: Path) -> None:
    output = build_valid_output(tmp_path / "secret-root")
    pose = output / "poses" / "result.pdbqt"
    target = tmp_path / "outside-secret.pdbqt"
    target.write_bytes(POSE)
    pose.unlink()
    try:
        pose.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {type(exc).__name__}")
    with pytest.raises(ScientificOutputError) as caught:
        validate_scientific_output(output, SHA_A, SHA_B, 1024)
    assert str(tmp_path) not in str(caught.value)
    assert "outside-secret" not in str(caught.value)


@pytest.mark.parametrize("target_name", ["result", "pose"])
def test_scientific_output_rejects_hardlinked_result_or_pose(
    tmp_path: Path, target_name: str
) -> None:
    output = build_valid_output(tmp_path)
    selected = (
        output / "result.json"
        if target_name == "result"
        else output / "poses" / "result.pdbqt"
    )
    outside = tmp_path / f"outside-{target_name}"
    outside.write_bytes(selected.read_bytes())
    selected.unlink()
    try:
        os.link(outside, selected)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {type(exc).__name__}")
    with pytest.raises(ScientificOutputError):
        validate_scientific_output(output, SHA_A, SHA_B, 1024)


def test_scientific_output_requires_real_poses_directory_and_redacts_payload(
    tmp_path: Path,
) -> None:
    output = build_valid_output(tmp_path / "host-secret")
    pose = output / "poses" / "result.pdbqt"
    pose.unlink()
    (output / "poses").rmdir()
    (output / "poses").write_bytes(b"payload-secret")
    with pytest.raises(ScientificOutputError) as caught:
        validate_scientific_output(output, SHA_A, SHA_B, 1024)
    rendered = str(caught.value)
    assert str(tmp_path) not in rendered
    assert "payload-secret" not in rendered


def test_artifact_publish_and_identifier_only_read(tmp_path: Path) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)
    record = registry.publish(job_id, "../display-only", POSE, "chemical/x-pdbqt")

    assert len(record.artifact_id) == 32
    assert record.job_id == job_id
    assert record.relative_path == f"jobs/{job_id}/published/{record.artifact_id}"
    assert record.size_bytes == len(POSE)
    assert record.sha256 == hashlib.sha256(POSE).hexdigest()
    assert registry.read_registered(job_id, record.artifact_id, 1024) == POSE
    with pytest.raises(KeyError):
        registry.read_registered(job_id, "../pose", 1024)
    assert str(store.db_path.parent) not in repr(registry)


def test_artifact_publish_rejects_final_replaced_after_link(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)
    original_link = os.link

    def link_then_replace(
        source: object,
        destination: object,
        *,
        follow_symlinks: bool = True,
    ) -> None:
        original_link(source, destination, follow_symlinks=follow_symlinks)
        replacement = Path(destination).with_suffix(".replacement")
        replacement.write_bytes(b"attacker")
        os.replace(replacement, destination)

    monkeypatch.setattr(artifacts_module.os, "link", link_then_replace)

    with pytest.raises(ValueError):
        registry.publish(job_id, "pose", POSE, "chemical/x-pdbqt")

    assert store.list_artifacts(job_id) == []


def test_artifact_publish_rejects_final_replaced_during_registration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)
    original_register = store.register_artifact

    def replace_then_register(
        artifact_id: str,
        registered_job_id: str,
        relative_path: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
    ) -> object:
        final = store.db_path.parent / Path(*relative_path.split("/"))
        replacement = final.with_suffix(".replacement")
        replacement.write_bytes(b"x" * size_bytes)
        os.replace(replacement, final)
        return original_register(
            artifact_id,
            registered_job_id,
            relative_path,
            media_type,
            size_bytes,
            sha256,
        )

    monkeypatch.setattr(store, "register_artifact", replace_then_register)

    with pytest.raises(ValueError):
        registry.publish(job_id, "pose", POSE, "chemical/x-pdbqt")

    assert store.list_artifacts(job_id) == []


def test_artifact_rejects_unknown_job_cross_job_media_type_and_bad_limits(
    tmp_path: Path,
) -> None:
    store, first_job = make_store_and_job(tmp_path)
    second, _ = store.create_or_get("idem-2", SHA_B, "trace-2")
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=8)
    record = registry.publish(first_job, "pose", b"12345678", "chemical/x-pdbqt")

    with pytest.raises(KeyError):
        registry.publish("f" * 32, "pose", b"x", "chemical/x-pdbqt")
    with pytest.raises(ValueError):
        registry.publish(first_job, "pose", b"x", "text/html")
    with pytest.raises(ValueError):
        registry.publish(first_job, "pose", b"123456789", "chemical/x-pdbqt")
    with pytest.raises(KeyError):
        registry.read_registered(second.job_id, record.artifact_id, 1024)
    for invalid in (True, "8", -1):
        with pytest.raises(ValueError):
            registry.read_registered(first_job, record.artifact_id, invalid)


def test_artifact_registry_enforces_fixed_constructor_ceiling(tmp_path: Path) -> None:
    store, _ = make_store_and_job(tmp_path)
    with pytest.raises(ValueError):
        ArtifactRegistry(
            store.db_path.parent,
            store,
            max_artifact_bytes=100 * 1024 * 1024 + 1,
        )


def test_artifact_read_rejects_size_hash_drift_and_symlink_replace(tmp_path: Path) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)

    size_record = registry.publish(job_id, "size", b"abc", "text/plain")
    size_path = store.db_path.parent / Path(*size_record.relative_path.split("/"))
    size_path.write_bytes(b"abcd")
    with pytest.raises(ValueError):
        registry.read_registered(job_id, size_record.artifact_id, 1024)

    hash_record = registry.publish(job_id, "hash", b"abc", "text/plain")
    hash_path = store.db_path.parent / Path(*hash_record.relative_path.split("/"))
    hash_path.write_bytes(b"xyz")
    with pytest.raises(ValueError):
        registry.read_registered(job_id, hash_record.artifact_id, 1024)

    link_record = registry.publish(job_id, "link", b"abc", "text/plain")
    link_path = store.db_path.parent / Path(*link_record.relative_path.split("/"))
    target = tmp_path / "target"
    target.write_bytes(b"abc")
    link_path.unlink()
    try:
        link_path.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {type(exc).__name__}")
    with pytest.raises(ValueError):
        registry.read_registered(job_id, link_record.artifact_id, 1024)


def test_artifact_read_rejects_missing_hardlink_and_registered_path_drift(
    tmp_path: Path,
) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)

    missing = registry.publish(job_id, "missing", b"abc", "text/plain")
    missing_path = store.db_path.parent / Path(*missing.relative_path.split("/"))
    missing_path.unlink()
    with pytest.raises(ValueError):
        registry.read_registered(job_id, missing.artifact_id, 1024)

    hardlink = registry.publish(job_id, "hardlink", b"abc", "text/plain")
    hardlink_path = store.db_path.parent / Path(*hardlink.relative_path.split("/"))
    outside = tmp_path / "outside"
    outside.write_bytes(b"abc")
    hardlink_path.unlink()
    try:
        os.link(outside, hardlink_path)
    except OSError as exc:
        pytest.skip(f"hardlinks unavailable: {type(exc).__name__}")
    with pytest.raises(ValueError):
        registry.read_registered(job_id, hardlink.artifact_id, 1024)

    drift = registry.publish(job_id, "drift", b"abc", "text/plain")
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE artifacts SET relative_path=? WHERE artifact_id=?",
            (f"jobs/{job_id}/published/other", drift.artifact_id),
        )
    with pytest.raises(ValueError):
        registry.read_registered(job_id, drift.artifact_id, 1024)


def test_artifact_db_failure_removes_published_file_and_redacts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)

    def fail_registration(*args: object, **kwargs: object) -> object:
        raise RuntimeError("database-secret")

    monkeypatch.setattr(store, "register_artifact", fail_registration)
    with pytest.raises(ValueError) as caught:
        registry.publish(job_id, "pose", POSE, "chemical/x-pdbqt")
    published = store.db_path.parent / "jobs" / job_id / "published"
    assert not published.exists() or not list(published.iterdir())
    assert store.list_artifacts(job_id) == []
    assert "database-secret" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


def test_artifact_uncertain_commit_preserves_file_when_confirmation_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)
    original_register = store.register_artifact

    def commit_then_fail(*args: object, **kwargs: object) -> object:
        original_register(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("register-secret")

    def fail_confirmation(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise RuntimeError("query-secret")

    monkeypatch.setattr(store, "register_artifact", commit_then_fail)
    monkeypatch.setattr(store, "get_artifact", fail_confirmation)
    with pytest.raises(ValueError) as caught:
        registry.publish(job_id, "pose", POSE, "chemical/x-pdbqt")

    records = store.list_artifacts(job_id)
    assert len(records) == 1
    recorded_path = store.db_path.parent / Path(*records[0].relative_path.split("/"))
    assert recorded_path.read_bytes() == POSE
    rendered = str(caught.value)
    assert "register-secret" not in rendered
    assert "query-secret" not in rendered
    assert str(tmp_path) not in rendered


def test_artifact_confirmed_commit_returns_success_after_register_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)
    original_register = store.register_artifact

    def commit_then_fail(*args: object, **kwargs: object) -> object:
        original_register(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("register-secret")

    monkeypatch.setattr(store, "register_artifact", commit_then_fail)

    record = registry.publish(job_id, "pose", POSE, "chemical/x-pdbqt")

    assert store.get_artifact(job_id, record.artifact_id) == record
    recorded_path = store.db_path.parent / Path(*record.relative_path.split("/"))
    assert recorded_path.read_bytes() == POSE


def test_artifact_conflicting_commit_preserves_file_and_fails_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)
    original_register = store.register_artifact

    def commit_conflict_then_fail(
        artifact_id: str,
        registered_job_id: str,
        relative_path: str,
        media_type: str,
        size_bytes: int,
        sha256: str,
    ) -> object:
        del media_type
        original_register(
            artifact_id,
            registered_job_id,
            relative_path,
            "text/plain",
            size_bytes,
            sha256,
        )
        raise RuntimeError("conflict-secret")

    monkeypatch.setattr(store, "register_artifact", commit_conflict_then_fail)
    with pytest.raises(ValueError) as caught:
        registry.publish(job_id, "pose", POSE, "chemical/x-pdbqt")

    records = store.list_artifacts(job_id)
    assert len(records) == 1
    assert records[0].media_type == "text/plain"
    recorded_path = store.db_path.parent / Path(*records[0].relative_path.split("/"))
    assert recorded_path.read_bytes() == POSE
    assert "conflict-secret" not in str(caught.value)
    assert str(tmp_path) not in str(caught.value)


@pytest.mark.parametrize(
    "interrupt_type",
    [KeyboardInterrupt, SystemExit, asyncio.CancelledError],
)
def test_artifact_committed_register_interrupt_preserves_file_and_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interrupt_type: type[BaseException],
) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)
    original_register = store.register_artifact

    def commit_then_interrupt(*args: object, **kwargs: object) -> object:
        original_register(*args, **kwargs)  # type: ignore[arg-type]
        raise interrupt_type()

    monkeypatch.setattr(store, "register_artifact", commit_then_interrupt)
    with pytest.raises(interrupt_type):
        registry.publish(job_id, "pose", POSE, "chemical/x-pdbqt")

    records = store.list_artifacts(job_id)
    assert len(records) == 1
    recorded_path = store.db_path.parent / Path(*records[0].relative_path.split("/"))
    assert recorded_path.read_bytes() == POSE


def test_artifact_precommit_interrupt_cleans_only_after_confirmed_absence(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)

    def interrupt_before_commit(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise KeyboardInterrupt

    monkeypatch.setattr(store, "register_artifact", interrupt_before_commit)
    with pytest.raises(KeyboardInterrupt):
        registry.publish(job_id, "pose", POSE, "chemical/x-pdbqt")

    assert store.list_artifacts(job_id) == []
    published = store.db_path.parent / "jobs" / job_id / "published"
    assert not published.exists() or not list(published.iterdir())


@pytest.mark.parametrize(
    "interrupt_type",
    [KeyboardInterrupt, SystemExit, asyncio.CancelledError],
)
def test_artifact_confirmation_interrupt_preserves_committed_file_and_type(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    interrupt_type: type[BaseException],
) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)
    original_register = store.register_artifact

    def commit_then_fail(*args: object, **kwargs: object) -> object:
        original_register(*args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError("register-secret")

    def interrupt_confirmation(*args: object, **kwargs: object) -> object:
        del args, kwargs
        raise interrupt_type()

    monkeypatch.setattr(store, "register_artifact", commit_then_fail)
    monkeypatch.setattr(store, "get_artifact", interrupt_confirmation)
    with pytest.raises(interrupt_type):
        registry.publish(job_id, "pose", POSE, "chemical/x-pdbqt")

    records = store.list_artifacts(job_id)
    assert len(records) == 1
    recorded_path = store.db_path.parent / Path(*records[0].relative_path.split("/"))
    assert recorded_path.read_bytes() == POSE


def test_artifact_publish_never_overwrites_competing_final(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    store, job_id = make_store_and_job(tmp_path)
    registry = ArtifactRegistry(store.db_path.parent, store, max_artifact_bytes=1024)
    original_replace = os.replace
    original_link = os.link

    def competing_replace(source: object, destination: object, *args: object, **kwargs: object) -> None:
        Path(destination).write_bytes(b"competing")
        original_replace(source, destination, *args, **kwargs)

    def competing_link(source: object, destination: object, *args: object, **kwargs: object) -> None:
        Path(destination).write_bytes(b"competing")
        original_link(source, destination, *args, **kwargs)

    monkeypatch.setattr(artifacts_module.os, "replace", competing_replace)
    monkeypatch.setattr(artifacts_module.os, "link", competing_link)
    with pytest.raises(ValueError):
        registry.publish(job_id, "pose", POSE, "chemical/x-pdbqt")

    assert store.list_artifacts(job_id) == []
    published = store.db_path.parent / "jobs" / job_id / "published"
    files = [path for path in published.iterdir() if not path.name.startswith(".")]
    assert len(files) == 1
    assert files[0].read_bytes() == b"competing"
    assert not list(published.glob(".*.part"))


@pytest.mark.parametrize("module_name", ["validation.py", "artifacts.py"])
def test_validation_artifact_sources_parse_as_python_310(module_name: str) -> None:
    source = Path(__file__).parents[2] / "src" / "sandbox_broker" / module_name
    ast.parse(source.read_text(encoding="utf-8"), feature_version=(3, 10))
