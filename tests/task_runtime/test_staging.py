from __future__ import annotations

import json
import math
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

import pytest

import src.task_runtime.staging as staging_module
from src.task_runtime.staging import DockingInputStager, ManifestError


DOCKING_CONFIG = {
    "center": [1, 2, 3],
    "size": [20, 20, 20],
    "exhaustiveness": 8,
    "num_modes": 10,
}
RAW_SMILES = "CC(=O)Oc1ccccc1C(=O)O"


def _stage_file(
    stager: DockingInputStager,
    task_id: str = "task-1",
    *,
    config: dict[str, Any] | None = None,
) -> Path:
    return stager.stage(
        task_id,
        "receptor.pdb",
        b"ATOM\n",
        "ligand.sdf",
        b"$$$$\n",
        None,
        DOCKING_CONFIG if config is None else config,
    )


def _stage_smiles(
    stager: DockingInputStager,
    task_id: str = "task-smiles",
) -> Path:
    return stager.stage(
        task_id,
        "receptor.pdb",
        b"ATOM\n",
        None,
        None,
        RAW_SMILES,
        DOCKING_CONFIG,
    )


def _manifest_payload(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _replace_manifest(path: Path, payload: object) -> None:
    path.write_text(json.dumps(payload), encoding="utf-8")


def _assert_safe_error(
    error: ManifestError,
    *,
    forbidden: tuple[str, ...] = (),
) -> None:
    assert error.reason_code.startswith("manifest_")
    message = str(error)
    assert message == error.reason_code
    for value in forbidden:
        if not value:
            continue
        assert value not in message
        assert value not in repr(error)


def test_staged_files_are_hashed_and_survive_request_scope(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)

    path = _stage_file(stager)
    manifest = stager.load_verified("task-1", path)

    assert manifest["schema"] == "DockingInputManifest@1"
    assert manifest["task_id"] == "task-1"
    assert manifest["type"] == "docking"
    assert manifest["receptor"]["sha256"]
    assert manifest["ligand"]["sha256"]
    assert manifest["ligand_mode"] == "file"
    assert manifest["config"] == {
        "center": [1.0, 2.0, 3.0],
        "size": [20.0, 20.0, 20.0],
        "exhaustiveness": 8,
        "num_modes": 10,
    }
    assert (tmp_path / "task-1" / "inputs" / "receptor.pdb").is_file()
    assert path == tmp_path.resolve() / "task-1" / "input_manifest.json"


def test_manifest_contains_only_relative_posix_paths_and_valid_digests(
    tmp_path: Path,
) -> None:
    path = _stage_file(DockingInputStager(tmp_path))
    payload = _manifest_payload(path)

    assert set(payload) == {
        "schema",
        "task_id",
        "type",
        "receptor",
        "ligand",
        "ligand_mode",
        "config",
        "config_hash",
        "created_at",
    }
    assert set(payload["receptor"]) == {"path", "size", "sha256"}
    assert set(payload["ligand"]) == {"path", "size", "sha256"}
    assert payload["receptor"]["path"] == "inputs/receptor.pdb"
    assert payload["ligand"]["path"] == "inputs/ligand.sdf"
    assert "\\" not in payload["receptor"]["path"]
    assert not Path(payload["receptor"]["path"]).is_absolute()
    for digest in (
        payload["receptor"]["sha256"],
        payload["ligand"]["sha256"],
        payload["config_hash"],
    ):
        assert len(digest) == 64
        assert digest == digest.lower()
        int(digest, 16)
    assert payload["created_at"].endswith("Z")
    assert str(tmp_path.resolve()) not in path.read_text(encoding="utf-8")


def test_smiles_is_staged_in_restricted_file_not_manifest_or_repr(
    tmp_path: Path,
) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_smiles(stager)
    payload = _manifest_payload(path)
    manifest_text = path.read_text(encoding="utf-8")

    assert payload["ligand_mode"] == "smiles"
    assert payload["ligand"]["path"] == "inputs/smiles.txt"
    assert RAW_SMILES not in manifest_text
    assert RAW_SMILES not in repr(stager)
    assert str(tmp_path.resolve()) not in repr(stager)
    assert (tmp_path / "task-smiles" / "inputs" / "smiles.txt").read_text(
        encoding="utf-8"
    ) == RAW_SMILES
    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) & 0o077 == 0
        assert (
            stat.S_IMODE((path.parent / "inputs" / "smiles.txt").stat().st_mode)
            & 0o077
            == 0
        )


def test_nonblank_smiles_is_staged_byte_exact_without_manifest_disclosure(
    tmp_path: Path,
) -> None:
    stager = DockingInputStager(tmp_path)
    raw_smiles = " CCO "

    path = stager.stage(
        "task-exact",
        "receptor.pdb",
        b"ATOM",
        None,
        None,
        raw_smiles,
        DOCKING_CONFIG,
    )

    assert (path.parent / "inputs" / "smiles.txt").read_bytes() == raw_smiles.encode(
        "utf-8"
    )
    assert raw_smiles not in path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "task_id",
    [
        "",
        ".",
        "..",
        "../other",
        r"..\other",
        "a/b",
        r"a\b",
        "%2e%2e",
        "%2Fetc",
        "%5cserver",
        "/absolute",
        r"C:\absolute",
        " task",
        "task ",
        "task\x00id",
        "任务-1",
        "a" * 129,
    ],
)
def test_stage_rejects_unsafe_task_ids_without_echoing_them(
    tmp_path: Path,
    task_id: str,
) -> None:
    stager = DockingInputStager(tmp_path)

    with pytest.raises(ManifestError) as caught:
        _stage_file(stager, task_id)

    _assert_safe_error(caught.value, forbidden=(task_id, str(tmp_path.resolve())))


@pytest.mark.parametrize(
    "name",
    [
        "",
        ".",
        "..",
        "../r.pdb",
        r"..\r.pdb",
        "folder/r.pdb",
        r"folder\r.pdb",
        "%2e%2e%2fr.pdb",
        "%5cr.pdb",
        "/tmp/r.pdb",
        r"C:\tmp\r.pdb",
        "r\x00.pdb",
        "受体.pdb",
        "r" * 257,
    ],
)
def test_stage_rejects_unsafe_input_names_without_echoing_them(
    tmp_path: Path,
    name: str,
) -> None:
    stager = DockingInputStager(tmp_path)

    with pytest.raises(ManifestError) as caught:
        stager.stage(
            "task-1",
            name,
            b"ATOM",
            "ligand.sdf",
            b"$$$$",
            None,
            DOCKING_CONFIG,
        )

    _assert_safe_error(caught.value, forbidden=(name, str(tmp_path.resolve())))


def test_stage_requires_nonempty_receptor_bytes(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    for receptor in (b"", bytearray(b"ATOM"), "ATOM", None):
        with pytest.raises(ManifestError) as caught:
            stager.stage(
                "task-1",
                "r.pdb",
                receptor,  # type: ignore[arg-type]
                "l.sdf",
                b"$$$$",
                None,
                DOCKING_CONFIG,
            )
        _assert_safe_error(caught.value)


@pytest.mark.parametrize(
    ("ligand_name", "ligand_bytes", "smiles"),
    [
        (None, None, None),
        ("l.sdf", b"$$$$", RAW_SMILES),
        (None, None, ""),
        (None, None, "   "),
        ("l.sdf", b"", None),
    ],
)
def test_stage_requires_exactly_one_nonempty_ligand_mode(
    tmp_path: Path,
    ligand_name: str | None,
    ligand_bytes: bytes | None,
    smiles: str | None,
) -> None:
    stager = DockingInputStager(tmp_path)

    with pytest.raises(ManifestError) as caught:
        stager.stage(
            "task-1",
            "r.pdb",
            b"ATOM",
            ligand_name,
            ligand_bytes,
            smiles,
            DOCKING_CONFIG,
        )

    _assert_safe_error(caught.value, forbidden=(RAW_SMILES, str(tmp_path.resolve())))


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"center": [1, 2], "size": [20, 20, 20]},
        {"center": [1, 2, 3], "size": [20, 20]},
        {"center": [1, 2, math.nan], "size": [20, 20, 20]},
        {"center": [1, 2, math.inf], "size": [20, 20, 20]},
        {"center": [True, 2, 3], "size": [20, 20, 20]},
        {"center": [1, 2, 3], "size": [20, 0, 20]},
        {"center": [1, 2, 3], "size": [20, 101, 20]},
        {"center": [1, 2, 3], "size": [20, True, 20]},
        {"center": [1, 2, 3], "size": [20, 20, 20], "exhaustiveness": True},
        {"center": [1, 2, 3], "size": [20, 20, 20], "exhaustiveness": 0},
        {"center": [1, 2, 3], "size": [20, 20, 20], "exhaustiveness": 65},
        {"center": [1, 2, 3], "size": [20, 20, 20], "num_modes": 0},
        {"center": [1, 2, 3], "size": [20, 20, 20], "num_modes": 51},
        {"center": [1, 2, 3], "size": [20, 20, 20], "command": "vina --evil"},
        {"center": [1, 2, 3], "size": [20, 20, 20], "output_path": "secret"},
    ],
)
def test_stage_rejects_invalid_or_extra_docking_config(
    tmp_path: Path,
    config: dict[str, Any],
) -> None:
    with pytest.raises(ManifestError) as caught:
        _stage_file(DockingInputStager(tmp_path), config=config)
    _assert_safe_error(caught.value, forbidden=(str(tmp_path.resolve()),))


def test_manifest_rejects_path_escape(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    payload = _manifest_payload(path)
    payload["receptor"]["path"] = "../../secret"
    _replace_manifest(path, payload)

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", path)

    _assert_safe_error(caught.value, forbidden=("../../secret", str(tmp_path.resolve())))


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.update(schema="DockingInputManifest@2"),
        lambda payload: payload.update(type="activity"),
        lambda payload: payload.update(task_id="task-2"),
        lambda payload: payload.update(extra="danger"),
        lambda payload: payload["receptor"].update(extra="danger"),
        lambda payload: payload["receptor"].update(size=True),
        lambda payload: payload["receptor"].update(sha256="not-a-digest"),
        lambda payload: payload.update(config_hash="F" * 64),
        lambda payload: payload.update(ligand_mode="smiles"),
        lambda payload: payload["config"].update(num_modes=True),
    ],
)
def test_load_rejects_schema_task_digest_mode_and_type_tampering(
    tmp_path: Path,
    mutation: Any,
) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    payload = _manifest_payload(path)
    mutation(payload)
    _replace_manifest(path, payload)

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", path)

    _assert_safe_error(caught.value, forbidden=(str(tmp_path.resolve()),))


@pytest.mark.parametrize("raw", ["{", "[]", "null", "true", '"text"'])
def test_load_rejects_malformed_or_dangerous_json_types(
    tmp_path: Path,
    raw: str,
) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    path.write_text(raw, encoding="utf-8")

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", path)

    _assert_safe_error(caught.value, forbidden=(str(tmp_path.resolve()),))


def test_load_rejects_cross_task_manifest_reference(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    first = _stage_file(stager, "task-1")
    _stage_file(stager, "task-2")

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-2", first)

    _assert_safe_error(caught.value, forbidden=(str(tmp_path.resolve()),))


@pytest.mark.parametrize("replacement", [b"BTOM\n", b"CHANGED-LENGTH"])
def test_load_rejects_changed_input_size_or_hash(
    tmp_path: Path,
    replacement: bytes,
) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    (path.parent / "inputs" / "receptor.pdb").write_bytes(replacement)

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", path)

    _assert_safe_error(caught.value, forbidden=(str(tmp_path.resolve()),))


def test_load_rejects_config_tampering_even_with_valid_shape(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    payload = _manifest_payload(path)
    payload["config"]["num_modes"] = 11
    _replace_manifest(path, payload)

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", path)

    _assert_safe_error(caught.value)


def test_load_rejects_smiles_manifest_relabelled_as_file(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_smiles(stager)
    payload = _manifest_payload(path)
    payload["ligand_mode"] = "file"
    _replace_manifest(path, payload)

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-smiles", path)

    _assert_safe_error(caught.value, forbidden=(RAW_SMILES, str(tmp_path.resolve())))


def test_file_ligand_cannot_use_reserved_smiles_staging_name(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)

    with pytest.raises(ManifestError) as caught:
        stager.stage(
            "task-1",
            "receptor.pdb",
            b"ATOM",
            "smiles.txt",
            b"CCO",
            None,
            DOCKING_CONFIG,
        )

    _assert_safe_error(caught.value, forbidden=(str(tmp_path.resolve()),))


def test_idempotent_concurrent_same_submission_has_one_valid_manifest(
    tmp_path: Path,
) -> None:
    stager = DockingInputStager(tmp_path)

    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(lambda _: _stage_file(stager), range(16)))

    assert len(set(paths)) == 1
    assert not list(tmp_path.rglob("*.tmp"))
    manifest = stager.load_verified("task-1", paths[0])
    assert manifest["receptor"]["size"] == 5
    assert len(list((tmp_path / "task-1").glob("input_manifest.json"))) == 1


def test_conflicting_restage_is_rejected_without_overwriting_accepted_manifest(
    tmp_path: Path,
) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    before = path.read_bytes()

    with pytest.raises(ManifestError) as caught:
        stager.stage(
            "task-1",
            "receptor.pdb",
            b"DIFFERENT",
            "ligand.sdf",
            b"$$$$\n",
            None,
            DOCKING_CONFIG,
        )

    _assert_safe_error(caught.value, forbidden=(str(tmp_path.resolve()),))
    assert path.read_bytes() == before
    assert stager.load_verified("task-1", path)["receptor"]["size"] == 5


def test_cleanup_preserves_protected_and_new_directories_and_removes_old(
    tmp_path: Path,
) -> None:
    stager = DockingInputStager(tmp_path)
    _stage_file(stager, "running")
    _stage_file(stager, "fresh")
    _stage_file(stager, "expired")
    now = time.time()
    os.utime(tmp_path / "running", (now - 1000, now - 1000))
    os.utime(tmp_path / "expired", (now - 1000, now - 1000))

    removed = stager.cleanup_expired(
        cutoff_epoch=now - 100,
        protected={"running"},
    )

    assert removed == ["expired"]
    assert (tmp_path / "running").is_dir()
    assert (tmp_path / "fresh").is_dir()
    assert not (tmp_path / "expired").exists()
    assert all(not Path(item).is_absolute() for item in removed)


def test_cleanup_rejects_invalid_protected_ids_without_deleting(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    _stage_file(stager, "expired")
    os.utime(tmp_path / "expired", (1, 1))

    with pytest.raises(ManifestError) as caught:
        stager.cleanup_expired(cutoff_epoch=time.time(), protected={"../expired"})

    _assert_safe_error(caught.value, forbidden=("../expired", str(tmp_path.resolve())))
    assert (tmp_path / "expired").is_dir()


def test_cleanup_preserves_unowned_safe_named_directory(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    unowned = tmp_path / "looks-like-a-task"
    unowned.mkdir()
    marker = unowned / "keep.txt"
    marker.write_text("not a staged task", encoding="utf-8")
    os.utime(unowned, (1, 1))

    removed = stager.cleanup_expired(cutoff_epoch=time.time(), protected=set())

    assert removed == []
    assert marker.read_text(encoding="utf-8") == "not a staged task"


def test_cleanup_ignores_symlink_entries_without_following_them(tmp_path: Path) -> None:
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    link = tmp_path / "linked-task"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks unavailable: {type(exc).__name__}")
    os.utime(outside, (1, 1))
    stager = DockingInputStager(tmp_path)

    assert stager.cleanup_expired(cutoff_epoch=time.time(), protected=set()) == []
    assert marker.read_text(encoding="utf-8") == "keep"


def test_constructor_and_stage_reject_symlink_components(tmp_path: Path) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    linked_root = tmp_path / "linked-root"
    try:
        linked_root.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks unavailable: {type(exc).__name__}")

    with pytest.raises(ManifestError) as caught:
        DockingInputStager(linked_root)

    _assert_safe_error(caught.value, forbidden=(str(linked_root), str(outside)))


def test_load_rejects_symlinked_input_or_manifest(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    receptor = path.parent / "inputs" / "receptor.pdb"
    outside = tmp_path / "outside.pdb"
    outside.write_bytes(receptor.read_bytes())
    receptor.unlink()
    try:
        receptor.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks unavailable: {type(exc).__name__}")

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", path)

    _assert_safe_error(caught.value, forbidden=(str(outside), str(tmp_path.resolve())))


def test_manifest_path_must_be_exact_task_manifest(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    alias = path.parent / "alias.json"
    alias.write_bytes(path.read_bytes())

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", alias)

    _assert_safe_error(caught.value, forbidden=(str(alias), str(tmp_path.resolve())))


def test_file_name_does_not_override_explicit_ligand_mode(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)

    path = stager.stage(
        "task-1",
        "smiles.txt",
        b"ATOM",
        "ligand.sdf",
        b"$$$$",
        None,
        DOCKING_CONFIG,
    )

    manifest = stager.load_verified("task-1", path)
    assert manifest["receptor"]["path"] == "inputs/smiles.txt"
    assert manifest["ligand_mode"] == "file"


def test_stage_rejects_symlinked_task_directory(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-task-outside"
    outside.mkdir()
    linked_task = tmp_path / "task-1"
    try:
        linked_task.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks unavailable: {type(exc).__name__}")

    with pytest.raises(ManifestError) as caught:
        _stage_file(stager)

    _assert_safe_error(caught.value, forbidden=(str(outside), str(tmp_path.resolve())))


def test_load_rejects_symlinked_manifest(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    outside = tmp_path / "manifest-copy.json"
    outside.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(outside)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"file symlinks unavailable: {type(exc).__name__}")

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", path)

    _assert_safe_error(caught.value, forbidden=(str(outside), str(tmp_path.resolve())))


def test_cleanup_preserves_old_task_with_nested_symlink(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager, "old-task")
    outside = tmp_path.parent / f"{tmp_path.name}-nested-outside"
    outside.mkdir()
    marker = outside / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    nested_link = path.parent / "inputs" / "linked"
    try:
        nested_link.symlink_to(outside, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlinks unavailable: {type(exc).__name__}")
    os.utime(path.parent, (1, 1))

    removed = stager.cleanup_expired(cutoff_epoch=time.time(), protected=set())

    assert removed == []
    assert path.parent.is_dir()
    assert marker.read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize(
    "task_id",
    [
        "task.",
        "CON",
        "con.txt",
        "PRN.log",
        "AUX.data",
        "NUL.bin",
        "CLOCK$",
        "COM1.out",
        "com9.anything",
        "LPT1.txt",
        "lpt9.log",
    ],
)
def test_task_ids_reject_windows_ambiguous_names(
    tmp_path: Path,
    task_id: str,
) -> None:
    stager = DockingInputStager(tmp_path)

    with pytest.raises(ManifestError) as caught:
        _stage_file(stager, task_id)

    _assert_safe_error(caught.value, forbidden=(task_id, str(tmp_path.resolve())))


@pytest.mark.parametrize(
    "name",
    [
        "receptor.",
        "CON.pdb",
        "prn.sdf",
        "AUX.mol2",
        "NUL.pdbqt",
        "CLOCK$",
        "COM1.pdb",
        "com9.sdf",
        "LPT1.pdb",
        "lpt9.sdf",
        "a" * 101,
    ],
)
def test_input_names_reject_windows_ambiguous_or_overlong_basenames(
    tmp_path: Path,
    name: str,
) -> None:
    stager = DockingInputStager(tmp_path)

    with pytest.raises(ManifestError) as caught:
        stager.stage(
            "task-1",
            name,
            b"ATOM",
            "ligand.sdf",
            b"$$$$",
            None,
            DOCKING_CONFIG,
        )

    _assert_safe_error(caught.value, forbidden=(name, str(tmp_path.resolve())))


def test_portable_100_byte_input_basename_is_accepted(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    receptor_name = f"{'r' * 96}.pdb"

    path = stager.stage(
        "task-1",
        receptor_name,
        b"ATOM",
        "ligand.sdf",
        b"$$$$",
        None,
        DOCKING_CONFIG,
    )

    assert stager.load_verified("task-1", path)["receptor"]["path"].endswith(
        receptor_name
    )


def test_impractical_final_input_path_is_rejected_before_sensitive_write(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stager = DockingInputStager(tmp_path)
    real_atomic_write = staging_module._atomic_write
    sensitive_writes: list[str] = []

    def record_writes(path: Path, content: bytes) -> None:
        if path.name in {"receptorx.pdb", "ligandxx.sdf", "smiles.txt"}:
            sensitive_writes.append(path.name)
        real_atomic_write(path, content)

    monkeypatch.setattr(staging_module, "_atomic_write", record_writes)

    with pytest.raises(ManifestError):
        stager.stage(
            "t" * 128,
            "receptorx.pdb",
            b"ATOM",
            "ligandxx.sdf",
            b"$$$$",
            None,
            DOCKING_CONFIG,
        )

    assert sensitive_writes == []


def test_receptor_and_ligand_names_cannot_collide_portably(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)

    with pytest.raises(ManifestError) as caught:
        stager.stage(
            "task-1",
            "Ligand.SDF",
            b"ATOM",
            "ligand.sdf",
            b"$$$$",
            None,
            DOCKING_CONFIG,
        )

    _assert_safe_error(caught.value, forbidden=(str(tmp_path.resolve()),))


def test_stage_rejects_26_mib_receptor_and_ligand_before_writing(
    tmp_path: Path,
) -> None:
    stager = DockingInputStager(tmp_path)
    oversized = b"x" * (26 * 1024 * 1024)

    with pytest.raises(ManifestError):
        stager.stage(
            "large-receptor",
            "receptor.pdb",
            oversized,
            "ligand.sdf",
            b"$$$$",
            None,
            DOCKING_CONFIG,
        )
    with pytest.raises(ManifestError):
        stager.stage(
            "large-ligand",
            "receptor.pdb",
            b"ATOM",
            "ligand.sdf",
            oversized,
            None,
            DOCKING_CONFIG,
        )

    assert not (tmp_path / "large-receptor").exists()
    assert not (tmp_path / "large-ligand").exists()


def test_stage_rejects_smiles_larger_than_16_kib_before_writing(
    tmp_path: Path,
) -> None:
    stager = DockingInputStager(tmp_path)
    oversized = "C" * (16 * 1024 + 1)

    with pytest.raises(ManifestError) as caught:
        stager.stage(
            "large-smiles",
            "receptor.pdb",
            b"ATOM",
            None,
            None,
            oversized,
            DOCKING_CONFIG,
        )

    _assert_safe_error(caught.value, forbidden=(oversized, str(tmp_path.resolve())))
    assert not (tmp_path / "large-smiles").exists()


def test_load_uses_descriptor_reads_not_path_read_helpers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)

    def forbidden_path_read(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("path-based read is forbidden")

    monkeypatch.setattr(Path, "read_bytes", forbidden_path_read)
    monkeypatch.setattr(Path, "open", forbidden_path_read)

    assert stager.load_verified("task-1", path)["task_id"] == "task-1"


def test_load_rejects_hardlinked_input(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    receptor = path.parent / "inputs" / "receptor.pdb"
    original = tmp_path / "original.pdb"
    receptor.replace(original)
    try:
        os.link(original, receptor)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"hardlinks unavailable: {type(exc).__name__}")

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", path)

    _assert_safe_error(caught.value, forbidden=(str(original), str(tmp_path.resolve())))


def test_load_rejects_descriptor_metadata_change(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    real_fstat = staging_module.os.fstat
    calls: dict[tuple[int, int, int], int] = {}

    def unstable_fstat(descriptor: int) -> os.stat_result:
        result = real_fstat(descriptor)
        key = (result.st_dev, result.st_ino, result.st_size)
        calls[key] = calls.get(key, 0) + 1
        if result.st_size == 5 and calls[key] >= 2:
            values = list(result)
            values[6] = result.st_size + 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(staging_module.os, "fstat", unstable_fstat)

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", path)

    _assert_safe_error(caught.value, forbidden=(str(tmp_path.resolve()),))


def test_load_rejects_path_replacement_during_descriptor_read_when_supported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    receptor = path.parent / "inputs" / "receptor.pdb"
    receptor_identity = receptor.stat().st_ino
    replacement = tmp_path / "replacement.pdb"
    replacement.write_bytes(b"BTOM\n")
    real_read = staging_module.os.read
    swapped = False
    unsupported: OSError | None = None

    def swap_after_read(descriptor: int, size: int) -> bytes:
        nonlocal swapped, unsupported
        data = real_read(descriptor, size)
        if not swapped and data and os.fstat(descriptor).st_ino == receptor_identity:
            try:
                os.replace(replacement, receptor)
            except OSError as exc:
                unsupported = exc
            else:
                swapped = True
        return data

    monkeypatch.setattr(staging_module.os, "read", swap_after_read)

    caught_error: ManifestError | None = None
    try:
        stager.load_verified("task-1", path)
    except ManifestError as exc:
        caught_error = exc
    if unsupported is not None:
        pytest.skip(f"open-file replacement unavailable: {type(unsupported).__name__}")
    assert swapped is True
    assert caught_error is not None
    _assert_safe_error(caught_error, forbidden=(str(tmp_path.resolve()),))


def test_resolve_verified_inputs_reverifies_and_returns_frozen_paths(
    tmp_path: Path,
) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)

    resolved = stager.resolve_verified_inputs("task-1", path)

    assert isinstance(resolved, staging_module.VerifiedDockingInputs)
    assert resolved.task_id == "task-1"
    assert resolved.receptor_path == path.parent / "inputs" / "receptor.pdb"
    assert resolved.ligand_path == path.parent / "inputs" / "ligand.sdf"
    assert resolved.ligand_mode == "file"
    assert str(tmp_path.resolve()) not in repr(resolved)
    with pytest.raises(FrozenInstanceError):
        resolved.task_id = "changed"  # type: ignore[misc]


def test_stage_verifies_published_manifest_before_reporting_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stager = DockingInputStager(tmp_path)
    calls: list[str] = []

    def reject_publish(task_id: str, manifest_path: Path) -> dict[str, Any]:
        calls.append(task_id)
        raise ManifestError("manifest_integrity_failed")

    monkeypatch.setattr(stager, "load_verified", reject_publish)

    with pytest.raises(ManifestError) as caught:
        _stage_file(stager)

    assert calls == ["task-1"]
    _assert_safe_error(caught.value, forbidden=(str(tmp_path.resolve()),))
    assert not (tmp_path / "task-1").exists()


@pytest.mark.parametrize("failure", [RecursionError(), MemoryError()])
def test_json_resource_failures_are_reported_as_malformed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)

    def fail_json(*args: Any, **kwargs: Any) -> Any:
        raise failure

    monkeypatch.setattr(staging_module.json, "loads", fail_json)

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", path)

    assert caught.value.reason_code == "manifest_malformed"


def test_json_nesting_guard_rejects_deep_payload_as_malformed(tmp_path: Path) -> None:
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)
    payload = _manifest_payload(path)
    nested: list[Any] = []
    cursor = nested
    for _ in range(40):
        child: list[Any] = []
        cursor.append(child)
        cursor = child
    payload["extra"] = nested
    _replace_manifest(path, payload)

    with pytest.raises(ManifestError) as caught:
        stager.load_verified("task-1", path)

    assert caught.value.reason_code == "manifest_malformed"


def test_permission_restriction_failure_aborts_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stager = DockingInputStager(tmp_path)
    real_chmod = Path.chmod

    def fail_inputs(self: Path, mode: int) -> None:
        if self.name == "inputs":
            raise OSError("permission denied")
        real_chmod(self, mode)

    monkeypatch.setattr(Path, "chmod", fail_inputs)

    with pytest.raises(ManifestError) as caught:
        _stage_file(stager)

    _assert_safe_error(caught.value, forbidden=(str(tmp_path.resolve()),))
    assert not (tmp_path / "task-1").exists()


def test_failed_sensitive_stage_is_owned_or_quarantined(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stager = DockingInputStager(tmp_path)
    real_atomic_write = staging_module._atomic_write
    real_rmtree = staging_module.shutil.rmtree

    def fail_manifest(path: Path, content: bytes) -> None:
        if path.name == "input_manifest.json":
            raise ManifestError("manifest_io_error")
        real_atomic_write(path, content)

    def fail_stage_delete(path: Any, *args: Any, **kwargs: Any) -> None:
        if Path(path).name.startswith(".stage-"):
            marker = Path(path) / ".medchat-staging-owner"
            if marker.exists():
                marker.unlink()
            raise OSError("simulated cleanup failure")
        real_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(staging_module, "_atomic_write", fail_manifest)
    monkeypatch.setattr(staging_module.shutil, "rmtree", fail_stage_delete)

    with pytest.raises(ManifestError) as caught:
        _stage_smiles(stager, "failed-smiles")

    _assert_safe_error(caught.value, forbidden=(RAW_SMILES, str(tmp_path.resolve())))
    assert not list(tmp_path.glob(".stage-*"))
    quarantined = [path for path in (tmp_path / ".trash").iterdir() if path.is_dir()]
    assert quarantined
    assert all((path / ".medchat-staging-owner").is_file() for path in quarantined)


def test_cleanup_removes_only_owned_expired_temp_and_quarantine_residue(
    tmp_path: Path,
) -> None:
    stager = DockingInputStager(tmp_path)
    marker_content = b"MedChatTaskStaging@1\n"
    abandoned = tmp_path / ".stage-abandoned"
    abandoned.mkdir()
    (abandoned / ".medchat-staging-owner").write_bytes(marker_content)
    (abandoned / "secret.txt").write_text("owned residue", encoding="utf-8")
    trash = tmp_path / ".trash"
    quarantined = trash / "quarantined-task"
    quarantined.mkdir(parents=True, exist_ok=True)
    (quarantined / ".medchat-staging-owner").write_bytes(marker_content)
    unowned = tmp_path / ".stage-unowned"
    unowned.mkdir()
    for path in (abandoned, quarantined, unowned):
        os.utime(path, (1, 1))

    removed = stager.cleanup_expired(cutoff_epoch=time.time(), protected=set())

    assert removed == []
    assert not abandoned.exists()
    assert not quarantined.exists()
    assert unowned.exists()


def test_cleanup_quarantine_swap_preserves_fresh_original_name(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stager = DockingInputStager(tmp_path)
    _stage_file(stager, "expired")
    os.utime(tmp_path / "expired", (1, 1))
    real_rmtree = staging_module.shutil.rmtree
    swapped = False

    def replace_before_delete(path: Any, *args: Any, **kwargs: Any) -> None:
        nonlocal swapped
        target = Path(path)
        if not swapped:
            swapped = True
            original = tmp_path / "expired"
            if original.exists():
                displaced = tmp_path / ".displaced-old"
                original.rename(displaced)
                original.mkdir()
                (original / "fresh.txt").write_text("fresh", encoding="utf-8")
                real_rmtree(displaced)
            else:
                original.mkdir()
                (original / "fresh.txt").write_text("fresh", encoding="utf-8")
        real_rmtree(target, *args, **kwargs)

    monkeypatch.setattr(staging_module.shutil, "rmtree", replace_before_delete)

    removed = stager.cleanup_expired(cutoff_epoch=time.time(), protected=set())

    assert removed == ["expired"]
    assert (tmp_path / "expired" / "fresh.txt").read_text(encoding="utf-8") == "fresh"


def test_cleanup_rename_guard_restores_directory_swapped_after_validation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stager = DockingInputStager(tmp_path)
    _stage_file(stager, "expired")
    original = tmp_path / "expired"
    os.utime(original, (1, 1))
    displaced = tmp_path / ".displaced-old"
    real_rename = Path.rename
    swapped = False

    def swap_before_rename(self: Path, target: Path) -> Path:
        nonlocal swapped
        if self == original and not swapped:
            swapped = True
            os.rename(original, displaced)
            staging_module.shutil.copytree(displaced, original)
            (original / "fresh.txt").write_text("fresh", encoding="utf-8")
            os.rename(original, target)
            return target
        return real_rename(self, target)

    monkeypatch.setattr(Path, "rename", swap_before_rename)

    removed = stager.cleanup_expired(cutoff_epoch=time.time(), protected=set())

    assert removed == []
    assert (original / "fresh.txt").read_text(encoding="utf-8") == "fresh"


def test_cleanup_delete_failure_leaves_quarantine_and_does_not_claim_removed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stager = DockingInputStager(tmp_path)
    _stage_file(stager, "expired")
    os.utime(tmp_path / "expired", (1, 1))

    def fail_delete(path: Any, *args: Any, **kwargs: Any) -> None:
        raise OSError("simulated delete failure")

    monkeypatch.setattr(staging_module.shutil, "rmtree", fail_delete)

    removed = stager.cleanup_expired(cutoff_epoch=time.time(), protected=set())

    assert removed == []
    assert not (tmp_path / "expired").exists()
    quarantined = [path for path in (tmp_path / ".trash").iterdir() if path.is_dir()]
    assert quarantined
    assert all((path / ".medchat-staging-owner").is_file() for path in quarantined)


def test_publish_fsyncs_inputs_temp_task_and_staging_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[Path] = []

    def record_barrier(path: Path) -> None:
        calls.append(path.resolve())

    monkeypatch.setattr(staging_module, "_fsync_directory", record_barrier)
    stager = DockingInputStager(tmp_path)
    path = _stage_file(stager)

    assert path.parent.parent.resolve() in calls
    assert any(item.name == "inputs" for item in calls)
    assert any(item.name.startswith(".stage-") for item in calls)


def test_post_publish_root_barrier_failure_quarantines_active_task(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stager = DockingInputStager(tmp_path)
    real_barrier = staging_module._fsync_directory

    def fail_root_after_publish(path: Path) -> None:
        if path.resolve() == tmp_path.resolve() and (tmp_path / "task-1").exists():
            raise ManifestError("manifest_io_error")
        real_barrier(path)

    monkeypatch.setattr(staging_module, "_fsync_directory", fail_root_after_publish)

    with pytest.raises(ManifestError) as caught:
        _stage_file(stager)

    assert caught.value.reason_code == "manifest_io_error"
    assert not (tmp_path / "task-1").exists()


def test_posix_directory_fsync_failure_is_not_silenced(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(staging_module, "_IS_WINDOWS", False, raising=False)

    def fail_open(*args: Any, **kwargs: Any) -> int:
        raise OSError("fsync unavailable")

    monkeypatch.setattr(staging_module.os, "open", fail_open)

    with pytest.raises(ManifestError) as caught:
        staging_module._fsync_directory(tmp_path)

    assert caught.value.reason_code == "manifest_io_error"
