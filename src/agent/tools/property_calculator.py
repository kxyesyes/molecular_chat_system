#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优化的分子属性计算工具
"""

from typing import Dict, List, Optional, Any
import logging
import math

try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Crippen, Lipinski, QED
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

from .base_tool import BaseMolecularTool
from .molecular_input import parse_molecular_smiles

logger = logging.getLogger(__name__)

_EVIDENCE_BOUNDARY = (
    "RDKit 计算描述符及启发式筛选仅供研究参考，不构成实验验证证据；"
    "不能据此判断口服潜力、生物利用度、膜透过性、选择性或疗效。"
)


class PropertyCalculator(BaseMolecularTool):
    """分子属性计算工具"""

    def __init__(self):
        super().__init__(
            name="property_calculator",
            description="Calculate molecular properties from SMILES"
        )

        # 触发关键词 - 覆盖"分析分子"所有常见表达
        self.trigger_words = [
            'calculate', 'compute', 'predict', 'estimate', 'analyze', 'evaluate',
            'property', 'properties', 'logp', 'qed', 'tpsa', 'lipinski',
            '计算', '预测', '估计', '分析', '评估', '查看',
            '属性', '性质', '性能', '分子量', '亲脂', '极性',
            '类药', '规则', '药物性质', '物化', '理化', '告诉我', '该分子',
        ]

    def should_use(self, query: str) -> bool:
        """判断是否应该使用此工具"""
        query_lower = query.lower()

        # 先提取 SMILES，有效 SMILES 是使用本工具的必要条件
        try:
            smiles_list = parse_molecular_smiles(query, self)
        except ValueError:
            return False
        has_valid_smiles = len(smiles_list) > 0
        if not has_valid_smiles:
            return False

        # 有 SMILES + 触发词，直接触发
        if any(word in query_lower for word in self.trigger_words):
            logger.info(f"PropertyCalculator triggered: {len(smiles_list)} SMILES found")
            return True

        # 有 SMILES 但无显式触发词，检查是否是"分析该分子"类型句式
        analysis_phrases = ['分析', '这个分子', '此分子', '这种分子',
                            'analyze', 'show', 'what is', 'tell me']
        if any(p in query_lower for p in analysis_phrases):
            logger.info("PropertyCalculator triggered by analysis phrase with SMILES")
            return True

        return False

    def execute(self, query: str) -> Dict[str, Any]:
        """执行属性计算"""
        result = self._create_base_result(query)

        if not self._check_rdkit(result):
            return result

        try:
            # 提取SMILES
            smiles_list = parse_molecular_smiles(query, self)

            # 计算所有SMILES的属性
            calculated_results = []
            formatted_outputs = []

            for smiles in smiles_list:
                props = self.calculate_properties(smiles)
                if props:
                    calculated_results.append({'smiles': smiles, 'properties': props})
                    formatted_outputs.append(self.format_properties(smiles, props))

            if not calculated_results:
                result['message'] = "无法计算分子属性。请检查SMILES结构是否正确。"
                result['reasoning'] = "提供的SMILES结构似乎无效或无法被RDKit处理。"
                return result

            # 成功 - 准备结果
            result['success'] = True
            result['data'] = calculated_results
            result['formatted'] = "\n\n".join(formatted_outputs)

            # 添加推理和解释
            if len(calculated_results) == 1:
                smiles = calculated_results[0]['smiles']
                props = calculated_results[0]['properties']
                result['reasoning'] = f"我成功计算了 {smiles} 的分子属性。{self._generate_brief_reasoning(props)}"
            else:
                result['reasoning'] = (
                    f"已计算 {len(calculated_results)} 个分子的 RDKit 描述符，"
                    f"并分别列出启发式筛选规则检查。{_EVIDENCE_BOUNDARY}"
                )

            result['message'] = f"成功计算了 {len(calculated_results)} 个分子的属性"

        except ValueError as e:
            result['message'] = str(e)
            result['reasoning'] = '输入校验失败，未进行分子性质计算。'
        except Exception as e:
            logger.error(f"Property calculation failed: {e}")
            result['message'] = f"计算失败: {str(e)}"
            result['reasoning'] = "在计算过程中发生了意外错误。"

        return result

    def calculate_properties(self, smiles: str) -> Optional[Dict[str, Any]]:
        """Calculate molecular properties using RDKit"""
        try:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return None

            qed_val = round(QED.qed(mol), 3)
            logp_val = round(Crippen.MolLogP(mol), 3)

            properties = {
                'molecular_formula': Chem.rdMolDescriptors.CalcMolFormula(mol),
                'molecular_weight': round(Descriptors.MolWt(mol), 2),
                'logp': logp_val,                          # ← 新增：脂水分配系数
                'hba': Lipinski.NumHAcceptors(mol),
                'hbd': Lipinski.NumHDonors(mol),
                'tpsa': round(Descriptors.TPSA(mol), 2),
                'rotatable_bonds': Lipinski.NumRotatableBonds(mol),
                'qed': qed_val,                            # 范围严格 [0, 1]
            }

            # ── 完整性校验（防止 RDKit 异常导致幻觉）──────────
            if not (0.0 <= properties['qed'] <= 1.0):
                logger.error(f"QED={properties['qed']} out of [0,1] for {smiles}, clamping")
                properties['qed'] = round(min(max(properties['qed'], 0.0), 1.0), 3)

            return properties

        except Exception as e:
            logger.error(f"Failed to calculate properties for {smiles}: {e}")
            return None

    def format_properties(self, smiles: str, props: Dict) -> str:
        """分别展示计算描述符和启发式规则，不推断实验性质。"""
        la = self._assess_lipinski_rules(props)

        def value(key, precision=None):
            number = self._descriptor_value(props, key)
            if number is None:
                return "缺失或不可用"
            return f"{number:.{precision}f}" if precision is not None else str(number)

        output = f"""\
## 🧬 分子属性分析报告

**结构：** `{smiles}`  
**分子式：** {props.get('molecular_formula') or '缺失或不可用'}

---

### 📊 RDKit 计算描述符
以下数值由输入结构计算，并非实测值。

| 参数 | 数值 |
|---|---|
| 分子量 (MW) | {value('molecular_weight', 2)} Da |
| 脂水分配系数 (LogP) | {value('logp')} |
| 拓扑极性表面积 (TPSA) | {value('tpsa', 2)} Å² |
| 氢键受体 (HBA) | {value('hba')} |
| 氢键供体 (HBD) | {value('hbd')} |
| 可旋转键数 | {value('rotatable_bonds')} |
| QED 类药性描述符 (0 ~ 1) | {value('qed')} |

### 💊 启发式筛选
{la['detailed_assessment']}

---

### 🔬 解读边界与后续验证
{self._generate_drug_chemistry_suggestions(props)}\
"""
        return output.strip()

    @staticmethod
    def _descriptor_value(props: Dict, key: str):
        """Missing/non-numeric values cannot support a rule assessment."""
        value = props.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return None
        return value if math.isfinite(value) else None

    def _assess_lipinski_rules(self, props: Dict) -> Dict:
        """共享四项 Ro5 与独立的两项 Veber 检查；保留历史返回键。"""
        lines = []
        summaries = []
        lipinski_violations = 0
        groups = (
            ("Lipinski Ro5", (
                ('molecular_weight', 'MW (Da)', 500),
                ('logp', 'LogP', 5),
                ('hba', 'HBA', 10),
                ('hbd', 'HBD', 5),
            )),
            ("Veber", (
                ('tpsa', 'TPSA (Å²)', 140),
                ('rotatable_bonds', '可旋转键数', 10),
            )),
        )
        for name, rules in groups:
            lines.append(f"**{name}（{len(rules)} 项启发式筛选）**")
            lines.append("")
            assessed = violations = 0
            for key, label, limit in rules:
                value = self._descriptor_value(props, key)
                if value is None:
                    lines.append(f"- ⚪ {label}：缺失或不可用，未评估（阈值 ≤ {limit}）")
                    continue
                assessed += 1
                if value > limit:
                    violations += 1
                    lines.append(f"- ⚠️ {label} = {value} > {limit} — 超出筛选阈值")
                else:
                    lines.append(f"- ✅ {label} = {value} ≤ {limit} — 未超出筛选阈值")
            summary = f"{name}：已评估 {assessed}/{len(rules)} 项，违反 {violations} 项"
            if assessed < len(rules):
                summary += "（输入不完整，违反数仅计已评估项，不能判定全部符合）"
            summaries.append(summary)
            lines.append("")
            if name == "Lipinski Ro5":
                lipinski_violations = violations

        return {
            'detailed_assessment': '\n'.join(lines).strip(),
            'overall_assessment': '；'.join(summaries) + '。' + _EVIDENCE_BOUNDARY,
            # Legacy key counts only the four Lipinski criteria, not Veber.
            'violations_count': lipinski_violations
        }

    def _generate_drug_chemistry_suggestions(self, props: Dict) -> str:
        """保留入口，仅提供描述符解读和独立验证建议。"""
        assessment = self._assess_lipinski_rules(props)
        qed = self._descriptor_value(props, 'qed')
        qed_text = (
            f"QED={qed}，是 0 ~ 1 的类药性描述符，不是成药成功概率。"
            if qed is not None else "QED 缺失或不可用，未作解读。"
        )
        return '\n'.join((
            f"- {assessment['overall_assessment']}",
            f"- {qed_text}",
            "- 建议先补齐缺失描述符，再结合研究目标审查筛选阈值；"
            "吸收、溶解度、膜透过性、选择性和疗效需要各自的独立实验验证。",
        ))

    def _generate_interpretation(self, smiles: str, props: Dict) -> str:
        """Use the same bounded assessment as the formatted report."""
        return "📋 分析: " + self._assess_lipinski_rules(props)['overall_assessment']

    def _generate_brief_reasoning(self, props: Dict) -> str:
        """Bound LLM-facing reasoning to the shared descriptor/rule assessment."""
        return self._assess_lipinski_rules(props)['overall_assessment']
