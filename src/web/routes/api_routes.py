"""
API 路由模块 - 分子对接和反向寻靶
"""
import os
import sys
import logging
import tempfile
import asyncio
import inspect
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional
from fastapi import UploadFile, File, Form, Header, HTTPException, Response, Body, Query
from starlette.concurrency import run_in_threadpool

from src.agent.persistence.redaction import redact_sensitive
from src.web.api_response import api_success

from .docking_routes import setup_docking_routes
from .molecule_utility_routes import setup_molecule_utility_routes
from .docking_report_routes import setup_docking_report_routes
from .reverse_target_routes import setup_reverse_target_routes
from .activity_prediction_routes import setup_activity_prediction_routes
from .activity_model_routes import setup_activity_model_routes
from .molecule_properties_routes import setup_molecule_properties_routes
from .agent_metrics_routes import setup_agent_metrics_routes

logger = logging.getLogger(__name__)


def _normalize_warning_strings(values: Any) -> List[str]:
    if not isinstance(values, list):
        return []
    warnings = []
    for value in values:
        if not isinstance(value, str):
            continue
        warning = value.strip()
        if not warning:
            continue
        warning = redact_sensitive(warning)
        if isinstance(warning, str) and warning not in warnings:
            warnings.append(warning)
    return warnings


def _parse_positive_int_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.getenv(name, str(default))))
    except ValueError:
        return default


_PHARM3D_EXECUTOR = ThreadPoolExecutor(
    max_workers=_parse_positive_int_env("REVERSE_TARGET_PHARM3D_WORKERS", 2)
)
_PHARM3D_CONCURRENCY = _parse_positive_int_env("REVERSE_TARGET_PHARM3D_CONCURRENCY", 2)
_PHARM3D_SEMAPHORE = asyncio.Semaphore(_PHARM3D_CONCURRENCY)


def _get_pharm3d_timeout(default: float = 25.0) -> float:
    try:
        return max(0.1, float(os.getenv("REVERSE_TARGET_PHARM3D_TIMEOUT_SECONDS", str(default))))
    except ValueError:
        return default


def _get_int_env(name: str, default: int, minimum: int = 1) -> int:
    try:
        return max(minimum, int(os.getenv(name, str(default))))
    except ValueError:
        return default


async def _read_upload_limited(upload: UploadFile, label: str) -> bytes:
    max_bytes = _get_int_env("MEDCHAT_MAX_UPLOAD_BYTES", 25 * 1024 * 1024)
    content = await upload.read(max_bytes + 1)
    if len(content) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"{label} exceeds the configured upload limit",
        )
    return content


async def _invoke_in_threadpool(func, *args, **kwargs):
    def invoke():
        result = func(*args, **kwargs)
        if inspect.isawaitable(result):
            return asyncio.run(result)
        return result

    return await run_in_threadpool(invoke)


def _validate_report_base64_payload(
    viewer_png_b64: Any,
    smiles_images_b64: Any,
) -> tuple[Optional[str], List[str]]:
    if viewer_png_b64 is not None and not isinstance(viewer_png_b64, str):
        raise HTTPException(
            status_code=422,
            detail="viewer_png_base64 must be a string or null",
        )
    if not isinstance(smiles_images_b64, list) or not all(
        isinstance(value, str) for value in smiles_images_b64
    ):
        raise HTTPException(
            status_code=422,
            detail="smiles_images must be a list of strings",
        )

    max_bytes = _get_int_env(
        "MEDCHAT_DOCKING_REPORT_MAX_BASE64_BYTES",
        10 * 1024 * 1024,
    )
    total_bytes = 0
    values = [viewer_png_b64]
    values.extend(smiles_images_b64)

    for value in values:
        if value is None:
            continue
        total_bytes += len(value.encode("utf-8"))
        if total_bytes > max_bytes:
            raise HTTPException(
                status_code=413,
                detail="Docking report image payload exceeds the configured limit",
            )
    return viewer_png_b64, smiles_images_b64


def _validate_docking_limits(
    *,
    size_x: float,
    size_y: float,
    size_z: float,
    exhaustiveness: int,
    num_modes: int,
    energy_range: float,
) -> None:
    if not all(0 < value <= 100 for value in (size_x, size_y, size_z)):
        raise HTTPException(status_code=422, detail="Docking box size must be within (0, 100]")
    if not 1 <= exhaustiveness <= 64:
        raise HTTPException(status_code=422, detail="exhaustiveness must be between 1 and 64")
    if not 1 <= num_modes <= 50:
        raise HTTPException(status_code=422, detail="num_modes must be between 1 and 50")
    if not 0 <= energy_range <= 20:
        raise HTTPException(status_code=422, detail="energy_range must be between 0 and 20")


def _get_pharm3d_candidate_pool_limit(top_k: int, max_refine: int) -> int:
    multiplier = _get_int_env("REVERSE_TARGET_PHARM3D_POOL_MULTIPLIER", 20, minimum=1)
    min_pool = _get_int_env("REVERSE_TARGET_PHARM3D_MIN_CANDIDATE_POOL", 250, minimum=1)
    max_pool = _get_int_env("REVERSE_TARGET_PHARM3D_MAX_CANDIDATE_POOL", 5000, minimum=1)
    desired = max(top_k * multiplier, max_refine, min_pool)
    return min(desired, max_pool)


async def _run_pharm3d_job(func, timeout_seconds: Optional[float] = None):
    loop = asyncio.get_running_loop()
    timeout = _get_pharm3d_timeout() if timeout_seconds is None else timeout_seconds
    async with _PHARM3D_SEMAPHORE:
        return await asyncio.wait_for(
            loop.run_in_executor(_PHARM3D_EXECUTOR, func),
            timeout=timeout,
        )


def _build_pharm3d_fallback(candidates: List[Dict[str, Any]], error: str = "") -> List[Dict[str, Any]]:
    fallback = []
    for cand in candidates:
        row = dict(cand)
        score_2d = row.get("final_similarity", 0.0)
        row["final_3d_score"] = score_2d
        row["pharm_combined_3d"] = None
        row["pharm_similarity"] = None
        row["alignment_score"] = None
        row["spatial_score"] = None
        row["pharm_features"] = []
        if error:
            row["pharm_error"] = error
        fallback.append(row)
    return fallback


def setup_api_routes(app, docking_service=None, task_runtime=None):
    """设置 API 路由。"""
    support = sys.modules[__name__]
    setup_docking_routes(app, docking_service=docking_service, task_runtime=task_runtime, _support=support)
    setup_molecule_utility_routes(app, _support=support)
    setup_docking_report_routes(app, docking_service=docking_service, _support=support)
    setup_reverse_target_routes(app, _support=support)
    setup_activity_prediction_routes(app, _support=support)
    setup_activity_model_routes(app, _support=support)
    setup_molecule_properties_routes(app, _support=support)
    setup_agent_metrics_routes(app, _support=support)
