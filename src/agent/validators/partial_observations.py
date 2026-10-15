"""Narrow trust boundary for observed, but not wholly accepted, activity batches.

Usable here means retainable observations, not eligibility for a ranking or a
scientific claim. Consumers must still apply their candidate-level requirements.
No quality flag can substitute for validation of the rows themselves.
"""
from html import escape

from src.system.scientific_contracts import ObservationStatus, ToolResult


def is_partial_family_activity(result: ToolResult) -> bool:
    return (result.tool_name == "activity_predictor" and result.status == ObservationStatus.PARTIAL
            and (not result.success or (isinstance(result.data, list) and any(
                isinstance(row, dict) and any(key in row for key in (
                    "family_id", "predicted_pIC50", "activity_probability", "activity_class"))
                for row in result.data))))


def is_valid_canonical_activity_batch(result: ToolResult) -> bool:
    """Legacy canonical partial checkpoints require an explicit, valid schema."""
    from src.agent.validators.domain_validators import ActivityResultValidator

    rows = result.data
    if not isinstance(rows, list) or not rows or not all(
        isinstance(row, dict) and row.get("task_type") in {"regression", "classification"}
        for row in rows
    ):
        return False
    if not any(row.get("success") is True for row in rows):
        return False  # Validly represented failures are not observed predictions.
    try:
        return ActivityResultValidator().validate(result) is None
    except (TypeError, ValueError, KeyError, AttributeError):
        return False


def partial_activity_error(result: ToolResult) -> str | None:
    from src.activity.family_contract import resolve_activity_family
    from src.agent.validators.domain_validators import ActivityResultValidator

    if (result.tool_name != "activity_predictor"
            or result.status != ObservationStatus.PARTIAL or result.error is not None):
        return "Not an error-free partial family activity observation"
    if (any(result.quality.get(key, False) is not False for key in ("demo_mode", "fallback_used"))
            or (result.provenance is not None
                and (result.provenance.demo_mode or result.provenance.fallback_used))):
        return "Partial activity observation lacks real model provenance"
    rows = result.data
    if not isinstance(rows, list) or not rows:
        return "Partial activity observation contains no prediction rows"
    try:
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("smiles"), str)
                    or not row["smiles"].strip() or not isinstance(row.get("family_id"), str)
                    or not row.get("requested_target")
                    or resolve_activity_family(row["requested_target"]) != row["family_id"]):
                return "Partial activity observation contains an invalid family row"
        invalid = ActivityResultValidator().validate(result)
        if invalid:
            return invalid
    except (TypeError, ValueError, KeyError, AttributeError):
        return "Partial activity observation contains invalid model provenance"
    if not any(type(row.get(key)) in (int, float) for row in rows
               for key in ("predicted_pIC50", "activity_probability")):
        return "Partial activity observation contains no observed predictions"
    return None


def has_usable_observation(result: ToolResult) -> bool:
    """Retain existing success handling; explicitly validate false-partial rows."""
    if result.error is not None or result.status not in {
        ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL,
    }:
        return False
    if is_partial_family_activity(result):
        return partial_activity_error(result) is None
    return result.success is True


def format_activity_observations(result: ToolResult) -> str:
    """Render only validated rows, never revive a failed tool's arbitrary prose."""
    if partial_activity_error(result) is not None:
        return ""

    def cell(value):
        if value is None:
            return "不可用"
        return escape(str(value)).replace("|", "\\|").replace("`", "\\`").replace("\n", " ").replace("\r", " ")

    lines = ["## 分子活性模型预测（非实验结论）", "状态：部分完成；原始预测保留，逐项检查排序资格。",
             "| 候选 | SMILES | 活性分类 | 活性概率 | 预测 pIC50 | 结果状态 |",
             "|---|---|---|---|---|---|"]
    for row in result.data:
        if row.get("classification_regression_consistent") is False:
            detail = "计算完成；分类与回归不一致，需复核；未用于活性排序"
        elif row.get("success") is True:
            detail = "检查通过；可用于活性排序"
        elif row.get("status") == "partial":
            detail = "部分阶段未完成；未用于活性排序"
        else:
            detail = "未得到可用预测；未用于活性排序"
        lines.append("| " + " | ".join(cell(value) for value in (
            row.get("candidate_id"), row.get("smiles"), row.get("activity_class"),
            row.get("activity_probability"), row.get("predicted_pIC50"), detail,
        )) + " |")
    lines.append("分类概率阈值：0.5；回归标签阈值：pIC50 5.0。冲突未通过修改预测值或阈值消除。")
    return "\n".join(lines)
