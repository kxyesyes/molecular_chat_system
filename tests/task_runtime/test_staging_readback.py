"""Readback integrity only: controlled inputs, no docking or scientific claims."""

import hashlib
import json
import os
from pathlib import Path

import pytest

import src.task_runtime.staging as staging_module
from src.task_runtime.staging import DockingInputStager, ManifestError
from tests.task_runtime.test_staging import DOCKING_CONFIG, RAW_SMILES


RECEPTOR = (
    b"ATOM      1  CA  ALA A   1       1.000   2.000   3.000  1.00 20.00           C  \n"
    b"END\n"
)
LIGAND_SDF = (
    b"methane\n  readback fixture\n\n"
    b"  1  0  0  0  0  0  0  0  0  0999 V2000\n"
    b"    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0\n"
    b"M  END\n$$$$\n"
)
LOCATOR = "input_manifest.json"


@pytest.fixture(params=["file", "smiles"])
def staged(tmp_path, request):
    stager = DockingInputStager(tmp_path / "staging")
    smiles = request.param == "smiles"
    path = stager.stage(
        "task-1", "receptor.pdb", RECEPTOR,
        None if smiles else "ligand.sdf", None if smiles else LIGAND_SDF,
        RAW_SMILES if smiles else None, DOCKING_CONFIG,
    )
    return stager, path, RAW_SMILES.encode() if smiles else LIGAND_SDF


def test_readback_returns_exact_current_bytes_and_detached_manifest(staged):
    stager, path, ligand = staged
    previous = stager.load_verified("task-1", path)
    result = stager.read_verified_input_bytes("task-1", LOCATOR)

    assert set(result) == {"manifest", "receptor_bytes", "ligand_bytes"}
    assert result["manifest"] == previous
    for key, expected in (("receptor", RECEPTOR), ("ligand", ligand)):
        value = result[key + "_bytes"]
        assert type(value) is bytes
        assert value == expected
        assert hashlib.sha256(value).hexdigest() == previous[key]["sha256"]
        with pytest.raises(TypeError):
            value[0] = 0

    result["manifest"]["config"]["center"][0] = 999
    result["manifest"]["receptor"]["sha256"] = "0" * 64
    again = stager.read_verified_input_bytes("task-1", LOCATOR)
    assert again["manifest"] == previous
    assert json.loads(path.read_text(encoding="utf-8")) == previous


def test_readback_uses_lock_and_secure_reader_without_writes_or_leases(staged, monkeypatch):
    stager, path, ligand = staged
    root = path.parent.parent
    before = {p.relative_to(root): p.read_bytes() if p.is_file() else None
              for p in root.rglob("*")}
    real_lock = stager._lock
    held = False

    class ObservedLock:
        def __enter__(self):
            nonlocal held
            real_lock.acquire()
            held = True

        def __exit__(self, *args):
            nonlocal held
            held = False
            real_lock.release()

    def forbidden(*args, **kwargs):
        pytest.fail("readback must not enter execution or create a lease")

    for name in ("execution_snapshot", "_create_execution_snapshot", "task_execution",
                 "task_execution_lease", "_task_lease"):
        monkeypatch.setattr(stager, name, forbidden)
    monkeypatch.setattr(stager, "_lock", ObservedLock())
    real_read = staging_module._secure_read_file
    reads = []

    def read(candidate, **kwargs):
        assert held
        reads.append((candidate, kwargs.copy()))
        return real_read(candidate, **kwargs)

    monkeypatch.setattr(staging_module, "_secure_read_file", read)
    result = stager.read_verified_input_bytes("task-1", LOCATOR)
    assert not held
    for key, expected in (("receptor", RECEPTOR), ("ligand", ligand)):
        record = result["manifest"][key]
        candidate = path.parent / record["path"]
        limit = (staging_module.MAX_SMILES_BYTES
                 if key == "ligand" and result["manifest"]["ligand_mode"] == "smiles"
                 else staging_module.MAX_DOCKING_INPUT_BYTES)
        assert [options for p, options in reads if p == candidate] == [
            dict(max_bytes=limit, expected_size=len(expected), collect=collect,
                 reason_code="manifest_integrity_failed", containment_root=path.parent)
            for collect in (False, True)
        ]
    assert before == {p.relative_to(root): p.read_bytes() if p.is_file() else None
                      for p in root.rglob("*")}


@pytest.mark.parametrize("task_id", [None, 1, "", "../task-1", "task/1", "CON", "x" * 129])
def test_readback_rejects_malformed_task_ids(staged, task_id):
    stager, _, _ = staged
    with pytest.raises(ManifestError, match="^manifest_invalid_task_id$"):
        stager.read_verified_input_bytes(task_id, LOCATOR)


@pytest.mark.parametrize("locator", [None, 1, "", "./input_manifest.json",
    "../input_manifest.json", "task-1/input_manifest.json", "inputs/receptor.pdb",
    "INPUT_MANIFEST.JSON", Path(LOCATOR)])
def test_readback_rejects_other_locators(staged, locator):
    stager, _, _ = staged
    with pytest.raises(ManifestError, match="^manifest_path_invalid$"):
        stager.read_verified_input_bytes("task-1", locator)


def test_readback_rejects_absolute_manifest_path(staged):
    stager, path, _ = staged
    with pytest.raises(ManifestError, match="^manifest_path_invalid$"):
        stager.read_verified_input_bytes("task-1", str(path))


@pytest.mark.parametrize("field", ["receptor", "ligand"])
@pytest.mark.parametrize("change", ["bytes", "size", "digest", "escape"])
def test_readback_rejects_changed_input_or_manifest(staged, field, change):
    stager, path, _ = staged
    payload = json.loads(path.read_text(encoding="utf-8"))
    candidate = path.parent / payload[field]["path"]
    reason = "manifest_integrity_failed"
    if change == "bytes":
        raw = candidate.read_bytes()
        candidate.write_bytes(bytes([raw[0] ^ 1]) + raw[1:])
    elif change == "size":
        payload[field]["size"] += 1
    elif change == "digest":
        payload[field]["sha256"] = "0" * 64
    else:
        payload[field]["path"] = "../outside"
        reason = "manifest_path_invalid"
    path.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ManifestError, match=f"^{reason}$"):
        stager.read_verified_input_bytes("task-1", LOCATOR)


@pytest.mark.parametrize("field", ["receptor", "ligand"])
@pytest.mark.parametrize("change", ["same_size", "grow", "replace", "missing"])
def test_readback_rejects_drift_after_manifest_validation(staged, monkeypatch, field, change):
    stager, path, _ = staged
    real_load = stager._load_verified_unlocked

    def load(*args):
        manifest = real_load(*args)
        candidate = path.parent / manifest[field]["path"]
        raw = candidate.read_bytes()
        altered = bytes([raw[0] ^ 1]) + raw[1:]
        if change == "missing":
            candidate.unlink()
        elif change == "replace":
            replacement = candidate.with_suffix(".replacement")
            replacement.write_bytes(altered)
            os.replace(replacement, candidate)
        else:
            candidate.write_bytes(raw + b"X" if change == "grow" else altered)
        return manifest

    monkeypatch.setattr(stager, "_load_verified_unlocked", load)
    with pytest.raises(ManifestError, match="^manifest_integrity_failed$"):
        stager.read_verified_input_bytes("task-1", LOCATOR)


@pytest.mark.parametrize("field", ["receptor", "ligand"])
@pytest.mark.parametrize("link_kind", ["symlink", "hardlink"])
@pytest.mark.parametrize("after_validation", [False, True])
def test_readback_rejects_linked_inputs(staged, monkeypatch, field, link_kind, after_validation):
    stager, path, _ = staged
    real_load = stager._load_verified_unlocked

    def link_input(manifest):
        candidate = path.parent / manifest[field]["path"]
        outside = path.parent.parent.parent / "outside-input"
        outside.write_bytes(candidate.read_bytes())
        candidate.unlink()
        try:
            if link_kind == "symlink":
                candidate.symlink_to(outside)
            else:
                os.link(outside, candidate)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"{link_kind} unavailable: {type(exc).__name__}")

    if after_validation:
        def load(*args):
            manifest = real_load(*args)
            link_input(manifest)
            return manifest
        monkeypatch.setattr(stager, "_load_verified_unlocked", load)
    else:
        link_input(real_load("task-1", path))
    reason = "manifest_path_invalid" if link_kind == "symlink" else "manifest_integrity_failed"
    with pytest.raises(ManifestError, match=f"^{reason}$"):
        stager.read_verified_input_bytes("task-1", LOCATOR)
