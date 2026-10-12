from __future__ import annotations

import importlib
import hashlib
import json
import sys
import types
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI, Request
from fastapi.testclient import TestClient

from src.docking.history_index import build_history_record, upsert_history_record

try:
    from src.web.routes.docking_routes import setup_docking_routes
except ModuleNotFoundError as exc:
    # The route package's convenience __init__ imports optional RDKit-backed
    # agent modules. Load this narrow route directly when that extra is absent.
    if exc.name != "rdkit":
        raise
    routes_package = types.ModuleType("src.web.routes")
    routes_package.__path__ = [str(Path(__file__).parents[1] / "src" / "web" / "routes")]
    sys.modules["src.web.routes"] = routes_package
    setup_docking_routes = importlib.import_module(
        "src.web.routes.docking_routes"
    ).setup_docking_routes


TASK_ID = "00000000-0000-4000-8000-000000000991"
OWNER = "durable-route-owner"


def _pdbqt_with_two_poses() -> bytes:
    return (
        "MODEL 1\n"
        "REMARK VINA RESULT:      -7.0      0.000      0.000\n"
        "REMARK SMILES C\n"
        "REMARK SMILES IDX 1 1\n"
        "ROOT\n"
        "ATOM      1  C   LIG     1       1.000   2.000   3.000  1.00  0.00     0.000 C \n"
        "ENDROOT\n"
        "TORSDOF 0\n"
        "ENDMDL\n"
        "MODEL 2\n"
        "REMARK VINA RESULT:      -6.5      1.000      2.000\n"
        "ROOT\n"
        "ATOM      1  C   LIG     1       4.000   5.000   6.000  1.00  0.00     0.000 C \n"
        "ENDROOT\n"
        "TORSDOF 0\n"
        "ENDMDL\n"
    ).encode("utf-8")


class _OwnedStore:
    def __init__(self, task_id: str, manifest_path: Path | None = None):
        self.task_id = task_id
        self.manifest_path = manifest_path

    def get_owned(self, task_id: str, owner_session_id: str):
        if task_id != self.task_id or owner_session_id != OWNER:
            raise KeyError(task_id)
        return SimpleNamespace(
            task_type="docking",
            status="succeeded",
            input_manifest_path=(
                str(self.manifest_path) if self.manifest_path is not None else None
            ),
        )


def _build_client(tmp_path: Path, docking_service=None):
    from src.task_runtime.staging import DockingInputStager

    manifest_path = DockingInputStager(tmp_path).stage(
        TASK_ID,
        "receptor.pdb",
        b"ATOM      1  C   ALA A   1       0.000   0.000   0.000\n",
        None,
        None,
        "C",
        {"center": [0.0, 0.0, 0.0], "size": [20.0, 20.0, 20.0]},
    )
    artifact = tmp_path / TASK_ID / "artifacts" / "docking_pose.pdbqt"
    artifact.parent.mkdir(parents=True)
    pose = _pdbqt_with_two_poses()
    artifact.write_bytes(pose)
    digest = hashlib.sha256(pose).hexdigest()
    (artifact.parent.parent / "completion_manifest.json").write_text(
        json.dumps(
            {
                "schema": "DockingCompletionManifest@1",
                "task_id": TASK_ID,
                "input_hash": "0" * 64,
                "config_hash": "1" * 64,
                "tool_version": "test-tool",
                "model_name": "test-model",
                "model_version": "1",
                "demo_mode": False,
                "fallback_used": False,
                "real_execution": True,
                "pose": {
                    "path": "artifacts/docking_pose.pdbqt",
                    "size": len(pose),
                    "sha256": digest,
                },
                "pose_count": 2,
                "best_energy": -7.0,
                "best_pose_index": 1,
                "validator_status": "succeeded",
                "attempt": 1,
                "completed_at": "2026-10-12T00:00:00Z",
                "result_metadata": {
                    "elapsed_ms": 1,
                    "formatted": "test completion",
                    "warnings": [],
                    "evidence": [],
                    "artifacts": [],
                    "quality": {
                        "real_execution": True,
                        "engine": "test",
                        "execution_status": "completed",
                        "validated": True,
                    },
                },
            }
        ),
        encoding="utf-8",
    )
    runtime = SimpleNamespace(
        config=SimpleNamespace(staging_root=tmp_path),
        store=_OwnedStore(TASK_ID, manifest_path),
    )
    app = FastAPI()

    @app.middleware("http")
    async def session(request: Request, call_next):
        request.scope["agent_session_id"] = OWNER
        return await call_next(request)

    setup_docking_routes(app, docking_service=docking_service, task_runtime=runtime)
    return TestClient(app, base_url="https://localhost")


def test_durable_artifact_is_available_through_legacy_viewer_result_route(tmp_path):
    client = _build_client(tmp_path)

    response = client.get(f"/api/docking/result/{TASK_ID}")

    assert response.status_code == 200, response.text
    assert response.content.startswith(b"MODEL 1")
    assert b"MODEL 2" in response.content


def test_durable_artifact_supports_pose_sdf_for_every_pose(tmp_path):
    import pytest

    pytest.importorskip("rdkit")
    client = _build_client(tmp_path)

    response = client.get(f"/api/docking/pose_sdf/{TASK_ID}?pose=2")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("chemical/x-mdl-sdfile")
    assert b"4.000" in response.content


def test_durable_interaction_analysis_uses_owned_staged_inputs(tmp_path, monkeypatch):
    from src.docking import interaction_analysis
    from src.docking import pose_export

    observed = {}

    def fake_analysis(receptor_path, ligand_path, *, pose_index):
        observed["receptor"] = Path(receptor_path)
        observed["ligand"] = Path(ligand_path)
        observed["receptor_text"] = Path(receptor_path).read_text(encoding="utf-8")
        observed["ligand_text"] = Path(ligand_path).read_text(encoding="utf-8")
        observed["pose_index"] = pose_index
        return {
            "status": "success",
            "reason_code": "analysis_completed",
            "interactions": [],
            "geometry_status": "available",
            "warnings": [],
            "provenance": {"analyzer": "test", "tool_derived": True},
        }

    monkeypatch.setattr(
        interaction_analysis,
        "analyze_docking_interactions",
        fake_analysis,
    )
    monkeypatch.setattr(
        pose_export,
        "pose_sdf_from_pdbqt",
        lambda *_args, **_kwargs: "synthetic SDF\n  V2000\n",
    )
    client = _build_client(tmp_path)

    response = client.get(f"/api/docking/interactions/{TASK_ID}?pose=2")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "success"
    assert response.json()["pose"] == 2
    assert observed["receptor"].name == "receptor.pdb"
    assert observed["receptor_text"].startswith("ATOM")
    assert observed["ligand"].name == "analysis_pose_2.sdf"
    assert "V2000" in observed["ligand_text"]
    assert observed["pose_index"] == 1


def test_durable_interaction_analysis_reports_missing_optional_backend(
    tmp_path, monkeypatch
):
    from src.docking import interaction_analysis
    from src.docking import pose_export

    monkeypatch.setattr(
        pose_export,
        "pose_sdf_from_pdbqt",
        lambda *_args, **_kwargs: "synthetic SDF\n  V2000\n",
    )
    monkeypatch.setattr(
        interaction_analysis,
        "_dependency_available",
        lambda _module: False,
    )
    client = _build_client(tmp_path)

    response = client.get(f"/api/docking/interactions/{TASK_ID}")

    assert response.status_code == 200, response.text
    assert response.json()["status"] == "unavailable"
    assert response.json()["reason_code"] == "dependency_missing"
    assert response.json()["interactions"] == []
    assert "prolif_and_mdanalysis_are_required" in response.json()["warnings"][0]


def test_durable_interaction_analysis_preserves_receptor_preparation_trace(tmp_path, monkeypatch):
    from src.docking import interaction_analysis
    from src.docking import pose_export

    observed = {}

    def fake_prepare(source, destination, *, trace=None):
        Path(destination).write_text(Path(source).read_text(encoding="utf-8"), encoding="utf-8")
        trace.update({
            "analysis_receptor_method": "reduce",
            "analysis_receptor_hydrogens": "explicit",
        })
        return True

    def fake_analysis(receptor_path, ligand_path, *, pose_index):
        observed["receptor"] = Path(receptor_path)
        return {
            "status": "success",
            "reason_code": "analysis_completed",
            "interactions": [],
            "geometry_status": "available",
            "warnings": [],
            "provenance": {"analyzer": "test", "tool_derived": True},
        }

    monkeypatch.setattr(interaction_analysis, "analyze_docking_interactions", fake_analysis)
    monkeypatch.setattr(pose_export, "pose_sdf_from_pdbqt", lambda *_args, **_kwargs: "synthetic SDF\n  V2000\n")
    service = SimpleNamespace(
        work_dir=str(tmp_path),
        prepare_analysis_receptor=fake_prepare,
    )

    response = _build_client(tmp_path, docking_service=service).get(
        f"/api/docking/interactions/{TASK_ID}?pose=2"
    )

    assert response.status_code == 200, response.text
    assert response.json()["provenance"]["analysis_receptor"] == {
        "analysis_receptor_method": "reduce",
        "analysis_receptor_hydrogens": "explicit",
    }
    assert observed["receptor"].name == "analysis_receptor.pdb"


def test_legacy_result_route_remains_owner_scoped(tmp_path):
    work_dir = tmp_path / "legacy"
    job_dir = work_dir / "docking_legacy-job"
    job_dir.mkdir(parents=True)
    result = b"legacy result"
    (job_dir / "result.pdbqt").write_bytes(result)
    upsert_history_record(
        work_dir,
        build_history_record(job_dir, job_id="legacy-job", owner_session_id=OWNER),
    )
    app = FastAPI()

    @app.middleware("http")
    async def session(request: Request, call_next):
        request.scope["agent_session_id"] = OWNER
        return await call_next(request)

    setup_docking_routes(
        app,
        docking_service=SimpleNamespace(work_dir=str(work_dir)),
        task_runtime=None,
    )
    response = TestClient(app, base_url="https://localhost").get(
        "/api/docking/result/legacy-job"
    )

    assert response.status_code == 200
    assert response.content == result
