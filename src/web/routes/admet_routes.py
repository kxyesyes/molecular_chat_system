"""Structured ADMET HTTP adapter.

This module owns transport validation and worker lifecycle only. Scientific
validation, model availability, result validation, provenance, and partial
result semantics remain in :class:`ADMETPredictor`.
"""

from __future__ import annotations

import asyncio
import logging
import math
import os
import re
import threading
from concurrent import futures
from uuid import uuid4
from collections.abc import Mapping
from typing import Any
from types import SimpleNamespace

from fastapi import FastAPI, HTTPException, Request

from .route_compat import lazy_dependency
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, field_validator

from src.agent.persistence.redaction import sanitize_bounded, sanitize_sensitive_text
from src.agent.tools.admet_predictor import ADMETPredictor
from src.agent.tools.base_tool import BaseMolecularTool
from src.agent.tools.molecular_input import MolecularInputUnavailable, parse_molecular_smiles
from src.web.process_isolation import IsolatedProcess, ProcessExecutionError
from src.web.request_auth import require_browser_session


logger = logging.getLogger(__name__)

_DEFAULT_MAX_CONCURRENCY = 1
_DEFAULT_REQUEST_TIMEOUT_SECONDS = 180.0
_DIAGNOSTIC_FIELDS = frozenset(
    {"message", "reasoning", "warnings", "error", "reason", "failure_reason", "backend_error"}
)


class AdmetPredictRequest(BaseModel):
    """The HTTP shape; this validation runs before the worker is admitted."""

    model_config = ConfigDict(extra="forbid")

    smiles: str = Field(min_length=1, max_length=8192)
    molecule_id: str = Field(default="molecule-001", min_length=1, max_length=128)

    @field_validator("smiles", "molecule_id", mode="before")
    @classmethod
    def _require_nonempty_string(cls, value: Any) -> str:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("must be a non-empty string")
        return value.strip()


class _InvalidSmiles(ValueError):
    """The complete requested structure failed the existing parser."""


def _execute_admet_request(smiles: str, molecule_id: str) -> Any:
    """Run one ADMET calculation in the current execution boundary."""

    predictor = None
    try:
        try:
            parse_molecular_smiles(smiles, _STRUCTURE_VALIDATOR)
        except MolecularInputUnavailable:
            return _validation_unavailable()
        except ValueError as exc:
            raise _InvalidSmiles from exc

        predictor = build_admet_predictor()
        return predictor.execute(
            {
                "smiles": [smiles],
                "molecule_ids": [molecule_id],
            }
        )
    finally:
        if predictor is not None:
            close = getattr(predictor, "close", None)
            if callable(close):
                try:
                    close()
                except Exception:
                    logger.warning("ADMET predictor cleanup failed; exception details omitted")


def _admet_child_job(smiles: str, molecule_id: str) -> dict[str, Any]:
    """Translate expected child outcomes without serializing exception details."""

    try:
        return {"kind": "result", "value": _execute_admet_request(smiles, molecule_id)}
    except _InvalidSmiles:
        return {"kind": "invalid_smiles"}
    except TimeoutError:
        return {"kind": "timeout"}
    except BaseException:
        return {"kind": "error"}


class _StructureValidationTool:
    """Minimal parser context; it performs no prediction or fallback logic."""

    exclude_words = frozenset()
    _is_plausible_smiles_lexeme = staticmethod(BaseMolecularTool._is_plausible_smiles_lexeme)


_STRUCTURE_VALIDATOR = _StructureValidationTool()


def _env_getter(source: Any):
    if callable(source):
        return source
    if source is not None and hasattr(source, "os"):
        return source.os.getenv
    return os.getenv


def _positive_int_env(source: Any, name: str, default: int) -> int:
    try:
        value = int(_env_getter(source)(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if value >= 1 else default


def _positive_float_env(source: Any, name: str, default: float) -> float:
    try:
        value = float(_env_getter(source)(name, str(default)))
    except (TypeError, ValueError):
        return default
    return value if math.isfinite(value) and value > 0 else default


def build_admet_predictor() -> ADMETPredictor:
    """Build the existing predictor; tests may replace this factory."""

    return ADMETPredictor()


def _safe_diagnostic_text(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    return sanitize_sensitive_text(value, max_chars=8192)[0]


def _safe_diagnostic_value(value: Any) -> Any:
    if isinstance(value, str):
        return _safe_diagnostic_text(value)
    return sanitize_bounded(value, max_depth=6, max_items=128, max_text_chars=8192)[0]


def _safe_tool_result(result: Any) -> dict[str, Any]:
    """Sanitize diagnostic fields while leaving science and provenance intact."""

    if not isinstance(result, Mapping):
        return _safe_execution_failure()
    if _has_unsafe_diagnostics(result):
        return _safe_execution_failure()

    output = dict(result)
    for field in ("message", "reasoning"):
        if field in output:
            output[field] = _safe_diagnostic_value(output[field])
    if "warnings" in output:
        output["warnings"] = _safe_diagnostic_value(output["warnings"])
    if "error" in output:
        output["error"] = _safe_diagnostic_value(output["error"])
    quality = output.get("quality")
    if isinstance(quality, Mapping):
        quality_copy = dict(quality)
        for field in _DIAGNOSTIC_FIELDS:
            if field in quality_copy:
                quality_copy[field] = _safe_diagnostic_value(quality_copy[field])
        output["quality"] = quality_copy
    _sanitize_row_diagnostics(output)
    if output.get("status") in {"unavailable", "failed", "timeout"}:
        output.setdefault("data", None)
        output.setdefault("message", None)
        output.setdefault("warnings", [])
        output.setdefault("quality", {})
        output.setdefault("provenance", None)
        output.setdefault("reasoning", None)
        output.setdefault("error", None)
    return output


def _safe_execution_failure() -> dict[str, Any]:
    return {
        "success": False,
        "status": "failed",
        "data": None,
        "message": "ADMET prediction failed.",
        "warnings": [],
        "quality": {},
        "provenance": None,
        "reasoning": "The calculation failed; no simulated scientific result was produced.",
        "error": {
            "code": "ADMET_EXECUTION_FAILED",
            "message": "ADMET prediction failed.",
        },
    }


def _safe_timeout_failure() -> dict[str, Any]:
    result = _safe_execution_failure()
    result["status"] = "timeout"
    result["error"] = {
        "code": "ADMET_TIMEOUT",
        "message": "ADMET prediction timed out.",
    }
    result["message"] = "ADMET prediction timed out."
    result["reasoning"] = "The calculation timed out; no simulated scientific result was produced."
    return result


def _validation_unavailable() -> dict[str, Any]:
    return {
        "success": False,
        "status": "unavailable",
        "data": None,
        "message": "RDKit structure validation is unavailable; no ADMET prediction was produced.",
        "warnings": [],
        "quality": {},
        "provenance": None,
        "reasoning": "The structure could not be validated, so no scientific result was fabricated.",
        "error": {
            "code": "ADMET_VALIDATION_UNAVAILABLE",
            "message": "RDKit structure validation is unavailable.",
        },
    }


def _safe_request_error(status: int, code: str, message: str) -> JSONResponse:
    state = "invalid_input" if status == 422 else "busy" if status == 429 else "failed"
    return JSONResponse(
        status_code=status,
        content={
            "success": False,
            "status": state,
            "data": [],
            "message": message,
            "warnings": [],
            "quality": {},
            "provenance": None,
            "reasoning": None,
            "error": {"code": code, "message": message},
        },
    )


def _has_unsafe_diagnostic(value: Any) -> bool:
    if isinstance(value, str):
        if "traceback" in value.casefold():
            return True
        return bool(
            re.search(r"(?i)\b(?:api[_-]?key|password|passwd|secret|token)\s*[:=]", value)
            or re.search(r"\bsk-[A-Za-z0-9_-]{8,}\b", value)
            or re.search(r"(?<![A-Za-z0-9])(?:[A-Za-z]:[\\/]|\\\\)[^\s;,]+", value)
            or re.search(r"(?<![A-Za-z0-9])/(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+", value)
        )
    if isinstance(value, Mapping):
        return any(_has_unsafe_diagnostic(item) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_unsafe_diagnostic(item) for item in value)
    return False


def _has_unsafe_diagnostics(result: Mapping[str, Any]) -> bool:
    for field in ("message", "warnings", "error", "reasoning"):
        if _has_unsafe_diagnostic(result.get(field)):
            return True
    quality = result.get("quality")
    return isinstance(quality, Mapping) and any(
        _has_unsafe_diagnostic(quality.get(field))
        for field in _DIAGNOSTIC_FIELDS
        if field in quality
    )


def _sanitize_row_diagnostics(output: dict[str, Any]) -> None:
    """Redact only per-row diagnostics; science and evidence remain intact."""

    rows = output.get("data")
    if not isinstance(rows, list):
        return
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping):
            continue
        safe_row = dict(row)
        for field in ("message", "reasoning", "error", "warnings"):
            if field not in safe_row:
                continue
            value = safe_row[field]
            if not _has_unsafe_diagnostic(value):
                safe_row[field] = _safe_diagnostic_value(value)
                continue
            if field == "warnings":
                safe_row[field] = ["详细诊断信息已隐藏。"]
            elif field == "error":
                safe_row[field] = {
                    "code": "ADMET_ROW_DIAGNOSTIC_REDACTED",
                    "message": "详细错误信息已隐藏。",
                }
            else:
                safe_row[field] = "详细诊断信息已隐藏。"
        rows[index] = safe_row


class _AdmetWorker:
    """App-scoped worker with non-queueing admission and physical cleanup.

    Production calculations run in a child process so request cancellation and
    hard deadlines can terminate the scientific work itself.  ``isolate=False``
    is intentionally available for deterministic unit fixtures that inject a
    predictor into the parent interpreter.
    """

    def __init__(
        self,
        support: Any = None,
        *,
        env_getter=None,
        executor_factory=None,
        isolate: bool = True,
        process_target=None,
    ):
        self._env_getter = env_getter or _env_getter(support)
        self._executor_factory = executor_factory or (
            getattr(support, "ThreadPoolExecutor", None) or futures.ThreadPoolExecutor
            if support is not None
            else futures.ThreadPoolExecutor
        )
        self._isolate = bool(isolate)
        self._process_target = process_target or _admet_child_job
        self._capacity = _positive_int_env(
            self._env_getter, "MEDCHAT_ADMET_MAX_CONCURRENCY", _DEFAULT_MAX_CONCURRENCY
        )
        self._admission = threading.BoundedSemaphore(self._capacity)
        self._executor = self._executor_factory(
            max_workers=self._capacity,
            thread_name_prefix="medchat-admet",
        )
        self._lock = threading.Lock()
        self._active_processes: dict[str, IsolatedProcess] = {}
        self._cancelled_jobs: set[str] = set()
        self._closed = False

    @property
    def closed(self) -> bool:
        with self._lock:
            return self._closed

    def acquire(self) -> bool:
        with self._lock:
            if self._closed:
                return False
        if not self._admission.acquire(blocking=False):
            return False
        with self._lock:
            if self._closed:
                self._admission.release()
                return False
        return True

    def submit(self, request: AdmetPredictRequest):
        job_id = uuid4().hex
        try:
            future = self._executor.submit(self._run, request, job_id)
            future._admet_job_id = job_id
            future.add_done_callback(lambda _: self._admission.release())
            return future
        except Exception:
            self._admission.release()
            raise

    def _run(self, request: AdmetPredictRequest, job_id: str) -> Any:
        if not self._isolate:
            return _execute_admet_request(request.smiles, request.molecule_id)

        process = IsolatedProcess(
            self._process_target,
            args=(request.smiles, request.molecule_id),
        )
        with self._lock:
            if self._closed or job_id in self._cancelled_jobs:
                self._cancelled_jobs.discard(job_id)
                raise ProcessExecutionError("ADMET job cancelled before start")
            self._active_processes[job_id] = process
        try:
            # Do not hold the worker registry lock across native process
            # startup.  Cancellation must be able to find and terminate a
            # process even when its start call is slow or stuck.
            process.start()
        except Exception:
            with self._lock:
                self._active_processes.pop(job_id, None)
                self._cancelled_jobs.discard(job_id)
            process.close()
            raise
        try:
            envelope = process.wait()
            if not isinstance(envelope, dict):
                raise ProcessExecutionError("ADMET child returned an invalid envelope")
            kind = envelope.get("kind")
            if kind == "result":
                return envelope.get("value")
            if kind == "invalid_smiles":
                raise _InvalidSmiles
            if kind == "timeout":
                raise TimeoutError("ADMET child timed out")
            raise ProcessExecutionError("ADMET child failed")
        finally:
            with self._lock:
                self._active_processes.pop(job_id, None)
                self._cancelled_jobs.discard(job_id)
            process.close()

    def cancel(self, future) -> bool:
        """Cancel queued work or physically terminate its scientific child."""

        job_id = getattr(future, "_admet_job_id", None)
        if callable(getattr(future, "done", None)) and future.done():
            if isinstance(job_id, str):
                with self._lock:
                    self._cancelled_jobs.discard(job_id)
            return False
        process = None
        if isinstance(job_id, str):
            with self._lock:
                self._cancelled_jobs.add(job_id)
                process = self._active_processes.get(job_id)
        cancelled = future.cancel()
        if process is not None:
            process.terminate()
            return True
        if cancelled and isinstance(job_id, str):
            with self._lock:
                self._cancelled_jobs.discard(job_id)
        return cancelled

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            active = list(self._active_processes.values())
        for process in active:
            process.terminate()
        self._executor.shutdown(wait=True, cancel_futures=True)


def _default_support() -> Any:
    """Return the legacy support shape without importing the route facade."""

    return SimpleNamespace(os=os, ThreadPoolExecutor=futures.ThreadPoolExecutor)


def setup_admet_routes(
    app: FastAPI,
    *,
    env_getter=None,
    executor_factory=None,
    _support=None,
    _isolate: bool | None = None,
    _process_target=None,
) -> None:
    """Register the single structured ADMET operation."""

    if getattr(app.state, "_admet_routes_registered", False):
        return

    if env_getter is None:
        env_getter = _env_getter(
            lazy_dependency(
                None,
                _support,
                "os",
                label="ADMET environment",
                default=os,
            )()
        )
    if executor_factory is None:
        executor_factory = lazy_dependency(
            None,
            _support,
            "ThreadPoolExecutor",
            label="ADMET executor",
            default=futures.ThreadPoolExecutor,
        )()
        if executor_factory is None:
            executor_factory = futures.ThreadPoolExecutor
    worker = getattr(app.state, "_admet_worker", None)
    if worker is None:
        isolate = True if _isolate is None else _isolate
        worker = _AdmetWorker(
            env_getter=env_getter,
            executor_factory=executor_factory,
            isolate=isolate,
            process_target=_process_target,
        )
        app.state._admet_worker = worker

        async def shutdown_admet_worker() -> None:
            await asyncio.to_thread(worker.close)

        app.router.add_event_handler("shutdown", shutdown_admet_worker)

    @app.post("/api/admet/predict")
    async def predict_admet(request: Request, payload: AdmetPredictRequest):
        require_browser_session(request)
        if not worker.acquire():
            return _safe_request_error(
                429,
                "ADMET_BUSY",
                "ADMET prediction capacity is busy; retry later.",
            )

        try:
            future = worker.submit(payload)
            result = await asyncio.wait_for(
                asyncio.shield(asyncio.wrap_future(future)),
                timeout=_positive_float_env(
                    env_getter,
                    "MEDCHAT_ADMET_REQUEST_TIMEOUT_SECONDS",
                    _DEFAULT_REQUEST_TIMEOUT_SECONDS,
                ),
            )
        except _InvalidSmiles:
            return _safe_request_error(422, "INVALID_SMILES", "SMILES 无效或缺失；请提供完整结构。")
        except HTTPException:
            raise
        except asyncio.TimeoutError:
            await asyncio.to_thread(worker.cancel, future)
            logger.warning("ADMET prediction timed out; exception details omitted")
            return JSONResponse(status_code=504, content=_safe_timeout_failure())
        except asyncio.CancelledError:
            await asyncio.to_thread(worker.cancel, future)
            raise
        except ProcessExecutionError:
            logger.warning("ADMET child process failed; exception details omitted")
            return JSONResponse(status_code=500, content=_safe_execution_failure())
        except TimeoutError:
            logger.warning("ADMET prediction timed out; exception details omitted")
            return JSONResponse(status_code=504, content=_safe_timeout_failure())
        except Exception:
            logger.exception("ADMET prediction execution failed; exception details omitted")
            return JSONResponse(status_code=500, content=_safe_execution_failure())
        safe_result = _safe_tool_result(result)
        status_codes = {
            "succeeded": 200,
            "partial": 200,
            "invalid_input": 422,
            "unavailable": 503,
            "failed": 500,
            "timeout": 504,
            "busy": 429,
        }
        status_code = status_codes.get(safe_result.get("status"))
        if status_code is None:
            safe_result = _safe_execution_failure()
            status_code = 500
        return JSONResponse(status_code=status_code, content=safe_result)

    app.state._admet_routes_registered = True
