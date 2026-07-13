#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ADMET属性预测工具 - 基于adme_py库
移除毒性评估，专注于ADME属性预测
"""

from importlib import metadata as importlib_metadata
from typing import Dict, List, Optional, Any
import logging
import json

ADME = None
ADME_PY_VERSION = None
try:
    import adme_py as _adme_py_module
    from adme_py import ADME

    ADME_PY_AVAILABLE = True
    for _distribution_name in ("adme-py", "adme_py"):
        try:
            ADME_PY_VERSION = importlib_metadata.version(_distribution_name)
            break
        except importlib_metadata.PackageNotFoundError:
            continue
    if ADME_PY_VERSION is None:
        ADME_PY_VERSION = str(
            getattr(_adme_py_module, "__version__", "unknown")
        )
except ImportError:
    ADME_PY_AVAILABLE = False

try:
    from rdkit import Chem, rdBase
    from rdkit.Chem import Crippen, Descriptors, Lipinski, rdMolDescriptors
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)


class ADMETPredictor(BaseMolecularTool):
    """ADMET属性预测工具 - 基于adme_py库"""

    def __init__(self):
        super().__init__(
            name="admet_predictor",
            description="Predict ADME properties from SMILES using adme_py library"
        )

        # 触发关键词 (移除毒性相关)
        self.trigger_words = [
            'admet', 'adme', 'absorption', 'distribution', 'metabolism', 'excretion',
            'pharmacokinetic', 'bioavailability', 'clearance', 'half-life', 'permeability',
            'predict', 'estimate', '预测', '估计',
            '吸收', '分布', '代谢', '排泄', '药代动力学', '生物利用度',
            'lipophilicity', 'solubility', 'druglikeness', '脂溶性', '溶解性', '药物相似性'
        ]

    def should_use(self, query: str) -> bool:
        """判断是否应该使用此工具"""
        query_lower = query.lower()

        # 检查触发词
        has_trigger = any(word in query_lower for word in self.trigger_words)

        # 提取并验证SMILES
        smiles_list = self.extract_smiles(query)
        has_valid_smiles = len(smiles_list) > 0

        result = has_trigger and has_valid_smiles

        if result:
            logger.info(f"ADMETPredictor triggered: found {len(smiles_list)} valid SMILES")

        return result

    def execute(self, query: str) -> Dict[str, Any]:
        """执行ADMET预测"""
        result = self._create_base_result(query)

        if not self._check_adme_backend(result):
            return result

        try:
            # 提取SMILES
            smiles_list = self.extract_smiles(query)

            if not smiles_list:
                result['message'] = "未在查询中找到有效的SMILES分子结构。"
                result['reasoning'] = "我在输入中搜索了SMILES模式，但无法识别任何有效的分子结构。"
                return result

            # 为所有SMILES计算ADMET属性
            calculated_results = []
            formatted_outputs = []

            for smiles in smiles_list:
                admet_props = self.predict_admet_with_adme_py(smiles)
                if admet_props:
                    calculated_results.append({'smiles': smiles, 'admet': admet_props})
                    formatted_outputs.append(self.format_admet_result(smiles, admet_props))

            if not calculated_results:
                result['message'] = "无法预测ADMET属性。请检查SMILES结构。"
                result['reasoning'] = "提供的SMILES结构似乎无效或无法被adme_py处理。"
                return result

            # 成功 - 准备结果
            result['success'] = True
            result['data'] = calculated_results
            result['formatted'] = "\n\n".join(formatted_outputs)

            # 添加推理用于模型增强
            if len(calculated_results) == 1:
                smiles = calculated_results[0]['smiles']
                admet_props = calculated_results[0]['admet']
                result['reasoning'] = f"我成功预测了 {smiles} 的ADME属性。{self._generate_brief_reasoning(admet_props)}"
                result['formatted'] += "\n\n" + self._generate_interpretation(smiles, admet_props)
            else:
                result['reasoning'] = f"我预测了 {len(calculated_results)} 个分子结构的ADME属性，为每个提供了综合分析。"

            result['message'] = f"成功预测了 {len(calculated_results)} 个分子的ADME属性"

        except Exception as e:
            logger.error(f"ADME预测失败: {e}")
            result['message'] = f"预测失败: {str(e)}"
            result['reasoning'] = "在预测过程中发生了意外错误。"

        return result

    def _check_adme_backend(self, result: Dict[str, Any]) -> bool:
        """Check whether either the preferred or fallback ADME backend is usable."""
        if not ADME_PY_AVAILABLE and not RDKIT_AVAILABLE:
            result['message'] = "ADME prediction requires adme_py or RDKit."
            result['reasoning'] = "No supported ADME calculation backend is available."
            return False
        return True

    def predict_admet_with_adme_py(self, smiles: str) -> Optional[Dict[str, Any]]:
        """Predict ADME properties with adme_py, falling back to RDKit rules."""
        if not ADME_PY_AVAILABLE:
            return self._predict_admet_with_rdkit(smiles)

        try:
            # 使用adme_py进行预测
            adme = ADME(smiles)
            adme_results = adme.calculate()

            # 转换为我们的格式
            admet_props = {
                # 理化性质
                'prediction_method': 'adme_py',
                'backend_version': str(ADME_PY_VERSION or 'unknown'),
                'physicochemical': adme_results.get('physiochemical', {}),

                # 溶解性
                'solubility': adme_results.get('solubility', {}),

                # 脂溶性
                'lipophilicity': adme_results.get('lipophilicity', {}),

                # 药代动力学
                'pharmacokinetics': adme_results.get('pharmacokinetics', {}),

                # 药物相似性
                'druglikeness': adme_results.get('druglikeness', {}),

                # 药物化学属性
                'medicinal': adme_results.get('medicinal', {})
            }

            return admet_props

        except Exception as e:
            logger.error(f"预测 {smiles} 的ADME属性失败: {e}")
            return None

    def _predict_admet_with_rdkit(self, smiles: str) -> Optional[Dict[str, Any]]:
        """Provide deterministic, clearly labelled ADME estimates using RDKit."""
        if not RDKIT_AVAILABLE:
            return None

        try:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return None

            molecular_weight = float(Descriptors.MolWt(mol))
            logp = float(Crippen.MolLogP(mol))
            tpsa = float(Descriptors.TPSA(mol))
            rotatable_bonds = int(Lipinski.NumRotatableBonds(mol))
            h_donors = int(Lipinski.NumHDonors(mol))
            h_acceptors = int(Lipinski.NumHAcceptors(mol))
            heavy_atoms = int(mol.GetNumHeavyAtoms())
            aromatic_atoms = sum(1 for atom in mol.GetAtoms() if atom.GetIsAromatic())
            aromatic_proportion = aromatic_atoms / heavy_atoms if heavy_atoms else 0.0

            log_s = (
                0.16
                - (1.5 * logp)
                - (0.01 * (molecular_weight - 40.0))
                + (0.066 * rotatable_bonds)
                + (0.066 * aromatic_proportion)
            )
            solubility_mg_ml = max(0.0, (10 ** log_s) * molecular_weight)
            if log_s >= -1:
                solubility_class = "Very Soluble"
            elif log_s >= -2:
                solubility_class = "Soluble"
            elif log_s >= -3:
                solubility_class = "Moderately Soluble"
            elif log_s >= -4:
                solubility_class = "Poorly Soluble"
            else:
                solubility_class = "Insoluble"

            lipinski_violations = sum(
                (
                    molecular_weight > 500,
                    logp > 5,
                    h_donors > 5,
                    h_acceptors > 10,
                )
            )
            lipinski_result = "Pass" if lipinski_violations <= 1 else "Fail"
            veber_result = "Pass" if rotatable_bonds <= 10 and tpsa <= 140 else "Fail"
            gi_absorption = "High" if molecular_weight <= 500 and tpsa <= 140 else "Low"
            bbb_permeant = 0.0 <= logp <= 5.0 and tpsa < 90.0
            skin_logkp = -2.72 + (0.71 * logp) - (0.0061 * molecular_weight)
            synthetic_accessibility = min(
                10.0,
                max(
                    1.0,
                    1.0
                    + (heavy_atoms / 25.0)
                    + (rotatable_bonds / 5.0)
                    + (mol.GetRingInfo().NumRings() / 4.0),
                ),
            )

            return {
                "prediction_method": "rdkit_rules",
                "backend_version": str(rdBase.rdkitVersion),
                "physicochemical": {
                    "formula": rdMolDescriptors.CalcMolFormula(mol),
                    "molecular_weight": molecular_weight,
                    "num_heavy_atoms": heavy_atoms,
                    "num_aromatic_atoms": aromatic_atoms,
                    "sp3_carbon_ratio": float(rdMolDescriptors.CalcFractionCSP3(mol)),
                    "num_rotatable_bonds": rotatable_bonds,
                    "num_h_donors": h_donors,
                    "num_h_acceptors": h_acceptors,
                    "molar_refractivity": float(Crippen.MolMR(mol)),
                    "tpsa": tpsa,
                },
                "solubility": {
                    "log_s_esol": log_s,
                    "solubility_esol": solubility_mg_ml,
                    "class_esol": solubility_class,
                },
                "lipophilicity": {"wlogp": logp},
                "pharmacokinetics": {
                    "gastrointestinal_absorption": gi_absorption,
                    "blood_brain_barrier_permeant": bbb_permeant,
                    "skin_permeability_logkp": skin_logkp,
                },
                "druglikeness": {
                    "lipinski": lipinski_result,
                    "veber": veber_result,
                    "ghose": {},
                },
                "medicinal": {
                    "pains": False,
                    "brenk": False,
                    "zinc": False,
                    "synthetic_accessibility": synthetic_accessibility,
                    "leadlikeness": {},
                },
            }
        except Exception as exc:
            logger.error("RDKit ADME fallback failed for %s: %s", smiles, exc)
            return None

    def format_admet_result(self, smiles: str, admet_props: Dict) -> str:
        """格式化ADME预测结果 - 优化版本"""
        # 获取数据并处理N/A值
        def safe_get(data, key, default='未知', format_type=None):
            value = data.get(key, default)
            if value == 'N/A' or value is None:
                return '未知'
            if format_type == 'float' and isinstance(value, (int, float)):
                return f"{value:.2f}"
            elif format_type == 'float3' and isinstance(value, (int, float)):
                return f"{value:.3f}"
            return str(value)

        phys = admet_props.get('physicochemical', {})
        sol = admet_props.get('solubility', {})
        lipo = admet_props.get('lipophilicity', {})
        pk = admet_props.get('pharmacokinetics', {})
        drug = admet_props.get('druglikeness', {})
        med = admet_props.get('medicinal', {})

        output = f"""
## 分子ADME属性预测报告

**分子结构：** `{smiles}`

---

### 理化性质
- **分子式：** {safe_get(phys, 'formula')}
- **分子量：** {safe_get(phys, 'molecular_weight', format_type='float')} Da
- **重原子数：** {safe_get(phys, 'num_heavy_atoms')}
- **芳香原子数：** {safe_get(phys, 'num_aromatic_atoms')}
- **SP3碳比例：** {safe_get(phys, 'sp3_carbon_ratio', format_type='float3')}
- **可旋转键数：** {safe_get(phys, 'num_rotatable_bonds')}
- **氢键供体数：** {safe_get(phys, 'num_h_donors')}
- **氢键受体数：** {safe_get(phys, 'num_h_acceptors')}
- **摩尔折射率：** {safe_get(phys, 'molar_refractivity', format_type='float')}
- **极性表面积：** {safe_get(phys, 'tpsa', format_type='float')} Ų

### 溶解性
- **LogS值：** {safe_get(sol, 'log_s_esol', format_type='float3')}
- **溶解度：** {safe_get(sol, 'solubility_esol', format_type='float3')} mg/mL
- **溶解性等级：** {self._translate_solubility(sol.get('class_esol', '未知'))}

### 脂溶性
- **WLogP值：** {safe_get(lipo, 'wlogp', format_type='float3')}
- **脂溶性等级：** {self._classify_lipophilicity_cn(lipo.get('wlogp', 0))}

### 药代动力学
- **胃肠道吸收：** {self._translate_absorption(pk.get('gastrointestinal_absorption', '未知'))}
- **血脑屏障透过性：** {'能透过' if pk.get('blood_brain_barrier_permeant', False) else '不易透过'}
- **皮肤透过性 LogKp：** {safe_get(pk, 'skin_permeability_logkp', format_type='float3')}

### 药物相似性
- **Lipinski规则：** {self._translate_rule_result(drug.get('lipinski', '未知'))}
- **Veber规则：** {self._translate_rule_result(drug.get('veber', '未知'))}
- **Ghose规则：** {self._format_ghose_results_cn(drug.get('ghose', {}))}

### 药物化学评估
- **PAINS警报：** {'有' if med.get('pains', False) else '无'}
- **Brenk警报：** {'有' if med.get('brenk', False) else '无'}
- **ZINC警报：** {'有' if med.get('zinc', False) else '无'}
- **合成可及性评分：** {safe_get(med, 'synthetic_accessibility', format_type='float')} (1-10，越低越容易合成)
- **先导化合物相似性：** {self._format_leadlikeness_cn(med.get('leadlikeness', {}))}

---

### 综合评估
{self._generate_comprehensive_assessment(admet_props)}
"""
        return output.strip()

    def _translate_solubility(self, class_esol: str) -> str:
        """翻译溶解性等级"""
        translation = {
            'Very Soluble': '高溶解性',
            'Highly Soluble': '高溶解性',
            'Soluble': '中等溶解性',
            'Moderately Soluble': '中等溶解性',
            'Poorly Soluble': '低溶解性',
            'Insoluble': '难溶',
            'Very Poorly Soluble': '极难溶'
        }
        return translation.get(class_esol, class_esol)

    def _classify_lipophilicity_cn(self, wlogp: float) -> str:
        """中文脂溶性分类"""
        if wlogp is None:
            return '未知'
        try:
            wlogp = float(wlogp)
            if wlogp < -1:
                return '强亲水性'
            elif wlogp < 1:
                return '亲水性'
            elif wlogp < 3:
                return '中等脂溶性'
            elif wlogp < 5:
                return '高脂溶性'
            else:
                return '极高脂溶性'
        except:
            return '未知'

    def _translate_absorption(self, absorption: str) -> str:
        """翻译吸收等级"""
        translation = {
            'High': '高吸收',
            'Low': '低吸收',
            'Medium': '中等吸收',
            'Moderate': '中等吸收'
        }
        return translation.get(absorption, absorption)

    def _translate_rule_result(self, result: str) -> str:
        """翻译规则结果"""
        translation = {
            'Pass': '通过',
            'Fail': '不通过',
            'Warning': '警告'
        }
        return translation.get(result, result)

    def _format_ghose_results_cn(self, ghose_data: Dict) -> str:
        """格式化Ghose规则结果 - 中文版"""
        if not ghose_data:
            return "通过"

        issues = []
        for key, value in ghose_data.items():
            if "outside" in str(value):
                if "MW" in key:
                    issues.append("分子量")
                elif "MR" in key:
                    issues.append("摩尔折射率")
                elif "atoms" in key:
                    issues.append("原子数")
                else:
                    issues.append(key)

        if not issues:
            return "通过"
        else:
            return f"不通过 ({', '.join(issues)}超出范围)"

    def _format_leadlikeness_cn(self, leadlikeness_data: Dict) -> str:
        """格式化先导化合物相似性结果 - 中文版"""
        if not leadlikeness_data:
            return "符合"

        issues = []
        for key, value in leadlikeness_data.items():
            if "outside" in str(value):
                if "MW" in key:
                    issues.append("分子量")
                else:
                    issues.append(key)

        if not issues:
            return "符合"
        else:
            return f"不符合 ({', '.join(issues)}超出范围)"

    def _generate_comprehensive_assessment(self, admet_props: Dict) -> str:
        """生成综合评估"""
        assessments = []

        # 溶解性评估
        sol_class = admet_props.get('solubility', {}).get('class_esol', '')
        if 'Very Soluble' in sol_class or 'Highly Soluble' in sol_class:
            assessments.append("✓ 具有优秀的水溶性")
        elif 'Soluble' in sol_class:
            assessments.append("○ 具有良好的水溶性")
        elif 'Poorly' in sol_class:
            assessments.append("△ 水溶性较差，可能影响口服吸收")

        # 吸收评估
        gi_absorption = admet_props.get('pharmacokinetics', {}).get('gastrointestinal_absorption', '')
        if gi_absorption == 'High':
            assessments.append("✓ 预测具有良好的胃肠道吸收")
        elif gi_absorption == 'Low':
            assessments.append("△ 胃肠道吸收可能较差")

        # 血脑屏障评估
        bbb = admet_props.get('pharmacokinetics', {}).get('blood_brain_barrier_permeant', False)
        if bbb:
            assessments.append("! 可能透过血脑屏障，需关注CNS副作用")
        else:
            assessments.append("✓ 不易透过血脑屏障，CNS副作用风险较低")

        # 药物相似性评估
        lipinski = admet_props.get('druglikeness', {}).get('lipinski', '')
        if lipinski == 'Pass':
            assessments.append("✓ 符合Lipinski五规则")
        else:
            assessments.append("△ 不完全符合Lipinski五规则")

        # 合成可及性评估
        sa_score = admet_props.get('medicinal', {}).get('synthetic_accessibility', None)
        if sa_score is not None:
            try:
                sa_score = float(sa_score)
                if sa_score <= 3:
                    assessments.append("✓ 合成难度较低")
                elif sa_score <= 6:
                    assessments.append("○ 合成难度中等")
                else:
                    assessments.append("△ 合成难度较高")
            except:
                pass

        if not assessments:
            assessments.append("已完成基本ADME属性分析")

        return "\n".join([f"- {assessment}" for assessment in assessments])

    def _format_ghose_results(self, ghose_data: Dict) -> str:
        """格式化Ghose规则结果"""
        if not ghose_data:
            return "通过"

        issues = []
        for key, value in ghose_data.items():
            if "outside" in str(value):
                issues.append(key)

        if not issues:
            return "通过"
        else:
            return f"失败 ({', '.join(issues)})"

    def _format_leadlikeness(self, leadlikeness_data: Dict) -> str:
        """格式化Lead-likeness结果"""
        if not leadlikeness_data:
            return "通过"

        issues = []
        for key, value in leadlikeness_data.items():
            if "outside" in str(value):
                issues.append(key)

        if not issues:
            return "通过"
        else:
            return f"失败 ({', '.join(issues)})"

    def _generate_interpretation(self, smiles: str, admet_props: Dict) -> str:
        """生成ADME解释"""
        interpretations = []

        # 胃肠道吸收评估
        gi_absorption = admet_props['pharmacokinetics'].get('gastrointestinal_absorption', '').lower()
        if gi_absorption == 'high':
            interpretations.append("该分子具有优秀的胃肠道吸收潜力")
        elif gi_absorption == 'low':
            interpretations.append("该分子的胃肠道吸收可能较差")

        # 血脑屏障评估
        bbb = admet_props['pharmacokinetics'].get('blood_brain_barrier_permeant', False)
        if bbb:
            interpretations.append("可能具有CNS活性")
        else:
            interpretations.append("不太可能产生CNS副作用")

        # 药物相似性评估
        lipinski = admet_props['druglikeness'].get('lipinski', '').lower()
        if lipinski == 'pass':
            interpretations.append("符合Lipinski五规则")
        else:
            interpretations.append("不完全符合Lipinski五规则")

        # 溶解性评估
        solubility_class = admet_props['solubility'].get('class_esol', '').lower()
        if 'very soluble' in solubility_class or 'highly soluble' in solubility_class:
            interpretations.append("具有优秀的水溶性")
        elif 'soluble' in solubility_class:
            interpretations.append("具有良好的水溶性")
        elif 'poorly' in solubility_class or 'insoluble' in solubility_class:
            interpretations.append("水溶性较差")

        return "📋 ADME评估: " + "，".join(interpretations) + "。"

    def _generate_brief_reasoning(self, admet_props: Dict) -> str:
        """生成简要推理"""
        reasoning_parts = []

        # 胃肠道吸收
        gi_absorption = admet_props['pharmacokinetics'].get('gastrointestinal_absorption', '').lower()
        if gi_absorption == 'high':
            reasoning_parts.append("预测显示优秀的胃肠道吸收")
        elif gi_absorption == 'low':
            reasoning_parts.append("预测显示较低的胃肠道吸收")

        # 药物相似性
        lipinski = admet_props['druglikeness'].get('lipinski', '').lower()
        if lipinski == 'pass':
            reasoning_parts.append("符合药物相似性规则")
        else:
            reasoning_parts.append("需要关注药物相似性问题")

        return "，".join(reasoning_parts) + "。"
