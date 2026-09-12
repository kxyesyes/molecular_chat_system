#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
活性预测工具封装
将 src.activity.predictor.ActivityPredictor 包装为标准 Agent Tool。
"""

from typing import Dict, Any
import logging
from copy import deepcopy
from src.agent.contracts import AgentErrorCode, ObservationStatus, ToolProvenance, ToolResult
from .activity_input import parse_activity_input
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
        try:
            text, smiles, _ = parse_activity_input(query, self)
        except ValueError:
            return False
        query_lower = text.lower()
        keywords = [
            '活性', 'ic50', 'pic50', '抑制',
            'activity', 'potency', 'inhibition', '活性预测',
        ]
        has_keyword = any(kw in query_lower for kw in keywords)
        has_smiles = bool(smiles)
        return has_keyword and has_smiles

    def execute(self, query):
        try:
            text, smiles, target = parse_activity_input(query, self)
        except ValueError as exc:
            return ToolResult.error_result(
                self.name, AgentErrorCode.INVALID_INPUT, str(exc),
                status=ObservationStatus.INVALID_INPUT)
        if target is None:
            return self._execute_legacy(text, smiles)
        try:
            from src.activity.prediction_service import predict_activity
            summary = predict_activity(smiles, target=target)
        except Exception:
            return ToolResult.error_result(
                self.name, AgentErrorCode.MODEL_UNAVAILABLE,
                "家族活性预测服务不可用；未使用其他模型替代。",
                status=ObservationStatus.UNAVAILABLE)
        from src.activity.family_contract import resolve_activity_family
        from src.activity.prediction_service import summarize_predictions

        try:
            rows = deepcopy(summary["results"])
            expected = summarize_predictions(rows)
            aligned = len(rows) == len(smiles) and all(
                row.get("smiles") == smi and row.get("requested_target") == target
                and row.get("family_id") == resolve_activity_family(target)
                for row, smi in zip(rows, smiles)
            )
            if (not aligned or summary["status"] != expected["status"]
                    or summary["success"] is not expected["success"]):
                raise ValueError("Family result contract mismatch")
        except (KeyError, TypeError, ValueError):
            return ToolResult.error_result(self.name, AgentErrorCode.INVALID_OUTPUT,
                                          "家族活性结果与请求或阶段状态不一致。")
        from src.agent.validators.domain_validators import ActivityResultValidator
        invalid = ActivityResultValidator().validate(ToolResult(self.name, False, "", data=rows))
        if invalid:
            return ToolResult.error_result(self.name, AgentErrorCode.INVALID_OUTPUT,
                                          "家族活性结果未通过数值或双模型溯源校验。")
        status = summary["status"]
        service_warnings = summary.get("warnings", [])
        warnings = [warning for warning in service_warnings if isinstance(warning, str)] \
            if isinstance(service_warnings, list) else []
        # Reuse the shared warning contract without altering original observations.
        warnings = list(dict.fromkeys(warnings + expected["warnings"]))
        labels = {"passed": "完成", "partial": "部分完成", "failed": "失败"}
        lines = ["## 分子活性模型预测（非实验结论）",
                 f"状态：{labels[status]}",
                 "| SMILES | 活性分类 | 活性概率 | 预测 pIC50 | 状态/阶段错误 |",
                 "|---|---|---|---|---|"]

        def cell(value):
            return str(value).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")

        def number(value):
            return f"{value:.4f}" if type(value) in (int, float) else "不可用"

        for row in rows:
            detail = labels.get(row.get("status"), "失败")
            if row.get("errors"):
                detail += "；" + str(row["errors"])
            lines.append("| " + " | ".join(map(cell, [row.get("smiles", ""),
                row.get("activity_class") or "不可用", number(row.get("activity_probability")),
                number(row.get("predicted_pIC50")), detail])) + " |")
        lines.extend(cell(warning) for warning in warnings)
        return ToolResult(
            tool_name=self.name, success=summary["success"] is True and status == "passed",
            message=f"家族活性预测{labels[status]}，仅完整双阶段结果视为成功。",
            data=rows, formatted="\n".join(lines), warnings=warnings,
            status={"passed": ObservationStatus.SUCCEEDED, "partial": ObservationStatus.PARTIAL,
                    "failed": ObservationStatus.FAILED}[status],
            quality={"prediction_status": status, "model_provenance": [deepcopy(r.get("provenance", {})) for r in rows]},
            evidence=[{"prediction": deepcopy(row)} for row in rows],
            provenance=ToolProvenance(tool_name=self.name, model_name="FamilyActivityPredictor"))

    def _execute_legacy(self, query: str, smiles_list) -> Dict[str, Any]:
        result = self._create_base_result(query)

        if not self._check_rdkit(result):
            return result

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
