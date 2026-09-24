#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ADMET属性预测工具 - 基于adme_py库
移除毒性评估，专注于ADME属性预测
"""

from importlib import metadata as importlib_metadata
from typing import Dict, List, Optional, Any
from urllib.parse import urlsplit
import logging
import math
import json
import re

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
from .molecular_input import MolecularInputUnavailable, parse_molecular_smiles
from src.agent.persistence.redaction import contains_secret_material, sanitize_bounded

logger = logging.getLogger(__name__)
_ADMET_PROSE_PATTERN = re.compile(
    r'(?<![A-Za-z0-9_])(?:BBB|CNS)[ \t]*(?:permeability|penetration|通透性|渗透性)(?![A-Za-z0-9_])',
    re.I,
)

_UNKNOWN = '未知（未计算或无有效结果）'
_NO_ASSESSMENT = '未形成可解释的ADME评估；缺失项目未知。'
_SOLUBILITY_LABELS = {
    'very soluble': '高溶解性', 'highly soluble': '高溶解性',
    'soluble': '中等溶解性', 'moderately soluble': '中等溶解性',
    'poorly soluble': '低溶解性', 'very poorly soluble': '极低溶解性',
    'insoluble': '难溶',
}
_ABSORPTION_LABELS = {'high': '高吸收', 'low': '低吸收', 'medium': '中等吸收', 'moderate': '中等吸收'}
_RULE_LABELS = {'pass': '通过', 'fail': '不通过', 'warning': '警告'}
_COUNTS = {'num_heavy_atoms', 'num_aromatic_atoms', 'num_rotatable_bonds', 'num_h_donors', 'num_h_acceptors'}
# Only producer-supplied diagnostics, not scientific rows or inferred evidence.
_DIAGNOSTICS = ('prediction_method', 'backend_version', 'source', 'provenance',
                'warnings', 'error', 'reason', 'failure_reason')


def _section(props, name):
    value = props.get(name)
    return value if isinstance(value, dict) else {}


def _public_source_url(value):
    """Narrow source exception, not a URL fetch or evidence endorsement.

    Only plain HTTP(S) DNS references: no userinfo, port, query, fragment,
    escapes, local host, or credential-shaped path. Other text stays redacted.
    """
    if (not isinstance(value, str) or len(value) > 2048
            or not re.fullmatch(r'[A-Za-z0-9:/._~-]+', value)):
        return False
    try:
        parsed = urlsplit(value)
        host = parsed.hostname or ''
        if (parsed.scheme not in ('http', 'https') or parsed.netloc.casefold() != host
                or len(host) > 253 or not re.fullmatch(
                    r'(?:[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}', host)
                or host.rsplit('.', 1)[-1] in ('localhost', 'local', 'internal')
                or parsed.query or parsed.fragment):
            return False
    except ValueError:
        return False
    # Colons/repeated slashes belong only to the outer URL scheme here, not
    # embedded drive paths, file URIs or slash-normalized UNC paths.
    if ':' in parsed.path or '//' in parsed.path:
        return False
    parts = parsed.path.split('/')
    return (not any(part in ('.', '..') for part in parts)
            and not contains_secret_material(value)
            and not any(contains_secret_material({part: 'source'}) for part in parts))


def _restore_public_sources(original, sanitized):
    # Traverse only the sanitizer's retained structure: never resurrect a
    # redacted secret container or anything beyond its depth/item limits.
    if isinstance(sanitized, dict) and isinstance(original, dict):
        for key in sanitized:
            supplied = original[key]
            if key == 'source' and _public_source_url(supplied):
                sanitized[key] = supplied
            else:
                _restore_public_sources(supplied, sanitized[key])
    elif isinstance(sanitized, list) and isinstance(original, (list, tuple)):
        for supplied, retained in zip(original, sanitized):
            _restore_public_sources(supplied, retained)


def _diagnostic_metadata(props):
    """Keep supplied failure context, not private payloads or fabricated rows."""
    metadata = {key: props[key] for key in _DIAGNOSTICS if key in props}
    sanitized = sanitize_bounded(metadata)[0]
    _restore_public_sources(metadata, sanitized)
    return sanitized


def _label(value, labels):
    return labels.get(value.strip().lower(), '未知') if isinstance(value, str) else '未知'


def _finite_number(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _valid_number(key, value):
    if key in _COUNTS:
        return type(value) is int and value >= 0
    if not _finite_number(value):
        return False
    if key == 'sp3_carbon_ratio':
        return 0 <= value <= 1
    if key == 'synthetic_accessibility':
        return 1 <= value <= 10
    if key == 'solubility_esol':
        return value >= 0
    return True


def _alert_text(value):
    if value is True:
        return '有（后端报告）'
    if value is False:
        return '无（后端报告；不等于安全）'
    return _UNKNOWN


def _bbb_text(value):
    estimate = '该方法估计可透过' if value is True else '该方法估计不易透过' if value is False else _UNKNOWN
    return estimate + '；不能据此判断CNS活性或副作用风险'


def _reported_rule_details(value):
    if not isinstance(value, dict):
        return []
    return [(key, item) for key, item in value.items()
            if isinstance(item, str) and re.search(r'\b(?:within|outside)\b', item, re.I)]


def _rule_details_text(value):
    if not isinstance(value, dict):
        return _label(value, _RULE_LABELS)
    details = _reported_rule_details(value)
    if not details:
        return _UNKNOWN
    supplied = '; '.join(f'{key}: {item}' for key, item in details)
    return f'已提供部分规则明细（{supplied}）；完整结论未知'


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
        try:
            smiles_list = parse_molecular_smiles(query, self, prose_pattern=_ADMET_PROSE_PATTERN)
        except Exception:
            return False
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
            smiles_list = parse_molecular_smiles(query, self, prose_pattern=_ADMET_PROSE_PATTERN)
        except ValueError as e:
            result['message'] = str(e)
            result['reasoning'] = (
                "无法确认完整结构，不将校验服务异常判断为分子无效。"
                if isinstance(e, MolecularInputUnavailable)
                else "完整 SMILES 输入校验未通过，未使用有效片段替代原始结构。"
            )
            return result
        except Exception:
            result['message'] = "SMILES 校验暂不可用，未执行 ADME 计算。"
            result['reasoning'] = "无法确认完整结构，不将校验服务异常判断为分子无效。"
            return result

        try:
            # 为所有SMILES计算ADMET属性
            calculated_results = []
            formatted_outputs = []

            for smiles in smiles_list:
                admet_props = self.predict_admet_with_adme_py(smiles)
                if admet_props and self._has_observations(admet_props):
                    calculated_results.append({'smiles': smiles, 'admet': admet_props})
                    formatted_outputs.append(self.format_admet_result(smiles, admet_props))
                elif isinstance(admet_props, dict):
                    diagnostic = _diagnostic_metadata(admet_props)
                    if diagnostic:
                        result.setdefault('quality', {}).setdefault('unassessed_admet', []).append(diagnostic)

            if not calculated_results:
                result['message'] = "未获得可用的ADME计算结果；未形成评估。"
                result['reasoning'] = "后端未返回可用观测，不能据此判断结构无效或分子安全。"
                return result

            # 成功 - 准备结果
            result['success'] = True
            result['data'] = calculated_results
            result['formatted'] = "\n\n".join(formatted_outputs)

            # 添加推理用于模型增强
            if len(calculated_results) == 1:
                smiles = calculated_results[0]['smiles']
                admet_props = calculated_results[0]['admet']
                result['reasoning'] = f"已返回 {smiles} 的已计算性质/规则估计；缺失项目未知。{self._generate_brief_reasoning(admet_props)}"
                result['formatted'] += "\n\n" + self._generate_interpretation(smiles, admet_props)
            else:
                result['reasoning'] = f"已返回 {len(calculated_results)} 个分子的已计算性质/规则估计；缺失项目未知。"

            result['message'] = f"已返回 {len(calculated_results)} 个分子的已计算性质/规则估计；缺失项目未知。"

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
            for key, value in _diagnostic_metadata(adme_results).items():
                if key not in ('prediction_method', 'backend_version'):
                    admet_props[key] = value

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
                    "pains": None,
                    "brenk": None,
                    "zinc": None,
                    "synthetic_accessibility": synthetic_accessibility,
                    "synthetic_accessibility_method": "rdkit_complexity_heuristic",
                    "leadlikeness": {},
                },
            }
        except Exception as exc:
            logger.error("RDKit ADME fallback failed for %s: %s", smiles, exc)
            return None

    @staticmethod
    def _has_observations(props):
        """Presence only: never impute a value or certify a complete assessment."""
        if not isinstance(props, dict):
            return False
        phys = _section(props, 'physicochemical')
        formula = phys.get('formula')
        if isinstance(formula, str) and formula.strip().lower() not in ('', 'unknown', '未知', 'n/a'):
            return True
        numbers = {
            'physicochemical': ('molecular_weight', 'molar_refractivity', 'tpsa', 'sp3_carbon_ratio', *_COUNTS),
            'solubility': ('log_s_esol', 'solubility_esol'),
            'lipophilicity': ('wlogp',),
            'pharmacokinetics': ('skin_permeability_logkp',),
            'medicinal': ('synthetic_accessibility',),
        }
        if any(_valid_number(key, _section(props, section).get(key))
               for section, keys in numbers.items() for key in keys):
            return True
        if type(_section(props, 'pharmacokinetics').get('blood_brain_barrier_permeant')) is bool:
            return True
        if any(type(_section(props, 'medicinal').get(key)) is bool for key in ('pains', 'brenk', 'zinc')):
            return True
        for section, key, labels in (
            ('solubility', 'class_esol', _SOLUBILITY_LABELS),
            ('pharmacokinetics', 'gastrointestinal_absorption', _ABSORPTION_LABELS),
            ('druglikeness', 'lipinski', _RULE_LABELS), ('druglikeness', 'veber', _RULE_LABELS),
        ):
            if _label(_section(props, section).get(key), labels) != '未知':
                return True
        return any(_reported_rule_details(_section(props, section).get(key))
                   for section, key in (('druglikeness', 'ghose'), ('medicinal', 'leadlikeness')))

    @staticmethod
    def _method_text(props):
        method = props.get('prediction_method')
        version = props.get('backend_version')
        version = version if isinstance(version, str) and version else '未知'
        if method == 'rdkit_rules':
            return f'RDKit-rule（规则估计，非训练模型预测、非实验结果）；版本：{version}'
        if method == 'adme_py':
            return f'adme_py（后端报告计算，不能据此认定为训练模型或实验结果）；版本：{version}'
        return '方法未知；不能据此认定为模型预测或实验结果'

    @staticmethod
    def _sa_text(props):
        med = _section(props, 'medicinal')
        value = med.get('synthetic_accessibility')
        if not _valid_number('synthetic_accessibility', value):
            return _UNKNOWN
        if props.get('prediction_method') == 'rdkit_rules':
            return f'{value:.2f}（局部结构复杂度启发式，非训练模型、非标准SA评分；不能确认实际合成难度）'
        method = med.get('synthetic_accessibility_method')
        method_text = f'后端提供方法：{method}' if isinstance(method, str) and method.strip() else '具体方法未提供'
        return f'{value:.2f}（后端报告值；{method_text}；不能确认实际合成难度）'

    def format_admet_result(self, smiles: str, admet_props: Dict) -> str:
        """格式化ADME预测结果 - 优化版本"""
        # 获取数据并处理N/A值
        def safe_get(data, key, default='未知', format_type=None):
            value = data.get(key, default)
            if key == 'formula':
                return value if isinstance(value, str) and value.strip().lower() not in ('', 'n/a', 'unknown', '未知') else '未知'
            if not _valid_number(key, value):
                return '未知'
            if format_type == 'float':
                return f"{value:.2f}"
            elif format_type == 'float3' and isinstance(value, (int, float)):
                return f"{value:.3f}"
            return str(value)

        phys = _section(admet_props, 'physicochemical')
        sol = _section(admet_props, 'solubility')
        lipo = _section(admet_props, 'lipophilicity')
        pk = _section(admet_props, 'pharmacokinetics')
        drug = _section(admet_props, 'druglikeness')
        med = _section(admet_props, 'medicinal')

        output = f"""
## 分子ADME属性预测报告

**分子结构：** `{smiles}`

**方法：** {self._method_text(admet_props)}
字段与单位沿用既有输出，本次未验证其科学校准或单位正确性；缺失项目未知。

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
- **脂溶性等级：** {self._classify_lipophilicity_cn(lipo.get('wlogp'))}

### 药代动力学
- **胃肠道吸收：** {self._translate_absorption(pk.get('gastrointestinal_absorption', '未知'))}
- **血脑屏障透过性：** {_bbb_text(pk.get('blood_brain_barrier_permeant'))}
- **皮肤透过性 LogKp：** {safe_get(pk, 'skin_permeability_logkp', format_type='float3')}

### 药物相似性
- **Lipinski规则：** {self._translate_rule_result(drug.get('lipinski', '未知'))}
- **Veber规则：** {self._translate_rule_result(drug.get('veber', '未知'))}
- **Ghose规则：** {self._format_ghose_results_cn(drug.get('ghose', {}))}

### 药物化学评估
- **PAINS警报：** {_alert_text(med.get('pains'))}
- **Brenk警报：** {_alert_text(med.get('brenk'))}
- **ZINC警报：** {_alert_text(med.get('zinc'))}
- **合成可及性报告值：** {self._sa_text(admet_props)}
- **先导化合物相似性：** {self._format_leadlikeness_cn(med.get('leadlikeness', {}))}

---

### 综合评估
{self._generate_comprehensive_assessment(admet_props)}
"""
        return output.strip()

    def _translate_solubility(self, class_esol: str) -> str:
        """Translate whole labels only, never a substring such as 'Soluble'."""
        return _label(class_esol, _SOLUBILITY_LABELS)

    def _classify_lipophilicity_cn(self, wlogp: float) -> str:
        """中文脂溶性分类"""
        if not _finite_number(wlogp):
            return '未知'
        if wlogp < -1:
            return '强亲水性'
        elif wlogp < 1:
            return '亲水性'
        elif wlogp < 3:
            return '中等脂溶性'
        elif wlogp < 5:
            return '高脂溶性'
        return '极高脂溶性'

    def _translate_absorption(self, absorption: str) -> str:
        """翻译吸收等级"""
        return _label(absorption, _ABSORPTION_LABELS)

    def _translate_rule_result(self, result: str) -> str:
        """翻译规则结果"""
        return _label(result, _RULE_LABELS)

    def _format_ghose_results_cn(self, ghose_data: Dict) -> str:
        return _rule_details_text(ghose_data)

    def _format_leadlikeness_cn(self, leadlikeness_data: Dict) -> str:
        return _rule_details_text(leadlikeness_data)

    def _generate_comprehensive_assessment(self, admet_props: Dict) -> str:
        parts = self._assessment_parts(admet_props)
        body = '\n'.join(f'- {part}' for part in parts) if parts else _NO_ASSESSMENT
        return self._method_text(admet_props) + '\n' + body

    def _assessment_parts(self, props):
        """Only describe supplied observations; missing leaves make no claim."""
        parts = []
        sol = self._translate_solubility(_section(props, 'solubility').get('class_esol'))
        if sol != '未知':
            parts.append('该方法报告溶解性：' + sol)
        pk = _section(props, 'pharmacokinetics')
        absorption = self._translate_absorption(pk.get('gastrointestinal_absorption'))
        if absorption != '未知':
            parts.append('该方法报告胃肠道吸收：' + absorption)
        bbb = pk.get('blood_brain_barrier_permeant')
        if type(bbb) is bool:
            parts.append(_bbb_text(bbb))
        for key in ('lipinski', 'veber'):
            rule = self._translate_rule_result(_section(props, 'druglikeness').get(key))
            if rule != '未知':
                parts.append(f'该方法报告{key}规则：{rule}')
        for section, key in (('druglikeness', 'ghose'), ('medicinal', 'leadlikeness')):
            details = _section(props, section).get(key)
            if _reported_rule_details(details):
                parts.append(f'{key}：' + _rule_details_text(details))
        med = _section(props, 'medicinal')
        for key in ('pains', 'brenk', 'zinc'):
            if type(med.get(key)) is bool:
                parts.append(f'{key}警报：' + _alert_text(med[key]))
        if _valid_number('synthetic_accessibility', med.get('synthetic_accessibility')):
            parts.append('合成可及性报告值：' + self._sa_text(props))
        return parts

    def _format_ghose_results(self, ghose_data: Dict) -> str:
        return _rule_details_text(ghose_data)

    def _format_leadlikeness(self, leadlikeness_data: Dict) -> str:
        return _rule_details_text(leadlikeness_data)

    def _generate_interpretation(self, smiles: str, admet_props: Dict) -> str:
        return '📋 ADME评估: ' + self._generate_brief_reasoning(admet_props)

    def _generate_brief_reasoning(self, admet_props: Dict) -> str:
        parts = self._assessment_parts(admet_props)
        body = '，'.join(parts) + '；缺失项目未知。' if parts else _NO_ASSESSMENT
        return self._method_text(admet_props) + '。' + body
