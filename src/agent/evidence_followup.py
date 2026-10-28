"""Bounded comparison of accepted observations, never a scientific executor.

Only the immediately preceding socket-local turn is reusable. This module has
no database, model, tool execution, prose extraction or shared state access.
"""
from collections.abc import Mapping
from copy import deepcopy
from html import escape
import json
import re

from src.agent.persistence.redaction import contains_secret_material
from src.agent.utils.validators import InputValidator


MAX_CANDIDATES = 16
MAX_EVIDENCE_BYTES = 262144
_FOLLOWUP = re.compile(
    r"\s*(?:请)?(?:只|仅)保留通过一致性检查的候选[，,]\s*"
    r"按(?:活性证据|pIC50)排序"
    r"(?:[；;，,]\s*解释(?:另外|其他)的候选为何没有参与排序)?"
    r"(?:[，,]\s*不要改动原始预测)?[。.!]?\s*", re.I)


def comparison_tools(query):
    """Recognize explicit multi-endpoint comparisons, not open-ended design."""
    from src.agent.planning.request_parsing import positive_intent_text, wants_admet, wants_activity
    from src.agent.planning.comparison_request import COMPARISON_PROPERTY_TERMS
    positive = positive_intent_text(query)
    if (not re.search(r"比较|对比|\bcompare\b", positive, re.I)
            or not wants_activity(query)
            or re.search(r"生成|设计|优化|对接|反向|寻靶|靶点搜索|靶点检索|"
                         r"\b(?:generate|design|optimi\w*|dock\w*|reverse)\b", positive, re.I)):
        return ()
    tools = []
    if COMPARISON_PROPERTY_TERMS.search(positive):
        tools.append("property_calculator")
    if wants_admet(query):
        tools.append("admet_predictor")
    return tuple(tools + ["activity_predictor"]) if tools else ()


def ranking_requested(query):
    """Share positive evidence-ordering intent between planning and display."""
    from src.agent.planning.request_parsing import positive_intent_text
    return bool(re.search(r"排序|\brank\b", positive_intent_text(query), re.I))


def _canonical(smiles):
    from rdkit import Chem
    if not isinstance(smiles, str) or not smiles:
        return None
    molecule = Chem.MolFromSmiles(smiles)
    return Chem.MolToSmiles(molecule) if molecule is not None else None


def _requested_smiles(query):
    identity = InputValidator().analyze_molecular_input(query)
    if identity.blocks_execution or not 1 <= len(identity.valid_smiles) <= MAX_CANDIDATES:
        return []
    return list(dict.fromkeys(_canonical(value) for value in identity.valid_smiles))


def _observations(result, tools):
    if not isinstance(result, Mapping) or not isinstance(result.get("tool_result_sequence"), list):
        return []
    # Copy structured observations only. Formatted/message/assistant text is not
    # an evidence source, including on failed tools.
    fields = ("tool_name", "status", "success", "data", "error", "warnings", "quality", "provenance")
    return [{key: deepcopy(item.get(key)) for key in fields}
            for item in result["tool_result_sequence"]
            if isinstance(item, dict) and item.get("tool_name") in tools]


def _bounded(value):
    try:
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
        return len(text.encode("utf-8")) <= MAX_EVIDENCE_BYTES and not contains_secret_material(text)
    except (TypeError, ValueError, RecursionError):
        return False


def _accepted(item):
    quality, provenance = item.get("quality"), item.get("provenance")
    return (item.get("status") in {"succeeded", "partial"}
        and (item.get("status") == "partial" or item.get("success") is True)
        and item.get("error") is None
        and isinstance(quality, dict) and quality.get("validated") is True
        and bool(quality.get("evidence_id"))
        and isinstance(provenance, dict) and bool(provenance.get("input_digest"))
        and provenance.get("tool_name") == item.get("tool_name")
        and provenance.get("demo_mode") is False and provenance.get("fallback_used") is False
        and not quality.get("demo_mode") and not quality.get("fallback_used"))


def _aligned_rows(item, smiles):
    rows = item.get("data")
    if not isinstance(rows, list) or len(rows) != len(smiles) or not all(isinstance(row, dict) for row in rows):
        return None
    identities = [_canonical(row.get("smiles")) for row in rows]
    if None in identities or len(set(identities)) != len(identities) or set(identities) != set(smiles):
        return None
    if any(row.get("canonical_smiles") is not None
           and _canonical(row["canonical_smiles"]) != identity for row, identity in zip(rows, identities)):
        return None
    return dict(zip(identities, rows))


def accepted_activity_evidence(result, query, session_id):
    """Detach one accepted activity batch with exact structure/target binding."""
    if (not isinstance(result, Mapping) or not isinstance(session_id, str)
            or not session_id or not result.get("trace_id")):
        return None
    smiles = _requested_smiles(query)
    observations = _observations(result, ("activity_predictor",))
    if not smiles or len(observations) != 1 or not _bounded(observations):
        return None
    item = observations[0]
    rows = _aligned_rows(item, smiles)
    if not _accepted(item) or rows is None:
        return None
    from src.agent.tools.activity_input import activity_target
    from src.activity.family_contract import resolve_activity_family
    try:
        # Use the same target grammar as the producing activity tool. Generic
        # design target selection interprets "排除原因" as target negation.
        target = activity_target(query, lambda value: _canonical(value) is not None)
        family = resolve_activity_family(target)
        if any(row.get("family_id") != family
               or resolve_activity_family(row.get("requested_target")) != family for row in rows.values()):
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    return {"session_id": session_id, "trace_id": result["trace_id"],
            "query": query, "smiles": smiles, "observations": observations}


def _exclusion(row):
    from src.activity.prediction_service import summarize_predictions
    if row.get("classification_regression_consistent") is False:
        return "分类与回归不一致，需复核"
    if row.get("status") != "passed" or row.get("success") is not True:
        return "预测未通过检查或阶段失败"
    try:
        if summarize_predictions([row])["status"] == "passed":
            return None
    except (TypeError, ValueError, KeyError, AttributeError):
        pass
    return "pIC50、单位、一致性或模型来源未通过检查"


def _cell(value):
    return escape(str(value)).replace("|", "\\|").replace("`", "&#96;").replace("\n", " ").replace("\r", " ")


def _render(smiles, observations, trace_id, tools, *, reused=False, activity_bound=True, ranking=True):
    by_tool, issues, aligned = {}, [], {}
    for tool in tools:
        found = [item for item in observations if item["tool_name"] == tool]
        item = found[0] if len(found) == 1 else None
        by_tool[tool] = item
        rows = _aligned_rows(item, smiles) if item is not None else None
        if item is None or not _accepted(item) or rows is None:
            issues.append(f"{tool}：不可用、未接受或候选未一一对应")
        else:
            aligned[tool] = rows
            if item["status"] == "partial":
                issues.append(f"{tool}：部分完成")
    activity = aligned.get("activity_predictor", {}) if activity_bound else {}
    eligible, excluded = [], []
    for smi in smiles:
        row = activity.get(smi)
        reason = _exclusion(row) if row is not None else "缺少可对应的已接受活性证据或靶点来源"
        if reason:
            excluded.append((smi, reason))
        else:
            eligible.append(row)
    # Do not compare unrelated model requests, even within the same family.
    identities = {row["provenance"]["request"]["identity"] for row in eligible}
    operation = "排序" if ranking else "比较"
    if len(identities) > 1:
        excluded.extend((row["smiles"], f"模型请求来源不同，不能直接{operation}") for row in eligible)
        eligible = []
    if ranking:
        eligible.sort(key=lambda row: -row["predicted_pIC50"])
    title = ("活性证据排序（复用上一轮）" if reused else "多指标比较与活性证据排序") if ranking else "多指标比较"
    order = "按预测 pIC50 降序；相同数值并列。" if ranking else "按输入顺序展示。"
    lines = [title, "",
        "标准：仅使用状态 passed、分类与回归一致且模型来源与有限 pIC50 通过检查的候选，" + order,
        "性质和 ADMET 仅作描述性信息与风险提示，不参与隐藏加权；缺失 ADMET 不删除已验证活性行。模型预测非实验结论。", "",
        ("| 排名 " if ranking else "") + "| SMILES | 靶点 | 活性分类 | 活性概率 | 预测 pIC50 |",
        ("|---" if ranking else "") + "|---|---|---|---|---|"]
    last, rank = None, 0
    for index, row in enumerate(eligible, 1):
        if row["predicted_pIC50"] != last:
            rank = index
        last = row["predicted_pIC50"]
        values = (row["smiles"], row.get("requested_target"), row.get("activity_class"),
                  row.get("activity_probability"), last)
        if ranking:
            values = (rank, *values)
        lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    if not eligible:
        lines.append(f"无可用候选参与活性{operation}；未推测任何缺失数值。")
    if any(tool != "activity_predictor" for tool in tools):
        show_properties = "property_calculator" in tools
        show_admet = "admet_predictor" in tools
        columns = ["SMILES"]
        if show_properties:
            columns.extend(["分子量 (Da)", "logP", "TPSA", "QED"])
        if show_admet:
            columns.append("ADMET 风险提示（描述性）")
        lines.extend(["", "| " + " | ".join(columns) + " |", "|" + "---|" * len(columns)])
        for smi in smiles:
            properties = aligned.get("property_calculator", {}).get(smi, {}).get("properties", {})
            admet_row = aligned.get("admet_predictor", {}).get(smi, {})
            admet = admet_row.get("admet", {})
            risk = "不可用，未通过检查"
            if admet_row.get("status", "succeeded") == "succeeded" and isinstance(admet, dict) and admet:
                risk = f"风险提示 {admet.get('risk_count', '不可用')}/{admet.get('total_endpoints', '不可用')}；详见原始端点"
            values = [smi]
            if show_properties:
                values.extend(properties.get(key, "不可用") for key in ("molecular_weight", "logp", "tpsa", "qed"))
            if show_admet:
                values.append(risk)
            lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    lines.extend(["", "排除原因："])
    lines.extend(f"- `{_cell(smi)}`：{reason}。原始预测保留。" for smi, reason in excluded)
    if not excluded:
        lines.append("无候选被排除：全部候选通过检查；不因用户提到‘另外的候选’而虚构排除。")
    if issues:
        lines.extend(["", "部分完成，并非全部请求的证据可用：", *issues])
    # Capture the concise view before technical source footers and raw JSON.
    # The first two policy paragraphs are replaced explicitly, not removed from
    # arbitrary model/tool prose. Accepted rows, ranking and exclusions are shared.
    labels = {'property_calculator': '分子性质', 'admet_predictor': 'ADMET 预测',
              'activity_predictor': '活性预测'}
    summary_order = ('按通过一致性检查的预测 pIC50 降序排列；相同数值并列。' if ranking
                     else '按输入顺序展示通过一致性检查的活性预测。')
    summary_lines = [lines[0], '', summary_order,
                     *lines[5:]]
    summary_answer = '\n'.join(summary_lines)
    for name, label in labels.items():
        summary_answer = summary_answer.replace(name + '：', label + '：')
    lines.extend(["", "来源：trace `" + _cell(trace_id) + "`。"])
    for tool, item in by_tool.items():
        if item:
            lines.append(f"- {tool} [{_cell(item['status'])}]；证据 `{_cell((item.get('quality') or {}).get('evidence_id'))}`。")
    if reused:
        lines.append("仅复用当前聊天紧邻上一轮结构化工具证据，未重新运行科学工具。")
    lines.extend(["", f"所有原始结构化结果（含未参与{operation}的行、阶段错误、靶点及模型来源；未更改预测或阈值）：",
        "```json", json.dumps(observations, ensure_ascii=False, indent=2).replace("`", "\\u0060").replace("<", "\\u003c"), "```"])
    partial = bool(issues or excluded)
    return {"final_answer": "\n".join(lines), "summary_answer": summary_answer, "success": not partial,
            "status": "partial" if partial else "completed", "partial": partial}


def followup_answer(query, history, session_id):
    """Return None outside the deliberately closed follow-up grammar."""
    from .activity_evidence_inspection import inspection_request, inspection_answer, valid_inspection_evidence
    from .contracts.concept_request import is_concept_explanation
    inspection = inspection_request(query)
    concept = is_concept_explanation(query)
    if inspection is False and not concept:
        return {'final_answer': '本次只读追问包含无法对应到已有活性记录的要求；未重新计算。'
                '可查看上一轮的原始活性概率、pIC50、分类、一致性和排除原因；'
                '新增指标或其他靶点没有可对应证据，请拆分请求。',
                'status': 'not_calculated', 'success': False}
    if inspection is None and not _FOLLOWUP.fullmatch(query):
        return None
    previous = history[-1].get("activity_evidence") if history else None
    if isinstance(previous, dict) and previous.get("session_id") == session_id and session_id:
        # Recheck detached data; never trust a history flag or assistant prose.
        result = {"trace_id": previous.get("trace_id"), "tool_result_sequence": previous.get("observations")}
        evidence = accepted_activity_evidence(result, previous.get("query", ""), session_id)
        if evidence is not None:
            if inspection is False and concept:
                # Multiple supported concept clauses may fall outside the
                # inspection grammar. Validate the source before falling through.
                if valid_inspection_evidence(evidence):
                    return None
                return inspection_answer(evidence, {'selectors': (), 'all': True})
            if inspection is not None:
                return inspection_answer(evidence, inspection)
            view = _render(evidence["smiles"], evidence["observations"], evidence["trace_id"],
                           ("activity_predictor",), reused=True)
            if valid_inspection_evidence(evidence):
                view['activity_evidence'] = evidence
            return view
        if concept:
            # A present but corrupt source is not the same as absent context.
            # Do not conceal its integrity failure behind an ordinary answer.
            return {'final_answer': '上一轮活性记录未通过完整性复核，无法安全解读原始预测；未重新计算，也未沿用该证据。',
                    'status': 'not_calculated', 'success': False}
    if concept:
        # No source is needed to explain a concept. Web admission uses the same
        # complete grammar and seals an ordinary-chat plan with no science.
        return None
    return {"final_answer": "缺少当前聊天紧邻上一轮可对应的已接受活性证据。请提供明确候选 SMILES 和靶点，或先完成预测；未从助手文字猜测数值。",
            "status": "not_calculated", "success": False}


def comparison_answer(query, result, session_id):
    tools = comparison_tools(query)
    if not tools:
        return None
    smiles = _requested_smiles(query)
    observations = _observations(result, tools)
    if not smiles or not _bounded(observations):
        return {"final_answer": "比较未完成：候选超过边界或结构化证据无法安全完整展示，请缩小请求范围。",
                "success": False, "status": "partial", "partial": True}
    evidence = accepted_activity_evidence(result, query, session_id)
    return _render(smiles, observations, result.get("trace_id"), tools,
                   activity_bound=evidence is not None, ranking=ranking_requested(query))
