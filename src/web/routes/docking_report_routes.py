"""Docking report route registration."""
from typing import Dict, Any
from fastapi import Body, HTTPException


def setup_docking_report_routes(app, docking_service=None, *, _support):
    """Register the original endpoints with dynamically resolved compatibility support."""
    @app.post("/api/docking/report/{job_id}")
    async def get_docking_report(job_id: str, payload: Dict[str, Any] = Body(None)):
        """生成并返回对接报告"""
        if not docking_service:
            raise HTTPException(status_code=503, detail="分子对接服务不可用")

        try:
            job_dir = _support.os.path.join(docking_service.work_dir, f"docking_{job_id}")
            result_file = _support.os.path.join(job_dir, "result.pdbqt")
            
            if not _support.os.path.exists(result_file):
                raise HTTPException(status_code=404, detail="对接结果文件不存在")

            results = docking_service.parse_vina_results(result_file)
            
            # 读取配置
            config_file = _support.os.path.join(job_dir, "config.txt")
            config_lines = []
            if _support.os.path.exists(config_file):
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
            viewer_png_b64, smiles_images_b64 = _support._validate_report_base64_payload(
                viewer_png_b64,
                smiles_images_b64,
            )

            # 生成报告内容
            from .report_generator import generate_report
            return generate_report(job_id, results, config_lines, fmt, viewer_png_b64, smiles_images_b64, job_dir)

        except HTTPException:
            raise
        except Exception as e:
            _support.logger.error(f"生成报告失败: {e}")
            raise HTTPException(status_code=500, detail=f"生成报告失败: {str(e)}")
