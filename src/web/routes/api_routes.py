"""
API 路由模块 - 分子对接和反向寻靶
"""
import base64
import binascii
import os
import sys
import logging
import math
import tempfile
import asyncio
import inspect
import zlib
from concurrent.futures import ThreadPoolExecutor
from typing import Dict, Any, List, Optional
from fastapi import UploadFile, File, Form, Header, HTTPException, Response, Body, Query
from starlette.concurrency import run_in_threadpool

from src.agent.persistence.redaction import redact_sensitive
from src.web.api_response import api_success
from src.web.process_isolation import IsolatedProcess, start_isolated_process

from .docking_routes import setup_docking_routes
from .molecule_utility_routes import setup_molecule_utility_routes
from .docking_report_routes import setup_docking_report_routes
from .reverse_target_routes import setup_reverse_target_routes
from .activity_prediction_routes import setup_activity_prediction_routes
from .activity_model_routes import setup_activity_model_routes
from .molecule_properties_routes import setup_molecule_properties_routes
from .admet_routes import setup_admet_routes
from .agent_metrics_routes import setup_agent_metrics_routes

logger = logging.getLogger(__name__)


class _DynamicLogger:
    """Keep post-registration logger patching without passing the support module."""

    def __getattr__(self, name):
        return getattr(logger, name)


_ROUTE_LOGGER = _DynamicLogger()


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


_PHARM3D_CONCURRENCY = _parse_positive_int_env("REVERSE_TARGET_PHARM3D_CONCURRENCY", 2)
_PHARM3D_SEMAPHORE = asyncio.Semaphore(_PHARM3D_CONCURRENCY)


def _get_pharm3d_timeout(default: float = 25.0) -> float:
    try:
        value = float(os.getenv("REVERSE_TARGET_PHARM3D_TIMEOUT_SECONDS", str(default)))
    except ValueError:
        return default
    return value if math.isfinite(value) and value >= 0.1 else default


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

    def _valid_png_bytes(decoded: bytes) -> bool:
        signature = b"\x89PNG\r\n\x1a\n"
        if len(decoded) < len(signature) + 12 or not decoded.startswith(signature):
            return False

        max_pixels = 50_000_000
        max_inflated = 64 * 1024 * 1024
        channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}
        valid_depths = {
            0: {1, 2, 4, 8, 16},
            2: {8, 16},
            3: {1, 2, 4, 8},
            4: {8, 16},
            6: {8, 16},
        }
        offset = len(signature)
        seen_ihdr = False
        seen_idat = False
        seen_iend = False
        idat_parts: list[bytes] = []
        width = height = bit_depth = color_type = interlace = None

        while offset < len(decoded):
            if len(decoded) - offset < 12:
                return False
            length = int.from_bytes(decoded[offset:offset + 4], "big")
            chunk_start = offset + 4
            chunk_end = chunk_start + 4 + length + 4
            if chunk_end > len(decoded):
                return False
            chunk_type = decoded[chunk_start:chunk_start + 4]
            data_start = chunk_start + 4
            data_end = data_start + length
            chunk_data = decoded[data_start:data_end]
            expected_crc = int.from_bytes(decoded[data_end:chunk_end], "big")
            actual_crc = zlib.crc32(chunk_type + chunk_data) & 0xFFFFFFFF
            if actual_crc != expected_crc:
                return False
            offset = chunk_end

            if not seen_ihdr and chunk_type != b"IHDR":
                return False
            if chunk_type == b"IHDR":
                if seen_ihdr or length != 13:
                    return False
                width = int.from_bytes(chunk_data[0:4], "big")
                height = int.from_bytes(chunk_data[4:8], "big")
                bit_depth = chunk_data[8]
                color_type = chunk_data[9]
                compression = chunk_data[10]
                filter_method = chunk_data[11]
                interlace = chunk_data[12]
                if (
                    width == 0 or height == 0 or width * height > max_pixels
                    or color_type not in channels
                    or bit_depth not in valid_depths.get(color_type, set())
                    or compression != 0 or filter_method != 0 or interlace not in {0, 1}
                ):
                    return False
                seen_ihdr = True
            elif chunk_type == b"IDAT":
                if seen_iend:
                    return False
                seen_idat = True
                idat_parts.append(chunk_data)
            elif chunk_type == b"IEND":
                if length != 0 or not seen_ihdr or not seen_idat or seen_iend:
                    return False
                seen_iend = True
                if offset != len(decoded):
                    return False

        if not (seen_ihdr and seen_idat and seen_iend and idat_parts):
            return False
        try:
            decompressor = zlib.decompressobj()
            inflated = decompressor.decompress(b"".join(idat_parts), max_inflated + 1)
            if len(inflated) > max_inflated or decompressor.unconsumed_tail:
                return False
            inflated += decompressor.flush()
        except zlib.error:
            return False
        if not decompressor.eof or not inflated:
            return False

        if interlace == 0:
            bits_per_pixel = channels[color_type] * bit_depth
            row_bytes = (width * bits_per_pixel + 7) // 8
            if len(inflated) != (row_bytes + 1) * height:
                return False
        return True

    def validate_png(value: Optional[str], label: str) -> Optional[str]:
        if value in (None, ""):
            return value
        try:
            decoded = base64.b64decode(value, validate=True)
        except (binascii.Error, ValueError):
            raise HTTPException(
                status_code=422,
                detail=f"{label} must contain valid base64 PNG data",
            ) from None
        if not _valid_png_bytes(decoded):
            raise HTTPException(
                status_code=422,
                detail=f"{label} must contain a PNG image",
            )
        return value

    viewer_png_b64 = validate_png(viewer_png_b64, "viewer_png_base64")
    smiles_images_b64 = [
        validate_png(value, f"smiles_images[{index}]")
        for index, value in enumerate(smiles_images_b64)
    ]
    return viewer_png_b64, smiles_images_b64


def _validate_docking_limits(
    *,
    center_x: float,
    center_y: float,
    center_z: float,
    size_x: float,
    size_y: float,
    size_z: float,
    exhaustiveness: int,
    num_modes: int,
    energy_range: float,
) -> None:
    if not all(math.isfinite(value) for value in (center_x, center_y, center_z)):
        raise HTTPException(status_code=422, detail="Docking box center must be finite")
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


def _pharm3d_refine_job(
    query_smiles: str,
    candidates: List[Dict[str, Any]],
    max_to_refine: int,
    alpha_2d: float,
    alpha_3d: float,
    timeout_seconds: float,
):
    from src.reverse_target.pharmacophore_refiner import refine_with_pharmacophore

    return refine_with_pharmacophore(
        query_smiles=query_smiles,
        candidates=candidates,
        max_to_refine=max_to_refine,
        alpha_2d=alpha_2d,
        alpha_3d=alpha_3d,
        timeout_seconds=timeout_seconds,
    )


def _pharm3d_query_job(smiles: str):
    from src.reverse_target.pharmacophore_refiner import get_molecule_pharmacophore

    return get_molecule_pharmacophore(smiles)


def _pharm3d_candidates_job(
    smiles: str,
    threshold: float,
    limit: int,
    organism_filter: str,
):
    """Load 2D candidates inside the same killable process boundary as 3D work."""

    from src.reverse_target.predictor import get_predictor

    return get_predictor().get_raw_similar_molecules(
        smiles=smiles,
        threshold=threshold,
        limit=limit,
        organism_filter=organism_filter,
    )


async def _stop_pharm3d_process(process, waiter):
    await asyncio.to_thread(process.terminate)
    if waiter is not None:
        try:
            await asyncio.wait_for(asyncio.shield(waiter), timeout=1.0)
        except asyncio.TimeoutError:
            # ``to_thread`` cannot stop the native wait itself, but cancelling
            # its asyncio wrapper prevents a late child error becoming an
            # unobserved task exception after the request has returned.
            waiter.cancel()
            try:
                await waiter
            except BaseException:
                pass
        except BaseException:
            pass
    await asyncio.to_thread(process.close)


async def _run_pharm3d_job(target, *args, timeout_seconds: Optional[float] = None):
    timeout = _get_pharm3d_timeout() if timeout_seconds is None else timeout_seconds
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("pharm3d timeout must be finite and positive")

    async def execute():
        async with _PHARM3D_SEMAPHORE:
            deadline = asyncio.get_running_loop().time() + timeout
            process = IsolatedProcess(target, args=args)
            waiter = None
            try:
                startup_budget = max(0.001, deadline - asyncio.get_running_loop().time())
                await start_isolated_process(process, timeout=startup_budget)
                waiter = asyncio.create_task(asyncio.to_thread(process.wait))
                remaining = max(0.001, deadline - asyncio.get_running_loop().time())
                return await asyncio.wait_for(asyncio.shield(waiter), timeout=remaining)
            finally:
                # Reap and observe the waiter before returning the admission slot.
                await _stop_pharm3d_process(process, waiter)

    # Queueing and process startup consume the same budget as native calculation.
    # wait_for waits for cancellation cleanup, so a timed-out child is not orphaned.
    return await asyncio.wait_for(execute(), timeout=timeout)


def _build_pharm3d_fallback(candidates: List[Dict[str, Any]], error: str = "") -> List[Dict[str, Any]]:
    fallback = []
    for cand in candidates:
        row = dict(cand)
        score_2d = row.get("final_similarity", 0.0)
        row["final_3d_score"] = None
        row["rank_score"] = score_2d
        row["score_semantics"] = "2d_similarity_fallback"
        row["pharm_combined_3d"] = None
        row["pharm_similarity"] = None
        row["alignment_score"] = None
        row["spatial_score"] = None
        row["pharm_features"] = []
        row["pharm_refinement_status"] = "fallback"
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
    setup_molecule_properties_routes(app, logger=_ROUTE_LOGGER)
    setup_admet_routes(app, _support=support)
    setup_agent_metrics_routes(app, logger=_ROUTE_LOGGER)
