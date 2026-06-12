"""
分子设计模块 API 路由
提供: 片段库查询、位点识别、化学取代、属性计算、AI推荐
"""
import os
import logging
from typing import Dict, Any
from fastapi import Body, Response
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from src.molecular_design.fragment_repository import get_fragment_database_path
from src.molecular_design.service import MolecularDesignService

logger = logging.getLogger(__name__)

# ── 路径配置 ──────────────────────────────────────
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
_FRAG_DB_PATH = get_fragment_database_path(_BASE_DIR)
_SAVE_DIR = os.path.join(_BASE_DIR, "data", "design_results")

if not os.path.exists(_SAVE_DIR):
    os.makedirs(_SAVE_DIR, exist_ok=True)

_DESIGN_ROUTE_PATHS = {
    "/api/design/fragments",
    "/api/design/detect_sites",
    "/api/design/substitute",
    "/api/design/properties",
    "/api/design/ai_recommend",
    "/api/design/save_molecule",
    "/api/design/export_history",
}


def _error_response(message: str, status_code: int = 400, **extra):
    payload = {"success": False, "error": str(message)}
    payload.update(extra)
    return JSONResponse(status_code=status_code, content=payload)


def setup_design_routes(app, model=None, config=None):
    """注册分子设计相关API路由"""
    existing_paths = {getattr(route, "path", None) for route in app.routes}
    if existing_paths & _DESIGN_ROUTE_PATHS:
        logger.info("分子设计 API 路由已存在，跳过重复注册")
        return

    design_service = MolecularDesignService(
        fragment_csv_path=_FRAG_DB_PATH,
        save_dir=_SAVE_DIR,
        model=model,
    )

    # ──────────────────────────────────────────────────
    # 1. 片段库查询
    # ──────────────────────────────────────────────────
    @app.get("/api/design/fragments")
    async def get_fragments(
        page: int = 1,
        page_size: int = 20,
        search: str = "",
        q: str = "",
        tags: str = "",
        # 物化性质
        label_lipophilic: int = None,
        label_hydrophilic: int = None,
        label_amphiphilic: int = None,
        label_high_sp3: int = None,
        label_low_tpsa: int = None,
        label_high_tpsa: int = None,
        label_high_mw: int = None,
        label_high_flexibility: int = None,
        # 酸碱性
        label_acidic: int = None,
        label_basic: int = None,
        label_zwitterionic: int = None,
        label_neutral: int = None,
        # 环系统
        label_has_aromatic_ring: int = None,
        label_has_aliphatic_ring: int = None,
        label_has_heterocycle: int = None,
        label_n_heterocycle: int = None,
        label_o_heterocycle: int = None,
        label_s_heterocycle: int = None,
        label_multi_aromatic: int = None,
        label_multi_heterocycle: int = None,
        label_aromatic_ring: int = None,
        # 元素富集
        label_has_halogen: int = None,
        label_high_halogen: int = None,
        label_nitrogen_rich: int = None,
        label_oxygen_rich: int = None,
        label_sulfur_rich: int = None,
        # 氢键
        label_high_hba: int = None,
        label_high_hbd: int = None,
        # 反应性与功能
        label_nucleophilic: int = None,
        label_electrophilic: int = None,
        label_strong_ewg: int = None,
        label_has_amide: int = None,
        label_has_ester: int = None,
        label_bioisostere: int = None,
    ):
        """查询片段库，支持搜索和标签过滤，返回分页结果"""
        try:
            label_params = {
                # 物化性质
                'label_lipophilic': label_lipophilic,
                'label_hydrophilic': label_hydrophilic,
                'label_amphiphilic': label_amphiphilic,
                'label_high_sp3': label_high_sp3,
                'label_low_tpsa': label_low_tpsa,
                'label_high_tpsa': label_high_tpsa,
                'label_high_mw': label_high_mw,
                'label_high_flexibility': label_high_flexibility,
                # 酸碱性
                'label_acidic': label_acidic,
                'label_basic': label_basic,
                'label_zwitterionic': label_zwitterionic,
                'label_neutral': label_neutral,
                # 环系统
                'label_has_aromatic_ring': label_has_aromatic_ring,
                'label_has_aliphatic_ring': label_has_aliphatic_ring,
                'label_has_heterocycle': label_has_heterocycle,
                'label_n_heterocycle': label_n_heterocycle,
                'label_o_heterocycle': label_o_heterocycle,
                'label_s_heterocycle': label_s_heterocycle,
                'label_multi_aromatic': label_multi_aromatic,
                'label_multi_heterocycle': label_multi_heterocycle,
                'label_aromatic_ring': label_aromatic_ring,
                # 元素富集
                'label_has_halogen': label_has_halogen,
                'label_high_halogen': label_high_halogen,
                'label_nitrogen_rich': label_nitrogen_rich,
                'label_oxygen_rich': label_oxygen_rich,
                'label_sulfur_rich': label_sulfur_rich,
                # 氢键
                'label_high_hba': label_high_hba,
                'label_high_hbd': label_high_hbd,
                # 反应性与功能
                'label_nucleophilic': label_nucleophilic,
                'label_electrophilic': label_electrophilic,
                'label_strong_ewg': label_strong_ewg,
                'label_has_amide': label_has_amide,
                'label_has_ester': label_has_ester,
                'label_bioisostere': label_bioisostere,
            }
            return await run_in_threadpool(
                design_service.query_fragments,
                page=page,
                page_size=page_size,
                search=search,
                q=q,
                tags=tags,
                label_filters=label_params,
            )

        except Exception as e:
            logger.error(f"片段库查询失败: {e}", exc_info=True)
            return _error_response(str(e), status_code=500)

    # ──────────────────────────────────────────────────
    # 2. 取代位点识别
    # ──────────────────────────────────────────────────
    @app.post("/api/design/detect_sites")
    async def detect_sites(payload: Dict[str, Any] = Body(...)):
        """
        识别 SMILES 中的取代位点 [*]
        返回位点原子索引及其化学环境信息
        """
        smiles = payload.get("smiles", "").strip()
        if not smiles:
            return _error_response("SMILES不能为空", status_code=400)
        try:
            return await run_in_threadpool(design_service.detect_sites, smiles)

        except ValueError as e:
            return _error_response(str(e), status_code=400, sites=[])
        except Exception as e:
            logger.error(f"位点识别失败: {e}")
            return _error_response(str(e), status_code=500, sites=[])


    @app.post("/api/design/substitute")
    async def substitute_fragment(payload: Dict[str, Any] = Body(...)):
        """
        执行化学取代:
        - parent_smiles: 母体分子 SMILES（含 [*] 取代位点）
        - fragment_smiles: 官能团片段 SMILES（含 [*] 连接点）
        """
        parent_smiles = payload.get("parent_smiles", "").strip()
        fragment_smiles = payload.get("fragment_smiles", "").strip()

        if not parent_smiles or not fragment_smiles:
            return _error_response("parent_smiles 和 fragment_smiles 不能为空", status_code=400)

        try:
            return await run_in_threadpool(design_service.substitute_fragment, parent_smiles, fragment_smiles)

        except ValueError as e:
            return _error_response(str(e), status_code=400)
        except Exception as e:
            logger.error(f"化学取代失败: {e}", exc_info=True)
            return _error_response(str(e), status_code=500)


    @app.post("/api/design/properties")
    async def calc_properties(payload: Dict[str, Any] = Body(...)):
        """计算分子的物化属性（LogP/MW/QED/TPSA/HBD/HBA/RotBonds/SA Score）"""
        smiles = payload.get("smiles", "").strip()
        if not smiles:
            return _error_response("SMILES不能为空", status_code=400)
        try:
            return await run_in_threadpool(design_service.calculate_properties, smiles)

        except ValueError as e:
            return _error_response(str(e), status_code=400)
        except Exception as e:
            logger.error(f"属性计算失败: {e}", exc_info=True)
            return _error_response(str(e), status_code=500)

    # ──────────────────────────────────────────────────
    # 5. AI 智能推荐片段
    # ──────────────────────────────────────────────────
    @app.post("/api/design/ai_recommend")
    async def ai_recommend(payload: Dict[str, Any] = Body(...)):
        """
        调用 LLM 根据用户指令推荐官能团片段，
        同时从片段库中筛选出相关片段返回
        """
        command      = payload.get("command", "").strip()
        current_smi  = payload.get("current_smiles", "")
        current_props = payload.get("current_props", {}) or {}

        if not command:
            return _error_response("指令不能为空", status_code=400)

        try:
            return await design_service.ai_recommend(command, current_smi, current_props)
        except ValueError as e:
            return _error_response(str(e), status_code=400)
        except Exception as e:
            logger.error(f"AI推荐失败: {e}", exc_info=True)
            return _error_response(str(e), status_code=500)

    # ──────────────────────────────────────────────────
    # 6. 保存当前分子
    # ──────────────────────────────────────────────────
    @app.post("/api/design/save_molecule")
    async def save_molecule(payload: Dict[str, Any] = Body(...)):
        """将当前设计的分子及其属性保存到 CSV 文件中"""
        smiles = payload.get("smiles", "").strip()
        props = payload.get("properties", {})
        if not smiles:
            return _error_response("SMILES不能为空", status_code=400)
        
        try:
            return await run_in_threadpool(design_service.save_molecule, smiles, props)
        except ValueError as e:
            return _error_response(str(e), status_code=400)
        except Exception as e:
            logger.error(f"保存分子失败: {e}")
            return _error_response(str(e), status_code=500)

    # ──────────────────────────────────────────────────
    # 7. 导出迭代历史
    # ──────────────────────────────────────────────────
    @app.post("/api/design/export_history")
    async def export_history(payload: Dict[str, Any] = Body(...)):
        """导出当前设计的迭代历史为 CSV 并提供下载"""
        history = payload.get("history", [])
        if not history:
            return _error_response("历史记录为空，无法导出", status_code=400)
            
        try:
            filename, csv_content = await run_in_threadpool(design_service.export_history_csv, history)
            
            return Response(
                content=csv_content,
                media_type="text/csv",
                headers={"Content-Disposition": f"attachment; filename={filename}"}
            )
        except ValueError as e:
            return _error_response(str(e), status_code=400)
        except Exception as e:
            logger.error(f"导出历史失败: {e}")
            return _error_response(str(e), status_code=500)

