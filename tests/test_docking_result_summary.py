"""Server-side verified docking result detail projections."""

from src.docking.molecular_docking_service import MolecularDockingService
from fastapi import FastAPI
from types import SimpleNamespace

from src.web.routes.docking_routes import setup_docking_routes
from src.web.routes.docking_routes import (
    _durable_pose_efficiency_rows,
    _legacy_pose_efficiency_rows,
)


def _atom(serial: int, x: float, y: float, z: float) -> str:
    return (
        f"HETATM{serial:5d}  C   LIG A   1"
        f"    {x:8.3f}{y:8.3f}{z:8.3f}  1.00  0.00     0.000 C"
    )


TWO_POSE_PDBQT = "\n".join(
    [
        "MODEL 1",
        "REMARK VINA RESULT: -8.400 0.000 0.000",
        _atom(1, 1.0, 2.0, 3.0),
        "ENDMDL",
        "MODEL 2",
        "REMARK VINA RESULT: -6.200 0.900 2.700",
        _atom(1, 5.0, 2.0, 3.0),
        "ENDMDL",
    ]
)


def test_parse_vina_results_text_returns_all_verified_pose_rows():
    rows = MolecularDockingService.parse_vina_results_text(TWO_POSE_PDBQT)

    assert [row["pose"] for row in rows] == [1, 2]
    assert rows[0]["binding_energy"] == -8.4
    assert rows[1]["rmsd_ub"] == 2.7


def test_parse_vina_results_text_rejects_incomplete_pose():
    incomplete = TWO_POSE_PDBQT.replace("ENDMDL", "", 1)

    assert MolecularDockingService.parse_vina_results_text(incomplete) == []


def test_legacy_pose_summary_calculates_ligand_efficiency_from_saved_ligand(tmp_path):
    job_dir = tmp_path / "docking_job-1"
    job_dir.mkdir()
    (job_dir / "ligand.pdbqt").write_text(
        _atom(1, 0.0, 0.0, 0.0) + "\n" + _atom(2, 1.0, 0.0, 0.0) + "\n",
        encoding="utf-8",
    )

    rows = _legacy_pose_efficiency_rows(
        [{"pose": 1, "binding_energy": -8.4}, {"pose": 2, "binding_energy": -6.2}],
        tmp_path,
        "job-1",
    )

    assert [row["ligand_efficiency"] for row in rows] == [4.2, 3.1]


def test_durable_pose_summary_calculates_ligand_efficiency_from_verified_smiles():
    rows = _durable_pose_efficiency_rows(
        [{"pose": 1, "binding_energy": -5.2}],
        {
            "manifest": {
                "ligand_mode": "smiles",
                "ligand": {"name": "ligand.smi"},
            },
            "ligand_bytes": b"CCO",
        },
    )

    assert rows[0]["ligand_efficiency"] == 1.733


def test_docking_routes_register_owned_result_summary_projection(tmp_path):
    app = FastAPI()
    setup_docking_routes(
        app,
        docking_service=SimpleNamespace(work_dir=str(tmp_path / "work")),
        task_runtime=None,
    )

    assert any(
        getattr(route, "path", None) == "/api/docking/result/{job_id}/summary"
        for route in app.routes
    )
