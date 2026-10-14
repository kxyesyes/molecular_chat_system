"""Docking report route registration."""
import logging
import os
from typing import Dict, Any
from fastapi import Body, HTTPException, Request
from src.web.api_response import api_error

from .route_compat import lazy_dependency


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
        if not docking_service:
            raise HTTPException(status_code=503, detail="分子对接服务不可用")

        from .docking_routes import _owned_history, _validate_job_id
        _validate_job_id(job_id)
        _owned_history(request, docking_service.work_dir, job_id)

        try:
            job_dir = os.path.join(docking_service.work_dir, f"docking_{job_id}")
            result_file = os.path.join(job_dir, "result.pdbqt")
            
            if not os.path.exists(result_file):
                raise HTTPException(status_code=404, detail="对接结果文件不存在")

            results = docking_service.parse_vina_results(result_file)
            
            # 读取配置
            config_file = os.path.join(job_dir, "config.txt")
            config_lines = []
            if os.path.exists(config_file):
                with open(config_file, 'r', encoding='utf-8', errors='ignore') as cf:
                    config_lines = [line.strip() for line in cf.readlines() if line.strip()]

            # 解析参数
            fmt = "md"
            viewer_png_b64 = None
            smiles_images_b64 = []
            if payload and isinstance(payload, dict):
                fmt = str(payload.get("format", "md")).lower()
                viewer_png_b64 = payload.get("viewer_png_base64")
                smiles_images_b64 = payload.get("smiles_images", [])
            viewer_png_b64, smiles_images_b64 = get_validator()(
                viewer_png_b64,
                smiles_images_b64,
            )

            # 生成报告内容
            from .report_generator import generate_report
            return generate_report(job_id, results, config_lines, fmt, viewer_png_b64, smiles_images_b64, job_dir)

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
