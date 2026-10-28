"""Read-only activity inspection language and report, never an executor.

Complete clauses select a view of already accepted observations. Unrecognized
no-recalculation inspection requests fail here rather than becoming new science.
"""
import re


_SMILES = r'[A-Za-z0-9@+\-\[\]().=#\\/:%*]+'
_PRIOR_ZH = r'(?:(?:上一轮|上次|刚才|上述)(?:的)?(?:比较|预测|计算)?(?:结果)?(?:中)?(?:的)?\s*)?'
_SUBJECT_ZH = rf'(?:它们?|该分子|这些候选|所有候选|全部候选|(?:候选|分子)?\s*({_SMILES}))'
_FIELDS_ZH = (r'(?:原始\s*)?(?:活性概率|pIC50|活性分类|分类结果|一致性(?:检查)?(?:结果)?|'
              r'排除原因|模型来源|(?:预测)?(?:结果|数值))')
_FIELDS_EN = (r'(?:(?:original|raw)\s+)?(?:activity\s+probabilit(?:y|ies)|pIC50|'
              r'activity\s+class(?:es)?|consistency(?:\s+checks?)?|exclusion\s+reasons|'
              r'model\s+provenance|predictions?|results?|values?)')
_WHY_ZH = re.compile(rf'(?:为什么|为何)\s*{_PRIOR_ZH}{_SUBJECT_ZH}\s*'
                     r'(?:被排除|未参与(?:比较|排序)|没有参与(?:比较|排序))', re.I)
_WHY_EN = re.compile(rf'why\s+(?:was|were|is)\s+({_SMILES})\s+excluded'
                     r'(?:\s+from\s+(?:the\s+)?(?:previous|last)\s+comparison)?', re.I)
_SHOW_ZH = re.compile(rf'(?:请\s*)?(?:列出|展示|显示|提供|查看)\s*{_PRIOR_ZH}'
                      rf'(?:{_SUBJECT_ZH}\s*的?\s*)?{_FIELDS_ZH}'
                      rf'(?:\s*(?:和|与|及|以及|、)\s*{_FIELDS_ZH})*', re.I)
_SHOW_EN = re.compile(rf'(?:please\s+)?(?:show|list|display|provide)\s+'
                      rf'(?:(?:its|their|the|(?:previous|last)(?:\s+comparison)?\s+results?\s*)\s+)?'
                      rf'{_FIELDS_EN}(?:\s+and\s+{_FIELDS_EN})*', re.I)
_PRIOR_VALUE = re.compile(
    r'(?:我)?(?:上一轮|上次|刚才)(?:的)?\s*(?:预测的?)?pIC50\s*(?:是)?(?:多少|什么)|'
    r'what\s+(?:was|is)\s+(?:my|the)\s+(?:previous|last)\s+(?:predicted\s+)?pIC50', re.I)
_EXPLAIN = re.compile(
    r'(?:请\s*)?(?:并\s*)?解释(?:分类(?:与|和)回归不一致(?:意味着什么|的含义|的原因)?|'
    r'(?:上一轮|上次|刚才|上述)(?:的)?活性(?:结果|预测))|'
    r'(?:and\s+)?(?:please\s+)?explain\s+(?:the\s+)?(?:'
    r'classification(?:/|\s+and\s+)regression\s+(?:disagreement|inconsistency)|'
    r'(?:previous|last)\s+activity\s+(?:results?|predictions?))', re.I)
_NO_RECALCULATE = re.compile(
    r'(?:请\s*)?(?:不要|不用|无需|不必)(?:再|重新)?(?:计算|重算|预测|运行科学工具)|'
    r'(?:please\s+)?(?:do\s+not|don[’\x27]t)\s+(?:recalculate|recompute|rerun(?:\s+(?:tools|predictions|calculations))?)|'
    r'without\s+(?:recalculating|recomputing|rerunning(?:\s+tools)?)', re.I)
_DISCLAIMER = re.compile(
    r'(?:也)?(?:请)?不要把预测当作实验结论|'
    r'(?:model\s+)?predictions?\s+are\s+not\s+experimental\s+(?:results?|conclusions?)', re.I)
_ACTIVITY_FIELDS = re.compile(
    r'活性|pIC50|分类|回归|一致性|排除|模型来源|'
    r'\b(?:activity|excluded|exclusion|classification|regression|consistency)\b|model\s+provenance', re.I)


def split_concept_prior_value(query):
    """Separate only fully recognized concept + prior-value requests."""
    from .contracts.concept_request import is_concept_explanation

    if not isinstance(query, str):
        return None
    clauses = [part.strip() for part in re.split(r'[，,；;。!?！？\n]|\.(?=\s|$)', query)
               if part.strip()]
    prior = [clause for clause in clauses if _PRIOR_VALUE.fullmatch(clause)]
    concepts = [clause for clause in clauses if not _PRIOR_VALUE.fullmatch(clause)]
    concept_query = '. '.join(concepts)
    if len(prior) != 1 or not is_concept_explanation(concept_query):
        return None
    return concept_query, prior[0]


def inspection_request(query):
    """None: other intent; dict: supported selection; False: denied inspection.

    A selector is a complete user-supplied structure, not a molecular name to
    resolve. Canonicalization/membership checks happen against validated data.
    """
    if not isinstance(query, str):
        return None
    clauses = [part.strip() for part in re.split(r'[，,；;。!?！？\n]|\.(?=\s|$)', query)
               if part.strip()]
    selectors, recognized, unknown, all_candidates = [], False, False, False
    activity_inspection = False
    for clause in clauses:
        match = next((match for pattern in (_WHY_ZH, _WHY_EN, _SHOW_ZH, _SHOW_EN)
                      if (match := pattern.fullmatch(clause))), None)
        if match is not None:
            recognized = True
            activity_inspection = activity_inspection or match.re in (_WHY_ZH, _WHY_EN) or bool(
                _ACTIVITY_FIELDS.search(clause))
            selectors.extend(value for value in match.groups() if value is not None)
            all_candidates = all_candidates or bool(re.search(r'所有候选|全部候选|这些候选|它们', clause))
        elif _EXPLAIN.fullmatch(clause) or _PRIOR_VALUE.fullmatch(clause):
            recognized = True
            activity_inspection = True
        elif not (_NO_RECALCULATE.fullmatch(clause) or _DISCLAIMER.fullmatch(clause)):
            unknown = True
    # Bind the domain to an affirmative inspection clause, not an unrelated
    # "do not recalculate activity" qualifier on property work.
    # Only this restrictive intent view omits prohibition tails. The admission
    # grammar above still checks every original clause, including unknown text.
    intent_clauses = [_NO_RECALCULATE.split(clause, maxsplit=1)[0].strip() for clause in clauses]
    if recognized and not unknown and activity_inspection:
        return {'selectors': tuple(selectors), 'all': all_candidates}
    # Keep this guard in the evidence-inspection domain. Quoting a prohibition
    # in unrelated chat must not disable scientific work in subsequent turns.
    inspection_intent = any(_ACTIVITY_FIELDS.search(clause) and (
        re.search(r'上一轮|上次|刚才|上述|\b(?:previous|last)\b', clause, re.I) or
        re.match(r'(?:请\s*)?(?:为什么|为何|解释|列出|展示|显示|提供|查看)|'
                 r'(?:please\s+)?(?:why|explain|show|list|display|provide)\b', clause, re.I))
        for clause in intent_clauses)
    # Permission detection is independent of the closed clause grammar. A
    # qualifier after a prohibition cannot restore permission to run tools.
    no_recalculation = _NO_RECALCULATE.search(query)
    return False if no_recalculation and inspection_intent else None


def valid_inspection_evidence(evidence):
    """Check row/domain integrity before display or retaining view authority."""
    from src.agent.tooling.activity_contract import ActivityRawOutput

    try:
        observation = evidence['observations'][0]
        for row in observation['data']:
            # Failed rows can legitimately lack model provenance, but malformed
            # containers must not crash display or acquire retained authority.
            provenance = row.get('provenance')
            if provenance is not None:
                if not isinstance(provenance, dict):
                    raise ValueError('invalid row provenance')
                models = provenance.get('models')
                if models is not None and (not isinstance(models, dict) or any(
                        models.get(task) is not None and not isinstance(models[task], dict)
                        for task in ('classification', 'regression'))):
                    raise ValueError('invalid row models')
        # A historical quality flag is not a substitute for the current domain
        # contract. Validate a view only; never serialize normalized values back.
        # The detached legacy envelope has already passed source/quality binding
        # in accepted_activity_evidence. Its reduced provenance is not a raw
        # ToolProvenance schema; validate row model provenance and stages here.
        ActivityRawOutput.model_validate({key: observation[key] for key in
            ('tool_name', 'success', 'status', 'data', 'error', 'quality', 'warnings')})
    except (ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError):
        return False
    return True


def inspection_answer(evidence, selection):
    """Render immutable original rows, including excluded/partial predictions."""
    from .evidence_followup import _canonical, _cell, _exclusion
    from src.activity.family_contract import LABEL_THRESHOLD, PROBABILITY_THRESHOLD

    if not valid_inspection_evidence(evidence):
        return {'final_answer': '上一轮活性记录未通过完整性复核，无法安全解读原始预测；未重新计算，也未沿用该证据。',
                'status': 'not_calculated', 'success': False}
    observation = evidence['observations'][0]
    selectors = selection['selectors']
    canonical = [_canonical(value) for value in selectors]
    if any(value is None or value not in evidence['smiles'] for value in canonical):
        names = '、'.join(f'`{_cell(value)}`' for value in selectors)
        return {'final_answer': f'缺少所选分子 {names} 在上一轮中的可对应活性证据；未改用其他候选，也未重新计算。',
                'status': 'not_calculated', 'success': False}
    selected = set(canonical) if canonical and not selection['all'] else set(evidence['smiles'])
    rows = [row for row in observation['data'] if _canonical(row['smiles']) in selected]
    lines = ['活性结果解读（复用上一轮证据）', '',
             '仅查看已验证的原始记录，未重新运行科学工具。模型预测非实验结论；需复核的值不能作为已确认活性。']
    partial = observation['status'] == 'partial'
    for row in rows:
        reason = _exclusion(row)
        partial = partial or reason is not None
        consistency = row.get('classification_regression_consistent')
        lines.extend(['', f"候选 `{_cell(row['smiles'])}` · 靶点 `{_cell(row.get('requested_target'))}`",
                      '', '| 原始字段 | 记录值 |', '|---|---|'])
        fields = [('活性概率', row.get('activity_probability')),
                  ('预测 pIC50', row.get('predicted_pIC50')),
                  ('活性分类', row.get('activity_class')),
                  ('分类与回归一致性', '不一致（false）' if consistency is False else
                   '一致（true）' if consistency is True else '未确定'),
                  ('记录状态', row.get('status'))]
        for label, value in fields:
            lines.append(f"| {label} | {_cell(value) if value is not None else '不可用'} |")
        lines.append('排除原因：' + reason + '。原始预测保留。' if reason else
                     '该候选未因一致性检查被排除；本次仅查看结果，不重新排序。')
        if (row.get('label_threshold') == LABEL_THRESHOLD
                and row.get('probability_threshold') == PROBABILITY_THRESHOLD):
            lines.append(f'本模型契约：分类概率阈值为 {PROBABILITY_THRESHOLD}，'
                         f'回归 pIC50 的活性标签阈值为 {LABEL_THRESHOLD}。'
                         '分类与回归分别预测；不一致表示两者给出的活性标签不同，'
                         '需要复核模型适用范围并用实验验证，不能据此确认实验活性或选择其中一个值当作真值。')
        else:
            partial = True
            lines.append('来源未提供本功能支持的阈值契约，不能进一步解释一致性；未补造阈值。')
        models = (row.get('provenance') or {}).get('models', {})
        for task, label in (('classification', '分类模型'), ('regression', '回归模型')):
            model = models.get(task, {}) if isinstance(models, dict) else {}
            model_id = model.get('model_id') if isinstance(model, dict) else None
            if model_id is not None:
                lines.append(f'{label}：`{_cell(model_id)}`。')
    # Model identifiers remain in the full audit view, not the result body.
    summary = '\n'.join(line for line in lines
                        if not line.startswith(('分类模型：', '回归模型：')))
    lines.extend(['', f"来源：trace `{_cell(evidence['trace_id'])}`；"
                  f"证据 `{_cell(observation['quality']['evidence_id'])}`。"])
    content = '\n'.join(lines)
    return {'final_answer': content, 'summary_answer': summary,
            'status': 'partial' if partial else 'completed', 'partial': partial,
            'success': not partial, 'activity_evidence': evidence}
