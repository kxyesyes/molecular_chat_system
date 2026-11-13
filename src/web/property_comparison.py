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
    prefix = re.sub(r'\s*[（(]\s*SMILES\s*[:：=]\s*$', '', prefix, flags=re.I)
    clause = re.split(r"[,，;；。.!！?？\n]", prefix)[-1].strip()
    if positive_intent_text(clause) != clause:
        return None
    match = re.fullmatch(
        r"(?:(?:请|现在|本次|只|仅)\s*)*"
        r"(?:计算|分析|评估|预测|使用|用|比较|对比|(?:把|将)(?:分析)?对象改为|改为)\s*"
        r"(?P<label>[A-Za-z0-9\u4e00-\u9fff][A-Za-z0-9\u4e00-\u9fff _-]{0,79})"
        r"\s*", clause, re.I,
    )
    if match is None:
        return None
    label = match["label"].strip()
    if re.search(r"和|与|及|或|并|不是|而非|排除|\b(?:and|or|not)\b", label, re.I):
        return None
    return label


def recorded_descriptor(query, history, session_id):
    """Read a requested value from the immediately preceding, session-bound record."""
    if deictic_property_match(query):
        # The property workflow resolves this structure or asks which molecule to use.
        return None
    match = re.fullmatch(
        r'\s*(?:请问\s*|请\s*)?(?P<prior>(?:使用|根据|基于|查看)?\s*(?:刚才|上轮|上一轮|之前)的?'
        r'(?:(?:计算)?(?:结果|记录)[，,：:]\s*)?)?(?:(?P<subject>[\w-]{1,80})的\s*)?'
        r'(?P<metric>分子量|MW|log\s*P)\s*(?:分别)?(?:是多少|为多少)[？?。]?'
        r'\s*(?:(?:请)?(?:不要|不用|无需)重新计算[。.!！]?)?\s*', query, re.I)
    deictic = ('它', '该分子', '这个分子')
    if not match or (not match['prior'] and match['subject'] not in deictic):
        return None
    evidence = history[-1].get('property_evidence') if history else None
    key = 'logp' if match['metric'].lower().startswith('log') else 'molecular_weight'
    if (not session_id or not isinstance(evidence, dict) or evidence.get('session_id') != session_id
            or not evidence.get('trace_id') or not evidence.get('evidence_id')
            or not evidence.get('canonical_smiles') or type(evidence.get(key)) not in (int, float)
            or not math.isfinite(evidence[key]) or match['subject'] not in (
                None, *deictic, evidence.get('canonical_smiles'), evidence.get('molecule_label'))):
        return {'content': '上一轮没有可与该分子对应的性质记录，请核对分子名称或 SMILES。',
                'status': 'not_calculated'}
    value = (f"logP 为 {evidence[key]:.3f}" if key == 'logp'
             else f"分子量为 {evidence[key]:.2f} Da")
    return {'content': f"上一轮结构 `{evidence['canonical_smiles']}` 的计算{value}。",
            'status': 'completed', 'reused_evidence': evidence}


def corrected_property_query(query, history):
    """Resume a narrowly specified, immediately rejected property request."""
    batch = re.fullmatch(r'\s*第(?P<ordinal>一|二|1|2)个(?:改成|改为|更正为|写错了[，,]\s*(?:应该是|应为))\s*'
                         r'(?P<smiles>[^\s，,。；;]+)\s*[，,]\s*(?:再算一次|(?:请)?继续比较)[。.!！]?\s*', query)
    if batch and history and history[-1].get('agent_used') is False:
        field = r'(?:分子量|分子式|log\s*p|tpsa|qed|hba|hbd|mw)'
        previous = re.fullmatch(
            r'\s*(?:请\s*)?(?:比较|对比)\s*(?P<first>[^\s，,。；;]+)\s*(?:和|与)\s*'
            r'(?P<second>[^\s，,。；;]+)\s*的\s*' + field
            + r'(?:\s*(?:和|与|及|、|[,，])\s*' + field + r')*\s*[。.!！]?\s*',
            history[-1].get('user', ''), re.I)
        if previous:
            from src.agent.tools.base_tool import BaseMolecularTool
            from src.agent.tools.molecular_input import parse_molecular_smiles
            parser = BaseMolecularTool('property-correction', '')
            selected = 0 if batch['ordinal'] in ('一', '1') else 1
            originals = [previous['first'], previous['second']]
            valid = []
            for value in originals + [batch['smiles']]:
                try:
                    valid.append(parse_molecular_smiles('SMILES: ' + value, parser) == [value])
                except ValueError:
                    valid.append(False)
            if not valid[selected] and valid[1 - selected] and valid[2]:
                start, end = previous.span('first' if selected == 0 else 'second')
                original_query = history[-1]['user']
                return original_query[:start] + batch['smiles'] + original_query[end:]
    if not history or not re.match(r"\s*(?:更正为|纠正为|correct(?:ed)?\s+to\b)", query, re.I):
        return query
    current = InputValidator().analyze_molecular_input(query)
    if len(current.valid_smiles) != 1 or current.blocks_execution:
        return query
    # Explicit new instructions always win. Only an otherwise bare correction
    # (optionally "please continue") may inherit the rejected operation.
    correction = (r"\s*(?:更正为|纠正为|correct(?:ed)?\s+to)\s*"
                  + re.escape(current.valid_smiles[0])
                  + r"\s*(?:[,，]\s*)?(?:(?:请)?继续(?:(?:原来|之前|原先)的计算)?|(?:please\s+)?continue)?[。.]?\s*")
    if not re.fullmatch(correction, query, re.I):
        return query
    previous = history[-1]
    old_query = previous.get("user", "")
    old = InputValidator().analyze_molecular_input(old_query)
    if previous.get("agent_used") is not False:
        return query
    if len(old.potential_smiles) == 1 and old.blocks_execution:
        old_smiles = old.potential_smiles[0]
    else:
        match = re.match(r"\s*(?:请\s*)?(?:只|仅)?(?:计算|分析|评估|查看)\s+"
                         r"(?P<smiles>[^\s，,。；;]+)\s*的\s*", old_query, re.I)
        if not match or not _pure_property_operation(old_query, match['smiles']):
            return query
        from src.agent.tools.base_tool import BaseMolecularTool
        from src.agent.tools.molecular_input import parse_molecular_smiles
        from src.system.molecular_input_parser import MolecularInputInvalid
        old_smiles = match['smiles']
        try:
            parse_molecular_smiles('SMILES: ' + old_smiles, BaseMolecularTool('property-correction', ''))
        except MolecularInputInvalid:
            pass
        else:
            return query
    if not _pure_property_operation(old_query, old_smiles):
        return query
    return old_query.replace(old_smiles, current.valid_smiles[0])


def deictic_property_match(query):
    fields = (r'(?:分子量|分子式|log\s*p|tpsa|qed|hba|hbd|可旋转键数)'
              r'(?:\s*(?:和|与|及|、|[,，])\s*'
              r'(?:分子量|分子式|log\s*p|tpsa|qed|hba|hbd|可旋转键数))*')
    return re.fullmatch(r'\s*(?:请)?(?:计算|查看)?\s*(?:刚才|上一轮)?(?:的)?(?:这个|该)分子的\s*('
                        + fields + r')\s*(?:是多少|分别是多少)?[？?。.!！]?\s*', query, re.I)


def contextual_property_query(query, history, session_id):
    """Resolve a single deictic descriptor request against the last accepted result."""
    match = deictic_property_match(query)
    lipinski = re.fullmatch(
        r'\s*(?:(?:根据|基于)(?:刚才|上一轮)的结果[，,]\s*)?'
        r'(?:请\s*判断\s*)?(?:这个分子|该分子|它)\s*(?:是否)?(?:符合|满足)\s*'
        r'Lipinski\s*(?:五规则|规则)(?:吗)?[？?。.!！]?'
        r'(?:\s*如果还缺少指标[，,]\s*请先计算再判断[。.!！?？]?)?\s*',
        query, re.I,
    )
    if not (match or lipinski) or not history:
        return query
    previous = history[-1]
    evidence = previous.get('property_evidence')
    if (previous.get('agent_used') is not True or not isinstance(evidence, dict)
            or evidence.get('session_id') != session_id
            or not evidence.get('trace_id') or not evidence.get('evidence_id')):
        return query
    from rdkit import Chem
    smiles = evidence.get('canonical_smiles')
    molecule = Chem.MolFromSmiles(smiles) if isinstance(smiles, str) else None
    if molecule is None or Chem.MolToSmiles(molecule) != smiles:
        return query
    if lipinski:
        return f'评估 SMILES: {smiles} 的 Lipinski 规则。'
    return f'计算 {smiles} 的 {match.group(1)}。'


def accepted_property_evidence(result, query, session_id):
    """Project detached, accepted runtime observations, not assistant text."""
    if not isinstance(result, Mapping):
        return None
    sequence = result.get("tool_result_sequence", [])
    if not isinstance(sequence, list):
        return None
    observations = [item for item in sequence if type(item) is dict
                    and item.get("tool_name") == "property_calculator"]
    if not observations:
        observations = [item for item in sequence if type(item) is dict
                        and item.get("tool_name") == "drug_likeness_assessment"]
    if len(observations) != 1:
        return None
    item = observations[0]
    provenance, quality = item.get("provenance"), item.get("quality")
    if (item.get("status") != "succeeded" or item.get("success") is not True
            or item.get("error") is not None
            or type(provenance) is not dict or type(quality) is not dict
            or quality.get("validated") is not True
            or not quality.get("evidence_id")
            or provenance.get("tool_name") != item["tool_name"]
            or provenance.get("demo_mode") is not False
            or provenance.get("fallback_used") is not False
            or quality.get("demo_mode") or quality.get("fallback_used")
            or not provenance.get("input_digest") or not result.get("trace_id")):
        return None
    data = item.get("data")
    if not isinstance(data, list) or len(data) != 1 or type(data[0]) is not dict:
        return None
    row = data[0]
    properties = (row.get("properties") if item["tool_name"] == "property_calculator"
                  else row.get("assessment", {}).get("molecular_properties"))
    if type(properties) is not dict:
        return None
    values = {key: properties.get(key) for key in ("molecular_weight", "logp")}
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values.values()):
        return None
    from src.system.molecular_input_parser import parse_molecular_inputs
    from src.agent.tools.base_tool import BaseMolecularTool
    try:
        identity = parse_molecular_inputs(query, BaseMolecularTool('property_evidence', ''))
    except ValueError:
        return None
    if len(identity) != 1:
        return None
    from rdkit import Chem
    molecule = Chem.MolFromSmiles(row.get("smiles", ""))
    if molecule is None or Chem.MolToSmiles(molecule) != identity[0].canonical_smiles:
        return None
    return {
        **values, "smiles": row["smiles"], "canonical_smiles": Chem.MolToSmiles(molecule),
        "molecule_label": _adjacent_molecule_label(query, identity[0].smiles),
        "session_id": session_id, "trace_id": result["trace_id"],
        "evidence_id": quality["evidence_id"], "tool_version": provenance.get("tool_version"),
    }


def accepted_batch_property_evidence(result, query, session_id):
    """Keep a complete, identity-matched batch of validated descriptors."""
    from src.agent.candidate_evidence_followup import _accepted
    from src.agent.tools.base_tool import BaseMolecularTool
    from src.system.molecular_input_parser import expand_labeled_comparison, parse_molecular_inputs
    from rdkit import Chem

    if not session_id or not isinstance(result, Mapping) or not result.get('trace_id'):
        return None
    sequence = result.get('tool_result_sequence')
    if not isinstance(sequence, list) or len(sequence) != 1 or not _accepted(sequence[0], 'property_calculator'):
        return None
    try:
        identities = parse_molecular_inputs(expand_labeled_comparison(query), BaseMolecularTool('property_evidence', ''))
    except ValueError:
        return None
    data = sequence[0].get('data')
    expected = [item.canonical_smiles for item in identities]
    if len(expected) < 2 or len(set(expected)) != len(expected) or not isinstance(data, list) or len(data) != len(expected):
        return None
    rows = {}
    for row in data:
        if not isinstance(row, dict) or not isinstance(row.get('smiles'), str):
            return None
        molecule = Chem.MolFromSmiles(row['smiles'])
        canonical = Chem.MolToSmiles(molecule) if molecule is not None else None
        properties = row.get('properties')
        if canonical not in expected or canonical in rows or not isinstance(properties, dict):
            return None
        values = {key: properties.get(key) for key in ('molecular_weight', 'logp', 'hba', 'hbd')}
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values.values()):
            return None
        rows[canonical] = values
    return {'session_id': session_id, 'trace_id': result['trace_id'],
            'evidence_id': sequence[0]['quality']['evidence_id'],
            'rows': [{'smiles': smiles, **rows[smiles]} for smiles in expected]}


def batch_lipinski_followup(query, history, session_id):
    """Evaluate the four Ro5 thresholds using the immediately prior batch."""
    previous = history[-1] if history else {}
    pending = previous.get('batch_lipinski_pending')
    selection = re.fullmatch(
        r'\s*(?:请)?(?:前(?P<first>两个|两种)|第(?P<a>[一二三四五六七八九十\d]+)个(?:和|与|、)第(?P<b>[一二三四五六七八九十\d]+)个)[。.!！?？]?\s*',
        query)
    structure_selection = re.fullmatch(
        r'\s*(?:请)?(?:选择\s*)?(?:SMILES\s*[:：=]\s*)?(?P<one>[A-Za-z0-9@+\-\[\]()\\/=#$%.*:]+?)'
        r'\s*(?:和|与|、)\s*(?:SMILES\s*[:：=]\s*)?(?P<two>[A-Za-z0-9@+\-\[\]()\\/=#$%.*:]+?)'
        r'[。.!！?？]?\s*', query, re.I)
    if (selection or structure_selection) and isinstance(pending, dict):
        if (pending.get('session_id') != session_id or not pending.get('trace_id')
                or not pending.get('evidence_id') or not pending.get('rows')):
            return {'content': '无法核对待选择的分子记录，请重新提交。', 'status': 'not_calculated'}
        numbers = {'一': 1, '二': 2, '三': 3, '四': 4, '五': 5, '六': 6, '七': 7, '八': 8, '九': 9, '十': 10}
        def ordinal(value):
            return int(value) if value.isdecimal() else numbers.get(value, 0)
        if structure_selection:
            from src.agent.candidate_evidence_followup import _canonical
            selected = [_canonical(structure_selection.group(name)) for name in ('one', 'two')]
            available = [row['smiles'] for row in pending['rows']]
            indices = tuple(available.index(value) + 1 if value in available else 0 for value in selected)
        else:
            indices = (1, 2) if selection.group('first') else (ordinal(selection.group('a')), ordinal(selection.group('b')))
        if len(set(indices)) != 2 or any(index < 1 or index > len(pending['rows']) for index in indices):
            return {'content': f"请从上一轮的 {len(pending['rows'])} 个结构中指定两个不同的结构或序号。",
                    'status': 'not_calculated', 'pending': pending}
        evidence = {**pending, 'rows': [pending['rows'][index - 1] for index in indices]}
        previous = {'agent_used': True, 'batch_property_evidence': evidence}
        query = '根据刚才的结果，分别判断这两个分子是否符合 Lipinski 五规则。'
    match = re.fullmatch(
        r'\s*(?:(?:根据|基于)(?:刚才|上一轮)的结果[，,]\s*)?(?:请)?分别判断'
        r'(?P<subject>这两个|这两种|这些)分子是否符合\s*Lipinski\s*(?:五规则|规则)[。.!！?？]?\s*',
        query, re.I)
    if not match:
        return None
    evidence = previous.get('batch_property_evidence')
    if (previous.get('agent_used') is not True or not isinstance(evidence, dict)
            or evidence.get('session_id') != session_id or not evidence.get('trace_id')
            or not evidence.get('evidence_id') or not evidence.get('rows')):
        return {'content': '无法核对上一轮完整的分子性质记录，请先比较这些分子的性质。', 'status': 'not_calculated'}
    if match.group('subject') != '这些' and len(evidence['rows']) != 2:
        return {'content': f"上一轮包含 {len(evidence['rows'])} 个分子，请按表格顺序选择（例如“前两个”），或给出两个 SMILES。",
                'status': 'not_calculated', 'pending': evidence}
    lines = ['| SMILES | MW (Da) | logP | HBA | HBD | 阈值检查 |', '|---|---:|---:|---:|---:|---|']
    uncertain = False
    for row in evidence['rows']:
        checks = [('molecular_weight', 500, 0.005), ('logp', 5, 0.0005), ('hba', 10, 0), ('hbd', 5, 0)]
        ambiguous = any(tolerance and abs(row[key] - limit) <= tolerance for key, limit, tolerance in checks)
        violations = sum(row[key] > limit for key, limit, _ in checks)
        verdict = '舍入导致边界未确定' if ambiguous else (f'超过 {violations} 项阈值' if violations else '四项均未超过阈值')
        uncertain |= ambiguous
        lines.append(f"| `{row['smiles']}` | {row['molecular_weight']:.2f} | {row['logp']:.3f} | {row['hba']} | {row['hbd']} | {verdict} |")
    lines.append('阈值为 MW ≤ 500、logP ≤ 5、HBA ≤ 10、HBD ≤ 5；规则筛选不能证明口服生物利用度或疗效。')
    return {'content': '\n'.join(lines), 'status': 'partial' if uncertain else 'completed',
            'reused_evidence': evidence}


def batch_logp_followup(query, history, session_id):
    """Compare two descriptors from the immediately prior accepted batch."""
    if not re.fullmatch(
        r'\s*(?:刚才|之前|上一轮)(?:比较的)?(?:这)?(?:两个|两种)分子[，,]?\s*'
        r'哪个\s*log\s*p\s*(?:更高|较高)[？?，,]\s*差值(?:是)?多少[？?。.!！]?'
        r'(?:\s*仅根据(?:刚才|之前|上一轮)的计算回答[。.!！]?)?\s*', query, re.I):
        return None
    previous = history[-1] if history else {}
    evidence = previous.get('batch_property_evidence')
    if ((previous.get('agent_used') is not True and previous.get('batch_property_evidence_reused') is not True)
            or not isinstance(evidence, dict) or evidence.get('session_id') != session_id
            or not evidence.get('trace_id') or not evidence.get('evidence_id')
            or not isinstance(evidence.get('rows'), list) or len(evidence['rows']) != 2):
        return {'content': '无法核对上一轮的两条分子性质记录，请重新比较两个 SMILES。',
                'status': 'not_calculated'}
    first, second = evidence['rows']
    values = [row.get('logp') for row in (first, second)]
    if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
        return {'content': '上一轮缺少可比较的 LogP 数据，请重新计算。', 'status': 'not_calculated'}
    difference = abs(values[0] - values[1])
    if difference < 0.001:
        conclusion = '两者显示值相近，无法根据当前精度确定高低。'
    else:
        higher = first if values[0] > values[1] else second
        conclusion = f"`{higher['smiles']}` 的 LogP 更高，差值为 {difference:.3f}。"
    return {'content': f"`{first['smiles']}`：{values[0]:.3f}；`{second['smiles']}`：{values[1]:.3f}。\n\n{conclusion}",
            'status': 'completed', 'reused_evidence': evidence}


def descriptor_answer(query, current, history, session_id):
    """Render only the explicitly limited MW/logP request, with source IDs."""
    result = descriptor_result(query, current, history, session_id)
    return result["final_answer"] if result is not None else None


def batch_descriptor_comparison(rows, metrics):
    """Describe two accepted rows without inferring unmeasured properties."""
    if len(rows) != 2:
        return ''
    fields = {'mw': ('分子量', 'molecular_weight'), 'logp': ('logP', 'logp'),
              'hba': ('HBA', 'hba'), 'hbd': ('HBD', 'hbd'),
              'tpsa': ('TPSA', 'tpsa'), 'qed': ('QED', 'qed'),
              'rotatable_bonds': ('可旋转键数', 'rotatable_bonds')}
    comparisons = []
    for metric in metrics:
        if metric not in fields:
            continue
        label, key = fields[metric]
        values = [row['properties'].get(key) for row in rows]
        if any(type(value) not in (int, float) or not math.isfinite(value) for value in values):
            continue
        relation = ('第一个结构较高' if values[0] > values[1] else
                    '第二个结构较高' if values[1] > values[0] else
                    '显示值相同，舍入精度不足以判断差异' if metric in {'mw', 'logp', 'tpsa', 'qed'}
                    else '两者相同')
        comparisons.append(f'{label}：{relation}')
    return '；'.join(comparisons) + '。' if comparisons else ''


def descriptor_result(query, current, history, session_id):
    """Project answer coverage separately from successful tool execution."""
    positive = positive_intent_text(query)
    prior_comparison = re.search(
        r'[，,]\s*(?:并|以及)?\s*与(?:刚才|之前|上一轮)(?:的)?(?:分子|结果)(?:比较|对比)\s*[。.!！?？]?\s*$',
        positive,
    )
    if (not (restricted_properties(query) or prior_comparison)
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
    # Summary presentation renders structured warnings separately above the
    # table; retain the issue in the full answer without repeating it there.
    summary_answer = '\n'.join(lines)
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
