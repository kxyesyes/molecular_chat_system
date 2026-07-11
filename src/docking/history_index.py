from __future__ import annotations

import json
import re
import threading
from datetime import datetime
from pathlib import Path
from typing import Any


HISTORY_INDEX_FILE = "docking_history_index.json"
_VINA_RESULT_RE = re.compile(r"REMARK\s+VINA\s+RESULT:\s*([\-+]?\d*\.?\d+)")
_HISTORY_INDEX_LOCK = threading.RLock()


def history_index_path(work_dir: str | Path) -> Path:
    return Path(work_dir).resolve() / HISTORY_INDEX_FILE


def _load_records(work_dir: str | Path) -> list[dict[str, Any]]:
    with _HISTORY_INDEX_LOCK:
        path = history_index_path(work_dir)
        if not path.exists():
            return []
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            return []

        raw_records = payload if isinstance(payload, list) else payload.get("records", [])
        return [record for record in raw_records if isinstance(record, dict)]


def _write_records(work_dir: str | Path, records: list[dict[str, Any]]) -> None:
    with _HISTORY_INDEX_LOCK:
        path = history_index_path(work_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = path.with_suffix(path.suffix + ".tmp")
        temp_path.write_text(
            json.dumps({"records": records}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(path)


def _parse_vina_summary(result_file: Path) -> tuple[float | None, int]:
    best_energy: float | None = None
    pose_count = 0
    if not result_file.exists():
        return best_energy, pose_count

    try:
        lines = result_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None, 0

    for line in lines:
        match = _VINA_RESULT_RE.match(line)
        if not match:
            continue
        energy = float(match.group(1))
        pose_count += 1
        if best_energy is None or energy < best_energy:
            best_energy = energy
    return best_energy, pose_count


def _directory_size(job_dir: Path) -> int:
    try:
        return sum(item.stat().st_size for item in job_dir.iterdir() if item.is_file())
    except Exception:
        return 0


def build_history_record(
    job_dir: str | Path,
    job_id: str | None = None,
    status: str | None = None,
) -> dict[str, Any]:
    job_path = Path(job_dir).resolve()
    resolved_job_id = job_id or job_path.name.replace("docking_", "", 1)
    result_file = job_path / "result.pdbqt"
    receptor_file = job_path / "receptor.pdbqt"
    ligand_file = job_path / "ligand.pdbqt"

    has_result = result_file.exists()
    has_receptor = receptor_file.exists()
    has_ligand = ligand_file.exists()
    best_energy, pose_count = _parse_vina_summary(result_file)

    try:
        timestamp = job_path.stat().st_mtime
    except Exception:
        timestamp = datetime.now().timestamp()

    record_status = status or (
        "completed" if has_result else ("processing" if has_receptor else "failed")
    )
    return {
        "job_id": resolved_job_id,
        "time": datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": timestamp,
        "status": record_status,
        "best_energy": best_energy,
        "pose_count": pose_count,
        "has_receptor": has_receptor,
        "has_ligand": has_ligand,
        "has_result": has_result,
        "size_bytes": _directory_size(job_path),
    }


def upsert_history_record(work_dir: str | Path, record: dict[str, Any]) -> None:
    with _HISTORY_INDEX_LOCK:
        job_id = str(record.get("job_id") or "").strip()
        if not job_id:
            return
        records_by_id = {
            str(existing.get("job_id")): existing
            for existing in _load_records(work_dir)
            if existing.get("job_id")
        }
        records_by_id[job_id] = record
        _write_records(work_dir, list(records_by_id.values()))


def read_history_page(
    work_dir: str | Path,
    page: int = 1,
    limit: int = 50,
) -> tuple[list[dict[str, Any]], int, int, int]:
    with _HISTORY_INDEX_LOCK:
        resolved_page = max(1, int(page or 1))
        resolved_limit = max(1, min(200, int(limit or 50)))
        records = sorted(
            _load_records(work_dir),
            key=lambda item: float(item.get("timestamp") or 0),
            reverse=True,
        )
        total = len(records)
        start = (resolved_page - 1) * resolved_limit
        end = start + resolved_limit
        return records[start:end], total, resolved_page, resolved_limit


def remove_history_record(work_dir: str | Path, job_id: str) -> None:
    with _HISTORY_INDEX_LOCK:
        records = [
            record
            for record in _load_records(work_dir)
            if str(record.get("job_id")) != str(job_id)
        ]
        _write_records(work_dir, records)


def clear_history_records(work_dir: str | Path) -> None:
    with _HISTORY_INDEX_LOCK:
        _write_records(work_dir, [])
