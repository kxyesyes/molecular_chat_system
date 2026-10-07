from __future__ import annotations

import hashlib
from pathlib import Path
from unittest.mock import Mock, patch

import pytest


def _repository(tmp_path: Path):
    from src.target_search.cache import TargetCacheRepository

    return TargetCacheRepository(tmp_path)


def _stage(project_root: Path, content: bytes = b"coordinate data") -> Path:
    from src.target_search.database import get_cache_dir

    relative = Path(".incoming") / "structure.cif.part"
    staged = get_cache_dir(project_root).joinpath(*relative.parts)
    staged.parent.mkdir(parents=True, exist_ok=True)
    staged.write_bytes(content)
    return relative


def test_coordinate_publication_records_file_integrity_metadata(tmp_path):
    content = b"data_structure\n_atom_site.Cartn_x\n"
    repository = _repository(tmp_path)
    staged = _stage(tmp_path, content)

    evidence = repository.publish_coordinate(
        "coordinate:integrity",
        "RCSB_PDB",
        "4WKQ",
        staged_path=staged.as_posix(),
        coordinate_suffix=".cif",
        species="Homo sapiens",
        source_version="pdb:4WKQ",
    )

    assert evidence.payload["sha256"] == hashlib.sha256(content).hexdigest()
    assert evidence.payload["size_bytes"] == len(content)
    assert evidence.payload["file_format"] == "cif"
    assert evidence.payload["species"] == "Homo sapiens"
    assert evidence.payload["source_version"] == "pdb:4WKQ"


def test_coordinate_cache_rejects_file_modified_after_publication(tmp_path):
    repository = _repository(tmp_path)
    staged = _stage(tmp_path, b"original")
    evidence = repository.publish_coordinate(
        "coordinate:tamper",
        "RCSB_PDB",
        "4WKQ",
        staged_path=staged.as_posix(),
        coordinate_suffix=".cif",
    )

    from src.target_search.database import get_cache_dir

    final_path = get_cache_dir(tmp_path) / evidence.payload["path"]
    final_path.write_bytes(b"tampered")

    assert repository.get("coordinate:tamper") is None


def test_structure_downloader_rejects_untrusted_override_url(tmp_path):
    from src.target_search.downloader import StructureDownloadError, StructureDownloader

    structure = {
        "id": 1,
        "source": "RCSB_PDB",
        "structure_id": "4WKQ",
        "file_format": "cif",
        "local_file_path": "data/target_db/cache/rcsb/4WKQ.cif",
        "download_url": "https://attacker.example/4WKQ.cif",
    }
    downloader = StructureDownloader(tmp_path)

    with patch("src.target_search.downloader.requests.get") as get:
        with pytest.raises(StructureDownloadError, match="trusted host"):
            downloader.prepare_structure_file(structure)

    get.assert_not_called()


def test_structure_cache_reuse_requires_matching_species_and_source_version(tmp_path):
    from src.target_search.downloader import StructureDownloader

    repository = _repository(tmp_path)
    staged = _stage(tmp_path, b"versioned coordinate")
    repository.publish_coordinate(
        "coordinate:AlphaFold:AF-P12345-F1:cif",
        "AlphaFold",
        "AF-P12345-F1:cif",
        staged_path=staged.as_posix(),
        coordinate_suffix=".cif",
        species="Homo sapiens",
        source_version="v6",
    )
    downloader = StructureDownloader(tmp_path, cache=repository)

    structure = {
        "id": 2,
        "source": "AlphaFold",
        "structure_id": "AF-P12345-F1",
        "file_format": "cif",
        "local_file_path": "data/target_db/cache/alphafold/P12345/AF-P12345-F1-model_v6.cif",
        "download_url": "https://alphafold.ebi.ac.uk/files/AF-P12345-F1-model_v5.cif",
        "organism": "Homo sapiens",
    }

    assert downloader._verified_cached_structure(structure, "cif") is None


def test_structure_cache_reuse_rejects_missing_scope_metadata(tmp_path):
    from src.target_search.downloader import StructureDownloader

    repository = _repository(tmp_path)
    staged = _stage(tmp_path, b"scoped coordinate")
    repository.publish_coordinate(
        "coordinate:RCSB_PDB:4WKQ:cif",
        "RCSB_PDB",
        "4WKQ:cif",
        staged_path=staged.as_posix(),
        coordinate_suffix=".cif",
        species="Homo sapiens",
        source_version="pdb:4WKQ",
    )
    downloader = StructureDownloader(tmp_path, cache=repository)

    structure = {
        "id": 3,
        "source": "RCSB_PDB",
        "structure_id": "4WKQ",
        "file_format": "cif",
        "local_file_path": "data/target_db/cache/rcsb/4WKQ.cif",
        "organism": None,
        "source_version": None,
    }

    assert downloader._verified_cached_structure(structure, "cif") is None


def test_coordinate_cache_read_treats_fingerprint_io_failure_as_invalid(tmp_path, monkeypatch):
    repository = _repository(tmp_path)
    staged = _stage(tmp_path, b"read failure coordinate")
    repository.publish_coordinate(
        "coordinate:read-failure",
        "RCSB_PDB",
        "4WKQ",
        staged_path=staged.as_posix(),
        coordinate_suffix=".cif",
    )

    def fail_fingerprint(*args, **kwargs):
        raise OSError("synthetic read failure")

    monkeypatch.setattr(
        "src.target_search.cache._coordinate_file_fingerprint", fail_fingerprint,
    )

    assert repository.get("coordinate:read-failure") is None
