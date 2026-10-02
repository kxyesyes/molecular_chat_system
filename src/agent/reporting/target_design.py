from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from src.agent.contracts import AgentResult, ToolResult

try:
    from rdkit import Chem

    RDKIT_AVAILABLE = True
except ImportError:  # pragma: no cover - covered by runtime truth reporting
    Chem = None
    RDKIT_AVAILABLE = False


def present_target_design_result(
    result: AgentResult,
    plan_metadata: Mapping[str, Any] | None = None,
) -> None:
    """Attach validated candidate cards and a deterministic scientific report."""
    metadata = dict(plan_metadata or {})
    requested_count = _positive_int(metadata.get("requested_count"), default=0)
    target_hint = str(metadata.get("target_hint") or "未指定靶点").strip()
    results_by_step = {
        str(item.quality.get("step_id") or item.tool_name): item
        for item in result.tool_results
    }
    generator = results_by_step.get("molecule_generation") or _find_tool_result(
        result.tool_results,
        "llm_molecular_generator",
    )
    properties = results_by_step.get("properties") or _find_tool_result(
        result.tool_results,
        "property_calculator",
    )

    property_map = _property_map(properties.data if properties else None)
    candidates, invalid_count, duplicate_count = _validated_candidates(
        generator.data if generator else None,
        property_map,
        requested_count=requested_count,
    )
    model_name = _generator_model(generator, candidates)
    complete = bool(requested_count and len(candidates) >= requested_count)

    presentation = {
        "kind": "molecule_candidates",
        "target": target_hint,
        "requested_count": requested_count,
        "actual_count": len(candidates),
        "complete": complete,
        "invalid_candidate_count": invalid_count,
        "duplicate_candidate_count": duplicate_count,
        "validation_method": "RDKit" if RDKIT_AVAILABLE else "unavailable",
        "molecules": candidates,
    }
    result.metadata = {**result.metadata, "presentation": presentation}
    result.final_answer = _build_report(
        target_hint=target_hint,
        requested_count=requested_count,
        candidates=candidates,
        model_name=model_name,
        results=result.tool_results,
        invalid_count=invalid_count,
        duplicate_count=duplicate_count,
    )


def _validated_candidates(
    raw_data: Any,
    property_map: Mapping[str, Mapping[str, Any]],
    requested_count: int,
) -> tuple[list[dict[str, Any]], int, int]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    invalid_count = 0
    duplicate_count = 0

    for item in _candidate_items(raw_data):
        raw_smiles = item.get("smiles") or item.get("SMILES")
        canonical = _canonical_smiles(raw_smiles)
        if canonical is None:
            invalid_count += 1
            continue
        if canonical in seen:
            duplicate_count += 1
            continue
        seen.add(canonical)
        candidates.append(
            {
                "smiles": canonical,
                "model": str(item.get("model") or "").strip(),
                "source": str(item.get("source") or "generated").strip(),
                "properties": dict(property_map.get(canonical) or {}),
                "validation": {"valid": True, "method": "RDKit"},
            }
        )
        if requested_count and len(candidates) >= requested_count:
            break

    return candidates, invalid_count, duplicate_count


def _candidate_items(raw_data: Any) -> Iterable[Mapping[str, Any]]:
    if isinstance(raw_data, Mapping):
        raw_data = (
            raw_data.get("molecules")
            or raw_data.get("candidates")
            or raw_data.get("data")
            or []
        )
    if not isinstance(raw_data, list):
        return []
    return [item for item in raw_data if isinstance(item, Mapping)]


def _canonical_smiles(value: Any) -> str | None:
    if not RDKIT_AVAILABLE or not isinstance(value, str):
        return None
    value = value.strip()
    if not value:
        return None
    mol = Chem.MolFromSmiles(value)
    if mol is None:
        return None
    return Chem.MolToSmiles(mol, canonical=True)


def _property_map(raw_data: Any) -> dict[str, Mapping[str, Any]]:
    if isinstance(raw_data, Mapping):
        raw_data = raw_data.get("molecules") or raw_data.get("data") or []
    if not isinstance(raw_data, list):
        return {}

    properties: dict[str, Mapping[str, Any]] = {}
    for item in raw_data:
        if not isinstance(item, Mapping):
            continue
        canonical = _canonical_smiles(item.get("smiles") or item.get("SMILES"))
        item_properties = item.get("properties")
        if canonical and isinstance(item_properties, Mapping):
            properties[canonical] = item_properties
    return properties


def _generator_model(
    generator: ToolResult | None,
    candidates: list[dict[str, Any]],
) -> str:
    if generator:
        model_name = generator.quality.get("model")
        if model_name:
            return str(model_name)
    for candidate in candidates:
        if candidate.get("model"):
            return str(candidate["model"])
    return "未记录"


def _build_report(
    *,
    target_hint: str,
    requested_count: int,
    candidates: list[dict[str, Any]],
    model_name: str,
    results: list[ToolResult],
    invalid_count: int,
    duplicate_count: int,
) -> str:
    lines = [
        f"## {target_hint} 候选分子设计结果",
        "",
        "### 执行摘要",
        "",
        f"- 目标：{target_hint}",
        f"- 请求候选数：{requested_count or '未指定'}",
        f"- 有效且唯一候选数：{len(candidates)}",
        f"- 分子生成模型：{model_name}",
        f"- 结构校验：{'RDKit canonical SMILES' if RDKIT_AVAILABLE else '不可用'}",
    ]
    if invalid_count:
        lines.append(f"- 已剔除无效结构：{invalid_count}")
    if duplicate_count:
        lines.append(f"- 已合并重复结构：{duplicate_count}")

    lines.extend(["", "### 工作流状态", "", "| 步骤 | 工具 | 状态 | 说明 |", "|---|---|---|---|"])
    for item in results:
        step_name = str(item.quality.get("step_id") or item.tool_name)
        status = _step_status(
            item,
            requested_count=requested_count,
            actual_candidate_count=len(candidates),
        )
        message = _table_text(item.message or "未提供说明")
        lines.append(f"| {step_name} | {item.tool_name} | {status} | {message} |")

    lines.extend(["", "### 已验证候选", ""])
    if candidates:
        lines.extend(
            [
                "| # | Canonical SMILES | MW | LogP | TPSA | QED |",
                "|---:|---|---:|---:|---:|---:|",
            ]
        )
        for index, candidate in enumerate(candidates, start=1):
            props = candidate.get("properties") or {}
            lines.append(
                "| {index} | `{smiles}` | {mw} | {logp} | {tpsa} | {qed} |".format(
                    index=index,
                    smiles=candidate["smiles"],
                    mw=_display_number(props.get("molecular_weight")),
                    logp=_display_number(props.get("logp")),
                    tpsa=_display_number(props.get("tpsa")),
                    qed=_display_number(props.get("qed"), digits=3),
                )
            )
    else:
        lines.append("未获得可由 RDKit 验证的候选结构。")

    warnings = _deduplicated_warnings(results)
    if warnings:
        lines.extend(["", "### 限制与警告", ""])
        lines.extend(f"- {warning}" for warning in warnings)

    lines.extend(
        [
            "",
            "### 科学解释边界",
            "",
            f"这些结构是计算生成的候选假设，不代表已验证的 {target_hint} 抑制剂、实验活性或临床结论。",
            "当前结果仅汇总实际成功工具产生的数据；未成功的模型步骤保留为失败或不可用状态。",
            "本工作流未执行分子对接，因此不提供任何能量数值。后续筛选应基于真实活性模型、ADMET 模型和准备完整的受体、配体与搜索盒参数继续进行。",
        ]
    )
    return "\n".join(lines)


def _find_tool_result(results: list[ToolResult], tool_name: str) -> ToolResult | None:
    return next((item for item in results if item.tool_name == tool_name), None)


def _step_status(
    result: ToolResult,
    *,
    requested_count: int,
    actual_candidate_count: int,
) -> str:
    if not result.success:
        return "失败/不可用"
    if result.tool_name == "llm_molecular_generator" and (
        bool(result.quality.get("partial_generation"))
        or (
            requested_count > 0
            and actual_candidate_count < requested_count
        )
    ):
        return "部分完成"
    return "完成"


def _deduplicated_warnings(results: list[ToolResult]) -> list[str]:
    warnings: list[str] = []
    seen: set[str] = set()
    for item in results:
        for warning in item.warnings:
            text = str(warning).strip()
            if text and text not in seen:
                seen.add(text)
                warnings.append(text)
    return warnings


def _display_number(value: Any, digits: int = 2) -> str:
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}"
    return "—"


def _table_text(value: Any) -> str:
    return " ".join(str(value).replace("|", "/").split())


def _positive_int(value: Any, default: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return default
    return parsed if parsed > 0 else default
