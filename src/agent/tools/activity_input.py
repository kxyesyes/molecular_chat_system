"""Activity context layered over the shared whole-structure parser."""
import re
from collections.abc import Mapping

from src.activity.family_contract import resolve_activity_family
from .molecular_input import (
    _FIELD_END, _MARKER, _looks_like_structure, _unquote, parse_molecular_smiles,
)


_TOKENS = re.compile(r"[A-Za-z0-9_-]+|丁酰胆碱酯酶")
_METRICS = frozenset({"ic50", "pic50", "ec50", "pec50", "ki", "pki", "kd", "pkd"})
_GENERIC_SUBJECTS = frozenset({"分子", "这个分子", "该分子"})
_CONTEXT_WORDS = frozenset({"target", "activity", "potency", "inhibition", "assess"})
_BACKGROUND = re.compile(r"\s*(?:研究背景|背景|background\b)", re.I)
_TARGET_LABEL = r"(针对|靶点\s*[:：]?|对|(?<![A-Za-z])(?:target|for|against)\s*[:=：]?)\s*"
# Include unrecognized values so explicit intent cannot vanish before validation.
_TARGET_VALUE = (
    r"((?:丁酰胆碱酯酶|[A-Za-z][A-Za-z0-9_-]*)(?=$|[\s；，,/和及与、]|(?:的)?(?:抑制活性|活性|p?IC50|p?EC50|p?Ki|p?Kd))"
    r"|[^\s；，,/和及与、]+)"
)
_LABELLED_TARGET = re.compile(
    r"(?:预测|评估)\s*([^\s；，,对]+?)\s*(?:的)?活性|"
    r"\b(?:predict|assess|evaluate)\s+([^\s；，,]+)\s+(?:activity|potency)\b",
    re.I,
)
_INVALID = "SMILES 无效或缺失；请提供完整结构，并用换行或分号分隔，不会提取片段替代。"


def _strict_family(value):
    # The registry resolver deliberately permits surrounding text. At this input
    # boundary every explicit identifier must be recognized, not only one alias.
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Missing target")
    tokens = re.findall(r"[\w-]+", value)
    if not tokens:
        raise ValueError("Missing target")
    families = {resolve_activity_family(token) for token in tokens}
    if len(families) != 1:
        raise ValueError("Conflicting targets")
    return families.pop()


def _target_context_start(text):
    matches = (re.search(_TARGET_LABEL, text, re.I), _LABELLED_TARGET.search(text))
    return min((match.start() for match in matches if match), default=None)


def _context_clauses(text):
    for clause in _FIELD_END.split(text):
        marker = _MARKER.search(clause)
        context = clause[:marker.start()] if marker else clause
        if marker:
            # A structure field must not hide a later explicit target, especially
            # when generated SMILES arrive separately in a structured payload.
            tail = clause[marker.end():]
            start = _target_context_start(tail)
            if start is not None:
                context += "；" + tail[start:]
        if _BACKGROUND.match(context):
            start = _target_context_start(context)
            if start is None:
                continue
            context = context[start:]
        yield context


def activity_target(text, validate_smiles):
    """Resolve bounded target positions; never infer arbitrary biomedical intent."""
    text = "；".join(_context_clauses(text))
    explicit = re.findall(r"(?:靶点|\btarget)\s*(?:[:：=]|\s)\s*([^；，,]*)", text, re.I)
    for value in explicit:
        _strict_family(value)
    positions = re.finditer(_TARGET_LABEL, text, re.I)
    # Preserve the legacy 'predict activity for CCO' molecule position. Explicit
    # target labels above are still authoritative, including target: CCO.
    positioned = []
    for match in positions:
        label = match[1]
        value_match = re.match(_TARGET_VALUE, text[match.end():], re.I)
        if value_match is None:
            raise ValueError("Missing explicit target")
        value = value_match[1]
        values = [value]
        tail = text[match.end() + value_match.end():]
        while following := re.match(
                r"\s*(?:和|及|与|、|[,，/]|\band\b|\bor\b)\s*"
                + _TARGET_VALUE, tail, re.I):
            values.append(following[1])
            tail = tail[following.end():]
        positioned.extend(value for value in values
                          if not (label.strip().casefold() == "for" and validate_smiles(value)))
    labelled = [match[1] or match[2] for match in _LABELLED_TARGET.finditer(text)]
    positioned.extend(value for value in labelled
                      if value.casefold() not in _METRICS | _GENERIC_SUBJECTS and not validate_smiles(value))
    for value in positioned:
        _strict_family(value)
    matches = []
    for token in _TOKENS.findall(text):
        try:
            _strict_family(token)
        except ValueError:
            continue
        matches.append(token)
    if matches:
        _strict_family(" ".join(matches))
        return positioned[0] if positioned else matches[0]
    return None


def _molecular_context(clause, tool):
    if not clause.strip() or _BACKGROUND.match(clause):
        return ""
    first = clause.strip().split()[0]
    if _looks_like_structure(_unquote(first), tool):
        # Names/invalid suffixes following a leading structure remain authoritative.
        return clause

    def replace_context(match):
        token = match.group()
        if token.casefold() in _METRICS | _CONTEXT_WORDS:
            return "语境"
        try:
            _strict_family(token)
        except ValueError:
            return token
        return "语境"

    clause = _TOKENS.sub(replace_context, clause)
    if first.casefold() in tool.exclude_words | _CONTEXT_WORDS | {"please", "assess"}:
        clause = "语境 " + clause
    return clause


def parse_activity_input(payload, tool):
    data = payload if isinstance(payload, Mapping) else {}
    text = data.get("query", "") if isinstance(payload, Mapping) else payload
    if not isinstance(text, str) or len(text) > 65536:
        raise ValueError(_INVALID)
    try:
        query_target = activity_target(text, tool.validate_smiles)
        target = data["target"] if "target" in data else query_target
        if target is not None or "target" in data:
            _strict_family(target)
        if query_target is not None and _strict_family(query_target) != _strict_family(target):
            raise ValueError("Conflicting structured and query targets")
    except ValueError:
        raise ValueError("靶点未知或存在歧义，请明确选择 PDE 或 BuChE 靶点。") from None

    if "smiles" in data:
        values = data["smiles"]
        values = [values] if isinstance(values, str) else values
        if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= 100:
            raise ValueError(_INVALID)
        for value in values:
            if (not isinstance(value, str) or not value or not value.isascii() or len(value) > 8192
                    or re.search(r"\s", value)
                    or parse_molecular_smiles("SMILES: " + value, tool) != [value]):
                raise ValueError(_INVALID)
        return text, list(values), target

    # Only context is adapted. Labelled SMILES fields (including quoted suffixes)
    # go byte-for-byte to the stronger shared parser; no lexical truncation.
    clauses = re.split(r"([\r\n；。;])", text)
    for index in range(0, len(clauses), 2):
        clause = clauses[index]
        marker = _MARKER.search(clause)
        if marker and not clause[marker.end():].isascii():
            # Some RDKit versions tolerate an attached Unicode suffix even with
            # parseName=False. Explicit fields cannot contain prose suffixes.
            raise ValueError(_INVALID)
        clauses[index] = (
            _molecular_context(clause[:marker.start()], tool) + clause[marker.start():]
            if marker else _molecular_context(clause, tool)
        )
    return text, parse_molecular_smiles("".join(clauses), tool), target
