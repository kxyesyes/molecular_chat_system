#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
类药评估工具 - 包含QED计算和药物相似性评估
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


class DrugLikenessAssessment(BaseMolecularTool):
    """类药评估工具"""

    def __init__(self):
        super().__init__(
            name="drug_likeness_assessment",
            description="Assess drug-likeness and medicinal chemistry properties from SMILES"
        )

        # 触发关键词
        self.trigger_words = [
            'drug-like', 'druglike', 'drug likeness', '类药', '药物相似性', '药物性质',
            'qed', 'medicinal', 'pharmaceutical', '药用', '医药',
            'lipinski', '利平斯基', 'rule of five', '五规则',
            'assess', 'evaluate', 'analysis', '评估', '分析', '评价'
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
            logger.info(f"DrugLikenessAssessment triggered: found {len(smiles_list)} valid SMILES")

        return result

    def execute(self, query: str) -> Dict[str, Any]:
        """执行类药评估"""
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

            # 计算所有SMILES的类药性质
            calculated_results = []
            formatted_outputs = []

            for smiles in smiles_list:
                assessment = self.assess_drug_likeness(smiles)
                if assessment:
                    calculated_results.append({'smiles': smiles, 'assessment': assessment})
                    formatted_outputs.append(self.format_assessment(smiles, assessment))

            if not calculated_results:
                result['message'] = "无法评估类药性质。请检查SMILES结构是否正确。"
                result['reasoning'] = "提供的SMILES结构似乎无效或无法被RDKit处理。"
                return result

            # 成功 - 准备结果
            result['success'] = True
            result['data'] = calculated_results
            result['formatted'] = "\n\n".join(formatted_outputs)

            # 添加推理和解释
            if len(calculated_results) == 1:
                smiles = calculated_results[0]['smiles']
                assessment = calculated_results[0]['assessment']
                result['reasoning'] = f"我成功评估了 {smiles} 的类药性质。{self._generate_brief_reasoning(assessment)}"
                result['formatted'] += "\n\n" + self._generate_interpretation(smiles, assessment)
            else:
                result['reasoning'] = f"我评估了 {len(calculated_results)} 个分子结构的类药性质，为每个提供了全面的药物相似性分析。"

            result['message'] = f"成功评估了 {len(calculated_results)} 个分子的类药性质"

        except Exception as e:
            logger.error(f"Drug-likeness assessment failed: {e}")
            result['message'] = f"评估失败: {str(e)}"
            result['reasoning'] = "在评估过程中发生了意外错误。"

        return result

    def assess_drug_likeness(self, smiles: str) -> Optional[Dict[str, Any]]:
        """评估类药性质"""
        try:
            mol = Chem.MolFromSmiles(smiles)
            if mol is None:
                return None

            # 基本描述符
            mw = Descriptors.MolWt(mol)
            logp = Crippen.MolLogP(mol)
            hba = Lipinski.NumHAcceptors(mol)
            hbd = Lipinski.NumHDonors(mol)
            tpsa = Descriptors.TPSA(mol)
            rotatable_bonds = Lipinski.NumRotatableBonds(mol)
            aromatic_rings = Descriptors.NumAromaticRings(mol)
            heteroatoms = Lipinski.NumHeteroatoms(mol)

            # QED计算
            qed_score = QED.qed(mol)

            # Lipinski's Rule of Five评估
            lipinski_violations = self._assess_lipinski_violations(mw, logp, hba, hbd)

            # Veber规则评估
            veber_compliance = self._assess_veber_rules(tpsa, rotatable_bonds)

            # Lead-likeness评估
            lead_likeness = self._assess_lead_likeness(mw, logp)

            # 综合评估
            overall_assessment = self._calculate_overall_assessment(
                qed_score, len(lipinski_violations), veber_compliance, lead_likeness
            )

            assessment = {
                'qed_score': round(qed_score, 3),
                'lipinski_rule_of_five': lipinski_violations,
                'veber_rules': veber_compliance,
                'lead_likeness': lead_likeness,
                'overall_assessment': overall_assessment,
                'molecular_properties': {
                    'molecular_weight': round(mw, 2),
                    'logp': round(logp, 3),
                    'hba': hba,
                    'hbd': hbd,
                    'tpsa': round(tpsa, 2),
                    'rotatable_bonds': rotatable_bonds,
                    'aromatic_rings': aromatic_rings,
                    'heteroatoms': heteroatoms
                }
            }

            return assessment

        except Exception as e:
            logger.error(f"Failed to assess drug-likeness for {smiles}: {e}")
            return None

    def _assess_lipinski_violations(self, mw: float, logp: float, hba: int, hbd: int) -> Dict[str, Any]:
        """评估Lipinski五规则违反情况"""
        violations = []
        details = {
            'molecular_weight': {'value': mw, 'limit': 500, 'pass': mw <= 500},
            'logp': {'value': logp, 'limit': 5, 'pass': logp <= 5},
            'hba': {'value': hba, 'limit': 10, 'pass': hba <= 10},
            'hbd': {'value': hbd, 'limit': 5, 'pass': hbd <= 5}
        }

        for param, info in details.items():
            if not info['pass']:
                violations.append(param)

        return {
            'violations': violations,
            'violation_count': len(violations),
            'compliance': len(violations) <= 1,  # 允许1个违反
            'details': details
        }

    def _assess_veber_rules(self, tpsa: float, rotatable_bonds: int) -> Dict[str, Any]:
        """评估Veber规则"""
        tpsa_pass = tpsa <= 140
        rotatable_bonds_pass = rotatable_bonds <= 10

        return {
            'tpsa_compliance': tpsa_pass,
            'rotatable_bonds_compliance': rotatable_bonds_pass,
            'overall_compliance': tpsa_pass and rotatable_bonds_pass,
            'details': {
                'tpsa': {'value': tpsa, 'limit': 140, 'pass': tpsa_pass},
                'rotatable_bonds': {'value': rotatable_bonds, 'limit': 10, 'pass': rotatable_bonds_pass}
            }
        }

    def _assess_lead_likeness(self, mw: float, logp: float) -> Dict[str, Any]:
        """评估lead-likeness"""
        mw_pass = 250 <= mw <= 350
        logp_pass = 1 <= logp <= 3

        return {
            'molecular_weight_compliance': mw_pass,
            'logp_compliance': logp_pass,
            'overall_compliance': mw_pass and logp_pass,
            'details': {
                'molecular_weight': {'value': mw, 'range': '250-350', 'pass': mw_pass},
                'logp': {'value': logp, 'range': '1-3', 'pass': logp_pass}
            }
        }

    def _calculate_overall_assessment(self, qed_score: float, lipinski_violations: int,
                                    veber_compliance: bool, lead_likeness: bool) -> Dict[str, Any]:
        """计算综合评估"""
        # 评分权重
        qed_weight = 0.4
        lipinski_weight = 0.3
        veber_weight = 0.2
        lead_weight = 0.1

        # 计算各项得分 (0-1)
        qed_normalized = qed_score  # QED已经是0-1
        lipinski_normalized = max(0, 1 - lipinski_violations / 4)  # 最多4个违反
        veber_normalized = 1.0 if veber_compliance else 0.5
        lead_normalized = 1.0 if lead_likeness else 0.3

        # 综合得分
        overall_score = (
            qed_weight * qed_normalized +
            lipinski_weight * lipinski_normalized +
            veber_weight * veber_normalized +
            lead_weight * lead_normalized
        )

        # 评级
        if overall_score >= 0.8:
            grade = "优秀"
            category = "高类药性"
        elif overall_score >= 0.6:
            grade = "良好"
            category = "中等类药性"
        elif overall_score >= 0.4:
            grade = "一般"
            category = "较低类药性"
        else:
            grade = "较差"
            category = "低类药性"

        return {
            'score': round(overall_score, 3),
            'grade': grade,
            'category': category,
            'component_scores': {
                'qed': qed_normalized,
                'lipinski': lipinski_normalized,
                'veber': veber_normalized,
                'lead_likeness': lead_normalized
            }
        }

    def format_assessment(self, smiles: str, assessment: Dict) -> str:
        """格式化类药评估输出"""
        qed = assessment['qed_score']
        lipinski = assessment['lipinski_rule_of_five']
        veber = assessment['veber_rules']
        overall = assessment['overall_assessment']
        props = assessment['molecular_properties']

        # QED解释
        if qed > 0.7:
            qed_desc = "优秀"
        elif qed > 0.5:
            qed_desc = "良好"
        elif qed > 0.3:
            qed_desc = "一般"
        else:
            qed_desc = "较差"

        output = f"""💊 类药性评估结果

SMILES: `{smiles}`

🎯 **综合评估:**
• 总体评分: {overall['score']} ({overall['grade']})
• 类药性类别: {overall['category']}
• QED评分: {qed} ({qed_desc})

📏 **Lipinski五规则评估:**
• 违反项数: {lipinski['violation_count']}/4
• 分子量: {props['molecular_weight']} g/mol {'✓' if lipinski['details']['molecular_weight']['pass'] else '✗'}
• LogP: {props['logp']} {'✓' if lipinski['details']['logp']['pass'] else '✗'}
• 氢键受体: {props['hba']} {'✓' if lipinski['details']['hba']['pass'] else '✗'}
• 氢键供体: {props['hbd']} {'✓' if lipinski['details']['hbd']['pass'] else '✗'}

🔬 **Veber规则评估:**
• 整体符合: {'是' if veber['overall_compliance'] else '否'}
• TPSA: {props['tpsa']} Ų {'✓' if veber['details']['tpsa']['pass'] else '✗'}
• 可旋转键: {props['rotatable_bonds']} {'✓' if veber['details']['rotatable_bonds']['pass'] else '✗'}

📊 **分子特征:**
• 芳香环数: {props['aromatic_rings']}
• 杂原子数: {props['heteroatoms']}"""

        return output

    def _generate_interpretation(self, smiles: str, assessment: Dict) -> str:
        """生成类药评估解释"""
        interpretations = []
        overall = assessment['overall_assessment']
        qed = assessment['qed_score']
        lipinski = assessment['lipinski_rule_of_five']

        # 综合评价
        interpretations.append(f"该分子的综合类药性评分为{overall['score']}，属于{overall['category']}")

        # QED特别说明
        if qed > 0.7:
            interpretations.append("QED评分优秀，具有很好的药物相似性特征")
        elif qed > 0.5:
            interpretations.append("QED评分良好，具有合理的药物相似性")
        else:
            interpretations.append("QED评分较低，药物相似性有限")

        # Lipinski违反分析
        if lipinski['violation_count'] == 0:
            interpretations.append("完全符合Lipinski五规则，预测具有良好的口服生物利用度")
        elif lipinski['violation_count'] == 1:
            interpretations.append("仅违反1条Lipinski规则，通常仍可接受")
        else:
            interpretations.append("违反多条Lipinski规则，可能影响药代动力学性质")

        return "📋 专业评估: " + "；".join(interpretations) + "。"

    def _generate_brief_reasoning(self, assessment: Dict) -> str:
        """生成简要推理"""
        reasoning_parts = []
        overall = assessment['overall_assessment']
        qed = assessment['qed_score']

        # 综合评估
        reasoning_parts.append(f"综合评分{overall['score']} ({overall['grade']})")

        # QED评估
        if qed > 0.7:
            reasoning_parts.append("QED评分优秀")
        elif qed > 0.5:
            reasoning_parts.append("QED评分良好")
        else:
            reasoning_parts.append("QED评分需要提升")

        return "，".join(reasoning_parts) + "。"