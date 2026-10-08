"""Molecule properties route registration."""
import logging
from typing import Dict, Any
from fastapi import Body, HTTPException

from src.molecular_design import chemistry

from .route_compat import lazy_dependency

_LOGGER = logging.getLogger(__name__)


def setup_molecule_properties_routes(app, *, logger=None, _support=None):
    """Register molecule-property endpoints with an explicit logger.

    ``_support`` remains a compatibility-only fallback for older direct callers;
    the application route registration passes ``logger`` explicitly.
    """
    get_logger = lazy_dependency(
        logger,
        _support,
        "logger",
        label="molecule properties logger",
        default=_LOGGER,
    )

    @app.post("/api/molecule/properties")
    async def calculate_molecule_properties(data: Dict[str, Any] = Body(...)):
        """计算分子的基础属性和ADMET属性"""
        try:
            smiles = data.get('smiles')
            if not smiles:
                raise HTTPException(status_code=400, detail="缺少SMILES参数")
            
            get_logger().info(f"计算分子属性: {smiles}")
            
            try:
                result = chemistry.calculate_properties(smiles)
            except ValueError:
                get_logger().warning(f"RDKit无法解析SMILES: {smiles}")
                return {
                    "success": False,
                    "error": f"无法识别的分子结构: {smiles}",
                    "properties": None
                }

            basic_properties = result["properties"]
            raw_properties = result.get("raw_properties", {})
            properties = {
                'basic': {
                    'molecular_weight': round(raw_properties.get("mw", basic_properties["mw"]), 2),
                    'logp': round(raw_properties.get("logp", basic_properties["logp"]), 2),
                    'hbd': basic_properties["hbd"],
                    'hba': basic_properties["hba"],
                    'tpsa': round(raw_properties.get("tpsa", basic_properties["tpsa"]), 2),
                    'rotatable_bonds': basic_properties["rotbonds"],
                    'qed': round(raw_properties.get("qed", basic_properties["qed"]), 3)
                },
                'admet': {}
            }

            # 本接口没有受支持的ADMET计算路径；基础性质不构成这些结论的证据。
            properties['admet'] = dict.fromkeys((
                'bbb_penetration', 'cyp_inhibition', 'hepatotoxicity',
                'solubility', 'bioavailability'), 'Unknown')
            properties['admet_metadata'] = {
                'availability': 'unavailable',
                'method': 'not_calculated',
                'warning': 'ADMET未计算；本接口仅计算基础理化性质，不能据此判断毒性、CNS安全性或体内表现。',
            }

            get_logger().info(f"属性计算完成: {len(properties['basic'])} 个基础属性, {len(properties['admet'])} 个ADMET属性")

            return {
                "success": True,
                "properties": properties,
                "smiles": smiles
            }
            
        except Exception as e:
            get_logger().error(f"分子属性计算失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "properties": None
            }
