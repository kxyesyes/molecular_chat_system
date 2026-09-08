"""Conservative activity input parsing; never repair an explicit structure."""
import re
from collections.abc import Mapping

from src.activity.family_contract import resolve_activity_family


_ACTIVITY_METRIC_LABELS = frozenset({"ic50", "pic50", "ec50", "pec50", "ki", "pki", "kd", "pkd"})


def activity_target(text, validate_smiles):
    # Only explicitly labelled background clauses are excluded, not inferred intent.
    text = "；".join(clause for clause in re.split(r"[；;。\n，,]", text)
                    if not re.match(r"\s*(?:研究背景|背景|background\b)", clause, re.I))
    explicit = re.findall(
        r"(?:针对|靶点\s*[:：]?|对|(?<![A-Za-z])target\s*[:=：]?)\s*"
        r"(丁酰胆碱酯酶|[A-Za-z][A-Za-z0-9_-]*)", text, re.I)
    explicit = [value for value in explicit if value.lower() not in {"activity", "potency"}]
    # A bounded target-label position, not arbitrary biomedical intent inference.
    labelled = re.findall(r"(?:预测|评估)\s*([A-Za-z][A-Za-z0-9_-]*)\s*(?:的)?活性", text)
    explicit.extend(value for value in labelled if not validate_smiles(value))
    for value in explicit:
        resolve_activity_family(value)  # Unknown explicit targets cannot be masked by aliases.
    # Chinese prose is a delimiter, but ASCII identifier suffixes stay intact.
    tokens = re.findall(r"[A-Za-z0-9_-]+|丁酰胆碱酯酶", text)
    matches = []
    for token in tokens:
        try:
            resolve_activity_family(token)
        except ValueError:
            continue
        matches.append(token)
    if matches:
        resolve_activity_family(" ".join(matches))  # Reject dual-family intent.
        return explicit[0] if explicit else matches[0]
    return None


def parse_activity_input(payload, tool):
    data = payload if isinstance(payload, Mapping) else {}
    text = data.get("query", "") if data else payload
    if not isinstance(text, str):
        raise ValueError("请提供文本查询及有效 SMILES。")
    try:
        query_target = activity_target(text, tool.validate_smiles)
        target = data["target"] if "target" in data else query_target
        if target is not None or "target" in data:
            resolve_activity_family(target)
        if query_target is not None and resolve_activity_family(query_target) != resolve_activity_family(target):
            raise ValueError("Conflicting structured and query targets")
    except ValueError:
        raise ValueError("靶点未知或存在歧义，请明确选择 PDE 或 BuChE 靶点。") from None

    if "smiles" in data:
        values = data["smiles"]
        values = [values] if isinstance(values, str) else values
    else:
        markers = list(re.finditer(r"(?i)SMILES\s*[:：=]\s*", text))
        if markers:
            values = []
            for marker in markers:
                tail = text[marker.end():]
                if tail.startswith(('"', "'", '`')):
                    end = tail.find(tail[0], 1)
                    if end == -1:
                        raise ValueError("SMILES 引号未闭合；请提供完整结构。")
                    values.append(tail[1:end])
                else:
                    # Spaces belong to the field; only clear prose/line delimiters end it.
                    value = re.match(r"[^\r\n\u4e00-\u9fff，；。！？：、;]+", tail)
                    values.append(value.group(0).strip() if value else "")
        else:
            # Whole tokens only: unmatched parentheses and invalid suffixes survive.
            tokens = re.findall(r"[^\s\u4e00-\u9fff，；。;]+", text)
            values = [t.strip("`\"',") for t in tokens]
            # Metric names in prose are not structures. Explicit SMILES fields
            # above remain untouched and must pass complete-structure validation.
            values = [t for t in values if t.casefold() not in _ACTIVITY_METRIC_LABELS
                      and tool._is_plausible_smiles_lexeme(t)]
    if (not isinstance(values, (list, tuple)) or not values
            or any(not isinstance(s, str) or not s or re.search(r"\s", s)
                   or not tool.validate_smiles(s) for s in values)):
        raise ValueError("SMILES 无效或缺失；请提供完整结构，并用换行或分号分隔说明，不会提取片段替代。")
    return text, list(values), target
