"""Bounded, socket-local descriptor context; never infer numbers from prose.

Only one validated molecule per turn can be reused. No shared cache, database
search, model-generated value, or implicit molecular identity is consulted.
"""
import math
import re
from collections.abc import Mapping

from src.agent.planning.request_parsing import positive_intent_text, restricted_properties
from src.agent.utils.validators import InputValidator


def _pure_property_operation(query, smiles):
    """Recognize a complete positive descriptor intent, not a keyword subset.

    Recovery is deliberately narrower than normal routing. Any extra action,
    negation, or unsupported prose needs a new explicit request from the user.
    """
    molecule = r"(?:SMILES\s*[:：]?\s*)?" + re.escape(smiles)
    field = (r"(?:分子性质|理化性质|基本性质|性质|分子量|logp|tpsa|hba|hbd|qed|mw|"
             r"molecular\s+weight|(?:molecular\s+)?properties)")
    fields = field + r"(?:\s*(?:和|与|及|、|[,，]|and\b)\s*" + field + r")*"
    action = r"(?:计算|分析|评估|查看)"
    intent = (
        r"(?:请\s*)?(?:只|仅)?(?:"
        + action + r"\s*" + molecule + r"\s*(?:的)?\s*" + fields
        + r"|(?:对|为)\s*" + molecule + r"\s*" + action + r"\s*" + fields + r")"
        + r"|(?:please\s+)?(?:calculate|compute|assess|analyze)\s+"
        + fields + r"\s+(?:for|of)\s+" + molecule
    )
    return re.fullmatch(r"\s*(?:" + intent + r")\s*[。.]?\s*", query, re.I) is not None


def _adjacent_molecule_label(query, smiles):
    """Bind a single name directly preceding the structure in a positive clause."""
    prefix = query.split(smiles, 1)[0]
    clause = re.split(r"[,，;；。.!！?？\n]", prefix)[-1].strip()
    if positive_intent_text(clause) != clause:
        return None
    match = re.fullmatch(
        r"(?:(?:请|现在|本次|只|仅)\s*)*"
        r"(?:计算|分析|评估|使用|用|比较|对比|(?:把|将)(?:分析)?对象改为|改为)\s*"
        r"(?P<label>[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff _-]{0,79})"
        r"\s*", clause, re.I,
    )
    if match is None:
        return None
    label = match["label"].strip()
    if re.search(r"和|与|及|或|并|不是|而非|排除|\b(?:and|or|not)\b", label, re.I):
        return None
    return label


def corrected_property_query(query, history):
    """Resume just an immediately rejected, single-structure property request."""
    if not history or not re.match(r"\s*(?:更正为|纠正为|correct(?:ed)?\s+to\b)", query, re.I):
        return query
    current = InputValidator().analyze_molecular_input(query)
    if len(current.valid_smiles) != 1 or current.blocks_execution:
        return query
    # Explicit new instructions always win. Only an otherwise bare correction
    # (optionally "please continue") may inherit the rejected operation.
    correction = (r"\s*(?:更正为|纠正为|correct(?:ed)?\s+to)\s*"
                  + re.escape(current.valid_smiles[0])
                  + r"\s*(?:[,，]\s*)?(?:(?:请)?继续|(?:please\s+)?continue)?[。.]?\s*")
    if not re.fullmatch(correction, query, re.I):
        return query
    previous = history[-1]
    old_query = previous.get("user", "")
    old = InputValidator().analyze_molecular_input(old_query)
    if (len(old.potential_smiles) != 1 or not old.blocks_execution
            or previous.get("agent_used") is not False
            or not _pure_property_operation(old_query, old.potential_smiles[0])):
        return query
    return old_query.replace(old.potential_smiles[0], current.valid_smiles[0])


def accepted_property_evidence(result, query, session_id):
    """Project detached, accepted runtime observations, not assistant text."""
    if not isinstance(result, Mapping):
        return None
    sequence = result.get("tool_result_sequence", [])
    if not isinstance(sequence, list):
        return None
    observations = [item for item in sequence if type(item) is dict
                    and item.get("tool_name") == "property_calculator"]
    if len(observations) != 1:
        return None
    item = observations[0]
    provenance, quality = item.get("provenance"), item.get("quality")
    if (item.get("status") != "succeeded" or item.get("success") is not True
            or item.get("error") is not None
            or type(provenance) is not dict or type(quality) is not dict
            or quality.get("validated") is not True
            or not quality.get("evidence_id")
            or provenance.get("tool_name") != "property_calculator"
            or provenance.get("demo_mode") is not False
            or provenance.get("fallback_used") is not False
            or quality.get("demo_mode") or quality.get("fallback_used")
            or not provenance.get("input_digest") or not result.get("trace_id")):
        return None
    data = item.get("data")
    if not isinstance(data, list) or len(data) != 1 or type(data[0]) is not dict:
        return None
    row = data[0]
    properties = row.get("properties")
    if type(properties) is not dict:
        return None
    values = {key: properties.get(key) for key in ("molecular_weight", "logp")}
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values.values()):
        return None
    identity = InputValidator().analyze_molecular_input(query)
    if len(identity.valid_smiles) != 1 or identity.blocks_execution:
        return None
    from rdkit import Chem
    molecule = Chem.MolFromSmiles(row.get("smiles", ""))
    requested = Chem.MolFromSmiles(identity.valid_smiles[0])
    if molecule is None or requested is None or Chem.MolToSmiles(molecule) != Chem.MolToSmiles(requested):
        return None
    return {
        **values, "smiles": row["smiles"], "canonical_smiles": Chem.MolToSmiles(molecule),
        "molecule_label": _adjacent_molecule_label(query, identity.valid_smiles[0]),
        "session_id": session_id, "trace_id": result["trace_id"],
        "evidence_id": quality["evidence_id"], "tool_version": provenance.get("tool_version"),
    }


def descriptor_answer(query, current, history, session_id):
    """Render only the explicitly limited MW/logP request, with source IDs."""
    result = descriptor_result(query, current, history, session_id)
    return result["final_answer"] if result is not None else None


def descriptor_result(query, current, history, session_id):
    """Project answer coverage separately from successful tool execution."""
    positive = positive_intent_text(query)
    if (not restricted_properties(query)
            or not re.search(r"分子量|molecular\s+weight|\bmw\b", positive, re.I)
            or not re.search(r"logp", positive, re.I)):
        return None
    compare = bool(re.search(r"比较|对比|\bcompare\b", positive, re.I))
    previous = history[-1].get("property_evidence") if history else None
    issues = []
    if compare:
        reference = re.search(r"刚才|之前|上一个|上一轮|previous|earlier", positive, re.I)
        named = re.search(r"(?:刚才|之前|上一轮)(?:的)?([^，,。\s]+?)的(?:分子量|性质)", positive)
        if (not reference or not isinstance(previous, dict)
                or previous.get("session_id") != session_id
                or (named and named[1].casefold() != (previous.get("molecule_label") or "").casefold())):
            issues.append("无法完成比较：缺少当前聊天上一轮可唯一对应的已验证性质证据。请提供两个分子的明确 SMILES 后重试；未猜测历史数值。")
            # Keep the accepted current descriptors, without inventing a
            # historical row or presenting this as a completed comparison.
            compare = False
    if current is None:
        issues.append("无法完成比较或性质展示：当前分子的已验证分子量/logP 证据不可用。")
        return {"final_answer": "\n".join(issues), "success": False,
                "status": "partial", "partial": True, "warnings": issues}
    rows = [("上轮（复用）", previous), ("本轮", current)] if compare else [("本轮", current)]
    lines = ["分子性质比较" if compare else "分子性质", "",
             "| 对象（SMILES） | 分子量 (Da) | logP（无量纲） |",
             "|---|---:|---:|"]
    for label, evidence in rows:
        lines.append(f"| {label} `{evidence['smiles']}` | {evidence['molecular_weight']:.2f} | {evidence['logp']:.3f} |")
    summary_answer = '\n'.join(issues + ([""] if issues else []) + lines)
    lines.extend(["", "来源：RDKit 计算描述符，非实测值；logP 为结构估算，不是 ADMET 模型预测。"])
    for label, evidence in rows:
        lines.append(f"- {label}：property_calculator v{evidence['tool_version']}；"
                     f"trace `{evidence['trace_id']}`；证据 `{evidence['evidence_id']}`。")
    if compare:
        lines.append("上轮数值仅复用上述成功且通过验证的工具结果，未重新计算或继承上轮工具请求。")
    return {"final_answer": "\n".join(issues + ([""] if issues else []) + lines),
            "summary_answer": summary_answer,
            "success": not issues, "status": "partial" if issues else "completed",
            "partial": bool(issues), "warnings": issues}
