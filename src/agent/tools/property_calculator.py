#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
优化的分子属性计算工具
"""

from typing import Dict, List, Optional, Any
import logging

try:
    from rdkit import Chem
    from rdkit.Chem import Descriptors, Crippen, Lipinski, QED
    RDKIT_AVAILABLE = True
except ImportError:
    RDKIT_AVAILABLE = False

from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)


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
        smiles_list = self.extract_smiles(query)
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
            smiles_list = self.extract_smiles(query)

            if not smiles_list:
                result['message'] = "在查询中未找到有效的SMILES分子结构。"
                result['reasoning'] = "我在输入中搜索了SMILES模式，但无法识别任何有效的分子结构。"
                return result

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
                result['formatted'] += "\n\n" + self._generate_interpretation(smiles, props)
            else:
                result['reasoning'] = f"我计算了 {len(calculated_results)} 个分子结构的属性，为每个提供了全面分析。"

            result['message'] = f"成功计算了 {len(calculated_results)} 个分子的属性"

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
        """格式化分子属性输出"""
        la = self._assess_lipinski_rules(props)

        qed = props['qed']
        if qed >= 0.75:   qed_desc = "优秀"
        elif qed >= 0.50: qed_desc = "良好"
        elif qed >= 0.30: qed_desc = "偏低"
        else:              qed_desc = "较低"

        logp = props.get('logp', 'N/A')
        if isinstance(logp, float):
            if logp < 0:   logp_desc = "高亲水"
            elif logp <= 3: logp_desc = "理想"
            elif logp <= 5: logp_desc = "偏高"
            else:           logp_desc = "过高"
        else:
            logp_desc = ""

        output = f"""\
## 🧬 分子属性分析报告

**结构：** `{smiles}`  
**分子式：** {props['molecular_formula']}

---

### 📊 物理化学属性
| 参数 | 数值 | 参考范围 | 评价 |
|---|---|---|---|
| 分子量 (MW) | {props['molecular_weight']:.2f} Da | ≤ 500 | {'✅' if props['molecular_weight'] <= 500 else '⚠️'} |
| 脂水分配系数 (LogP) | {logp} | ≤ 5 | {'✅' if isinstance(logp, float) and logp <= 5 else '⚠️'} ({logp_desc}) |
| 极性表面积 (TPSA) | {props['tpsa']:.2f} | ≤ 140 | {'✅' if props['tpsa'] <= 140 else '⚠️'} |
| 氢键受体 (HBA) | {props['hba']} | ≤ 10 | {'✅' if props['hba'] <= 10 else '⚠️'} |
| 氢键供体 (HBD) | {props['hbd']} | ≤ 5 | {'✅' if props['hbd'] <= 5 else '⚠️'} |
| 可旋转键数 | {props['rotatable_bonds']} | ≤ 10 | {'✅' if props['rotatable_bonds'] <= 10 else '⚠️'} |
| **QED 类药评分** | **{qed}** | **0 ~ 1** | **{qed_desc}** |

### 💊 Lipinski Ro5 + Veber 评估
{la['detailed_assessment']}

> {la['overall_assessment']}

---

### 🔬 优化建议
{self._generate_drug_chemistry_suggestions(props)}\
"""
        return output.strip()

    def _assess_lipinski_rules(self, props: Dict) -> Dict:
        """评估 Lipinski Ro5（含 LogP）"""
        lines = []
        violations = 0

        def chk(ok, pass_txt, fail_txt):
            nonlocal violations
            if ok:
                lines.append(f"- ✅ {pass_txt}")
            else:
                lines.append(f"- ⚠️ {fail_txt}")
                violations += 1

        chk(props['molecular_weight'] <= 500,
            f"MW = {props['molecular_weight']:.1f} Da (≤ 500)",
            f"MW = {props['molecular_weight']:.1f} Da > 500 — 分子量偏大")

        logp = props.get('logp')
        if logp is not None:
            chk(logp <= 5,
                f"LogP = {logp:.2f} (≤ 5)",
                f"LogP = {logp:.2f} > 5 — 亲脂性过高")

        chk(props['hba'] <= 10,
            f"HBA = {props['hba']} (≤ 10)",
            f"HBA = {props['hba']} > 10 — 氢键受体过多")

        chk(props['hbd'] <= 5,
            f"HBD = {props['hbd']} (≤ 5)",
            f"HBD = {props['hbd']} > 5 — 氢键供体过多")

        chk(props['tpsa'] <= 140,
            f"TPSA = {props['tpsa']:.1f} (≤ 140)",
            f"TPSA = {props['tpsa']:.1f} > 140 — 极性表面积偏大")

        if violations == 0:
            overall = "✅ 完全符合 Lipinski Ro5，口服药物潜力优秀"
        elif violations == 1:
            overall = "⚠️ 违反 1 条规则，整体仍具有较好的口服药物性质"
        else:
            overall = f"❌ 违反 {violations} 条规则，需结构优化"

        return {
            'detailed_assessment': '\n'.join(lines),
            'overall_assessment': overall,
            'violations_count': violations
        }

    def _generate_drug_chemistry_suggestions(self, props: Dict) -> str:
        """生成药物化学建议（含 LogP / QED）"""
        suggestions = []

        mw = props['molecular_weight']
        if mw > 500:
            suggestions.append("🔸 分子量偏高（>500 Da），考虑简化结构")
        elif mw < 160:
            suggestions.append("🔸 分子量较低，可适当增加复杂度")
        else:
            suggestions.append(f"✅ 分子量 {mw:.1f} Da，在理想范围内")

        logp = props.get('logp')
        if logp is not None:
            if logp > 5:
                suggestions.append(f"🔸 LogP={logp:.2f} 过高，水溶性差，建议引入亲水基团")
            elif logp < 0:
                suggestions.append(f"🔸 LogP={logp:.2f} 过低，亲脂性不足，细胞膜透过可能受限")
            else:
                suggestions.append(f"✅ LogP={logp:.2f}，亲脂性处于理想区间")

        if props['tpsa'] > 140:
            suggestions.append("🔸 TPSA 偏高，口服吸收可能受限，建议降低极性基团")
        elif props['tpsa'] < 20:
            suggestions.append("🔸 TPSA 过低，分子选择性可能不足")
        else:
            suggestions.append(f"✅ TPSA={props['tpsa']:.1f}，极性表面积适宜")

        if props['hbd'] > 5:
            suggestions.append("🔸 氢键供体过多（>5），口服吸收可能下降")
        elif props['hba'] > 10:
            suggestions.append("🔸 氢键受体过多（>10），膜透过性可能受限")
        else:
            suggestions.append("✅ 氢键供/受体数量合理")

        if props['rotatable_bonds'] > 10:
            suggestions.append("🔸 可旋转键较多，构象稳定性可能较差")
        else:
            suggestions.append(f"✅ 可旋转键数={props['rotatable_bonds']}，分子柔性适中")

        qed = props['qed']
        if qed >= 0.75:
            suggestions.append(f"✅ QED={qed}，类药性优秀，综合成药潜力强")
        elif qed >= 0.50:
            suggestions.append(f"✅ QED={qed}，类药性良好")
        elif qed >= 0.30:
            suggestions.append(f"⚠️ QED={qed}，类药性偏低，建议优化结构")
        else:
            suggestions.append(f"❌ QED={qed}，类药性较低，需显著结构改造")

        return '\n'.join([f"- {s}" for s in suggestions])

    def _generate_interpretation(self, smiles: str, props: Dict) -> str:
        """Generate brief interpretation of properties (without QED)"""
        interpretations = []

        # Lipinski's Rule of Five (without LogP check)
        violations = 0
        violation_details = []

        if props['molecular_weight'] > 500:
            violations += 1
            violation_details.append("分子量过大")
        if props['hba'] > 10:
            violations += 1
            violation_details.append("氢键受体过多")
        if props['hbd'] > 5:
            violations += 1
            violation_details.append("氢键供体过多")

        if violations == 0:
            interpretations.append("符合利平斯基规则的主要要求，预测具有较好的药物性质")
        elif violations == 1:
            interpretations.append(f"违反1条利平斯基规则({violation_details[0]})，通常是可接受的")
        else:
            interpretations.append(f"违反{violations}条利平斯基规则，可能存在药物性质问题")

        # TPSA interpretation
        if props['tpsa'] <= 60:
            interpretations.append("极性表面积较小，预测具有良好的膜透过性")
        elif props['tpsa'] <= 140:
            interpretations.append("极性表面积适中，预测具有合理的生物利用度")
        else:
            interpretations.append("极性表面积较大，可能影响膜透过性")

        return "📋 分析: " + "；".join(interpretations) + "。"

    def _generate_brief_reasoning(self, props: Dict) -> str:
        """Generate brief reasoning for LLM enhancement (without QED)"""
        reasoning_parts = []

        # Lipinski compliance (simplified)
        violations = sum([
            props['molecular_weight'] > 500,
            props['hba'] > 10,
            props['hbd'] > 5
        ])

        if violations == 0:
            reasoning_parts.append("符合利平斯基规则主要要求")
        elif violations <= 2:
            reasoning_parts.append(f"违反{violations}条利平斯基规则")
        else:
            reasoning_parts.append("多条利平斯基规则违反")

        # TPSA assessment
        if props['tpsa'] <= 140:
            reasoning_parts.append("极性表面积在合理范围")
        else:
            reasoning_parts.append("极性表面积偏高")

        return "，".join(reasoning_parts) + "。"