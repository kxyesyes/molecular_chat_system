"""Molecule utility HTTP adapters."""
import logging
from typing import Dict, Any

from fastapi import Body, Query, HTTPException, Response

from src.molecular_design import visualization

from .route_compat import lazy_dependency


_LOGGER = logging.getLogger(__name__)


def _smiles_to_3d_sync(smiles: str) -> dict[str, Any]:
    """Run the scientific 3D operation outside the event loop."""
    try:
        return visualization.smiles_to_3d(smiles)
    except visualization.MoleculeVisualizationError as error:
        status_code = 500 if error.code == "pdb_conversion_failed" else 400
        raise HTTPException(status_code=status_code, detail=str(error)) from None


def _smiles_to_image_sync(smiles: str, width: int, height: int) -> bytes:
    """Render a scientific molecule image in a worker thread."""
    try:
        return visualization.smiles_to_image(smiles, width, height)
    except visualization.MoleculeVisualizationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


def _mcs_sync(
    smiles1: str,
    smiles2: str,
    width: int,
    height: int,
    timeout: int,
) -> dict[str, Any]:
    """Calculate and render MCS in a worker thread."""
    try:
        return visualization.mcs(smiles1, smiles2, width, height, timeout)
    except visualization.MoleculeVisualizationError as error:
        raise HTTPException(status_code=400, detail=str(error)) from None


def _register_molecule_utility_routes(
    app,
    *,
    get_invoker,
    get_logger,
):
    @app.post("/api/docking/smiles_to_3d")
    async def smiles_to_3d(payload: Dict[str, Any] = Body(...)):
        """将SMILES转换为3D结构用于预览"""
        raw_smiles = payload.get("smiles", "")
        if not isinstance(raw_smiles, str):
            raise HTTPException(status_code=400, detail="SMILES字符串必须是字符串")
        smiles = raw_smiles.strip()
        if not smiles:
            raise HTTPException(status_code=400, detail="SMILES字符串不能为空")
        try:
            return await get_invoker()(_smiles_to_3d_sync, smiles)
        except HTTPException:
            raise
        except Exception:
            get_logger().exception("SMILES转3D失败")
            raise HTTPException(status_code=500, detail="3D结构生成失败，请稍后重试") from None

    @app.get("/api/utils/smiles_to_image")
    async def smiles_to_image(
        smiles: str,
        width: int = Query(300, ge=64, le=2048),
        height: int = Query(200, ge=64, le=2048),
    ):
        """生成分子2D图片"""
        try:
            content = await get_invoker()(
                _smiles_to_image_sync,
                smiles,
                width,
                height,
            )
            return Response(content=content, media_type="image/png")
        except HTTPException as error:
            # Preserve the legacy route contract: RDKit/input failures from
            # this image endpoint were exposed as HTTP 500 responses.
            legacy_detail = f"{error.status_code}: {error.detail}"
            get_logger().error(f"生成分子图片失败: {legacy_detail}")
            raise HTTPException(status_code=500, detail=legacy_detail)
        except Exception:
            get_logger().exception("生成分子图片失败")
            raise HTTPException(status_code=500, detail="分子图片生成失败，请稍后重试") from None

    @app.get("/api/utils/mcs")
    async def get_mcs(
        smiles1: str,
        smiles2: str,
        width: int = Query(360, ge=64, le=2048),
        height: int = Query(260, ge=64, le=2048),
        timeout: int = Query(3, ge=1, le=30),
    ):
        """计算两分子的最大公共子结构(MCS)，返回SMARTS与高亮SVG"""
        if not smiles1 or not smiles2:
            raise HTTPException(status_code=400, detail="SMILES cannot be empty")
        try:
            return await get_invoker()(
                _mcs_sync,
                smiles1,
                smiles2,
                width,
                height,
                timeout,
            )
        except HTTPException:
            raise
        except Exception:
            get_logger().exception("MCS计算失败")
            raise HTTPException(status_code=500, detail="MCS计算失败，请稍后重试") from None


def register_molecule_utility_routes(
    app,
    *,
    invoke_in_threadpool,
    logger=None,
):
    """Register production molecule utility routes with narrow providers."""
    return _register_molecule_utility_routes(
        app,
        get_invoker=lambda: invoke_in_threadpool,
        get_logger=lambda: logger or _LOGGER,
    )


def setup_molecule_utility_routes(
    app,
    *,
    invoke_in_threadpool=None,
    logger=None,
    _support=None,
):
    """Compatibility registration entry point for older direct callers."""
    return _register_molecule_utility_routes(
        app,
        get_invoker=lazy_dependency(
            invoke_in_threadpool,
            _support,
            "_invoke_in_threadpool",
            label="molecule utility threadpool",
        ),
        get_logger=lazy_dependency(
            logger,
            _support,
            "logger",
            label="molecule utility logger",
            default=_LOGGER,
        ),
    )
