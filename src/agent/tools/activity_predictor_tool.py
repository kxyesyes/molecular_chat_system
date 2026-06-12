#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
活性预测工具封装
将 src.activity.predictor.ActivityPredictor 包装为标准 Agent Tool。
"""

from typing import Dict, Any
import logging
from .base_tool import BaseMolecularTool

logger = logging.getLogger(__name__)


class ActivityPredictorTool(BaseMolecularTool):
    """活性预测工具：输入 SMILES → 输出 RG-MPNN 活性预测得分"""

    def __init__(self):
        super().__init__(
            name="activity_predictor",
            description="基于 RG-MPNN 图神经网络，预测分子的生物活性得分(pIC50)。输入：包含 SMILES 的查询文本。输出：活性得分与高/中/低分类。"
        )
        self._predictor = None

    def _get_predictor(self):
        """延迟加载 ActivityPredictor"""
        if self._predictor is None:
            try:
                from src.activity.predictor import get_predictor
                self._predictor = get_predictor()
                logger.info("✅ ActivityPredictor 加载成功")
            except Exception as e:
                logger.error(f"❌ 加载活性预测器失败: {e}")
                raise
        return self._predictor

    def should_use(self, query: str) -> bool:
        query_lower = query.lower()
        keywords = [
            '活性', 'ic50', 'pic50', '抑制',
            'activity', 'potency', 'inhibition', '活性预测',
        ]
        has_keyword = any(kw in query_lower for kw in keywords)
        has_smiles = bool(self.extract_smiles(query))
        return has_keyword and has_smiles

    def execute(self, query: str) -> Dict[str, Any]:
        result = self._create_base_result(query)

        if not self._check_rdkit(result):
            return result

        smiles_list = self.extract_smiles(query)
        if not smiles_list:
            result['message'] = "未在查询中检测到有效的 SMILES 结构。请提供分子的 SMILES 字符串。"
            return result

        logger.info(f"活性预测: {smiles_list}")

        try:
            predictor = self._get_predictor()
            predictions = predictor.predict(smiles_list)

            lines = [
                "## 🔬 分子活性预测结果 (RG-MPNN)",
                "",
                "| SMILES | 活性得分 | 活性等级 | 置信度 | 备注 |",
                "|--------|---------|---------|--------|------|",
            ]

            for pred in predictions:
                if pred.get('success'):
                    smiles = pred['smiles']
                    score = pred['activity_score']
                    cls = pred['class']
                    conf = pred.get('confidence', 'N/A')
                    note = pred.get('note', '')

                    cls_emoji = {"High": "🟢", "Medium": "🟡", "Low": "🔴"}.get(cls, "⚪")

                    if isinstance(conf, float):
                        conf_str = f"{conf:.2f}"
                    else:
                        conf_str = str(conf)

                    lines.append(
                        f"| `{smiles[:40]}` | {score:.2f} | {cls_emoji} {cls} | {conf_str} | {note} |"
                    )
                else:
                    lines.append(
                        f"| `{pred.get('smiles', 'N/A')[:40]}` | - | ❌ 失败 | - | {pred.get('error', '')} |"
                    )

            result['success'] = True
            result['data'] = predictions
            result['formatted'] = "\n".join(lines)
            result['message'] = f"完成 {len(predictions)} 个分子的活性预测"

        except Exception as e:
            logger.error(f"活性预测失败: {e}", exc_info=True)
            result['message'] = f"预测失败: {str(e)}"

        return result
