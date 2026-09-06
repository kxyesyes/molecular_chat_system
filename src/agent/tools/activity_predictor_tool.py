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
            description="基于已注册 RG-MPNN 模型及其科学 metadata，按模型任务与 endpoint 返回分子活性预测。输入：包含 SMILES 的查询文本。"
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
            successful_predictions = [
                pred for pred in predictions if pred.get("success")
            ]

            lines = [
                "## 🔬 分子活性预测结果 (RG-MPNN)",
                "",
                "| SMILES | 任务 | Endpoint | 预测值 | 单位 | 备注 |",
                "|--------|------|----------|--------|------|------|",
            ]

            for pred in predictions:
                if pred.get('success'):
                    smiles = pred['smiles']
                    task_type = pred.get('task_type', '')
                    endpoint = pred.get('endpoint', '')
                    units = pred.get('units', '')
                    note = pred.get('note', '')

                    if task_type == "classification":
                        prediction_value = pred.get("probability")
                        value_label = "probability"
                    else:
                        prediction_value = pred.get("value")
                        value_label = "value"
                    if not isinstance(prediction_value, (int, float)):
                        lines.append(
                            f"| `{smiles[:40]}` | {task_type or '-'} | {endpoint or '-'} | ❌ schema error | {units or '-'} | Missing numeric {value_label} |"
                        )
                        continue

                    lines.append(
                        f"| `{smiles[:40]}` | {task_type} | {endpoint} | "
                        f"{value_label}={float(prediction_value):.4f} | {units} | {note} |"
                    )
                else:
                    lines.append(
                        f"| `{pred.get('smiles', 'N/A')[:40]}` | - | - | ❌ 失败 | - | {pred.get('error', '')} |"
                    )

            result['success'] = bool(successful_predictions)
            result['data'] = predictions
            result['formatted'] = "\n".join(lines) if successful_predictions else ""
            result['message'] = (
                f"完成 {len(successful_predictions)} 个分子的任务感知活性预测"
                if successful_predictions
                else "RG-MPNN 未返回任何真实活性预测结果"
            )
            metadata = getattr(predictor, "current_model_metadata", None) or {}
            model_provenance = {
                "model_id": metadata.get("model_id"),
                "weights_sha256": metadata.get("weights_sha256"),
                "endpoint": metadata.get("endpoint"),
                "units": metadata.get("units"),
                "task_type": metadata.get("task_type"),
                "demo_mode": bool(getattr(predictor, "demo_mode", True)),
            }
            for prediction in successful_predictions:
                prediction["model_provenance"] = dict(model_provenance)
            result['quality'] = {
                "model_provenance": model_provenance
            }

        except Exception as e:
            logger.error(f"活性预测失败: {e}", exc_info=True)
            result['message'] = f"预测失败: {str(e)}"

        return result
