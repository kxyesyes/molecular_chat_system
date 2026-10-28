"""Activity context layered over the shared whole-structure parser."""
import re
from collections.abc import Mapping

from src.activity.family_contract import resolve_activity_family
from src.agent.contracts.comparison_presentation import without_comparison_presentation
from src.agent.contracts.target_request import _TARGET_ROLE, analyze_target_request
from .molecular_input import (
    _FIELD_END, _MARKER, _looks_like_structure, _unquote, parse_molecular_smiles,
)


_TOKENS = re.compile(r"[A-Za-z0-9_-]+|丁酰胆碱酯酶")
_METRICS = frozenset({"ic50", "pic50", "ec50", "pec50", "ki", "pki", "kd", "pkd"})
_ACTIVITY_ENDPOINTS = re.compile(
    r"(?<![A-Za-z0-9])(?:p?ic50|p?ec50|p?ki|p?kd)(?![A-Za-z0-9])",
    re.I,
)
_ACTIVITY_TERMS = re.compile(
    r"活性(?:预测)?|抑制(?:活性)?|potency|inhibition|activity(?:\s+prediction)?",
    re.I,
)
_ACTIVITY_NEGATION = re.compile(
    r"(?:不要|无需|不需要|不必|禁止|请勿|勿|do\s+not|don't|no)"
    r"[^\r\n，,。；;:：]{0,32}$",
    re.I,
)
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
_MOLECULE_LIST_TOKEN = r"[^\s\u4e00-\u9fff，！？：、；。;,]"
_CHINESE_COMPOUND_LABEL = re.compile(r"对[\u4e00-\u9fff]+[酚胺酸醚醇醛酮酯烷烯炔苯腈]")
_TARGET_LIKE_LABEL = re.compile(r"酶|蛋白|受体|通道|靶点|抗体|核酸|基因")
_NAMED_MOLECULE_IN_LIST = re.compile(
    rf"(?:(?<!{_MOLECULE_LIST_TOKEN})(?P<previous>{_MOLECULE_LIST_TOKEN}+)[ \t]*[、，,和及与]"
    r"|(?:^|[；;]|预测|评估|比较))[ \t]*"
    r"(?P<name>对[\u4e00-\u9fff]+)[ \t]+"
    # Leave this structure unconsumed so it can anchor the next list member.
    rf"(?=(?P<smiles>{_MOLECULE_LIST_TOKEN}+)(?=[ \t]*(?:$|[\u4e00-\u9fff，！？：、；。;,])))"
)
_INVALID = "SMILES 无效或缺失；请提供完整结构，并用换行或分号分隔，不会提取片段替代。"


def activity_intent_requested(text):
    """Return whether text explicitly requests activity work.

    Endpoint words are activity intent too, but an explicit negation immediately
    governing the endpoint/activity phrase must prevent tool admission.
    """
    if not isinstance(text, str):
        return False
    matches = list(_ACTIVITY_TERMS.finditer(text)) + list(_ACTIVITY_ENDPOINTS.finditer(text))
    for match in matches:
        prefix = text[max(0, match.start() - 40):match.start()]
        if not _ACTIVITY_NEGATION.search(prefix):
            return True
    return False


def _canonical_endpoint(value):
    if not isinstance(value, str):
        raise ValueError("Invalid activity endpoint")
    normalized = value.strip().casefold()
    aliases = {
        "ic50": "IC50", "pic50": "pIC50",
        "ec50": "EC50", "pec50": "pEC50",
        "ki": "Ki", "pki": "pKi",
        "kd": "Kd", "pkd": "pKd",
    }
    try:
        return aliases[normalized]
    except KeyError:
        raise ValueError("不支持的活性端点") from None


def requested_activity_endpoint(payload):
    """Extract one exact endpoint without silently converting units."""
    data = payload if isinstance(payload, Mapping) else {}
    text = data.get("query", "") if isinstance(payload, Mapping) else payload
    if not isinstance(text, str):
        return None
    detected = [_canonical_endpoint(match.group()) for match in _ACTIVITY_ENDPOINTS.finditer(text)]
    structured = _canonical_endpoint(data["endpoint"]) if "endpoint" in data and data["endpoint"] is not None else None
    unique = list(dict.fromkeys(detected))
    if structured is not None and unique and unique != [structured]:
        raise ValueError("结构化活性端点与请求文本不一致")
    if len(unique) > 1:
        raise ValueError("一次请求包含多个不一致的活性端点")
    return structured or (unique[0] if unique else None)


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
    # Target extraction must use only positive operation clauses.  Otherwise a
    # prohibition such as “不要做对接” is misread by the single-character
    # Chinese target label “对” as an additional target named “接”.
    text = re.sub(
        r"(?:不要|无需|不需要|不做|不运行|不执行|禁止|跳过|避免|勿|"
        r"do\s+not|don't|without|skip|exclude)\s*"
        r"(?:做|进行|运行|执行|开始)?\s*(?:对接|docking)"
        r"(?=\s|$|[。.!?！？；;，,])",
        "",
        text,
        flags=re.I,
    )
    text = _prose_sentence_periods(text, validate_smiles)
    if _TARGET_ROLE.search(text):
        request = analyze_target_request(text)
        if request.needs_clarification:
            raise ValueError("Ambiguous design target")
        _strict_family(request.targets[0])
        # Only a fully validated role/design/tail may treat 靶点 as a role noun,
        # not a forward target label. Keep all identifiers and other declarations
        # for the activity-specific checks below; never bypass them wholesale.
        text = _TARGET_ROLE.sub(
            lambda match: match[0].removesuffix(match[2]) + "语境", text,
        )
    text = "；".join(_context_clauses(text))
    explicit = re.findall(r"(?:靶点|\btarget)\s*(?:[:：=]|\s)\s*([^；，,]*)", text, re.I)
    for value in explicit:
        _strict_family(value)
    molecule_name_positions = set()
    for match in _NAMED_MOLECULE_IN_LIST.finditer(text):
        # A following structure alone cannot turn an unknown target into a
        # molecule label. Require chemical naming syntax, never protein-like text.
        if (not _CHINESE_COMPOUND_LABEL.fullmatch(match["name"])
                or _TARGET_LIKE_LABEL.search(match["name"])):
            continue
        # 对 may be the positional prefix of a compound name. Accept the first
        # item or a delimited list item only with its own complete structure;
        # a preceding item, when present, must be a complete structure too.
        try:
            _strict_family(match["name"][1:])
        except ValueError:
            if ((match["previous"] is None or validate_smiles(match["previous"]))
                    and validate_smiles(match["smiles"])):
                molecule_name_positions.add(match.start("name"))
    positions = re.finditer(_TARGET_LABEL, text, re.I)
    # Preserve the legacy 'predict activity for CCO' molecule position. Explicit
    # target labels above are still authoritative, including target: CCO.
    positioned = []
    for match in positions:
        if match.start() in molecule_name_positions:
            continue
        # A resolved candidate ordinal is a molecule object, not a target name.
        # Structure/reference admission remains the caller's responsibility.
        if match[1] == "对" and re.match(
                r"第\s*(?:[1-9]\d?|[一二三四五六七八九十]+)\s*个\s*(?:候选(?:分子)?|分子)"
                r"(?=\s*(?:做|进行|优化|$|[，,。;；]))", text[match.end():]):
            continue
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


def _prose_sentence_periods(text, validate_smiles):
    """Disambiguate one sentence period only after a complete prose molecule."""
    if re.match(r'\s*(?:(?:请\s*)?(?:比较|对比)|(?:please\s+)?compare\b)', text, re.I):
        # Admission, binding and tool parsing must see the same complete
        # comparison operands. This local view never replaces the raw query.
        text = without_comparison_presentation(text)
    clauses = re.split(r"([\r\n；。;])", text)
    for index in range(0, len(clauses), 2):
        clause = clauses[index]
        # An explicit field or a structure-leading batch is authoritative. Only
        # a bounded English request may give its final period prose meaning.
        if _MARKER.search(clause) or not re.match(
                r"\s*(?:please\s+)?(?:compare|predict|assess|evaluate|calculate|analyze)\b",
                clause, re.I):
            continue
        ending = re.search(r"(?<=\s)([^\s]+)\.([ \t]*)$", clause)
        if ending and validate_smiles(ending[1]):
            # Validate the entire remainder: internal salt/disconnection dots,
            # malformed suffixes and repeated trailing dots must not be sliced.
            period = ending.end(1)
            clauses[index] = clause[:period] + clause[period + 1:]
    return "".join(clauses)


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
    context = _prose_sentence_periods(text, tool.validate_smiles)
    clauses = re.split(r"([\r\n；。;])", context)
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
