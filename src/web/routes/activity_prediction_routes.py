"""Activity prediction route registration."""
import asyncio
import csv
import io
import math
import os
from pathlib import Path
import threading
from typing import Optional
from fastapi import UploadFile, File, Form, HTTPException


_SMILES_COLUMN_ALIASES = {"smiles", "smile", "canonical_smiles", "structure"}
_DEFAULT_ACTIVITY_TIMEOUT_SECONDS = 60.0
_DEFAULT_ACTIVITY_MAX_BATCH_ROWS = 100
_DEFAULT_ACTIVITY_MAX_CONCURRENCY = 2


def _positive_float_env(name: str, default: float, *, minimum: float = 0.1) -> float:
    try:
        value = float(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value >= minimum else default


def _positive_int_env(name: str, default: int, *, minimum: int = 1) -> int:
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= minimum else default


# A timed-out request must not release capacity while its worker is still
# running.  A thread semaphore keeps admission non-blocking for the event loop
# and bounds the number of in-flight calculations even when a worker cannot be
# force-cancelled safely.
_ACTIVITY_ADMISSION = threading.BoundedSemaphore(
    _positive_int_env("MEDCHAT_ACTIVITY_MAX_CONCURRENCY", _DEFAULT_ACTIVITY_MAX_CONCURRENCY)
)


_ACTIVITY_WORKERS: set[asyncio.Task] = set()


class _ActivityLease:
    """Release on physical exit, not on cancellation of an asyncio wrapper."""

    def __init__(self, slots):
        self.slots = slots
        self.lock = threading.Lock()
        self.state = "pending"

    def abandon_pending(self):
        with self.lock:
            if self.state == "pending":
                self.state = "finished"
                self.slots.release()

    def run(self, function):
        with self.lock:
            if self.state != "pending":
                return None  # timed out/cancelled while queued: do not compute
            self.state = "running"
        try:
            return function()
        finally:
            with self.lock:
                self.state = "finished"
                self.slots.release()

    def observe(self, worker):
        _ACTIVITY_WORKERS.discard(worker)
        self.abandon_pending()  # dispatch failure before the worker started
        if not worker.cancelled():
            worker.exception()  # consume late exceptions; never log raw input


async def _invoke_activity_with_budget(_support, function, *, operation: str):
    """Run one activity computation with bounded admission and request timeout.

    ``asyncio`` cannot safely stop arbitrary RDKit/PyTorch work running in a
    thread.  On timeout we therefore stop waiting for the request, keep the
    worker drained in the background, and retain its admission slot until it
    finishes.  This distinguishes request timeout from computation
    cancellation without allowing timed-out requests to multiply indefinitely.
    """
    slots = _ACTIVITY_ADMISSION
    if not slots.acquire(blocking=False):
        raise HTTPException(
            status_code=429,
            detail={
                "code": "ACTIVITY_CAPACITY_EXCEEDED",
                "message": "活性预测并发资源已满，请稍后重试。",
                "operation": operation,
            },
        )

    lease = _ActivityLease(slots)
    timeout = _positive_float_env(
        "MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", _DEFAULT_ACTIVITY_TIMEOUT_SECONDS
    )
    try:
        worker = asyncio.create_task(_support._invoke_in_threadpool(lambda: lease.run(function)))
        _ACTIVITY_WORKERS.add(worker)
        worker.add_done_callback(lease.observe)
        # Unlike wait_for, wait distinguishes worker TimeoutError from deadline
        # expiry and does not cancel a physical calculation on request timeout.
        done, _ = await asyncio.wait((worker,), timeout=timeout)
        if done:
            return await worker
        raise HTTPException(
            status_code=504,
            detail={
                "code": "ACTIVITY_REQUEST_TIMEOUT",
                "message": "活性预测超过请求时间限制，未返回科学结果。请求已停止等待，后台计算仍受并发上限约束。",
                "operation": operation,
                "compute_disposition": "draining",
            },
        ) from None
    finally:
        # Cancel queued execution; running work owns its slot until run() exits,
        # even if the loop is closed or its asyncio wrapper is cancelled.
        lease.abandon_pending()


def _parse_batch_smiles(content: str, *, filename: str, smiles_column: str | None = None) -> list[str]:
    """Parse one SMILES per text row or a headered CSV without shifting columns."""
    suffix = Path(filename or "").suffix.casefold()
    if suffix in {".csv", ".tsv"}:
        sample = content[:8192]
        try:
            dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
        except csv.Error:
            dialect = csv.excel_tab if suffix == ".tsv" else csv.excel
        reader = csv.DictReader(io.StringIO(content), dialect=dialect)
        headers = [str(header).strip() for header in (reader.fieldnames or []) if header is not None]
        if not headers:
            raise ValueError("CSV 缺少表头，无法定位 SMILES 列")
        requested = str(smiles_column or "").strip()
        if requested:
            matches = [header for header in headers if header.casefold() == requested.casefold()]
        else:
            matches = [header for header in headers if header.casefold() in _SMILES_COLUMN_ALIASES]
        if len(matches) != 1:
            raise ValueError("CSV 必须明确且唯一地提供 SMILES 列")
        selected = matches[0]
        values: list[str] = []
        for row in reader:
            value = row.get(selected)
            values.append("" if value is None else str(value).strip())
        return values

    if suffix not in {"", ".txt", ".smi", ".smiles"}:
        raise ValueError("仅支持 CSV、TSV 或每行一个 SMILES 的文本文件")
    # Text input has historically allowed an optional name/comment after the
    # first token; keep that compatibility while CSV/TSV uses the named field
    # above and never treats its header as a molecule.
    values: list[str] = []
    for line in content.splitlines():
        parts = line.strip().replace(",", " ").split()
        if parts:
            values.append(parts[0])
    return values


def setup_activity_prediction_routes(app, *, _support):
    """Register the original endpoints with dynamically resolved compatibility support."""
    @app.post("/api/activity/predict")
    async def activity_predict(
        smiles: str = Form(...),
        target: Optional[str] = Form(None),
    ):
        """活性预测 API"""
        try:
            def run_activity_prediction():
                from src.activity.prediction_service import predict_activity
                return predict_activity(smiles, target=target)

            return await _invoke_activity_with_budget(
                _support, run_activity_prediction, operation="predict"
            )
        except HTTPException:
            raise
        except Exception:
            _support.logger.error("活性预测请求失败")
            raise HTTPException(status_code=500, detail="活性预测服务不可用")

    @app.post("/api/activity/batch_predict")
    async def activity_batch_predict(
        file: UploadFile = File(...),
        target: Optional[str] = Form(None),
        smiles_column: Optional[str] = Form(None),
    ):
        """活性批量预测 API"""
        try:
            content = await _support._read_upload_limited(file, "activity batch file")
            text = content.decode("utf-8-sig")
            smiles_list = _parse_batch_smiles(
                text,
                filename=file.filename or "",
                smiles_column=smiles_column,
            )
            max_rows = _positive_int_env(
                "MEDCHAT_ACTIVITY_MAX_BATCH_ROWS", _DEFAULT_ACTIVITY_MAX_BATCH_ROWS
            )
            if len(smiles_list) > max_rows:
                raise HTTPException(
                    status_code=413,
                    detail={
                        "code": "ACTIVITY_BATCH_LIMIT_EXCEEDED",
                        "message": f"批量活性预测最多支持 {max_rows} 行。",
                        "max_rows": max_rows,
                        "received_rows": len(smiles_list),
                    },
                )
            
            def run_activity_batch_prediction():
                from src.activity.prediction_service import predict_activity
                return predict_activity(smiles_list, target=target)

            return await _invoke_activity_with_budget(
                _support, run_activity_batch_prediction, operation="batch_predict"
            )
        except HTTPException:
            raise
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="批量文件必须是 UTF-8 文本") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception:
            _support.logger.error("批量活性预测请求失败")
            raise HTTPException(status_code=500, detail="批量活性预测服务不可用")
