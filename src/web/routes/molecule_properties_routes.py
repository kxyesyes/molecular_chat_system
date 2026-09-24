"""Molecule properties route registration."""
from typing import Dict, Any
from fastapi import Body, HTTPException


def setup_molecule_properties_routes(app, *, _support):
    """Register the original endpoints with dynamically resolved compatibility support."""
    @app.post("/api/molecule/properties")
    async def calculate_molecule_properties(data: Dict[str, Any] = Body(...)):
        """计算分子的基础属性和ADMET属性"""
        try:
            smiles = data.get('smiles')
            if not smiles:
                raise HTTPException(status_code=400, detail="缺少SMILES参数")
            
            _support.logger.info(f"计算分子属性: {smiles}")
            
            # 直接使用RDKit计算属性，避免工具的SMILES提取逻辑
            try:
                from rdkit import Chem
                from rdkit.Chem import Descriptors, Crippen, Lipinski, QED
                
                # 验证SMILES - 尝试多种解析方式
                mol = Chem.MolFromSmiles(smiles)
                
                # 如果标准解析失败，尝试不做清洗的解析（可能包含部分错误但能读取） -> 再手动清洗
                if mol is None:
                    mol = Chem.MolFromSmiles(smiles, sanitize=False)
                    if mol:
                        try:
                            Chem.SanitizeMol(mol)
                        except Exception:
                            mol = None

                if mol is None:
                    _support.logger.warning(f"RDKit无法解析SMILES: {smiles}")
                    return {
                        "success": False,
                        "error": f"无法识别的分子结构: {smiles}",
                        "properties": None
                    }
                
                # 计算基础属性
                properties = {
                    'basic': {
                        'molecular_weight': round(Descriptors.MolWt(mol), 2),
                        'logp': round(Crippen.MolLogP(mol), 2),
                        'hbd': Lipinski.NumHDonors(mol),
                        'hba': Lipinski.NumHAcceptors(mol),
                        'tpsa': round(Descriptors.TPSA(mol), 2),
                        'rotatable_bonds': Lipinski.NumRotatableBonds(mol),
                        'qed': round(QED.qed(mol), 3)
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
                
                _support.logger.info(f"属性计算完成: {len(properties['basic'])} 个基础属性, {len(properties['admet'])} 个ADMET属性")
                
                return {
                    "success": True,
                    "properties": properties,
                    "smiles": smiles
                }
                
            except Exception as rdkit_error:
                _support.logger.error(f"RDKit计算失败: {rdkit_error}")
                raise
            
        except Exception as e:
            _support.logger.error(f"分子属性计算失败: {e}", exc_info=True)
            return {
                "success": False,
                "error": str(e),
                "properties": None
            }
