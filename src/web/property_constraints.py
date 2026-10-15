"""Visible threshold checks from accepted, identity-aligned descriptor evidence."""
import re

from src.agent.property_constraints import requested_property_bounds, check_property_bounds

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")


def _accepted(item, name):
    quality, provenance = item.get("quality"), item.get("provenance")
    return (item.get("tool_name") == name and item.get("status") == "succeeded"
            and item.get("success") is True and item.get("error") is None
            and type(quality) is dict and quality.get("validated") is True
            and isinstance(quality.get("evidence_id"), str) and _ID.fullmatch(quality["evidence_id"])
            and type(provenance) is dict and provenance.get("tool_name") == name
            and provenance.get("demo_mode") is False and provenance.get("fallback_used") is False
            and isinstance(provenance.get("input_digest"), str)
            and 0 < len(provenance["input_digest"]) <= 256)


def _canonical(value):
    from rdkit import Chem
    if not isinstance(value, str) or not 0 < len(value) <= 512:
        return None
    molecule = Chem.MolFromSmiles(value)
    return Chem.MolToSmiles(molecule) if molecule is not None else None


def generation_constraint_report(result, query):
    bounds, unknown = requested_property_bounds(query)
    if not bounds and not unknown:
        return None
    sequence = result.get("tool_result_sequence", [])
    if type(sequence) is not list:
        return None
    generators = [item for item in sequence if type(item) is dict and item.get("tool_name") == "llm_molecular_generator"]
    properties = [item for item in sequence if type(item) is dict and item.get("tool_name") == "property_calculator"]
    # Optimization has its own baseline/candidate verifier and comparison table.
    if len(generators) != 1 or len(properties) != 1 or not _accepted(generators[0], "llm_molecular_generator"):
        return None
    data = generators[0].get("data")
    candidates = data.get("candidates") if type(data) is dict else None
    if type(candidates) is not list or not 1 <= len(candidates) <= 32:
        return None
    expected = {}
    for row in candidates:
        if type(row) is not dict or not isinstance(row.get("candidate_id"), str) or not _ID.fullmatch(row["candidate_id"]):
            return None
        identity = _canonical(row.get("canonical_smiles"))
        if identity is None or row["candidate_id"] in expected or identity in expected.values():
            return None
        expected[row["candidate_id"]] = identity
    item = properties[0]
    rows = item.get("data")
    trace = result.get("trace_id")
    valid = bool(isinstance(trace, str) and _ID.fullmatch(trace)
                 and _accepted(item, "property_calculator") and type(rows) is list and len(rows) == len(expected))
    by_id = {}
    if valid:
        for row in rows:
            if (type(row) is not dict or row.get("candidate_id") not in expected
                    or row["candidate_id"] in by_id
                    or row.get("success", True) is not True
                    or row.get("status", "succeeded") != "succeeded"
                    or row.get("error") is not None or row.get("error_code")
                    or _canonical(row.get("smiles")) != expected[row["candidate_id"]]
                    or type(row.get("properties")) is not dict):
                valid = False
                break
            by_id[row["candidate_id"]] = row["properties"]
    lines = ["## 用户约束逐项检查", "", "| Candidate | Constraint | RDKit value | Result |", "|---|---|---|---|"]
    for identity in expected:
        for check in check_property_bounds(bounds, by_id.get(identity) if valid else None):
            state = "未验证" if check["satisfied"] is None else "满足" if check["satisfied"] else "不满足"
            value = check["value"] if check["value"] is not None else "不可用"
            lines.append(f"| {identity} | {check['metric']} {check['operator']} {check['threshold']} {check['unit']} | {value} | {state} |")
    if unknown:
        lines.append("未验证的约束语法：" + ", ".join(unknown))
    if valid:
        lines.append(f"来源：{trace} / {item['quality']['evidence_id']}；独立 RDKit 描述符，不是实验验证。")
    else:
        lines.append("独立性质证据缺失或身份/来源检查未通过；未宣称约束已满足。")
    return "\n".join(lines)
