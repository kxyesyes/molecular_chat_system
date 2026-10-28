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


def _add_source(row, item):
    source = {"tool_name": item["tool_name"], "evidence_id": item["quality"]["evidence_id"]}
    row.setdefault("sources", []).append(source)


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
                else:
                    admet = value.get("admet")
                    if not isinstance(admet, dict):
                        continue
                    total, risk = (_safe_number(admet.get(key)) for key in ("total_endpoints", "risk_count"))
                    row["admet"] = {"total_endpoints": total, "risk_count": risk}
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
                _add_source(row, item)

    if len(candidates) == 0 or not any(row.get("sources") for row in candidates.values()):
        return None
    payload = {"session_id": session_id, "trace_id": result["trace_id"],
               "query": query if isinstance(query, str) else "",
               "candidates": list(candidates.values())}
    encoded = str(payload)
    if len(encoded.encode("utf-8")) > 262144 or contains_secret_material(encoded):
        return None
    return payload


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


def followup_answer(query, history, session_id):
    """Explain/reuse candidate evidence without running generation or predictors."""
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
