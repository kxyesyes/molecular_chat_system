import json
import time
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.routes.api_routes import setup_api_routes


ADMIN_TOKEN = "unit-admin-token"


class MockDockingService:
    def __init__(self, work_dir: Path):
        self.work_dir = str(work_dir)


def admin_headers() -> dict[str, str]:
    return {"X-MedChat-Admin-Token": ADMIN_TOKEN}


def write_history_index(work_dir: Path, count: int = 120) -> None:
    records = []
    now = time.time()
    for index in range(count):
        job_id = f"{index:04d}"
        job_dir = work_dir / f"docking_{job_id}"
        job_dir.mkdir()
        # Deliberately disagree with the index. The history API should trust the
        # lightweight completion index instead of reparsing result.pdbqt on GET.
        (job_dir / "result.pdbqt").write_text(
            "REMARK VINA RESULT: -1.0 0.0 0.0\n",
            encoding="utf-8",
        )
        records.append(
            {
                "job_id": job_id,
                "time": f"2026-07-01 10:{index % 60:02d}:00",
                "timestamp": now - index,
                "status": "completed",
                "best_energy": -9.9 - index,
                "pose_count": 2,
                "has_receptor": True,
                "has_ligand": True,
                "has_result": True,
                "size_bytes": 1234 + index,
            }
        )
    (work_dir / "docking_history_index.json").write_text(
        json.dumps({"records": records}),
        encoding="utf-8",
    )


def test_docking_history_reads_index_with_pagination(tmp_path, monkeypatch):
    monkeypatch.setenv("MEDCHAT_ADMIN_TOKEN", ADMIN_TOKEN)
    write_history_index(tmp_path, count=120)

    app = FastAPI()
    setup_api_routes(app, docking_service=MockDockingService(tmp_path))
    client = TestClient(app)

    started = time.perf_counter()
    response = client.get(
        "/api/docking/history?page=2&limit=5",
        headers=admin_headers(),
    )
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert response.status_code == 200
    payload = response.json()
    assert payload["success"] is True
    assert payload["total"] == 120
    assert payload["page"] == 2
    assert payload["limit"] == 5
    assert len(payload["history"]) == 5
    assert payload["history"][0]["job_id"] == "0005"
    assert payload["history"][0]["best_energy"] == -14.9
    assert elapsed_ms < 100
