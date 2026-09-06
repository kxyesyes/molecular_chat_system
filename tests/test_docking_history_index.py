import json
import multiprocessing
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.web.routes.api_routes import setup_api_routes
from src.docking import history_index as history_index_module
from src.docking.history_index import (
    read_history_page,
    remove_history_record,
    upsert_history_record,
)


class MockDockingService:
    def __init__(self, work_dir: Path):
        self.work_dir = str(work_dir)


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


def _multiprocess_upsert_worker(
    work_dir: str,
    worker_index: int,
    record_count: int,
    start_event,
) -> None:
    start_event.wait(timeout=10)
    for record_index in range(record_count):
        upsert_history_record(
            work_dir,
            {
                "job_id": f"process-{worker_index}-{record_index}",
                "timestamp": float(worker_index * record_count + record_index),
                "status": "completed",
            },
        )


def test_docking_history_reads_index_with_pagination(tmp_path, monkeypatch):
    write_history_index(tmp_path, count=120)

    def fail_if_legacy_result_is_parsed(_result_file):
        raise AssertionError("history GET must not parse result.pdbqt")

    monkeypatch.setattr(
        history_index_module,
        "_parse_vina_summary",
        fail_if_legacy_result_is_parsed,
    )

    app = FastAPI()
    setup_api_routes(app, docking_service=MockDockingService(tmp_path))
    client = TestClient(app)

    started = time.perf_counter()
    response = client.get(
        "/api/docking/history?page=2&limit=5",
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
    # This is only a coarse regression budget; the structural assertion above
    # proves that request latency does not grow with pose-file parsing.
    assert elapsed_ms < 500


def test_docking_history_concurrent_mutation_and_reads_preserve_records(tmp_path):
    initial_keep = {f"keep-{index}" for index in range(10)}
    initial_remove = {f"remove-{index}" for index in range(20)}
    for index, job_id in enumerate(sorted(initial_keep | initial_remove)):
        upsert_history_record(
            tmp_path,
            {"job_id": job_id, "timestamp": float(index), "status": "completed"},
        )

    writer_count = 8
    records_per_writer = 25
    remover_count = 4
    reader_count = 4
    barrier = threading.Barrier(writer_count + remover_count + reader_count)

    def write_records(worker_index: int) -> None:
        barrier.wait()
        for record_index in range(records_per_writer):
            job_id = f"writer-{worker_index}-{record_index}"
            upsert_history_record(
                tmp_path,
                {
                    "job_id": job_id,
                    "timestamp": float(worker_index * records_per_writer + record_index),
                    "status": "completed",
                },
            )

    def remove_records(worker_index: int) -> None:
        barrier.wait()
        for record_index in range(worker_index, len(initial_remove), remover_count):
            remove_history_record(tmp_path, f"remove-{record_index}")

    def read_records() -> None:
        barrier.wait()
        for _ in range(50):
            read_history_page(tmp_path, page=1, limit=200)
            json.loads((tmp_path / "docking_history_index.json").read_text(encoding="utf-8"))

    with ThreadPoolExecutor(
        max_workers=writer_count + remover_count + reader_count
    ) as executor:
        futures = [executor.submit(write_records, index) for index in range(writer_count)]
        futures.extend(
            executor.submit(remove_records, index) for index in range(remover_count)
        )
        futures.extend(executor.submit(read_records) for _ in range(reader_count))
        for future in futures:
            future.result()

    payload = json.loads(
        (tmp_path / "docking_history_index.json").read_text(encoding="utf-8")
    )
    final_job_ids = {record["job_id"] for record in payload["records"]}
    expected_written = {
        f"writer-{worker_index}-{record_index}"
        for worker_index in range(writer_count)
        for record_index in range(records_per_writer)
    }
    assert initial_keep | expected_written <= final_job_ids
    assert final_job_ids.isdisjoint(initial_remove)


def test_docking_history_multiprocess_upserts_preserve_all_records(tmp_path):
    context = multiprocessing.get_context("spawn")
    worker_count = 6
    records_per_worker = 20
    start_event = context.Event()
    processes = [
        context.Process(
            target=_multiprocess_upsert_worker,
            args=(str(tmp_path), worker_index, records_per_worker, start_event),
        )
        for worker_index in range(worker_count)
    ]

    try:
        for process in processes:
            process.start()
        start_event.set()
        for process in processes:
            process.join(timeout=30)

        assert all(not process.is_alive() for process in processes)
        assert [process.exitcode for process in processes] == [0] * worker_count
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
            process.join(timeout=5)

    payload = json.loads(
        (tmp_path / "docking_history_index.json").read_text(encoding="utf-8")
    )
    final_job_ids = {record["job_id"] for record in payload["records"]}
    expected_job_ids = {
        f"process-{worker_index}-{record_index}"
        for worker_index in range(worker_count)
        for record_index in range(records_per_worker)
    }
    assert final_job_ids == expected_job_ids


def test_docking_history_lock_rejects_nonfinite_timing_configuration(monkeypatch):
    monkeypatch.setenv("MEDCHAT_DOCKING_HISTORY_LOCK_TIMEOUT_SECONDS", "nan")
    monkeypatch.setenv("MEDCHAT_DOCKING_HISTORY_LOCK_POLL_SECONDS", "inf")

    assert history_index_module._configured_positive_float(
        "MEDCHAT_DOCKING_HISTORY_LOCK_TIMEOUT_SECONDS",
        10.0,
    ) == 10.0
    assert history_index_module._configured_positive_float(
        "MEDCHAT_DOCKING_HISTORY_LOCK_POLL_SECONDS",
        0.05,
    ) == 0.05
