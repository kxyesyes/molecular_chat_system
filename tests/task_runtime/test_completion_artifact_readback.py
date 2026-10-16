"""Artifact integrity fixtures only; no docking execution or scientific claims."""

import hashlib
import json
import os
import stat
from dataclasses import replace

import pytest

import src.task_runtime.completion as completion_module
from src.task_runtime.completion import (
    AUTHORITY_COMMITTED,
    CompletionError,
    DockingCompletionStore,
    DockingResultMetadata,
)
from src.task_runtime.staging import DockingInputStager
from src.task_runtime.docking_execution import _manifest_input_hash


TASK = "task-1"
POSE = (
    b"MODEL 1\nREMARK VINA RESULT: -7.000 0.000 0.000\nROOT\n"
    b"ATOM      1  C   LIG A   1       0.000   0.000   0.000  0.00  0.00    +0.000 C\n"
    b"ENDROOT\nTORSDOF 0\nENDMDL\n"
)


def _digest(completion):
    return hashlib.sha256(json.dumps(
        completion.to_dict(), sort_keys=True, separators=(",", ":"),
        ensure_ascii=True, allow_nan=False,
    ).encode("ascii")).hexdigest()


@pytest.fixture
def completed(tmp_path):
    """Publish through real staging, capture, prepare, finalize and load checks."""
    root = tmp_path / "staging"
    stager = DockingInputStager(root)
    path = stager.stage(
        TASK, "receptor.pdb", b"ATOM\n", "ligand.sdf", b"$$$$\n", None,
        {"center": [1, 2, 3], "size": [20, 20, 20],
         "exhaustiveness": 8, "num_modes": 10},
    )
    staged = stager.load_verified(TASK, path)
    store = DockingCompletionStore(root)
    attempt = store.begin_artifact_attempt(TASK)
    records = []
    contents = []
    for index in range(8):
        content = bytes([index, 255, 0]) + b"fixture\r\n"
        source = path.parent / f"output-{index}.txt"
        source.write_bytes(content)
        record = store.capture_additional_artifact(
            attempt, artifact_index=8 - index, path=source,
        )
        assert record is not None
        if index == 7:
            record = replace(record, mime_type=None)
        records.append(record)
        contents.append(content)
    pose_source = path.parent / "source.pdbqt"
    pose_source.write_bytes(POSE)
    metadata = DockingResultMetadata(
        elapsed_ms=1, formatted="readback fixture \u6d4b\u8bd5", warnings=[], evidence=[],
        artifacts=records,
        quality={"real_execution": True, "validated": True,
                 "engine": "vina", "execution_status": "succeeded"},
    )
    prepared = store.prepare(
        TASK, input_hash=_manifest_input_hash(staged), config_hash=staged["config_hash"],
        tool_version="fixture-1", model_name=None, model_version=None,
        demo_mode=False, fallback_used=False, real_execution=True,
        pose_source=pose_source, pose_containment_root=path.parent,
        expected_pose_size=len(POSE), expected_pose_sha256=hashlib.sha256(POSE).hexdigest(),
        pose_count=1, best_energy=-7.0, best_pose_index=1,
        validator_status="succeeded", attempt=1, result_metadata=metadata,
        artifact_attempt=attempt,
    )
    completion, durable, authority = store.finalize(prepared)
    assert durable and authority == AUTHORITY_COMMITTED
    assert store.load_verified(
        TASK, input_hash=completion.input_hash, config_hash=completion.config_hash,
    ) == completion
    return store, completion, contents


def _read(completed, artifact_id="pose", **changes):
    store, completion, _ = completed
    kwargs = dict(task_id=TASK, input_hash=completion.input_hash,
                  config_hash=completion.config_hash,
                  completion_digest=_digest(completion), artifact_id=artifact_id)
    kwargs.update(changes)
    return store.read_verified_artifact(**kwargs)


def _artifact_path(completed, artifact_id="pose"):
    store, completion, _ = completed
    record = (completion.pose if artifact_id == "pose" else
              completion.result_metadata.artifacts[int(artifact_id[-2:])])
    return store.manifest_path(TASK).parent / record.path


def _change_manifest(completed, change):
    path = completed[0].manifest_path(TASK)
    payload = json.loads(path.read_bytes())
    change(payload)
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_read_verified_artifact_method_exists():
    assert callable(getattr(DockingCompletionStore, "read_verified_artifact", None))


def test_exact_bytes_schema_ordered_extras_and_safe_mime(completed):
    _, completion, contents = completed
    expected = [("pose", POSE, "chemical/x-pdbqt")]
    expected += [(f"extra-{i:02d}", content,
                  "application/octet-stream" if i == 7 else "text/plain")
                 for i, content in enumerate(contents)]
    for artifact_id, content, mime in expected:
        result = _read(completed, artifact_id)
        assert result == {
            "content": content, "sha256": hashlib.sha256(content).hexdigest(),
            "size": len(content), "mime_type": mime, "artifact_id": artifact_id,
            "completion_digest": _digest(completion),
        }
        assert type(result) is dict
        for key, kind in {"content": bytes, "sha256": str, "size": int,
                          "mime_type": str, "artifact_id": str,
                          "completion_digest": str}.items():
            assert type(result[key]) is kind


@pytest.mark.parametrize("field", ["input_hash", "config_hash", "completion_digest"])
def test_identity_mismatch(completed, field):
    with pytest.raises(CompletionError, match="completion_identity_mismatch"):
        _read(completed, **{field: "0" * 64})


class HookString(str):
    def trap(self, *args, **kwargs):
        raise AssertionError("caller string hook executed")

    __str__ = __eq__ = __hash__ = __getitem__ = __fspath__ = trap
    strip = startswith = encode = trap


class HookObject:
    def trap(self, *args, **kwargs):
        raise AssertionError("caller object hook executed")

    __str__ = __eq__ = __hash__ = __index__ = __fspath__ = trap


@pytest.mark.parametrize("field", ["task_id", "input_hash", "config_hash",
                                  "completion_digest", "artifact_id"])
@pytest.mark.parametrize("bad", [True, 0, None, b"pose", HookString("pose"), HookObject()],
                         ids=["bool", "int", "none", "bytes", "str-subclass", "hooks"])
def test_native_strings_only_without_caller_hooks(tmp_path, field, bad):
    (tmp_path / TASK).mkdir()
    store = DockingCompletionStore(tmp_path)
    kwargs = dict(task_id=TASK, input_hash="a" * 64, config_hash="b" * 64,
                  completion_digest="c" * 64, artifact_id="pose")
    kwargs[field] = HookString(kwargs[field]) if type(bad) is HookString else bad
    expected = ("completion_invalid_task" if field == "task_id" else
                "completion_artifact_invalid" if field == "artifact_id" else
                "completion_schema_invalid")
    with pytest.raises(CompletionError, match=expected):
        store.read_verified_artifact(**kwargs)


@pytest.mark.parametrize("field", ["input_hash", "config_hash", "completion_digest"])
@pytest.mark.parametrize("bad", ["A" * 64, "a" * 63, "a" * 65, "g" * 64, "a" * 64 + "\n"])
def test_hashes_are_exact_lower_hex(tmp_path, field, bad):
    (tmp_path / TASK).mkdir()
    kwargs = dict(task_id=TASK, input_hash="a" * 64, config_hash="b" * 64,
                  completion_digest="c" * 64, artifact_id="pose")
    kwargs[field] = bad
    with pytest.raises(CompletionError, match="completion_schema_invalid"):
        DockingCompletionStore(tmp_path).read_verified_artifact(**kwargs)


@pytest.mark.parametrize("bad", ["", "../task-1", "task-1/pose", "task-1\\pose", "a" * 129])
def test_task_validation_is_preserved(tmp_path, bad):
    with pytest.raises(CompletionError, match="completion_invalid_task"):
        DockingCompletionStore(tmp_path).read_verified_artifact(
            bad, input_hash="a" * 64, config_hash="b" * 64,
            completion_digest="c" * 64, artifact_id="pose",
        )


def test_rejects_unknown_foreign_path_and_absent_ids(completed):
    store, _, _ = completed
    bad_ids = ["", "POSE", "pose\n", "extra-0", "extra-08", "extra-99",
               "extra-00\n", "../task-2/pose", "task-2:pose",
               "artifacts/docking_pose.pdbqt", str(_artifact_path(completed)),
               "C:\\foreign\\pose", "extra-00/../pose"]
    for artifact_id in bad_ids:
        with pytest.raises(CompletionError, match="completion_artifact_invalid"):
            _read(completed, artifact_id)
    _change_manifest(completed, lambda p: p["result_metadata"].update(artifacts=[]))
    current = store.load_verified(TASK, input_hash=completed[1].input_hash,
                                  config_hash=completed[1].config_hash)
    with pytest.raises(CompletionError, match="completion_artifact_invalid"):
        _read(completed, "extra-00", completion_digest=_digest(current))


def test_completion_digest_is_canonical_not_raw_json(completed):
    path = completed[0].manifest_path(TASK)
    payload = json.loads(path.read_bytes())
    path.write_text(json.dumps(payload, indent=4, ensure_ascii=False), encoding="utf-8")
    assert _read(completed)["content"] == POSE


@pytest.mark.parametrize("when", ["before", "after"])
def test_rejects_metadata_drift_across_byte_read(completed, monkeypatch, when):
    real_read = completion_module._secure_read_regular
    target = _artifact_path(completed)
    triggered = []

    def drift():
        _change_manifest(completed, lambda p: p["result_metadata"].update(formatted="changed"))
        triggered.append(True)

    def read(path, **kwargs):
        if path == target and when == "before":
            drift()
        result = real_read(path, **kwargs)
        if path == target and when == "after":
            drift()
        return result

    monkeypatch.setattr(completion_module, "_secure_read_regular", read)
    with pytest.raises(CompletionError, match="completion_identity_mismatch"):
        _read(completed)
    assert triggered == [True]


@pytest.mark.parametrize("artifact_id", ["pose", "extra-00"])
@pytest.mark.parametrize("when", ["before", "during", "after"])
def test_same_size_tamper_not_hidden_by_old_path(completed, monkeypatch, artifact_id, when):
    path = _artifact_path(completed, artifact_id)
    original = path.read_bytes()
    changed = bytes([original[0] ^ 1]) + original[1:]
    real_read = completion_module._secure_read_regular
    triggered = []
    if when == "before":
        path.write_bytes(changed)

    def read(candidate, **kwargs):
        if candidate != path:
            return real_read(candidate, **kwargs)
        triggered.append(True)
        if when == "during":
            path.write_bytes(changed)
        result = real_read(candidate, **kwargs)
        if when == "during":
            # Restore the old path before the final verification: the bytes
            # actually returned by the secure reader must still be rejected.
            path.write_bytes(original)
        if when == "after":
            path.write_bytes(changed)
        return result

    monkeypatch.setattr(completion_module, "_secure_read_regular", read)
    with pytest.raises(CompletionError, match="completion_artifact_invalid"):
        _read(completed, artifact_id)
    assert len(triggered) == (0 if when == "before" else 1)


@pytest.mark.parametrize("when", ["before", "after-bytes", "during-final-load"])
def test_invalidation_blocks_even_late_in_final_load(completed, monkeypatch, when):
    store, _, _ = completed
    marker = store.manifest_path(TASK).parent / "completion_invalid.marker"
    if when == "before":
        marker.write_bytes(b"invalid")
    real_read = completion_module._secure_read_regular
    real_hash = completion_module._secure_hash_regular
    target = _artifact_path(completed)
    bytes_read = False
    triggered = []

    def read(path, **kwargs):
        nonlocal bytes_read
        result = real_read(path, **kwargs)
        if path == target:
            bytes_read = True
            if when == "after-bytes":
                marker.write_bytes(b"invalid")
                triggered.append(True)
        return result

    def hash_file(path, **kwargs):
        result = real_hash(path, **kwargs)
        if bytes_read and when == "during-final-load" and not triggered:
            marker.write_bytes(b"invalid")
            triggered.append(True)
        return result

    monkeypatch.setattr(completion_module, "_secure_read_regular", read)
    monkeypatch.setattr(completion_module, "_secure_hash_regular", hash_file)
    with pytest.raises(CompletionError, match="completion_ownership_uncertain"):
        _read(completed)
    assert len(triggered) == (0 if when == "before" else 1)


@pytest.mark.parametrize("artifact_id", ["pose", "extra-00"])
@pytest.mark.parametrize("kind", ["symlink", "hardlink", "reparse"])
def test_links_rejected(completed, tmp_path, artifact_id, kind):
    path = _artifact_path(completed, artifact_id)
    if kind == "reparse" and os.name != "nt":
        pytest.skip("Windows reparse points are not available on this platform")
    target = tmp_path / "foreign-bytes"
    target.write_bytes(path.read_bytes())
    path.unlink()
    try:
        if kind == "hardlink":
            os.link(target, path)
        else:
            path.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        pytest.skip(f"{kind} creation unavailable: {type(exc).__name__} {getattr(exc, 'winerror', None)}")
    if kind == "reparse":
        assert path.lstat().st_file_attributes & stat.FILE_ATTRIBUTE_REPARSE_POINT
    with pytest.raises(CompletionError, match="completion_artifact_invalid"):
        _read(completed, artifact_id)


def test_read_only_bounded_reads_and_immutable_return(completed, monkeypatch):
    store, completion, _ = completed
    root = store.manifest_path(TASK).parent

    def snapshot():
        return {p.relative_to(root): p.read_bytes() if p.is_file() else None
                for p in root.rglob("*")}

    before = snapshot()
    real_read = completion_module._secure_read_regular
    reads = []

    def read(path, **kwargs):
        reads.append((path, kwargs.copy()))
        return real_read(path, **kwargs)

    monkeypatch.setattr(completion_module, "_secure_read_regular", read)
    for artifact_id, record, limit in [
        ("pose", completion.pose, completion_module.MAX_DOCKING_POSE_BYTES),
        ("extra-00", completion.result_metadata.artifacts[0],
         completion_module.MAX_ADDITIONAL_ARTIFACT_BYTES),
    ]:
        reads.clear()
        result = _read(completed, artifact_id)
        artifact_reads = [kw for path, kw in reads if path == root / record.path]
        assert artifact_reads == [{"max_bytes": limit, "containment_root": root,
                                   "expected_size": record.size,
                                   "invalid_code": "completion_artifact_invalid"}]
        assert sum(path == store.manifest_path(TASK) for path, _ in reads) == 2
        assert snapshot() == before
        content = result["content"]
        with pytest.raises(TypeError):
            content[0] = 0
    path = _artifact_path(completed, "extra-00")
    path.write_bytes(b"changed after return")
    assert result["content"] == completed[2][0]
    assert hashlib.sha256(result["content"]).hexdigest() == result["sha256"]
