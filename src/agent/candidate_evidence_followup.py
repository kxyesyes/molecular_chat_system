"""Read-only follow-up over target-design candidate evidence.

This module never calls a scientific tool.  It only accepts validated
observations already produced in the current Web session and explains when a
requested evidence-weighted ranking is incomplete.
"""
from __future__ import annotations

from copy import deepcopy
import math
import re
from collections import OrderedDict

from rdkit import Chem

from src.agent.persistence.redaction import contains_secret_material
from src.agent.planning.request_parsing import positive_intent_text


_MAX_CANDIDATES = 32
_RANKING_REQUEST = re.compile(r"(?:重新|再次|再)?\s*(?:排序|排名|重排|rank(?:ing)?)", re.I)
_CANDIDATE_CONTEXT = re.compile(r"候选|分子|compound|candidate|molecule|ADMET|性质|evidence", re.I)
_GENERATION = re.compile(r"生成|设计|优化|generate|design|optimi[sz]", re.I)
_OTHER_RANKING_DOMAIN = re.compile(
    r"反向|寻靶|候选靶点|活性|pIC50|IC50|\b(?:reverse|targets?|activity|potency)\b", re.I)
_REUSE_REQUEST = re.compile(
    r"刚才|之前|上一轮|已返回|已有|重新排序|再次排序|重排|"
    r"\b(?:previous|prior|earlier|existing|returned|rerank|re-rank)\b", re.I)


def _canonical(value):
    if not isinstance(value, str) or not value or len(value) > 8192:
        return None
    try:
        molecule = Chem.MolFromSmiles(value)
        return Chem.MolToSmiles(molecule) if molecule is not None else None
    except (TypeError, ValueError):
        return None


def _accepted(item, tool_name):
    if not isinstance(item, dict) or item.get("tool_name") != tool_name:
        return False
    quality, provenance = item.get("quality"), item.get("provenance")
    return (item.get("status") == "succeeded" and item.get("success") is True
            and item.get("error") is None and isinstance(quality, dict)
            and quality.get("validated") is True and isinstance(quality.get("evidence_id"), str)
            and isinstance(provenance, dict) and provenance.get("tool_name") == tool_name
            and bool(provenance.get("input_digest"))
            and not quality.get("demo_mode") and not quality.get("fallback_used")
            and provenance.get("demo_mode") is not True
            and provenance.get("fallback_used") is not True)


def _safe_number(value):
    return value if type(value) in (int, float) and math.isfinite(value) else None


def hydrophobic_comparison(evidence, session_id):
    """Compare only two session-bound, accepted calculated logP observations."""
    rows = evidence.get('candidates') if isinstance(evidence, dict) else None
    if (not session_id or not isinstance(evidence, dict) or evidence.get('session_id') != session_id
            or not evidence.get('trace_id') or not isinstance(rows, list) or len(rows) != 2
            or any(not isinstance(row, dict) or not isinstance(row.get('canonical_smiles'), str)
                   or not isinstance(row.get('properties'), dict)
                   or _safe_number(row['properties'].get('logp')) is None
                   or not any(isinstance(source, dict) and source.get('tool_name') == 'property_calculator'
                              and source.get('evidence_id') for source in row.get('sources', []))
                   for row in rows)):
        return None
    left, right = rows
    a, b = left['properties']['logp'], right['properties']['logp']
    values = f"`{left['canonical_smiles']}` 的计算 logP 为 {a:.6g}，`{right['canonical_smiles']}` 为 {b:.6g}。"
    if a == b:
        comparison = '显示的 logP 相同，舍入精度不足以判断亲脂性差异。'
    else:
        winner = left if a > b else right
        comparison = f"按计算 logP 比较，`{winner['canonical_smiles']}` 更亲脂。"
    return values + comparison + '计算 logP 不是水溶解度测量；电离状态和溶液条件也会影响实际表现。'


def solubility_prediction_comparison(evidence, session_id, requested):
    rows = evidence.get('candidates') if isinstance(evidence, dict) else None
    if (not session_id or not isinstance(evidence, dict) or evidence.get('session_id') != session_id
            or not evidence.get('trace_id') or not isinstance(rows, list) or len(rows) != 2
            or not isinstance(requested, list) or len(requested) != 2
            or [row.get('canonical_smiles') for row in rows] != requested):
        return None
    endpoints = []
    for row in rows:
        if not any(isinstance(source, dict) and source.get('tool_name') == 'admet_predictor'
                   and source.get('evidence_id') for source in row.get('sources', [])):
            return None
        endpoint = row.get('admet', {}).get('endpoints', {}).get('Solubility_AqSolDB')
        if (not isinstance(endpoint, dict) or endpoint.get('unit') != 'log(mol/L)'
                or endpoint.get('task_type') != 'regression' or _safe_number(endpoint.get('value')) is None):
            return None
        endpoints.append(endpoint['value'])
    a, b = endpoints
    values = f"`{requested[0]}`：{a:.6g}；`{requested[1]}`：{b:.6g} log(mol/L)。"
    if a == b:
        return values + '显示的预测值相同，舍入精度不足以判断差异；结果不是实测值。'
    winner = requested[0] if a > b else requested[1]
    return (values + f'按该模型预测，`{winner}` 的水溶解度更高；预测值不是实测值。'
            '本次未评估模型适用域，也未指定 pH 等测量条件。')


def _add_source(row, item):
    source = {"tool_name": item["tool_name"], "evidence_id": item["quality"]["evidence_id"]}
    row.setdefault("sources", []).append(source)


def accepted_generated_candidate_evidence(result, query, session_id):
    """Bind one validated generation-only candidate to its requested target."""
    from src.agent.contracts.candidates import CandidateSet
    from src.agent.contracts.target_request import analyze_target_request
    from src.agent.validators.molecule_candidates import CandidateValidationUnavailable
    from src.agent.validators.result_validator import AgentResultValidator
    if not isinstance(result, dict) or not session_id or not result.get('trace_id'):
        return None
    sequence = result.get('tool_result_sequence')
    if not isinstance(sequence, list) or any(isinstance(item, dict) and item.get('tool_name') == 'activity_predictor' for item in sequence):
        return None
    generated = [item for item in sequence if _accepted(item, 'llm_molecular_generator')]
    target = analyze_target_request(query)
    if len(generated) != 1 or target.needs_clarification:
        return None
    try:
        candidates = CandidateSet.from_dict(generated[0]['data'])
        AgentResultValidator._revalidate_candidate_set(candidates, trusted_checkpoint=False)
    except (ValueError, TypeError, CandidateValidationUnavailable):
        return None
    if len(candidates.candidates) != 1:
        return None
    return {'session_id': session_id, 'trace_id': result['trace_id'], 'target': target.targets[0],
            'smiles': candidates.candidates[0].canonical_smiles}


def accepted_candidate_evidence(result, query, session_id):
    """Detach validated candidate/property/ADMET observations for later display."""
    if not isinstance(result, dict) or not isinstance(session_id, str) or not session_id:
        return None
    sequence = result.get("tool_result_sequence")
    if not isinstance(sequence, list) or not result.get("trace_id"):
        return None
    candidates = OrderedDict()

    def row_for(smiles):
        canonical = _canonical(smiles)
        if canonical is None:
            return None
        if canonical not in candidates:
            if len(candidates) >= _MAX_CANDIDATES:
                return None
            candidates[canonical] = {"canonical_smiles": canonical}
        return candidates[canonical]

    for item in sequence:
        tool = item.get("tool_name") if isinstance(item, dict) else None
        if tool not in {"property_calculator", "admet_predictor", "candidate_ranker"}:
            continue
        if not _accepted(item, tool):
            continue
        data = item.get("data")
        if tool in {"property_calculator", "admet_predictor"}:
            if not isinstance(data, list):
                continue
            for value in data:
                if not isinstance(value, dict):
                    continue
                row = row_for(value.get("smiles"))
                if row is None:
                    continue
                if tool == "property_calculator":
                    properties = value.get("properties")
                    if not isinstance(properties, dict):
                        continue
                    mw, logp = (_safe_number(properties.get(key)) for key in ("molecular_weight", "logp"))
                    if mw is None or logp is None:
                        continue
                    row["properties"] = {"molecular_weight": mw, "logp": logp}
                    tpsa = _safe_number(properties.get('tpsa'))
                    if tpsa is not None and isinstance(query, str) and re.search(r'(?<![A-Za-z0-9_])TPSA(?![A-Za-z0-9_])', query, re.I):
                        row['properties']['tpsa'] = tpsa
                    for key, pattern in (
                        ('hbd', r'(?<![A-Za-z0-9_])HBD(?![A-Za-z0-9_])|氢键(?:供体|供受体)'),
                        ('hba', r'(?<![A-Za-z0-9_])HBA(?![A-Za-z0-9_])|氢键(?:受体|供受体|供体(?:数|数量)?(?:和|与|、)受体)'),
                    ):
                        count = _safe_number(properties.get(key))
                        if (isinstance(query, str) and re.search(pattern, query, re.I)
                                and count is not None and count >= 0 and count == int(count)):
                            row['properties'][key] = int(count)
                else:
                    admet = value.get("admet")
                    if not isinstance(admet, dict):
                        continue
                    total, risk = (_safe_number(admet.get(key)) for key in ("total_endpoints", "risk_count"))
                    row["admet"] = {"total_endpoints": total, "risk_count": risk}
                    if isinstance(admet.get('model_version'), str) and len(admet['model_version']) <= 40:
                        row['admet']['model_version'] = admet['model_version']
                    ids, endpoints = admet.get('risk_endpoint_ids'), admet.get('endpoints')
                    if (isinstance(ids, list) and isinstance(endpoints, dict)
                            and len(ids) <= 64 and all(isinstance(i, str) and i in endpoints for i in ids)):
                        names = [endpoints[i].get('name', i) for i in ids if isinstance(endpoints[i], dict)]
                        if len(names) == len(ids) and all(isinstance(n, str) and len(n) <= 160 for n in names):
                            row['admet']['risk_names'] = names
                        threshold = _safe_number(admet.get('risk_threshold'))
                        endpoint_records = {}
                        invalid_endpoints = []
                        if len(endpoints) <= 100:
                            for key, endpoint in endpoints.items():
                                if (not isinstance(key, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,100}', key)
                                        or not isinstance(endpoint, dict) or endpoint.get('source') != 'admet_ai_model'):
                                    continue
                                if endpoint.get('validity') == 'out_of_range':
                                    invalid_endpoints.append(key)
                                    continue
                                number = _safe_number(endpoint.get('value'))
                                unit = endpoint.get('unit')
                                task, category = endpoint.get('task_type'), endpoint.get('category')
                                if number is None or not isinstance(unit, str) or len(unit) > 80:
                                    continue
                                record = {'value': number, 'unit': unit, 'task_type': task}
                                dataset_url = endpoint.get('dataset_url')
                                if (isinstance(dataset_url, str) and len(dataset_url) <= 256
                                        and dataset_url.startswith('https://tdcommons.ai/')):
                                    record['dataset_url'] = dataset_url
                                adverse = (task == 'classification' and (category == 'Toxicity'
                                    or any(marker in key for marker in ('CYP', 'Pgp', 'hERG', 'DILI', 'NR-', 'SR-'))))
                                if (adverse and threshold is not None and 0 <= threshold <= 1 and 0 <= number <= 1
                                        and (key in ids) == (number >= threshold)):
                                    record.update(threshold=threshold, hit=number >= threshold)
                                endpoint_records[key] = record
                            row['admet']['endpoints'] = endpoint_records
                            row['admet']['invalid_endpoints'] = invalid_endpoints
                        dili = [(key, endpoints[key]) for key in endpoints if 'DILI' in key.upper()
                                and isinstance(endpoints[key], dict)]
                        if threshold is not None and 0 <= threshold <= 1 and len(dili) == 1:
                            endpoint = dili[0][1]
                            probability = _safe_number(endpoint.get('value'))
                            if (probability is not None and 0 <= probability <= 1
                                    and (dili[0][0] in ids) == (probability >= threshold)
                                    and endpoint.get('task_type') == 'classification'
                                    and endpoint.get('category') == 'Toxicity'
                                    and endpoint.get('source') == 'admet_ai_model'):
                                row['admet']['dili_risk'] = {'value': probability, 'threshold': threshold,
                                                            'hit': probability >= threshold}
                _add_source(row, item)
        else:
            if not isinstance(data, dict):
                continue
            ranked = data.get("ranked_candidates")
            if not isinstance(ranked, list) or len(ranked) > _MAX_CANDIDATES:
                continue
            for value in ranked:
                if not isinstance(value, dict):
                    continue
                row = row_for(value.get("canonical_smiles") or value.get("smiles"))
                rank, score = (_safe_number(value.get(key)) for key in ("rank", "score"))
                if row is None or rank is None or score is None:
                    continue
                row["baseline_rank"] = int(rank)
                row["baseline_score"] = score
                details = value.get("ranking_evidence")
                if isinstance(details, dict):
                    row["ranking_evidence"] = deepcopy(details)
                _add_source(row, item)

    if len(candidates) == 0 or not any(row.get("sources") for row in candidates.values()):
        return None
    payload = {"session_id": session_id, "trace_id": result["trace_id"],
               "query": query if isinstance(query, str) else "",
               "candidates": list(candidates.values())}
    generators = [item for item in sequence if _accepted(item, "llm_molecular_generator")]
    if len(generators) == 1:
        generated = generators[0].get("data", {}).get("candidates")
        if isinstance(generated, list):
            order = [_canonical(item.get("canonical_smiles")) if isinstance(item, dict) else None
                     for item in generated]
            if (len(order) == len(candidates) and None not in order
                    and len(set(order)) == len(order) and set(order) == set(candidates)):
                payload["generated_order"] = order
    encoded = str(payload)
    if len(encoded.encode("utf-8")) > 262144 or contains_secret_material(encoded):
        return None
    return payload


def ranking_explanation_followup(query, history, session_id):
    """Explain the recorded ranking of two displayed generated candidates."""
    safety = re.fullmatch(
        r'\s*候选\s*(\d+)\s*的\s*ADMET\s*得分(?:是|为)\s*(\d+(?:\.\d+)?)'
        r'\s*[，,]\s*(?:这)?(?:是否|是不是|能否|可以)说明(?:它|该分子)?完全安全[？?。.!！]?\s*',
        query, re.I)
    if safety:
        evidence = history[-1].get('candidate_evidence') if history else None
        if (not isinstance(evidence, dict) or evidence.get('session_id') != session_id
                or evidence.get('generation_scope_verified') is not True):
            return {'content': '无法确认上一轮候选及其排序证据，请核对候选记录。', 'status': 'not_calculated'}
        order = evidence.get('generated_order', [])
        number = int(safety.group(1))
        rows = {row['canonical_smiles']: row for row in evidence.get('candidates', [])}
        row = rows.get(order[number - 1]) if 1 <= number <= len(order) else None
        details = row.get('ranking_evidence') if row else None
        score = _safe_number(details.get('admet_score')) if isinstance(details, dict) else None
        if score is None or not 0 <= score <= 1:
            return {'content': '该候选缺少可核对的 ADMET 排序分项，不能判断其安全性；未重新预测。',
                    'status': 'partial', 'reused_evidence': evidence}
        claimed = float(safety.group(2))
        discrepancy = '问题中的分数与记录不一致。' if abs(claimed - score) > 0.000001 else ''
        return {'content': f"候选 {number} `{row['canonical_smiles']}` 的 ADMET 排序分项为 {score:.6g}。"
                + discrepancy + '这项分数概括预测的风险筛查阈值命中情况，不能说明分子完全安全，'
                '也不是人体安全概率。未命中不等于没有风险；仍需结合模型适用范围、暴露条件和实验结果判断。'
                '未重新预测。', 'status': 'completed', 'reused_evidence': evidence}
    match = re.fullmatch(
        r'\s*为什么\s*候选\s*(\d+)\s*排在\s*候选\s*(\d+)\s*之前[？?]'
        r'(?:\s*请分别说明性质[、,，]\s*ADMET\s*和活性对排序的影响[。.!！]?)?\s*',
        query, re.I)
    if not match:
        match = re.fullmatch(r'\s*为什么\s*候选\s*(\d+)\s*比\s*候选\s*(\d+)\s*(?:的)?(?:排名|排序)(?:更)?高[？?。.!！]?\s*', query)
    neutral = False
    if not match:
        match = re.fullmatch(r'\s*(?:请)?(?:解释|说明)\s*候选\s*(\d+)\s*(?:和|与|及)\s*候选\s*(\d+)\s*的(?:排序|排名)(?:依据|原因)[？?。.!！]?\s*', query)
        neutral = match is not None
    if not match:
        return None
    evidence = history[-1].get('candidate_evidence') if history else None
    if (not isinstance(evidence, dict) or evidence.get('session_id') != session_id
            or evidence.get('generation_scope_verified') is not True):
        return {'content': '无法确认上一轮候选及其排序证据，请重新运行候选设计和排序。',
                'status': 'not_calculated'}
    order = evidence.get('generated_order', [])
    numbers = [int(match.group(i)) for i in (1, 2)]
    rows = {row['canonical_smiles']: row for row in evidence.get('candidates', [])}
    if (numbers[0] == numbers[1] or not all(1 <= n <= len(order) for n in numbers)
            or any(order[n - 1] not in rows for n in numbers)):
        return {'content': '无法对应所指候选，请核对上一轮展示的候选序号。', 'status': 'not_calculated',
                'reused_evidence': evidence}
    selected = [rows[order[n - 1]] for n in numbers]
    lines, all_contributions = [], []
    for number, row in zip(numbers, selected):
        details = row.get('ranking_evidence')
        weights = details.get('weights_used') if isinstance(details, dict) else None
        scores = [details.get(k) for k in ('property_score', 'admet_score', 'activity_score')] if isinstance(details, dict) else []
        used = [weights.get(k) for k in ('properties', 'admet', 'activity')] if isinstance(weights, dict) else []
        total = _safe_number(row.get('baseline_score'))
        rank = row.get('baseline_rank')
        if (len(scores) != 3 or len(used) != 3 or total is None or type(rank) is not int
                or any(_safe_number(v) is None or not 0 <= v <= 1 for v in scores + used)
                or abs(sum(s * w for s, w in zip(scores, used)) - total) > 0.000002):
            return {'content': '上一轮缺少可核对的完整排序分项，无法解释这两个候选的分数；未重新计算。',
                    'status': 'partial', 'reused_evidence': evidence}
        contributions = [s * w for s, w in zip(scores, used)]
        all_contributions.append(contributions)
        lines.append(f"候选 {number} `{row['canonical_smiles']}`：第 {rank} 名，总分 {total:.6g}；"
                     + '；'.join(f'{label} {score:.6g} × {weight:.6g} = {part:.6g}'
                                for label, score, weight, part in zip(
                                    ('性质', 'ADMET', '活性'), scores, used, contributions)) + '。')
    relation = '确实排在前面' if selected[0]['baseline_rank'] < selected[1]['baseline_rank'] else '并未排在前面'
    differences = [a - b for a, b in zip(*all_contributions)]
    comparison = '；'.join(f'{label}贡献差 {difference:+.6g}' for label, difference in
                          zip(('性质', 'ADMET', '活性'), differences))
    introduction = (f'以下是上一轮候选 {numbers[0]} 与候选 {numbers[1]} 的实际排序依据。' if neutral
                    else f'按上一轮实际排序，候选 {numbers[0]} {relation}。')
    return {'content': introduction + '\n\n' + '\n\n'.join(lines)
            + f'\n\n候选 {numbers[0]} 相对候选 {numbers[1]}：{comparison}；正值有利于前者。'
            + '\n\n分项是该次排序使用的归一化得分；这是固定权重的研究优先级，不代表实验验证。未重新计算。',
            'status': 'completed', 'reused_evidence': evidence}


def ranking_followup_requested(query):
    if not isinstance(query, str):
        return False
    positive = positive_intent_text(query)
    return bool(_RANKING_REQUEST.search(positive)
                and _CANDIDATE_CONTEXT.search(positive)
                and _REUSE_REQUEST.search(positive)
                and not _GENERATION.search(positive)
                and not _OTHER_RANKING_DOMAIN.search(positive))


def _merge(history, session_id):
    merged = OrderedDict()
    for entry in history if isinstance(history, list) else []:
        evidence = entry.get("candidate_evidence") if isinstance(entry, dict) else None
        if not isinstance(evidence, dict) or evidence.get("session_id") != session_id:
            continue
        for candidate in evidence.get("candidates", []):
            canonical = candidate.get("canonical_smiles") if isinstance(candidate, dict) else None
            if not canonical:
                continue
            current = merged.setdefault(canonical, {"canonical_smiles": canonical})
            for key in ("properties", "admet"):
                if isinstance(candidate.get(key), dict):
                    current[key] = deepcopy(candidate[key])
            for key in ("baseline_rank", "baseline_score"):
                if key in candidate and key not in current:
                    current[key] = candidate[key]
            current.setdefault("sources", []).extend(deepcopy(candidate.get("sources", [])))
    return list(merged.values())


def endpoint_explanation_followup(query, history, session_id):
    """Explain a single molecule's previously validated hERG and DILI scores."""
    unavailable = re.fullmatch(
        r'\s*基于刚才的结果[，,]哪些预测不可用[？?]请分别列出\s*'
        r'(?P<first>\S+)\s+和\s+(?P<second>\S+)\s*的端点[，,]不要补造数值[。.!！]?\s*', query)
    if unavailable:
        evidence = history[-1].get('candidate_evidence') if history else None
        rows = evidence.get('candidates') if isinstance(evidence, dict) else None
        requested = [_canonical(unavailable.group(key)) for key in ('first', 'second')]
        if (not session_id or not isinstance(evidence, dict) or evidence.get('session_id') != session_id
                or not evidence.get('trace_id') or not isinstance(rows, list)
                or None in requested or len(set(requested)) != 2):
            return {'content': '无法确认上一轮对应分子的 ADMET 记录；未重新计算。', 'status': 'not_calculated'}
        selected = [next((row for row in rows if isinstance(row, dict)
                          and row.get('canonical_smiles') == name), None) for name in requested]
        if any(not row or not isinstance(row.get('admet'), dict)
               or not isinstance(row['admet'].get('invalid_endpoints'), list)
               or not any(isinstance(source, dict) and source.get('tool_name') == 'admet_predictor'
                          and source.get('evidence_id') for source in row.get('sources', []))
               for row in selected):
            return {'content': '无法确认上一轮对应分子的 ADMET 记录；未重新计算。', 'status': 'not_calculated'}
        lines = []
        for row in selected:
            endpoints = row['admet']['invalid_endpoints']
            detail = '、'.join(endpoints) if endpoints else '未记录超出物理范围的端点'
            lines.append(f"`{row['canonical_smiles']}`：{detail}。")
        lines.append('列出的端点因模型输出超出物理范围而不可用；未重新计算。')
        return {'content': '\n\n'.join(lines), 'status': 'completed', 'reused_evidence': evidence}
    half_life = re.fullmatch(
        r'\s*(?:这个|该|刚才的|上一轮的)(?:分子)?的?\s*(?:预测)?半衰期(?:是)?多少[？?。]?\s*', query)
    if half_life:
        evidence = history[-1].get('candidate_evidence') if history else None
        rows = evidence.get('candidates', []) if isinstance(evidence, dict) and session_id and evidence.get('session_id') == session_id else []
        if len(rows) != 1 or not isinstance(rows[0].get('admet'), dict):
            return {'content': '无法确认上一轮唯一分子的 ADMET 结果，请指定分子并先完成预测；未重新计算。',
                    'status': 'not_calculated'}
        row = rows[0]
        admet = row['admet']
        name = row['canonical_smiles']
        if 'Half_Life_Obach' in admet.get('invalid_endpoints', []):
            text = f'分子 `{name}` 的半衰期模型输出超出物理范围，预测不可用；未重新计算。'
            status = 'partial'
        else:
            endpoint = admet.get('endpoints', {}).get('Half_Life_Obach')
            value = _safe_number(endpoint.get('value')) if isinstance(endpoint, dict) else None
            if value is None or value < 0 or endpoint.get('unit') != 'hr' or endpoint.get('task_type') != 'regression':
                text = f'分子 `{name}` 的可用半衰期预测未返回或无法核对；未重新计算。'
                status = 'not_calculated'
            else:
                text = f'分子 `{name}` 的模型预测半衰期为 {value:.6g} 小时；这是预测值，不是实验测定结果。未重新计算。'
                status = 'completed'
        return {'content': text, 'status': status, 'reused_evidence': evidence}
    match = re.fullmatch(
        r'\s*(?:刚才|上一轮|上次)(?:的)?\s*(?:hERG\s*(?:和|与|、)\s*DILI|'
        r'DILI\s*(?:和|与|、)\s*hERG)\s*(?:数值|预测值|结果)'
        r'(?:分别)?(?:代表什么|意味着什么|是什么意思)[？?。]'
        r'(?:\s*(?:可以|能否|是否)(?:直接)?(?:当作|视为)人体(?:发生)?'
        r'(?:心脏毒性(?:和|与|、)肝损伤|肝损伤(?:和|与|、)心脏毒性)的概率吗?[？?。]?)?\s*',
        query, re.I)
    if not match:
        return None
    evidence = history[-1].get('candidate_evidence') if history else None
    rows = evidence.get('candidates') if isinstance(evidence, dict) else None
    if (not isinstance(evidence, dict) or evidence.get('session_id') != session_id
            or not evidence.get('trace_id') or not isinstance(rows, list) or len(rows) != 1
            or not any(source.get('tool_name') == 'admet_predictor' and source.get('evidence_id')
                       for source in rows[0].get('sources', []) if isinstance(source, dict))):
        return {'content': '无法确认上一轮单个分子的 ADMET 预测记录，请重新提供结构并预测。',
                'status': 'not_calculated'}
    row = rows[0]
    endpoints = row.get('admet', {}).get('endpoints', {})
    lines = [f"分子：`{row['canonical_smiles']}`。"]
    missing = False
    for key in ('hERG', 'DILI'):
        endpoint = endpoints.get(key)
        value = _safe_number(endpoint.get('value')) if isinstance(endpoint, dict) else None
        if (value is None or not 0 <= value <= 1 or endpoint.get('task_type') != 'classification'):
            lines.append(f'{key}：上一轮未返回可核对的分类预测值。')
            missing = True
            continue
        meaning = 'hERG 通道阻断' if key == 'hERG' else '药物性肝损伤'
        lines.append(f'{key}：{value:.4g}，是{meaning}的模型分类预测分数。')
    lines.append('这些分数不能直接当作人体发生心脏毒性或肝损伤的概率；'
                 '应结合模型适用域、剂量和暴露水平及相关实验和临床证据判断。')
    return {'content': '\n\n'.join(lines), 'status': 'partial' if missing else 'completed',
            'reused_evidence': evidence}


def followup_answer(query, history, session_id):
    """Explain/reuse candidate evidence without running generation or predictors."""
    oral = re.fullmatch(
        r'\s*(?:根据|基于)(?:刚才|上一轮|之前)的(?:计算|结果|性质)[，,]\s*'
        r'(?:这两者|这两个(?:分子|候选)?|它们)(?:中)?(?:哪一个|哪个)更适合'
        r'(?:作为)?口服(?:药物)?先导物[？?。.!！]'
        r'(?:\s*(?:请)?(?:解释|说明)(?:依据|原因)(?:[，,]\s*(?:并)?说明(?:还)?缺少哪些(?:关键)?数据)?)?'
        r'[？?。.!！]?\s*', query)
    if not oral:
        oral = re.fullmatch(
            r'\s*(?:结合|根据|基于)(?:这些|上述|刚才的|上一轮的)(?:性质|结果|计算)[，,]\s*'
            r'(?:你|请)?\s*(?:推荐|选择|建议)\s*(?:哪一个|哪个|哪种)(?:优先)?'
            r'(?:作为|做)?口服(?:药物)?先导(?:物)?[？?。.!！]'
            r'(?:\s*(?:为什么|请解释(?:原因|依据)?)[？?。.!！]?)?\s*', query)
    if not oral:
        from src.agent.utils.validators import InputValidator
        positive = positive_intent_text(query)
        oral = (re.search(r'口服(?:药物)?先导(?:物)?', positive)
                and _REUSE_REQUEST.search(positive)
                and re.search(r'更适合|推荐|选择|认定|断定', positive)
                and not InputValidator().contains_calculation_request(positive)
                and not re.search(r'生成|设计|优化|对接|检索|搜索|查询|测定|测量', positive))
    if oral:
        evidence = history[-1].get('candidate_evidence') if history else None
        rows = evidence.get('candidates') if isinstance(evidence, dict) else None
        if isinstance(rows, list) and len(rows) == 2:
            requested = [row.get('canonical_smiles') for row in rows]
            solubility = solubility_prediction_comparison(evidence, session_id, requested)
            if solubility:
                return {'content': ('不能仅据水溶解度预测认定哪个分子更适合作为口服先导物。\n\n'
                    + solubility + '\n\n还需结合目标靶点的活性和选择性、相关 pH 下的实测溶解度与溶出、'
                    '渗透性、代谢稳定性、体内暴露和安全性证据综合判断。'
                    '这些验证证据尚未在本轮结果中建立；本次仅复用已有预测，未重新计算。'),
                    'status': 'partial', 'reused_evidence': evidence}
        if (not session_id or not isinstance(evidence, dict) or evidence.get('session_id') != session_id
                or not isinstance(rows, list) or len(rows) != 2
                or any(not isinstance(row, dict) or not isinstance(row.get('properties'), dict)
                       or not any(source.get('tool_name') == 'property_calculator' and source.get('evidence_id')
                                  for source in row.get('sources', []) if isinstance(source, dict))
                       for row in rows)):
            return {'content': '缺少当前会话中两者可核对的性质结果，无法比较口服先导物；请重新计算。',
                    'status': 'partial'}
        lines = ['根据上一轮已验证的计算结果：']
        for row in rows:
            props = row['properties']
            parts = [f"分子量 {props['molecular_weight']:.2f} Da", f"logP {props['logp']:.3f}"]
            if 'tpsa' in props:
                parts.append(f"TPSA {props['tpsa']:.2f} Å²")
            for key in ('hbd', 'hba'):
                if key in props:
                    parts.append(f'{key.upper()} {props[key]}')
            lines.append(f"- `{row['canonical_smiles']}`：" + '；'.join(parts) + '。')
        lines.append('这些结构计算的描述符可用于初筛，'
                     '但不足以确定哪个更适合作为口服先导物。'
                     '还需结合目标及生化活性、选择性、溶解度、渗透性、代谢稳定性、暴露和安全性数据综合判断；'
                     '本次未重新计算或预测。')
        return {'content': '\n'.join(lines), 'status': 'partial', 'reused_evidence': evidence}
    if not ranking_followup_requested(query):
        return None
    rows = _merge(history, session_id)
    if not rows:
        return {"final_answer": "缺少当前会话中可复核的候选证据；未重新生成或重新预测。",
                "status": "not_calculated", "success": False}
    rows.sort(key=lambda row: (row.get("baseline_rank", 10**9), row["canonical_smiles"]))
    missing_admet = [row for row in rows if not isinstance(row.get("admet"), dict)]
    lines = ["候选证据复核", "", "本次只复用当前会话已接受的性质、ADMET 和原始性质排序记录；未重新生成分子或调用预测工具。", "",
             "| 候选 | 分子性质 | ADMET | 原有性质排序 |", "|---|---|---|---:|"]
    for index, row in enumerate(rows, 1):
        props = row.get("properties")
        prop_text = (f"MW {props['molecular_weight']:.2f}；logP {props['logp']:.3f}"
                     if props else "缺失")
        admet = row.get("admet")
        admet_text = (f"已返回，风险筛查 {admet['risk_count']:.0f}/{admet['total_endpoints']:.0f}"
                      if admet and admet.get("risk_count") is not None and admet.get("total_endpoints") is not None
                      else "缺失")
        rank = row.get("baseline_rank", "未记录")
        lines.append(f"| 候选 {index} `{row['canonical_smiles']}` | {prop_text} | {admet_text} | {rank} |")
    if missing_admet:
        lines.extend(["", "当前不能完成完整的 ADMET 加权重排：至少一个候选缺少可对应的 ADMET 证据。"
                      "已有性质排序仅作为参考，不能把缺失证据当作低风险或零分。",
                      "请先对缺少 ADMET 的候选执行预测，再要求基于完整证据重排。"])
        status, success = "partial", False
    else:
        lines.extend(["", "所有候选均有可对应的 ADMET 汇总，但当前仅复核已记录证据；如需新的权重规则，请明确排序标准。"])
        status, success = "completed", True
    return {"final_answer": "\n".join(lines), "status": status, "success": success,
            "partial": status == "partial"}
