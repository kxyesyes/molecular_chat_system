"""Security and integrity contracts for the molecule-only pharm3d cache."""

from __future__ import annotations

import json
import pickle
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


def _params():
    return {
        "embedding_method": "ETKDGv3",
        "num_confs": 5,
        "random_seed": 42,
        "num_threads": 1,
        "enforce_chirality": True,
        "forcefield": "MMFF94",
    }


def _payload():
    return {
        "success": True,
        "features": [{"family": "Donor", "pos": [0.0, 0.0, 0.0]}],
        "feature_counts": {"Donor": 1},
        "mol_block": "mol block",
        "properties": {"MW": 46.07},
        "conformer_status": "optimized",
        "embedding_method": "ETKDGv3",
        "mmff_converged": True,
    }


def test_cache_is_json_only_and_never_unpickles_legacy_file(tmp_path, monkeypatch):
    from src.reverse_target import pharmacophore_cache as cache

    legacy = tmp_path / "legacy.pkl"
    legacy.write_bytes(pickle.dumps({"success": True}))
    monkeypatch.setattr(pickle, "load", lambda *_args, **_kwargs: pytest.fail("pickle.load was called"))

    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCO",
        rdkit_version="2025.03.6",
        generation_params=_params(),
    ) is None
    assert legacy.exists()


def test_cache_key_and_payload_hash_are_full_sha256_and_bind_metadata(tmp_path):
    from src.reverse_target import pharmacophore_cache as cache

    path = cache.save_cache(
        tmp_path,
        canonical_smiles="CCO",
        payload=_payload(),
        rdkit_version="2025.03.6",
        generation_params=_params(),
        now=datetime(2026, 10, 6, tzinfo=timezone.utc),
    )

    document = json.loads(path.read_text(encoding="utf-8"))
    assert path.suffix == ".json"
    assert len(path.stem) == 64
    assert document["cache_key"] == path.stem
    assert len(document["cache_key"]) == 64
    assert len(document["payload_sha256"]) == 64
    assert document["payload_size"] > 0
    assert document["schema_version"] == 1
    assert document["scope"] == "molecule_only"
    assert document["scientific_source"] == "RDKit"
    assert "target" not in document
    assert "species" not in document
    assert document["rdkit_version"] == "2025.03.6"
    assert document["generation_params"] == _params()

    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCO",
        rdkit_version="2025.03.6",
        generation_params=_params(),
        now=datetime(2026, 10, 6, tzinfo=timezone.utc),
    ) == _payload()

    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCN",
        rdkit_version="2025.03.6",
        generation_params=_params(),
    ) is None
    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCO",
        rdkit_version="2025.03.7",
        generation_params=_params(),
    ) is None
    changed = {**_params(), "random_seed": 43}
    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCO",
        rdkit_version="2025.03.6",
        generation_params=changed,
    ) is None


def test_cache_rejects_payload_tampering_and_structure_mismatch(tmp_path):
    from src.reverse_target import pharmacophore_cache as cache

    path = cache.save_cache(
        tmp_path,
        canonical_smiles="CCO",
        payload=_payload(),
        rdkit_version="2025.03.6",
        generation_params=_params(),
    )
    document = json.loads(path.read_text(encoding="utf-8"))
    document["payload"]["features"][0]["pos"][0] = 99.0
    path.write_text(json.dumps(document), encoding="utf-8")

    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCO",
        rdkit_version="2025.03.6",
        generation_params=_params(),
    ) is None

    path.unlink()
    cache.save_cache(
        tmp_path,
        canonical_smiles="CCO",
        payload=_payload(),
        rdkit_version="2025.03.6",
        generation_params=_params(),
    )
    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCN",
        rdkit_version="2025.03.6",
        generation_params=_params(),
    ) is None


def test_molecule_cache_rejects_target_and_species_scope_fields(tmp_path):
    from src.reverse_target import pharmacophore_cache as cache

    with pytest.raises(ValueError, match="molecule-only"):
        cache.save_cache(
            tmp_path,
            canonical_smiles="CCO",
            payload={**_payload(), "species": "human"},
            rdkit_version="2025.03.6",
            generation_params=_params(),
        )

    with pytest.raises(ValueError, match="molecule-only"):
        cache.save_cache(
            tmp_path,
            canonical_smiles="CCO",
            payload={**_payload(), "metadata": {"target_id": "PDE5A"}},
            rdkit_version="2025.03.6",
            generation_params=_params(),
        )


def test_cache_rejects_expired_oversized_and_malformed_documents(tmp_path):
    from src.reverse_target import pharmacophore_cache as cache

    created = datetime(2026, 10, 1, tzinfo=timezone.utc)
    path = cache.save_cache(
        tmp_path,
        canonical_smiles="CCO",
        payload=_payload(),
        rdkit_version="2025.03.6",
        generation_params=_params(),
        now=created,
        ttl_seconds=60,
    )
    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCO",
        rdkit_version="2025.03.6",
        generation_params=_params(),
        now=created + timedelta(seconds=61),
    ) is None

    path.write_bytes(b"not json")
    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCO",
        rdkit_version="2025.03.6",
        generation_params=_params(),
    ) is None

    path.write_bytes(b"{}")
    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCO",
        rdkit_version="2025.03.6",
        generation_params=_params(),
    ) is None

    path.write_bytes(b"x" * (cache.MAX_CACHE_BYTES + 1))
    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCO",
        rdkit_version="2025.03.6",
        generation_params=_params(),
    ) is None


def test_concurrent_writers_leave_one_valid_complete_document(tmp_path):
    from src.reverse_target import pharmacophore_cache as cache

    errors = []

    def write():
        try:
            cache.save_cache(
                tmp_path,
                canonical_smiles="CCO",
                payload=_payload(),
                rdkit_version="2025.03.6",
                generation_params=_params(),
            )
        except Exception as exc:  # pragma: no cover - makes thread failures visible
            errors.append(exc)

    threads = [threading.Thread(target=write) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert errors == []
    assert cache.load_cache(
        tmp_path,
        canonical_smiles="CCO",
        rdkit_version="2025.03.6",
        generation_params=_params(),
    ) == _payload()
    assert list(tmp_path.glob("*.tmp")) == []


def test_refiner_does_not_return_corrupt_cache_as_scientific_result(tmp_path, monkeypatch):
    import src.reverse_target.pharmacophore_refiner as refiner

    monkeypatch.setenv("REVERSE_TARGET_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(refiner, "_CACHE_DIR", None)
    monkeypatch.setattr(refiner, "generate_3d_conformer_with_status", lambda *_args, **_kwargs: (None, {
        "conformer_status": "embedding_failed",
        "embedding_method": "ETKDGv3",
        "forcefield": "MMFF94",
        "mmff_converged": False,
        "conformer_energy": None,
    }))

    refiner._save_cache("CCO", {"success": True, "conformer_status": "optimized"})
    result = refiner.get_molecule_pharmacophore("CCO")

    assert result["success"] is False
    assert "3D 构象生成失败" in result["error"]


def test_refiner_only_reuses_a_complete_verified_scientific_result(tmp_path, monkeypatch):
    import src.reverse_target.pharmacophore_refiner as refiner

    monkeypatch.setenv("REVERSE_TARGET_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(refiner, "_CACHE_DIR", None)
    payload = _payload()
    payload["conformer_energy"] = -1.25
    refiner._save_cache("CCO", payload)
    monkeypatch.setattr(
        refiner,
        "generate_3d_conformer_with_status",
        lambda *_args, **_kwargs: pytest.fail("verified cache should be reused"),
    )

    assert refiner.get_molecule_pharmacophore("CCO") == payload
