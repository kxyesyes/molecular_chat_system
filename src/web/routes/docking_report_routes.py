"""Docking report route registration."""
import asyncio
import logging
from pathlib import Path
import tempfile
from typing import Dict, Any
from fastapi import Body, HTTPException, Request
from src.web.api_response import api_error

from .route_compat import lazy_dependency
from .docking_consent_guard import report_consent_guard


_LOGGER = logging.getLogger(__name__)


def _register_docking_report_routes(
    app,
    docking_service=None,
    *,
    get_validator,
    get_logger,
):
    @app.post("/api/docking/report/{job_id}")
    async def get_docking_report(job_id: str, request: Request, payload: Dict[str, Any] = Body(None)):
        """生成并返回对接报告"""
        if not docking_service and not hasattr(app.state, "docking_report_task"):
            raise HTTPException(status_code=503, detail="分子对接服务不可用")

        from .docking_routes import _owned_history, _validate_job_id, _session_id
        _validate_job_id(job_id)
        _session_id(request)
        guard = report_consent_guard(app)
        guard.require_legacy(job_id)
        durable = None
        try:
            if docking_service is None:
                raise HTTPException(404, "对接任务不存在")
            _owned_history(request, docking_service.work_dir, job_id)
        except HTTPException as exc:
            resolver = getattr(app.state, "docking_report_task", None)
            if exc.status_code != 404 or resolver is None:
                raise
            durable = await resolver(job_id, request)
            if durable is None:
                raise
        guard.require_legacy(job_id)

        try:
            from .report_generator import generate_report
            payload = payload or {}
            fmt = str(payload.get("format", "md")).lower()
            pose = payload.get("pose")
            if pose is not None and (type(pose) is not int or pose < 1):
                raise HTTPException(422, "无效构象编号")
            image, ligand_images = get_validator()(payload.get("viewer_png_base64"), payload.get("smiles_images", []))

            def read_pose_rows(directory):
                from src.docking.molecular_docking_service import MolecularDockingService
                result_file = directory / "result.pdbqt"
                if not result_file.is_file():
                    raise HTTPException(404, "对接结果文件不存在")
                parser = docking_service.parse_vina_results if docking_service else MolecularDockingService.parse_vina_results
                rows = parser(str(result_file))
                if pose is not None and not any(r.pose_index == pose for r in rows):
                    raise HTTPException(422, "所选构象不在已验证结果中")
                return rows

            with tempfile.TemporaryDirectory(prefix="docking_report_") as tmp:
                if durable:
                    from src.task_runtime.staging import DockingInputStager
                    runtime, root, manifest_path = durable
                    directory = Path(tmp)
                    raw = await app.state.docking_report_pose(job_id, request)
                    (directory / "result.pdbqt").write_bytes(raw)
                    manifest = await asyncio.to_thread(DockingInputStager(root).load_verified_locator,
                                                       job_id, Path(manifest_path).name)
                    config = manifest["config"]
                    lines = [f"{key}_{axis} = {v}" for key in ("center", "size")
                             for axis, v in zip("xyz", config[key])]
                    lines.extend(f"{key} = {config[key]}" for key in ("exhaustiveness", "num_modes"))
                    lines.append("energy_range = 3.0")
                    (directory / "config.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")
                else:
                    directory = Path(docking_service.work_dir) / f"docking_{job_id}"
                    config_file = directory / "config.txt"
                    lines = []
                    if config_file.is_file():
                        lines = [s.strip() for s in config_file.read_text(encoding="utf-8").splitlines()
                                 if s.strip() and s.partition("=")[0].strip() not in {"receptor", "ligand", "out", "log"}]
                rows = await asyncio.to_thread(read_pose_rows, directory)
                options = {}
                if pose is not None:
                    reader = getattr(app.state, "docking_report_interactions", None)
                    analysis = await reader(job_id, request, pose) if reader else {"status": "unavailable"}
                    options = {"pose": pose, "analysis": analysis}
                response = await asyncio.to_thread(generate_report, job_id, rows, lines, fmt,
                                                   image, ligand_images, str(directory), **options)
                if durable:
                    guard.require_unambiguous_durable(runtime, job_id)
                else:
                    guard.require_legacy(job_id)
                return response

        except HTTPException:
            raise
        except Exception:
            get_logger().exception("生成报告失败")
            return api_error("DOCKING_REPORT_FAILED", "生成对接报告失败", status_code=500)


def register_docking_report_routes(
    app,
    docking_service=None,
    *,
    validate_report_base64_payload,
    logger=None,
):
    """Register production report routes with narrow dependencies."""
    return _register_docking_report_routes(
        app,
        docking_service=docking_service,
        get_validator=lambda: validate_report_base64_payload,
        get_logger=lambda: logger or _LOGGER,
    )


def setup_docking_report_routes(
    app,
    docking_service=None,
    *,
    validate_report_base64_payload=None,
    logger=None,
    _support=None,
):
    """Compatibility registration entry point for older direct callers."""
    return _register_docking_report_routes(
        app,
        docking_service=docking_service,
        get_validator=lazy_dependency(
            validate_report_base64_payload,
            _support,
            "_validate_report_base64_payload",
            label="docking report validator",
        ),
        get_logger=lazy_dependency(
            logger,
            _support,
            "logger",
            label="docking report logger",
            default=_LOGGER,
        ),
    )
