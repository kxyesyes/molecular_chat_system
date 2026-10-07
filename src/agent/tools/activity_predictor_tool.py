#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
活性预测工具封装
将 src.activity.predictor.ActivityPredictor 包装为标准 Agent Tool。
"""

from copy import deepcopy
import hashlib
from src.activity.family_contract import resolve_activity_family
from src.agent.contracts import (
    AgentErrorCode,
    AgentExecutionError,
    ObservationStatus,
    ToolProvenance,
    ToolResult,
)
from .activity_input import activity_intent_requested, parse_activity_input, requested_activity_endpoint
from .base_tool import BaseMolecularTool
from src.activity.request_selection import (
    ActivityModelRequest,
    ModelSelectionError,
    request_provenance_matches,
)


class ActivityPredictorTool(BaseMolecularTool):
    """活性预测工具：输入 SMILES → 输出 RG-MPNN 活性预测得分"""

    # The selected family bundle is only known after request-local validation.
    # WorkflowRunSession uses checkpoint_model_version to bind reuse to that
    # exact request; if it cannot resolve an identity, reuse remains disabled.
    checkpoint_reuse_requires_runtime_identity = True

    def __init__(self):
        super().__init__(
            name="activity_predictor",
            description="基于已注册 RG-MPNN 模型及其科学 metadata，按模型任务与 endpoint 返回分子活性预测。输入：包含 SMILES 的查询文本。"
        )

    def should_use(self, query: str) -> bool:
        try:
            text, smiles, _ = parse_activity_input(query, self)
        except ValueError:
            return False
        has_keyword = activity_intent_requested(text)
        has_smiles = bool(smiles)
        return has_keyword and has_smiles

    def checkpoint_model_version(self, input_data):
        """Return the request-bound model identity used for checkpoint reuse.

        This performs registry/bundle validation only; it does not run either
        prediction stage.  Returning ``None`` fails closed and preserves the
        old no-reuse behavior when the request cannot be bound to a verified
        model pair.
        """
        try:
            _, _, target = parse_activity_input(input_data, self)
            endpoint = requested_activity_endpoint(input_data)
            if target is None:
                return None
            request_model = input_data.get("model_request") if isinstance(input_data, dict) else None
            if request_model is not None and not isinstance(request_model, dict):
                return None
            request_payload = dict(request_model or {})
            request_payload.setdefault("target", target)
            if endpoint is not None:
                request_payload.setdefault("endpoint", endpoint)
            expected = ActivityModelRequest.from_mapping(request_payload)
            if expected.family_id != resolve_activity_family(target):
                return None
            from src.activity.prediction_service import get_family_predictor
            return get_family_predictor().request_identity(
                target=target, model_request=request_payload)
        except Exception:
            return None

    def validate_checkpoint_result(self, input_data, result):
        """Accept a restored result only when it is bound to this request."""
        try:
            _, smiles, target = parse_activity_input(input_data, self)
            if target is None or not isinstance(result.data, list) or len(result.data) != len(smiles):
                return False
            candidate_ids = input_data.get("candidate_ids") if isinstance(input_data, dict) else None
            if candidate_ids is not None and (
                not isinstance(candidate_ids, list) or len(candidate_ids) != len(smiles)
            ):
                return False
            request_model = input_data.get("model_request") if isinstance(input_data, dict) else None
            if request_model is not None and not isinstance(request_model, dict):
                return False
            request_payload = dict(request_model or {})
            request_payload.setdefault("target", target)
            endpoint = requested_activity_endpoint(input_data)
            if endpoint is not None:
                request_payload.setdefault("endpoint", endpoint)
            expected = ActivityModelRequest.from_mapping(request_payload)
            if expected.family_id != resolve_activity_family(target):
                return False
            for index, row in enumerate(result.data):
                if not isinstance(row, dict) or row.get("smiles") != smiles[index]:
                    return False
                if row.get("requested_target") != target:
                    return False
                if candidate_ids is not None and row.get("candidate_id") != candidate_ids[index]:
                    return False
                if not request_provenance_matches(row.get("provenance"), expected):
                    return False
            return True
        except Exception:
            return False

    def execute(self, query):
        candidate_ids = query.get("candidate_ids") if isinstance(query, dict) else None
        if candidate_ids is not None and (
            not isinstance(candidate_ids, list)
            or any(not isinstance(value, str) or not value for value in candidate_ids)
        ):
            return ToolResult.error_result(
                self.name, AgentErrorCode.INVALID_INPUT,
                "候选 ID 必须是与 SMILES 一一对应的非空字符串列表。",
                status=ObservationStatus.INVALID_INPUT,
            )
        try:
            text, smiles, target = parse_activity_input(query, self)
            endpoint = requested_activity_endpoint(query)
        except ValueError as exc:
            return ToolResult.error_result(
                self.name, AgentErrorCode.INVALID_INPUT, str(exc),
                status=ObservationStatus.INVALID_INPUT)
        if candidate_ids is not None and len(candidate_ids) != len(smiles):
            return ToolResult.error_result(
                self.name,
                AgentErrorCode.INVALID_INPUT,
                "候选 ID 必须与 SMILES 一一对应。",
                status=ObservationStatus.INVALID_INPUT,
            )
        if endpoint is not None and endpoint != "pIC50":
            return ToolResult.error_result(
                self.name, AgentErrorCode.MODEL_UNAVAILABLE,
                f"当前真实活性模型仅登记为 pIC50，无法直接提供 {endpoint}；未使用其他端点替代。",
                status=ObservationStatus.UNAVAILABLE,
            )
        if target is None:
            message = "活性预测需要明确的靶点（PDE 或 BuChE）；未选择默认或全局模型。"
            # Keep one structured failure row per input.  This is a validation
            # rejection, not an inference call, so no model is loaded and no
            # scientific value is populated.
            rows = [{
                "smiles": smi,
                "candidate_id": candidate_id,
                "requested_target": None,
                "success": False,
                "status": "failed",
                "execution_status": "failed",
                "error": "explicit_target_required",
                "errors": {"target": "explicit_target_required"},
                "warnings": [message],
            } for smi, candidate_id in zip(smiles, candidate_ids or [None] * len(smiles))]
            return ToolResult(
                tool_name=self.name,
                success=False,
                message=message,
                data=rows,
                error=AgentExecutionError(
                    code=AgentErrorCode.INVALID_INPUT,
                    message=message,
                ),
                warnings=[message],
                evidence=[{"prediction": deepcopy(row)} for row in rows],
                quality={
                    "prediction_status": "failed",
                    "candidate_ids_bound": candidate_ids is not None,
                    "model_loaded": False,
                },
                status=ObservationStatus.INVALID_INPUT,
            )
        try:
            from src.activity.prediction_service import predict_activity
            request_model = query.get("model_request") if isinstance(query, dict) else None
            if request_model is not None and not isinstance(request_model, dict):
                return ToolResult.error_result(
                    self.name, AgentErrorCode.INVALID_INPUT,
                    "模型请求参数必须是结构化对象。",
                    status=ObservationStatus.INVALID_INPUT,
                )
            request_payload = dict(request_model or {})
            request_payload.setdefault("target", target)
            if endpoint is not None:
                request_payload.setdefault("endpoint", endpoint)
            expected_request = ActivityModelRequest.from_mapping(request_payload)
            if expected_request.family_id != resolve_activity_family(target):
                return ToolResult.error_result(
                    self.name, AgentErrorCode.INVALID_INPUT,
                    "模型请求靶点家族与当前预测靶点不一致。",
                    status=ObservationStatus.INVALID_INPUT,
                )
            if expected_request.endpoint != "pIC50":
                return ToolResult.error_result(
                    self.name, AgentErrorCode.MODEL_UNAVAILABLE,
                    f"当前真实活性模型仅登记为 pIC50，无法直接提供 {expected_request.endpoint}。",
                    status=ObservationStatus.UNAVAILABLE,
                )
            if request_model is None:
                summary = predict_activity(smiles, target=target)
            else:
                summary = predict_activity(smiles, target=target, model_request=request_model)
        except ModelSelectionError as exc:
            return ToolResult.error_result(
                self.name, AgentErrorCode.INVALID_INPUT, str(exc),
                status=ObservationStatus.INVALID_INPUT)
        except Exception:
            return ToolResult.error_result(
                self.name, AgentErrorCode.MODEL_UNAVAILABLE,
                "家族活性预测服务不可用；未使用其他模型替代。",
                status=ObservationStatus.UNAVAILABLE)
        from src.activity.prediction_service import summarize_predictions

        try:
            rows = deepcopy(summary["results"])
            if candidate_ids is not None and len(candidate_ids) != len(rows):
                raise ValueError("Candidate ID count mismatch")
            if candidate_ids is not None:
                for row, candidate_id in zip(rows, candidate_ids):
                    row["candidate_id"] = candidate_id
            expected = summarize_predictions(rows)
            aligned = len(rows) == len(smiles) and all(
                row.get("smiles") == smi and row.get("requested_target") == target
                and row.get("family_id") == resolve_activity_family(target)
                for row, smi in zip(rows, smiles)
            )
            if (not aligned or summary["status"] != expected["status"]
                    or summary["success"] is not expected["success"]):
                raise ValueError("Family result contract mismatch")
            for row in rows:
                has_claim = any(row.get(key) is not None for key in (
                    "predicted_pIC50", "activity_probability", "activity_class"))
                row_provenance = row.get("provenance")
                if has_claim or (isinstance(row_provenance, dict)
                                 and row_provenance.get("request") is not None):
                    if not request_provenance_matches(row_provenance, expected_request):
                        raise ValueError("Family result request identity mismatch")
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
        request_identities = {
            row.get("provenance", {}).get("request", {}).get("identity")
            for row in rows
            if isinstance(row.get("provenance"), dict)
            and isinstance(row["provenance"].get("request"), dict)
            and row["provenance"]["request"].get("identity")
        }
        request_identity = next(iter(request_identities)) if len(request_identities) == 1 else None
        data_versions = sorted({
            model.get("prepared_dataset_sha256")
            for row in rows
            for model in (row.get("provenance", {}).get("models", {}) or {}).values()
            if isinstance(model, dict) and model.get("prepared_dataset_sha256")
        })
        input_structures = [
            {"smiles": row.get("smiles"),
             "sha256": "sha256:" + hashlib.sha256(
                 str(row.get("smiles", "")).encode("utf-8")
             ).hexdigest()}
            for row in rows
        ]
        labels = {"passed": "完成", "partial": "部分完成", "failed": "失败"}
        needs_review = any(row.get("classification_regression_consistent") is False for row in rows)
        review_message = "计算已完成，分类与回归不一致，需复核"
        summary_label = labels[status] + ("；含需复核结果" if needs_review else "")
        lines = ["## 分子活性模型预测（非实验结论）",
                 f"状态：{summary_label}",
                 "| SMILES | 活性分类 | 活性概率 | 预测 pIC50 | 状态/阶段错误 |",
                 "|---|---|---|---|---|"]

        def cell(value):
            return str(value).replace("|", "\\|").replace("\n", " ").replace("<", "&lt;").replace(">", "&gt;")

        def number(value):
            return f"{value:.4f}" if type(value) in (int, float) else "不可用"

        for row in rows:
            detail = labels.get(row.get("status"), "失败")
            if row.get("classification_regression_consistent") is False:
                detail = review_message
            if row.get("errors"):
                detail += "；" + str(row["errors"])
            lines.append("| " + " | ".join(map(cell, [row.get("smiles", ""),
                row.get("activity_class") or "不可用", number(row.get("activity_probability")),
                number(row.get("predicted_pIC50")), detail])) + " |")
        lines.extend(cell(warning) for warning in warnings)
        return ToolResult(
            tool_name=self.name, success=summary["success"] is True and status == "passed",
            message=f"家族活性预测{summary_label}；模型预测不等同于实验结论。",
            data=rows, formatted="\n".join(lines), warnings=warnings,
            status={"passed": ObservationStatus.SUCCEEDED, "partial": ObservationStatus.PARTIAL,
                    "failed": ObservationStatus.FAILED}[status],
            quality={
                "prediction_status": status,
                "model_provenance": [deepcopy(r.get("provenance", {})) for r in rows],
                "source": "registered_family_rg_mpnn",
                "model_version": request_identity,
                "data_version": data_versions,
                "input_structures": input_structures,
                "candidate_ids_bound": candidate_ids is not None,
                "requested_endpoint": endpoint or "pIC50",
                "request_model_identity": request_identity,
            },
            evidence=[{"prediction": deepcopy(row)} for row in rows],
            provenance=ToolProvenance(
                tool_name=self.name,
                model_name="FamilyActivityPredictor",
                model_version=request_identity,
            ))
