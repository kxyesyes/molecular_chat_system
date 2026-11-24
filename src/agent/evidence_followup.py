"""Bounded comparison of accepted observations, never a scientific executor.

Follow-ups use the preceding turn. An explicit batch reference may select
an older delivered activity record from the same socket.
"""
from collections.abc import Mapping
from copy import deepcopy
from html import escape
import json
import math
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
_PIC50_CONVERSION = re.compile(
    r'\s*(?:请)?(?:把|将)\s*(?:刚才|上一轮|上次)(?:的)?\s*(?:预测的?)?\s*'
    r'(?:(?P<smiles>[A-Za-z0-9@+\-\[\]().=#\\/:%*]+)\s*的\s*)?'
    r'pIC50\s*(?:换算|转换)\s*(?:为|成)\s*IC50\s*[（(]\s*(?P<unit>nM|微摩尔|μM|µM|uM)\s*[）)]'
    r'(?:\s*[，,；;]\s*(?:并)?\s*(?:请)?(?:说明(?:这个)?结果(?:代表|意味着)什么|'
    r'(?P<explain>解释为什么分类(?:和|与)回归结果不一致)))?'
    r'(?:\s*是多少)?[。.!！?？]?\s*', re.I)
_PIC50_NATURAL_CONVERSION = re.compile(
    r'\s*(?:请)?(?:把|将)?(?:这个|刚才的|上一轮的|它的|其|(?P<position>第[0-9一二三四五六七八九十百]+个)(?:分子|候选)?的)'
    r'\s*pIC50\s*(?:换算|转换)\s*(?:成|为)\s*'
    r'(?:多少)?\s*(?P<unit>微摩尔|μM|µM|uM|nM)\s*[？?。.!！]?\s*', re.I)
_NAMED_ACTIVITY_BATCH = re.compile(
    r'\s*(?:请)?(?:回到|回看)\s*(?:(?:刚才|之前|(?P<latest>最近展示))(?:的)?)?(?:那批)?\s*'
    r'(?:含\s*(?P<anchor>[A-Za-z0-9@+\-\[\]().=#\\/:%*]+)\s*的\s*)?'
    r'(?P<target>[A-Za-z][A-Za-z0-9_-]*)\s*(?:的)?活性预测(?:结果)?\s*[，,:：]\s*'
    r'(?P<question>.+?)(?:\s*(?:只|仅)使用已有结果[。.!！]?)?\s*', re.I | re.S)
_CHANGE_CONCENTRATION_UNIT = re.compile(
    r'\s*(?:能否?|可以|请|那)?(?:把|将)?(?:刚才)?(?:那个|这个)?'
    r'(?:(?:微摩尔|纳摩尔|[μµun]M)?(?:数值|结果))?(?:帮我)?'
    r'(?:换算[成为]|换[成为]|改用|换用|用)\s*(?P<unit>微摩尔|纳摩尔|[μµun]M)'
    r'\s*(?:表示)?(?:吗|呢)?[？?。！!]?\s*', re.I)


def comparison_tools(query):
    """Recognize explicit multi-endpoint comparisons, not open-ended design."""
    from src.agent.planning.request_parsing import positive_intent_text, wants_admet, wants_activity
    from src.agent.contracts.generation_request import requests_structural_modification
    from src.agent.planning.comparison_request import COMPARISON_PROPERTY_TERMS
    positive = positive_intent_text(query)
    if (not re.search(r"比较|对比|\bcompare\b", positive, re.I)
            or not wants_activity(query)
            or requests_structural_modification(positive)
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


def accepted_generated_activity_evidence(result, candidates, query, session_id):
    """Bind an accepted activity batch to the verified generated candidate set."""
    if (not isinstance(result, Mapping) or not isinstance(candidates, dict) or not session_id
            or candidates.get('session_id') != session_id or candidates.get('trace_id') != result.get('trace_id')
            or candidates.get('generation_scope_verified') is not True):
        return None
    rows = candidates.get('candidates')
    if not isinstance(rows, list) or not 1 <= len(rows) <= MAX_CANDIDATES:
        return None
    smiles = [_canonical(row.get('canonical_smiles')) if isinstance(row, dict) else None for row in rows]
    if None in smiles or len(set(smiles)) != len(smiles):
        return None
    observations = _observations(result, ('activity_predictor',))
    if len(observations) != 1 or not _bounded(observations):
        return None
    item = observations[0]
    aligned = _aligned_rows(item, smiles)
    if not _accepted(item) or aligned is None:
        return None
    from src.agent.tools.activity_input import activity_target
    from src.activity.family_contract import resolve_activity_family
    from src.agent.activity_evidence_inspection import valid_inspection_evidence
    try:
        family = resolve_activity_family(activity_target(query, lambda value: _canonical(value) is not None))
        if any(row.get('family_id') != family or resolve_activity_family(row.get('requested_target')) != family
               for row in aligned.values()):
            return None
    except (ValueError, TypeError, AttributeError):
        return None
    evidence = {'session_id': session_id, 'trace_id': result['trace_id'], 'query': query,
                'smiles': smiles, 'observations': observations}
    return evidence if valid_inspection_evidence(evidence) else None


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


def _converted_activity(row, unit, explain):
    value = row.get('predicted_pIC50')
    try:
        exponent = 9 if unit.lower() == 'nm' else 6
        converted = 10 ** (exponent - value) if type(value) in (int, float) and math.isfinite(value) else None
    except OverflowError:
        converted = None
    if converted is None or not math.isfinite(converted) or converted <= 0:
        return {'final_answer': '上一轮没有可换算的有效 pIC50 预测值。',
                'status': 'not_calculated', 'success': False}
    review = _exclusion(row)
    note = '；分类与回归不一致或证据不完整，需复核' if review else ''
    if unit == '微摩尔':
        unit = '微摩尔（μM）'
    answer = (f"分子 `{_cell(row['smiles'])}` 对 {_cell(row['requested_target'])} 的预测 pIC50 "
              f"为 {value:.6g}，按 IC50（{unit}）= 10^({exponent} − pIC50) 换算约为 {converted:.4g} {unit}{note}。"
              '这是由模型预测值换算的浓度，表示预测的半数抑制浓度，不能作为实验测定结果。')
    if explain:
        from src.activity.family_contract import LABEL_THRESHOLD, PROBABILITY_THRESHOLD
        probability = row.get('activity_probability')
        answer += (f" 原始活性概率：{_cell(probability)}；预测分类：{_cell(row.get('activity_class'))}。"
                   if probability is not None else ' 原始活性概率不可用。')
        if (row.get('label_threshold') == LABEL_THRESHOLD
                and row.get('probability_threshold') == PROBABILITY_THRESHOLD):
            answer += (f' 分类概率阈值为 {PROBABILITY_THRESHOLD}，回归 pIC50 的活性标签阈值为 {LABEL_THRESHOLD}。'
                       '分类与回归分别预测；不一致表示两者给出的活性标签不同，具体原因无法由这些结果确定，需复核并以实验验证。')
        else:
            answer += ' 未提供可验证的阈值信息，无法进一步解释一致性。'
    return {'final_answer': answer, 'status': 'partial' if review else 'completed',
            'success': not bool(review)}


def named_activity_readback(query, history, session_id):
    """Resolve a named batch within this socket, then use ordinary read-only evidence handling."""
    from .activity_evidence_inspection import inspection_request
    name = _NAMED_ACTIVITY_BATCH.fullmatch(query)
    if not name or history is None:
        return None
    question = name['question']
    if not (_PIC50_NATURAL_CONVERSION.fullmatch(question) or _PIC50_CONVERSION.fullmatch(question)
            or isinstance(inspection_request(question), dict)):
        return None
    batches = {}
    target = name['target'].casefold()
    for entry in reversed(history):
        record = entry.get('activity_evidence')
        order = entry.get('activity_display_order')
        if not (isinstance(record, dict) and order and session_id and record.get('session_id') == session_id
                and record.get('trace_id') and (not name['anchor'] or name['anchor'] in order)):
            continue
        observations = record.get('observations')
        if not isinstance(observations, list) or len(observations) != 1 or not isinstance(observations[0], dict):
            continue
        rows = observations[0].get('data')
        if isinstance(rows, list) and rows and all(
                isinstance(row, dict) and str(row.get('requested_target')).casefold() == target for row in rows):
            batches.setdefault(record['trace_id'], entry)
            if name['latest']:
                break
    if len(batches) != 1:
        content = (f"找不到本会话近期展示的匹配 {name['target']} 活性批次。"
                   if not batches else
                   f"近期有多个 {name['target']} 活性批次。请指定其中一批独有的 SMILES，或说“回到最近展示的 {name['target']} 活性预测”并附上问题。")
        return {'final_answer': content, 'status': 'not_calculated', 'success': False}
    source, = batches.values()
    view = followup_answer(question, [source], session_id)
    if view and view.get('activity_evidence') is not None:
        view['activity_display_order'] = source['activity_display_order']
    return view


def followup_answer(query, history, session_id):
    """Return None outside the deliberately closed follow-up grammar."""
    from .activity_evidence_inspection import inspection_request, inspection_answer, valid_inspection_evidence
    from .contracts.concept_request import is_concept_explanation
    domain_question = re.fullmatch(
        r'\s*(?:刚才|上一轮)(?:这个|该)分子是否在\s*(?P<target>[A-Za-z][A-Za-z0-9_-]*)'
        r'\s*模型的适用域内[？?]\s*依据是什么[？?。]?\s*', query, re.I)
    if domain_question:
        previous = history[-1].get('activity_evidence') if history else None
        evidence = None
        if isinstance(previous, dict) and session_id and previous.get('session_id') == session_id:
            evidence = accepted_activity_evidence(
                {'trace_id': previous.get('trace_id'), 'tool_result_sequence': previous.get('observations')},
                previous.get('query', ''), session_id)
        rows = evidence['observations'][0]['data'] if evidence and valid_inspection_evidence(evidence) else []
        from src.activity.family_contract import resolve_activity_family
        try:
            family = resolve_activity_family(domain_question.group('target'))
            matched = [row for row in rows if resolve_activity_family(row['requested_target']) == family]
        except (ValueError, TypeError, KeyError):
            matched = []
        if len(rows) != 1 or len(matched) != 1:
            return {'final_answer': '无法核对上一轮唯一分子和靶点的活性预测记录；请先完成对应预测。未重新计算。',
                    'status': 'not_calculated', 'success': False}
        row = matched[0]
        return {'final_answer': (f"上一轮 `{_cell(row['smiles'])}` 对 {_cell(row['requested_target'])} 的预测记录中，"
                '未提供经过验证的该结构适用域评估，因此无法判断它是否处于模型适用域内。'
                '需要模型定义的适用域方法、训练数据及相应阈值，不能仅凭已有预测分数判断；未重新计算。'),
                'status': 'partial', 'success': False, 'activity_evidence': evidence}
    inactive_question = re.fullmatch(
        r'\s*预测分类为无活性[，,]\s*是否意味着(?:这个|该)分子(?:一定|必然)没有\s*'
        r'(?P<target>[A-Za-z][A-Za-z0-9_-]*)\s*(?:的)?抑制作用[？?。]?\s*', query, re.I)
    if inactive_question:
        previous = history[-1].get('activity_evidence') if history else None
        evidence = None
        if isinstance(previous, dict) and session_id and previous.get('session_id') == session_id:
            evidence = accepted_activity_evidence(
                {'trace_id': previous.get('trace_id'), 'tool_result_sequence': previous.get('observations')},
                previous.get('query', ''), session_id)
        rows = evidence['observations'][0]['data'] if evidence and valid_inspection_evidence(evidence) else []
        from src.activity.family_contract import resolve_activity_family
        try:
            family = resolve_activity_family(inactive_question.group('target'))
            matched = [row for row in rows if resolve_activity_family(row['requested_target']) == family]
        except (ValueError, TypeError, KeyError):
            matched = []
        if len(rows) != 1 or len(matched) != 1 or matched[0].get('activity_class') != '无活性':
            return {'final_answer': '无法核对上一轮唯一分子、靶点及无活性分类；请先完成对应预测。未重新计算。',
                    'status': 'not_calculated', 'success': False}
        row = matched[0]
        answer = (f"不能。`{_cell(row['smiles'])}` 对 {_cell(row['requested_target'])} 的“无活性”"
                  '是模型根据分类阈值得出的预测，并不证明没有抑制作用或实验活性。'
                  '需要核查模型适用域、预测误差和实验条件，并通过合适的实验验证。')
        return {'final_answer': answer, 'status': 'completed', 'success': True, 'activity_evidence': evidence}
    last = history[-1] if history else {}
    previous = last.get('activity_evidence')
    owned = isinstance(previous, dict) and session_id and previous.get('session_id') == session_id
    order = last.get('activity_display_order', ()) if owned else ()
    current = last.get('activity_position') if order else None
    conversion = _PIC50_CONVERSION.fullmatch(query) or _PIC50_NATURAL_CONVERSION.fullmatch(query)
    request = None
    selector = None
    position = current
    if conversion:
        request = {'unit': conversion['unit'], 'explain': bool(conversion.groupdict().get('explain'))}
        selector = conversion.groupdict().get('smiles')
        if conversion.groupdict().get('position'):
            from .activity_evidence_inspection import batch_reply_position
            position = batch_reply_position(conversion['position'], current)
    elif owned and last.get('activity_conversion'):
        from .activity_evidence_inspection import batch_reply_position
        unit_change = _CHANGE_CONCENTRATION_UNIT.fullmatch(query)
        if unit_change:
            unit = unit_change['unit']
            request = {**last['activity_conversion'],
                       'unit': 'nM' if unit.casefold() in ('nm', '纳摩尔') else 'μM'}
        elif (position := batch_reply_position(query, current, choices=order)) is not None:
            request = last['activity_conversion']
        elif current is None and re.fullmatch(r'[A-Za-z0-9@+\-\[\]().=#\\/:%*]+', query.strip()):
            if _canonical(query.strip()) is not None:
                request, selector = last['activity_conversion'], query.strip()
    if request:
        evidence = accepted_activity_evidence(
            {'trace_id': previous.get('trace_id'), 'tool_result_sequence': previous.get('observations')},
            previous.get('query', ''), session_id) if owned else None
        if evidence is None or not valid_inspection_evidence(evidence):
            return {'final_answer': '无法确认上一轮的活性预测证据；请先完成对应分子和靶点的预测。',
                    'status': 'not_calculated', 'success': False}
        rows = evidence['observations'][0]['data']
        retained = {'activity_evidence': evidence, 'activity_conversion': request, 'activity_position': current}
        if selector is None and position is not None:
            if not 1 <= position <= len(order):
                return {**retained, 'final_answer': '无法确定要换算本批中的哪一个分子，请指定已列出的序号或 SMILES。',
                        'status': 'not_calculated', 'success': False}
            selector = order[position - 1]
        if selector is not None:
            selected = _canonical(selector)
            rows = [row for row in rows if selected is not None and _canonical(row.get('smiles')) == selected]
            if len(rows) != 1:
                return {**retained, 'final_answer': '上一轮没有该分子对应的可验证活性预测；请核对 SMILES。',
                        'status': 'not_calculated', 'success': False}
        elif len(rows) != 1:
            return {**retained, 'final_answer': '本批有多个分子，请指定要换算的分子序号或 SMILES。',
                    'status': 'not_calculated', 'success': False}
        row = rows[0]
        if row['smiles'] in order:
            retained['activity_position'] = order.index(row['smiles']) + 1
        return {**retained, **_converted_activity(row, **request)}
    species_question = re.fullmatch(
        r'(?:这个|刚才的|上一轮的)(?:(?:预测)?结果|预测值)能(?:否|不能|不可以|不能否|)说明\s*'
        r'(?P<smiles>该分子|这个分子|[A-Za-z0-9@+\-\[\]().=#\\/:%*]+)\s*对\s*人源\s*'
        r'(?P<target>[A-Za-z0-9-]+)\s*有活性(?:吗)?[？?]'
        r'(?:\s*请说明依据和限制[。.!！]?)?\s*', query, re.I)
    if species_question:
        previous = history[-1].get('activity_evidence') if history else None
        evidence = None
        if isinstance(previous, dict) and previous.get('session_id') == session_id and session_id:
            evidence = accepted_activity_evidence(
                {'trace_id': previous.get('trace_id'), 'tool_result_sequence': previous.get('observations')},
                previous.get('query', ''), session_id)
        rows = evidence['observations'][0]['data'] if evidence and valid_inspection_evidence(evidence) else []
        selector = species_question.group('smiles')
        canonical = _canonical(selector) if selector not in {'该分子', '这个分子'} else None
        target = species_question.group('target').casefold()
        matched = [row for row in rows if ((canonical is not None and _canonical(row['smiles']) == canonical)
                   or (selector in {'该分子', '这个分子'} and len(rows) == 1))
                   and str(row.get('requested_target', '')).casefold() == target]
        if len(matched) != 1 or matched[0].get('predicted_pIC50') is None:
            return {'final_answer': '缺少该分子和靶点对应的已验证活性预测；未重新计算。',
                    'status': 'not_calculated', 'success': False}
        row = matched[0]
        scope = (row.get('provenance') or {}).get('scope') or {}
        species = scope.get('species') if isinstance(scope, dict) else None
        scope_text = {'mixed': '模型范围为混合物种，不能将其视为人源特异预测。',
                      'human': '模型范围标注为人源，但预测不能证明实验活性。'}.get(
                          species, '模型的物种范围未明确为人源，不能将其视为人源特异预测。')
        disagreement = ('分类与回归预测不一致，需要复核。'
                        if row.get('classification_regression_consistent') is False else '')
        answer = (f"不能根据该结果确认 `{_cell(row['smiles'])}` 对 {_cell(row['requested_target'])} "
                  f"具有人源实验活性。预测 pIC50 为 {_cell(row['predicted_pIC50'])}；"
                  f"活性概率为 {_cell(row.get('activity_probability'))}。{scope_text}{disagreement}"
                  '该结果未证明模型适用域覆盖此结构；需要结合适用域评估及人源实验验证。')
        return {'final_answer': answer, 'status': 'partial', 'success': False, 'activity_evidence': evidence}
    proof = re.fullmatch(r'这是否证明\s+([^\s]+)\s+对\s+([^\s]+)\s+没有活性[？?。]?', query.strip())
    if proof:
        previous = history[-1].get('activity_evidence') if history else None
        evidence = None
        if isinstance(previous, dict) and previous.get('session_id') == session_id and session_id:
            result = {'trace_id': previous.get('trace_id'), 'tool_result_sequence': previous.get('observations')}
            evidence = accepted_activity_evidence(result, previous.get('query', ''), session_id)
        from src.activity.family_contract import resolve_activity_family
        try:
            canonical = _canonical(proof.group(1))
            family = resolve_activity_family(proof.group(2))
            rows = evidence['observations'][0]['data'] if evidence and valid_inspection_evidence(evidence) else []
            matched = [row for row in rows if _canonical(row['smiles']) == canonical
                       and resolve_activity_family(row['requested_target']) == family]
        except (ValueError, TypeError, KeyError):
            matched = []
        if len(matched) != 1:
            return {'final_answer': '缺少该分子和靶点对应的已验证活性记录；未重新计算。',
                    'status': 'not_calculated', 'success': False}
        return {'final_answer': f'对 `{proof.group(1)}` 和 `{proof.group(2)}` 的模型预测不能证明该分子没有实验活性；'
                '需要结合模型适用范围并通过实验验证。', 'status': 'completed', 'success': True,
                'activity_evidence': evidence}
    inspection = inspection_request(query, batch=last if order else None)
    concept = is_concept_explanation(query)
    if inspection is False and not concept:
        return {'final_answer': '本次只读追问包含无法对应到已有活性记录的要求；未重新计算。'
                '可查看上一轮的原始活性概率、pIC50、分类、一致性和排除原因；'
                '新增指标或其他靶点没有可对应证据，请拆分请求。',
                'status': 'not_calculated', 'success': False}
    if inspection is None and not _FOLLOWUP.fullmatch(query):
        return None
    if owned:
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
                positions = inspection.get('positions', ())
                if any(not 1 <= pos <= len(order) for pos in positions):
                    view = {'final_answer': '无法确定要查看本批中的哪一个分子，请指定已列出的序号或 SMILES。',
                            'status': 'not_calculated', 'success': False}
                    if order and valid_inspection_evidence(evidence):
                        view.update(activity_evidence=evidence,
                                    activity_position=last.get('activity_position'))
                        if inspection.get('brief'):
                            view['activity_brief'] = True
                    return view
                single_position = len(positions) == 1 and not inspection['selectors'] and not inspection['all']
                inspection['selectors'] += tuple(order[pos - 1] for pos in positions)
                view = inspection_answer(evidence, inspection)
                if inspection.get('brief') and 'activity_evidence' in view and not last.get('activity_conversion'):
                    view['activity_brief'] = True
                if single_position and 'activity_evidence' in view:
                    view['activity_position'] = positions[0]
                    if inspection.get('brief') and last.get('activity_conversion'):
                        view['activity_conversion'] = last['activity_conversion']
                return view
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
