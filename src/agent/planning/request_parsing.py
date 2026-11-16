"""Stateless Planner parsing; existing domain contracts own scientific grammar."""
import re
import math
from collections.abc import Callable
from typing import Any

from src.agent.contracts.generation_request import (
    has_generation_intent, parse_generation_count, validate_generation_count,
)
from src.agent.contracts.target_request import analyze_target_request
from src.target_identifiers import TARGET_PATTERN


_DOCKING_FIELD = re.compile(
    r"(?P<label>receptor(?:_path)?|ligand(?:_path)?|"
    r"protein(?:_path)?|蛋白(?:文件|结构)?|受体|配体)"
    r"\s*[:：=]?\s*"
    r"(?P<value>[^\r\n；;。]+?)"
    r"(?=\s*(?:[；;。]|(?:和|and)\s*"
    r"(?:receptor(?:_path)?|ligand(?:_path)?|"
    r"protein(?:_path)?|蛋白(?:文件|结构)?|受体|配体)\b|"
    r"(?:receptor(?:_path)?|ligand(?:_path)?|protein(?:_path)?|"
    r"蛋白(?:文件|结构)?|受体|配体|center|size|box\s*center|"
    r"box\s*size|中心|尺寸|盒子中心|盒子大小)\s*[:：=]|"
    r"(?:进行|使用|执行)|(?:perform|run)\b)|$)",
    re.I,
)
_DOCKING_VECTOR = re.compile(
    r"(?P<label>center|size|box\s*center|box\s*size|中心|尺寸|盒子中心|盒子大小)"
    r"\s*[:：=]\s*\[(?P<value>[^\]]+)\]",
    re.I,
)


def parse_structured_docking_input(query: str) -> dict[str, Any] | None:
    """Parse an explicitly labelled docking request without guessing inputs.

    A natural-language target name remains unstructured and is intentionally
    left to ``MolecularDocking``'s refusal path.  This parser only binds a
    request when all four required labelled fields are present.
    """
    if not isinstance(query, str):
        return None
    fields: dict[str, str] = {}
    for match in _DOCKING_FIELD.finditer(query):
        label = match.group('label').strip().lower()
        value = match.group('value').strip().strip('`"\'')
        value = value.rstrip('，；;。').strip()
        if (
            label.startswith('receptor')
            or label.startswith('protein')
            or label in {'蛋白', '蛋白文件', '蛋白结构', '受体'}
        ):
            key = 'receptor_path'
        else:
            key = 'ligand_path'
        if key in fields and fields[key] != value:
            return None
        fields[key] = value

    vectors: dict[str, list[float]] = {}
    for match in _DOCKING_VECTOR.finditer(query):
        label = match.group('label').strip().lower()
        try:
            values = [float(item.strip()) for item in re.split(r'[,，]', match.group('value'))]
        except (TypeError, ValueError):
            return None
        if len(values) != 3 or not all(math.isfinite(value) for value in values):
            return None
        if label in {'center', 'box center', '中心', '盒子中心'}:
            key = 'center'
        else:
            key = 'size'
        if key in vectors and vectors[key] != values:
            return None
        vectors[key] = values

    if not fields.get('receptor_path') or not fields.get('ligand_path'):
        return None
    if 'center' not in vectors or 'size' not in vectors:
        return None
    return {
        'receptor_path': fields['receptor_path'],
        'ligand_path': fields['ligand_path'],
        'center': vectors['center'],
        'size': vectors['size'],
    }


def looks_like_design(query: str) -> bool:
    return has_generation_intent(query) and not target_lookup_result_annotation(query)


def requests_lipinski_compliant_candidates(query: str) -> bool:
    """Recognize a rule the newly generated molecules must meet."""
    rule = r"(?:Lipinski(?:['’]s)?|利平斯基|五规则|rule[ -]of[ -]five)"
    compliance = (r"(?<!不)(?:符合|满足|遵循)\s*" + rule
                  + r"|\b(?:satisf(?:y|ying)|meet(?:ing)?|follow(?:ing)?|comply(?:ing)?\s+with)\s+(?:the\s+)?" + rule
                  + r"|" + rule + r"[ -]compliant\b")
    positive = positive_intent_text(query)
    if not looks_like_design(positive):
        return False
    for clause in re.split(r"[，,；;。\n]", positive):
        if not re.search(compliance, clause, re.I) or re.search(r"不(?:要求|必|用)|\bnot\b", clause, re.I):
            continue
        if looks_like_design(clause) or re.match(
                r"\s*(?:要求|且|并|均|都|各候选|候选分子|它们)?\s*(?:均|都)?\s*(?:" + compliance + ")", clause, re.I):
            return True
    return False


def requests_generated_properties(query: str) -> bool:
    """Recognize an explicit descriptor step whose subject is the generated candidates."""
    text = positive_intent_text(query)
    field = (r'(?:理化性质|分子性质|性质|分子式|分子量|(?:molecular\s+)?properties|'
             r'molecular\s+weight|MW|logP|TPSA|HBA|HBD|QED)')
    action = re.search(
        r'(?:并且?|再|然后|[，,；;。]|\band\b|\bthen\b)\s*(?:计算|评估|calculate|compute|assess)\s*'
        r'(?:(?:它们|它|该分子|各候选|每个分子)的|其|their\s+|its\s+|the\s+)?\s*'
        + field + r'(?:\s*(?:和|与|及|、|and\b)\s*' + field + r')*'
        + r'\s*(?=[，,；;。.!！?？]|$)', text, re.I)
    return bool(action and looks_like_design(text[:action.start()]))


def target_lookup_result_annotation(query: str) -> bool:
    """Recognize target-search result wording, not a design request.

    ``返回`` is also part of the generation grammar for requests returning new
    SMILES. In a target lookup, ``返回结构编号`` or ``是否适合后续对接`` is a
    presentation requirement and must not switch planning to target-driven
    design or molecule generation.
    """
    lookup = re.search(
        r'(?:查找|检索|搜索|查询|find|search|lookup|look\s+up).{0,120}'
        r'(?:靶点|蛋白(?:质)?|结构|pdb|uniprot|target|protein|structures?)',
        query, re.I,
    )
    annotation = re.search(
        r'返回|给出|列出|说明|编号|是否|适合|可用于|后续|继续|'
        r'\bsuitable\b|\bappropriate\b|\bfor\s+docking\b|\bdocking\b',
        query, re.I,
    )
    if not lookup or not annotation:
        return False
    return not re.search(
        r'生成|设计|创建|合成|制作|构建|开始.*(?:对接|docking)|'
        r'运行.*(?:对接|docking)|执行.*(?:对接|docking)|'
        r'\b(?:generate|design|create|synthesi[sz]e|produce|make|build)\b',
        query, re.I,
    )


def extract_target_hint(query: str) -> str:
    request = analyze_target_request(query)
    return request.targets[0] if not request.needs_clarification else query.strip()


_STRUCTURE_METHOD_OBJECT = re.compile(
    rf"(?:{TARGET_PATTERN.pattern}\s*(?:的\s*)?)?"
    r"(?:蛋白(?:质)?结构预测(?:的)?(?:方法|算法|原理|技术|思路|理论)|"
    r"\bprotein\s+structure\s+prediction\s+"
    r"(?:methods?|algorithms?|principles?|techniques?|approach(?:es)?|theor(?:y|ies))\b)"
    rf"(?:\s+(?:for|of)\s+{TARGET_PATTERN.pattern})?", re.I,
)


def target_lookup_text(query: str) -> str:
    """Lookup-only view; knowledge targets cannot authorize a structure search.

    Leave the caller's original query/audit and global design target collection
    untouched. Preserve all remaining qualifications for tool validation.
    """
    # In a complete short lookup, "target information" is the lookup object,
    # not a forward target label whose identifier is the word "information".
    # Do not normalize partial matches, unknown members or qualified requests.
    short = re.fullmatch(
        rf"\s*(?:please\s+)?(?:lookup|look\s+up|find|search)\s+"
        rf"({TARGET_PATTERN.pattern})\s+target\s+information[.!?]?\s*", query, re.I,
    )
    if short:
        return 'lookup ' + short[1]
    return _STRUCTURE_METHOD_OBJECT.sub("", query)


def looks_like_unauthorized_tool_request(query: str) -> bool:
    lowered = query.lower()
    return (
        "run_docking" in lowered
        and any(marker in lowered for marker in ("未授权", "忽略系统限制", "unauthorized", "ignore system"))
    )


def extract_requested_count(query: str, default: int) -> int:
    return parse_generation_count(query, default=default)


def requested_count(query: str, metadata: dict[str, Any] | None, *, default: int,
                    extract_count: Callable[..., int]) -> int:
    metadata = metadata or {}
    return validate_generation_count(
        metadata["requested_count"] if "requested_count" in metadata
        else extract_count(query, default=default)
    )


def extract_top_n(query: str, default: int) -> int:
    match = re.search(r"(?:前\s*|top\s*)(\d+)", query, re.I)
    if not match:
        return default
    return max(1, min(100, int(match.group(1))))


# ASCII dots inside SMILES, decimals and paths are not sentence boundaries.
# Ambiguous unspaced ASCII prose stays in the same clause (fail closed).
_CLAUSE_END = r"[,，;；。!！?？\n]|\.(?=\s|$|[\u3400-\u9fff\"'”’])|但是|而是|\bbut\b|\band\s+then\b"
# Only the explicit activity opt-out followed by an ADMET calculation has an
# unambiguous restart here. Do not generalize conjunction + verb: "do not
# calculate properties and predict activity" shares its negation. Full-clause
# matching also prevents a later operation from inheriting this exemption.
_OPERATION_SMILES = r"[A-Za-z0-9@+\-\[\]()=#./\\%:]+"
_NEW_OPERATION = re.compile(
    r"\s*(?:skip\s+activity\s+and\s+|不要\s*预测\s*活性\s*并(?:且)?\s*)"
    r"(?P<positive>(?:please\s+)?calculate\s+ADMET(?:\s+for\s+" + _OPERATION_SMILES + r")?"
    r"|(?:请\s*)?计算\s*(?:ADMET|" + _OPERATION_SMILES + r"\s*的\s*ADMET))\s*", re.I,
)
# Consume explicit path tokens before considering any internal punctuation.
# Ambiguous unspaced text attached to a path stays in its original clause.
_PATH_START = r"(?:[A-Za-z]:[\\/]|\.{1,2}[\\/]|[\\/])"
_PATH_TOKEN = (
    rf'"{_PATH_START}[^"\r\n]*"|'
    rf"'{_PATH_START}[^'\r\n]*'|"
    rf"`{_PATH_START}[^`\r\n]*`|"
    + _PATH_START + r"[^\s,，;；。!?！？\"'`]*(?<!\.)"
)
_CLAUSE_PART = re.compile(_PATH_TOKEN + r"|(?P<boundary>" + _CLAUSE_END + r")", re.I)
# 别 is imperative except inside compounds such as 分别/特别/识别/类别.
_NEGATION = r"(?:不要|无需|不需要|不做|不运行|不预测|禁止|跳过|避免|勿|(?<![分特区识鉴辨类级差个])别|\bwithout\b|\bdo\s+not\b|\bdon't\b|\bskip\b|\bexclude\b)"


def without_past_result_actions(query: str) -> str:
    """Remove action words used as references to past results."""
    # In “刚才的水溶解度预测是多少”, prediction is a result noun.
    # Retain its endpoint and predicate, and leave new action clauses intact.
    query = re.sub(r'((?:刚才|上一轮|之前|上次)\s*的[^，。？！!?；;\n]+?)'
                   r'预测(?=\s*(?:是多少|为多少))', r'\1', query)
    return re.sub(r'(刚才|上一轮|之前|上次)\s*'
                  r'(?:给出|返回|报告|展示|显示|计算|预测)\s*的', r'\1的', query)


def positive_intent_text(query: str) -> str:
    """Remove negated operation spans, not unrelated positive clauses.

    This is shared by routing and deterministic planning. In particular the
    mere occurrence of a tool/endpoint in a prohibition is not authorization.
    """
    from src.agent.contracts.docking_intent import without_docking_energy_prohibition
    query = without_docking_energy_prohibition(query)
    text = re.sub(r"不要\s*跳过|do\s+not\s+skip|don't\s+skip", "", query, flags=re.I)
    # Keep punctuation intact: it can be part of a receptor path, a decimal
    # docking-box coordinate, or the canonical generation grammar.
    clauses, start = [], 0
    for match in _CLAUSE_PART.finditer(text):
        if match.group("boundary") is not None:
            clauses.extend((text[start:match.start()], match[0]))
            start = match.end()
    clauses.append(text[start:])
    output = []
    for clause in clauses:
        if restart := _NEW_OPERATION.fullmatch(clause):
            clause = restart['positive']
        # Also cover the ordinary object-before-prohibition order.
        clause = re.sub(
            r"(?:admet|adme|毒性|活性|RG-MPNN|对接|docking|类药性)\s*" + _NEGATION + r".*$",
            "", clause, flags=re.I,
        )
        output.append(re.split(_NEGATION, clause, maxsplit=1, flags=re.I)[0])
    return "".join(output)


def wants_admet(query: str) -> bool:
    return bool(re.search(r"(?<![A-Za-z0-9_])admet?(?![A-Za-z0-9_])|吸收|分布|代谢|排泄|毒性|风险",
                          positive_intent_text(query), re.I))


def wants_activity(query: str) -> bool:
    """Return true only for a positive activity request, not a negation."""
    if not isinstance(query, str):
        return False
    return bool(re.search(r"活性|\b(?:potency|activity|inhibition)\b|(?<![A-Za-z0-9_-])RG-MPNN(?![A-Za-z0-9_-])",
                          positive_intent_text(query), re.I))


def property_calculation_forbidden(query: str) -> bool:
    """A design bound does not override an explicit descriptor prohibition."""
    from src.agent.contracts.docking_intent import without_docking_energy_prohibition
    query = without_docking_energy_prohibition(query)
    text = re.sub(r"不要\s*跳过|do\s+not\s+skip|don't\s+skip", "", query, flags=re.I)
    operation = r"(?:(?:计算|运行|执行|评估|预测|进行)|\b(?:calculat\w*|comput\w*|run\w*|perform\w*|predict\w*)\b)"
    subject = r"(?:(?:候选)?(?:分子的?)?(?:理化性质|性质|描述符|分子量)|property_calculator\b|\b(?:properties|property|descriptors?|MW|logP|TPSA|QED|HBA|HBD)\b)"
    qualifier = r"(?:(?:任何|全部|分子)|\b(?:any|all|the|molecular)\b)"
    negation = rf"(?:{_NEGATION}|不(?=计算|运行|执行|评估|预测|进行))"
    calculation_tail = r"(?=\s*(?:$|[,，;；。.!！?？\n]|计算|评估|\b(?:calculations?|computations?|assessments?|checks?)\b))"
    return bool(re.search(
        negation + rf"\s*(?:(?:{operation}\s*)+(?:{qualifier}\s*)*{subject}"
        + rf"|(?:{qualifier}\s*)*{subject}{calculation_tail})"
        + rf"|{subject}\s*" + negation + rf"\s*{operation}", text, re.I))


def restricted_properties(query: str) -> bool:
    """An explicit 'only' limits the default assessment bundle to descriptors."""
    positive = positive_intent_text(query)
    return bool(
        re.search(r"只|仅|\bonly\b", positive, re.I)
        and re.search(r"分子量|logp|molecular\s+weight|性质|properties", positive, re.I)
        and not re.search(r"类药性|成药性|lipinski|drug.likeness", positive, re.I)
        and not wants_admet(query)
        and not wants_activity(query)
    )
