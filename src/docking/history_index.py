from __future__ import annotations

from contextlib import contextmanager
import json
import math
import os
import re
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from src.task_runtime.private_permissions import restrict_private_path


HISTORY_INDEX_FILE = "docking_history_index.json"
HISTORY_LOCK_FILE = "docking_history_index.lock"
DEFAULT_LOCK_TIMEOUT_SECONDS = 10.0
DEFAULT_LOCK_POLL_SECONDS = 0.05
_VINA_RESULT_RE = re.compile(r"REMARK\s+VINA\s+RESULT:\s*([\-+]?\d*\.?\d+)")
_HISTORY_INDEX_LOCK = threading.RLock()


def _restrict_private_path(path: Path, mode: int) -> None:
    restrict_private_path(path, mode)


def history_index_path(work_dir: str | Path) -> Path:
    return Path(work_dir).resolve() / HISTORY_INDEX_FILE


def _history_lock_path(work_dir: str | Path) -> Path:
    return Path(work_dir).resolve() / HISTORY_LOCK_FILE


def _configured_positive_float(name: str, default: float) -> float:
    try:
        value = float(os.environ.get(name, default))
        if not math.isfinite(value) or value <= 0:
            raise ValueError
        return value
    except (TypeError, ValueError):
        return default


def _try_lock_file(lock_file) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)


def _unlock_file(lock_file) -> None:
    lock_file.seek(0)
    if os.name == "nt":
        import msvcrt

        msvcrt.locking(lock_file.fileno(), msvcrt.LK_UNLCK, 1)
        return

    import fcntl

    fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


@contextmanager
def _interprocess_history_lock(work_dir: str | Path) -> Iterator[None]:
    lock_path = _history_lock_path(work_dir)
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_private_path(lock_path.parent, 0o700)
    timeout_seconds = _configured_positive_float(
        "MEDCHAT_DOCKING_HISTORY_LOCK_TIMEOUT_SECONDS",
        DEFAULT_LOCK_TIMEOUT_SECONDS,
    )
    poll_seconds = _configured_positive_float(
        "MEDCHAT_DOCKING_HISTORY_LOCK_POLL_SECONDS",
        DEFAULT_LOCK_POLL_SECONDS,
    )
    deadline = time.monotonic() + timeout_seconds
    lock_file = lock_path.open("a+b", buffering=0)
    _restrict_private_path(lock_path, 0o600)
    acquired = False
    try:
        lock_file.seek(0, os.SEEK_END)
        if lock_file.tell() == 0:
            lock_file.write(b"\0")
            lock_file.flush()
            os.fsync(lock_file.fileno())

        while not acquired:
            try:
                _try_lock_file(lock_file)
                acquired = True
            except (BlockingIOError, OSError) as error:
                if time.monotonic() >= deadline:
                    raise TimeoutError(
                        "Timed out acquiring docking history lock "
                        f"after {timeout_seconds:g} seconds: {lock_path}"
                    ) from error
                time.sleep(min(poll_seconds, max(0.0, deadline - time.monotonic())))
        yield
    finally:
        release_error = None
        if acquired:
            try:
                _unlock_file(lock_file)
            except OSError as error:
                release_error = RuntimeError(
                    f"Failed to release docking history lock: {lock_path}"
                )
                release_error.__cause__ = error
        lock_file.close()
        if release_error is not None:
            raise release_error


@contextmanager
def _history_transaction(work_dir: str | Path) -> Iterator[None]:
    with _HISTORY_INDEX_LOCK:
        with _interprocess_history_lock(work_dir):
            yield


def _load_records_unlocked(work_dir: str | Path) -> list[dict[str, Any]]:
    path = history_index_path(work_dir)
    if not path.exists():
        return []
    _restrict_private_path(path, 0o600)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []

    raw_records = payload if isinstance(payload, list) else payload.get("records", [])
    return [record for record in raw_records if isinstance(record, dict)]


def _write_records_unlocked(
    work_dir: str | Path,
    records: list[dict[str, Any]],
) -> None:
    path = history_index_path(work_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    _restrict_private_path(path.parent, 0o700)
    temp_path = path.with_name(
        f".{path.name}.{os.getpid()}.{threading.get_ident()}.{uuid.uuid4().hex}.tmp"
    )
    try:
        with temp_path.open("w", encoding="utf-8", newline="\n") as temp_file:
            json.dump(
                {"records": records},
                temp_file,
                ensure_ascii=False,
                indent=2,
            )
            temp_file.flush()
            os.fsync(temp_file.fileno())
        _restrict_private_path(temp_path, 0o600)
        os.replace(temp_path, path)
        _restrict_private_path(path, 0o600)
    finally:
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass


def _parse_vina_summary(result_file: Path) -> tuple[float | None, int]:
    energies: list[float] = []
    current_energy: float | None = None
    current_atom_count = 0
    current_atom_serials: set[int] = set()
    model_count = 0
    in_model = False
    if not result_file.exists():
        return None, 0

    try:
        lines = result_file.read_text(encoding="utf-8", errors="ignore").splitlines()
    except Exception:
        return None, 0

    score_re = re.compile(
        r"REMARK\s+VINA\s+RESULT:\s*"
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+"
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)\s+"
        r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?)"
    )
    for line in lines:
        if line.startswith("MODEL"):
            if in_model:
                return None, 0
            fields = line.split()
            if len(fields) != 2 or not fields[1].isdigit() or int(fields[1]) != model_count + 1:
                return None, 0
            model_count += 1
            in_model = True
            current_energy = None
            current_atom_count = 0
            current_atom_serials = set()
            continue
        if line == "ENDMDL":
            if not in_model or current_energy is None or current_atom_count == 0:
                return None, 0
            energies.append(current_energy)
            in_model = False
            continue
        if not in_model:
            if line.startswith(("ATOM  ", "HETATM")):
                return None, 0
            continue
        match = score_re.fullmatch(line.strip())
        if match:
            if current_energy is not None:
                return None, 0
            try:
                values = tuple(float(value) for value in match.groups())
            except (TypeError, ValueError, OverflowError):
                return None, 0
            if not all(math.isfinite(value) for value in values):
                return None, 0
            if values[1] < 0 or values[2] < values[1]:
                return None, 0
            current_energy = values[0]
        elif line.startswith("REMARK VINA RESULT:"):
            return None, 0
        elif line.startswith(("ATOM  ", "HETATM")):
            try:
                serial = int(line[6:11].strip())
                coordinates = tuple(float(line[start:start + 8]) for start in (30, 38, 46))
            except (TypeError, ValueError, IndexError):
                return None, 0
            if (
                serial <= 0
                or serial in current_atom_serials
                or not all(math.isfinite(value) for value in coordinates)
            ):
                return None, 0
            current_atom_serials.add(serial)
            current_atom_count += 1
        elif (
            line.startswith("REMARK")
            or line.strip() in {"ROOT", "ENDROOT"}
            or line.startswith(("BRANCH", "ENDBRANCH", "TORSDOF"))
        ):
            continue
        elif line.strip():
            return None, 0

    if in_model or not energies:
        return None, 0
    return min(energies), len(energies)


def _directory_size(job_dir: Path) -> int:
    try:
        return sum(item.stat().st_size for item in job_dir.iterdir() if item.is_file())
    except Exception:
        return 0


def _read_run_manifest(job_dir: Path) -> dict[str, Any]:
    manifest_path = job_dir / "run_manifest.json"
    if not manifest_path.is_file():
        return {}
    try:
        payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _best_pose_index(result_file: Path) -> int | None:
    """Return the pose index selected by the strict Vina parser."""
    if not result_file.is_file():
        return None
    try:
        # Import lazily so the lightweight history index remains importable
        # without initializing the docking service or its adapters.
        from .molecular_docking_service import MolecularDockingService

        results = MolecularDockingService.parse_vina_results(str(result_file))
        return results[0].pose_index if results else None
    except Exception:
        return None


def _heavy_atom_count(ligand_file: Path) -> int:
    if not ligand_file.is_file():
        return 0
    try:
        count = 0
        for line in ligand_file.read_text(encoding="utf-8", errors="strict").splitlines():
            if line.startswith(("ATOM  ", "HETATM")) and not line[12:16].strip().upper().startswith("H"):
                count += 1
        return count
    except (OSError, UnicodeError):
        return 0


def _ligand_efficiency(best_energy: float | None, heavy_atom_count: int) -> float | None:
    if (
        type(best_energy) not in (int, float)
        or not math.isfinite(float(best_energy))
        or type(heavy_atom_count) is not int
        or heavy_atom_count <= 0
    ):
        return None
    return round(-float(best_energy) / heavy_atom_count, 3)


def build_history_record(
    job_dir: str | Path,
    job_id: str | None = None,
    status: str | None = None,
    owner_session_id: str | None = None,
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
    manifest = _read_run_manifest(job_path)
    best_pose_index = _best_pose_index(result_file)
    heavy_atom_count = _heavy_atom_count(ligand_file)
    artifacts = {
        name: name
        for name in ("receptor.pdbqt", "ligand.pdbqt", "result.pdbqt", "run_manifest.json")
        if (job_path / name).is_file()
    }

    try:
        timestamp = job_path.stat().st_mtime
    except Exception:
        timestamp = datetime.now().timestamp()

    record_status = status or (
        "completed" if has_result else ("processing" if has_receptor else "failed")
    )
    record = {
        "job_id": resolved_job_id,
        "time": datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M:%S"),
        "timestamp": timestamp,
        "status": record_status,
        "best_energy": best_energy,
        "pose_count": pose_count,
        "best_pose_index": best_pose_index,
        "ligand_efficiency": _ligand_efficiency(best_energy, heavy_atom_count),
        "has_receptor": has_receptor,
        "has_ligand": has_ligand,
        "has_result": has_result,
        "size_bytes": _directory_size(job_path),
        "artifacts": artifacts,
        "manifest_file": "run_manifest.json" if manifest else None,
        "docking_box": manifest.get("docking_box"),
        "search": manifest.get("search"),
        "preprocessing": manifest.get("preprocessing"),
        "preprocessing_policy": manifest.get("preprocessing_policy"),
        "preprocessing_state": manifest.get("preprocessing_state"),
        "execution": manifest.get("execution"),
    }
    if type(owner_session_id) is str and owner_session_id.strip():
        record["owner_session_id"] = owner_session_id.strip()
    return record


def get_history_record(
    work_dir: str | Path,
    job_id: str,
    *,
    owner_session_id: str | None = None,
) -> dict[str, Any] | None:
    """Return one history record only when its optional owner matches exactly."""
    with _history_transaction(work_dir):
        for record in _load_records_unlocked(work_dir):
            if str(record.get("job_id") or "") != str(job_id):
                continue
            if owner_session_id is None or record.get("owner_session_id") == owner_session_id:
                return dict(record)
            return None
    return None


def upsert_history_record(work_dir: str | Path, record: dict[str, Any]) -> None:
    job_id = str(record.get("job_id") or "").strip()
    if not job_id:
        return
    with _history_transaction(work_dir):
        records_by_id = {
            str(existing.get("job_id")): existing
            for existing in _load_records_unlocked(work_dir)
            if existing.get("job_id")
        }
        records_by_id[job_id] = record
        _write_records_unlocked(work_dir, list(records_by_id.values()))


def read_history_page(
    work_dir: str | Path,
    page: int = 1,
    limit: int = 50,
    owner_session_id: str | None = None,
) -> tuple[list[dict[str, Any]], int, int, int]:
    with _history_transaction(work_dir):
        resolved_page = max(1, int(page or 1))
        resolved_limit = max(1, min(200, int(limit or 50)))
        records = sorted(
            [
                record for record in _load_records_unlocked(work_dir)
                if owner_session_id is None
                or record.get("owner_session_id") == owner_session_id
            ],
            key=lambda item: float(item.get("timestamp") or 0),
            reverse=True,
        )
        total = len(records)
        start = (resolved_page - 1) * resolved_limit
        end = start + resolved_limit
        return records[start:end], total, resolved_page, resolved_limit


def remove_history_record(work_dir: str | Path, job_id: str) -> None:
    with _history_transaction(work_dir):
        records = [
            record
            for record in _load_records_unlocked(work_dir)
            if str(record.get("job_id")) != str(job_id)
        ]
        _write_records_unlocked(work_dir, records)


def clear_history_records(work_dir: str | Path) -> None:
    with _history_transaction(work_dir):
        _write_records_unlocked(work_dir, [])
