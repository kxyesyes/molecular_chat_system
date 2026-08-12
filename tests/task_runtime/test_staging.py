from __future__ import annotations

import json
import math
import os
import stat
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

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
