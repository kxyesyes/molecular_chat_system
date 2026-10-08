"""Activity prediction route registration."""
import asyncio
import csv
import io
import logging
import math
import os
from pathlib import Path
import threading
from typing import Optional
from fastapi import UploadFile, File, Form, HTTPException, Request
from fastapi.responses import JSONResponse
from src.web.process_isolation import IsolatedProcess, ProcessExecutionError, start_isolated_process
from src.web.request_auth import require_browser_session

from .route_compat import lazy_dependency


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


# A timed-out request must terminate its child before releasing capacity.
_ACTIVITY_ADMISSION = threading.BoundedSemaphore(
    _positive_int_env("MEDCHAT_ACTIVITY_MAX_CONCURRENCY", _DEFAULT_ACTIVITY_MAX_CONCURRENCY)
)

def _activity_child_job(payload, target):
    """Execute one activity request in a separately terminable process."""

    try:
        from src.activity.prediction_service import predict_activity

        return {
            "kind": "result",
            "value": predict_activity(payload, target=target),
        }
    except TimeoutError:
        return {"kind": "timeout"}
    except BaseException:
        return {"kind": "error"}


async def _stop_activity_process(process, waiter):
    await asyncio.to_thread(process.terminate)
    if waiter is not None and not waiter.done():
        try:
            await asyncio.wait_for(asyncio.shield(waiter), timeout=1.0)
        except asyncio.TimeoutError:
            waiter.cancel()
            try:
                await waiter
            except BaseException:
                pass
        except BaseException:
            pass
    await asyncio.to_thread(process.close)


async def _invoke_isolated_activity(
    payload,
    target,
    *,
    operation: str,
    timeout: float,
):
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

    process = IsolatedProcess(target, args=payload)
    waiter = None
    deadline = asyncio.get_running_loop().time() + timeout
    try:
        startup_budget = max(0.001, deadline - asyncio.get_running_loop().time())
        try:
            await start_isolated_process(process, timeout=startup_budget)
        except asyncio.TimeoutError:
            raise HTTPException(
                status_code=504,
                detail={
                    "code": "ACTIVITY_REQUEST_TIMEOUT",
                    "message": "活性预测超过请求时间限制，科学计算子进程已终止。",
                    "operation": operation,
                    "compute_disposition": "terminated",
                },
            ) from None
        waiter = asyncio.create_task(asyncio.to_thread(process.wait))
        try:
            remaining = max(0.001, deadline - asyncio.get_running_loop().time())
            envelope = await asyncio.wait_for(asyncio.shield(waiter), timeout=remaining)
        except asyncio.TimeoutError:
            await _stop_activity_process(process, waiter)
            raise HTTPException(
                status_code=504,
                detail={
                    "code": "ACTIVITY_REQUEST_TIMEOUT",
                    "message": "活性预测超过请求时间限制，科学计算子进程已终止。",
                    "operation": operation,
                    "compute_disposition": "terminated",
                },
            ) from None
        except asyncio.CancelledError:
            await _stop_activity_process(process, waiter)
            raise
        if not isinstance(envelope, dict):
            raise ProcessExecutionError("activity child returned an invalid envelope")
        kind = envelope.get("kind")
        if kind == "result":
            return envelope.get("value")
        if kind == "timeout":
            raise HTTPException(
                status_code=504,
                detail={
                    "code": "ACTIVITY_COMPUTE_TIMEOUT",
                    "message": "活性预测计算超时，未返回科学结果。",
                    "operation": operation,
                    "compute_disposition": "terminated",
                },
            )
        raise ProcessExecutionError("activity child failed")
    except ProcessExecutionError:
        raise HTTPException(
            status_code=500,
            detail={
                "code": "ACTIVITY_EXECUTION_FAILED",
                "message": "活性预测服务不可用，未返回科学结果。",
                "operation": operation,
            },
        ) from None
    finally:
        if process.is_alive():
            await _stop_activity_process(process, waiter)
        else:
            await asyncio.to_thread(process.close)
        slots.release()


async def _invoke_activity_with_budget(
    *,
    operation: str,
    isolated_payload,
    isolated_target=_activity_child_job,
):
    timeout = _positive_float_env(
        "MEDCHAT_ACTIVITY_TIMEOUT_SECONDS", _DEFAULT_ACTIVITY_TIMEOUT_SECONDS
    )
    return await _invoke_isolated_activity(
        isolated_payload,
        isolated_target,
        operation=operation,
        timeout=timeout,
    )


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
        # Normalize only for matching; DictReader rows retain the original keys.
        headers = [header for header in (reader.fieldnames or []) if header is not None]
        if not headers:
            raise ValueError("CSV 缺少表头，无法定位 SMILES 列")
        requested = str(smiles_column or "").strip()
        if requested:
            matches = [header for header in headers if header.strip().casefold() == requested.casefold()]
        else:
            matches = [header for header in headers if header.strip().casefold() in _SMILES_COLUMN_ALIASES]
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


def _attach_activity_task_receipt(
    *, owner_session_id: str, task_type: str, result
):
    """Persist a session-bound receipt for a synchronous prediction result."""

    from src.task_runtime.legacy_bridge import attach_legacy_task_id
    from src.task_runtime.manager import get_task_manager

    list_response = isinstance(result, list)
    if list_response:
        # Legacy callers may still return a raw row list.  The rows are
        # scientific observations, so aggregate them with the same domain
        # contract used by the service and Agent adapter instead of counting
        # transport-shaped ``success`` flags in the HTTP layer.
        from src.activity import prediction_service

        try:
            domain_summary = prediction_service.summarize_predictions(result)
        except (TypeError, ValueError):
            domain_summary = {"status": "failed", "success": False, "warnings": []}
        summary = {
            "status": domain_summary["status"],
            "success": domain_summary["success"],
            "count": len(result),
            "results": result,
        }
        if domain_summary.get("warnings"):
            summary["warnings"] = domain_summary["warnings"]
    elif not isinstance(result, dict):
        result = {
            "status": "failed",
            "success": False,
            "error": "活性预测返回了无效结果",
        }
        summary = result
    else:
        summary = result
    receipt = attach_legacy_task_id(
        get_task_manager(),
        task_type=task_type,
        owner_session_id=owner_session_id,
        result=summary,
    )
    if list_response:
        return JSONResponse(
            content=result,
            headers={"X-MedChat-Task-ID": receipt["task_id"]},
        )
    return receipt


def setup_activity_prediction_routes(
    app,
    *,
    invoke_activity_with_budget=None,
    read_upload_limited=None,
    logger=None,
    _support=None,
):
    """Register activity prediction endpoints with explicit runtime dependencies.

    ``_support`` remains a compatibility-only fallback for older direct callers;
    application registration passes narrow budget, upload and logging dependencies.
    """
    get_activity_invoker = lazy_dependency(
        invoke_activity_with_budget,
        _support,
        "_ROUTE_ACTIVITY_INVOKER",
        label="activity budget",
    )
    get_upload_reader = lazy_dependency(
        read_upload_limited,
        _support,
        "_read_upload_limited",
        label="activity upload",
    )
    get_logger = lazy_dependency(
        logger,
        _support,
        "logger",
        label="activity logger",
        default=logging.getLogger(__name__),
    )

    async def invoke_with_budget(*, operation, isolated_payload):
        return await get_activity_invoker()(
            operation=operation,
            isolated_payload=isolated_payload,
        )

    async def read_upload(upload, label):
        return await get_upload_reader()(upload, label)

    @app.post("/api/activity/predict")
    async def activity_predict(
        request: Request,
        smiles: str = Form(...),
        target: Optional[str] = Form(None),
    ):
        """活性预测 API"""
        owner_session_id = require_browser_session(request)
        try:
            result = await invoke_with_budget(
                operation="predict",
                isolated_payload=(smiles, target),
            )
            return _attach_activity_task_receipt(
                owner_session_id=owner_session_id,
                task_type="activity_prediction",
                result=result,
            )
        except HTTPException:
            raise
        except Exception:
            get_logger().error("活性预测请求失败")
            raise HTTPException(status_code=500, detail="活性预测服务不可用")

    @app.post("/api/activity/batch_predict")
    async def activity_batch_predict(
        request: Request,
        file: UploadFile = File(...),
        target: Optional[str] = Form(None),
        smiles_column: Optional[str] = Form(None),
    ):
        """活性批量预测 API"""
        owner_session_id = require_browser_session(request)
        try:
            content = await read_upload(file, "activity batch file")
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
            
            result = await invoke_with_budget(
                operation="batch_predict",
                isolated_payload=(smiles_list, target),
            )
            return _attach_activity_task_receipt(
                owner_session_id=owner_session_id,
                task_type="activity_batch_prediction",
                result=result,
            )
        except HTTPException:
            raise
        except UnicodeDecodeError:
            raise HTTPException(status_code=400, detail="批量文件必须是 UTF-8 文本") from None
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        except Exception:
            get_logger().error("批量活性预测请求失败")
            raise HTTPException(status_code=500, detail="批量活性预测服务不可用")
