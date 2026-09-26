"""Conservative target selection boundary for planned scientific requests.

Entity vocabulary is shared; negation/switching/selectivity is deliberately not
inferred by the activity-family resolver. Such requests require clarification.
"""
import re
from dataclasses import dataclass

from src.target_identifiers import TARGET_PATTERN, canonical_target_identifier, target_mentions


_VALUE = (
    r'(丁酰胆碱酯酶|[A-Za-z][A-Za-z0-9_-]*|'
    r'[\u4e00-\u9fff]+?(?=生成|设计|候选|[和及与、，,。\s]|$))'
)
_LABEL = re.compile(
    r'(?:针对|靶点(?:\s*[:：]|\s+|(?=[A-Za-z]))|改为|换成|'
    r'\b(?:against|target(?:ing)?)\s*[:=]?)\s*' + _VALUE, re.I,
)
_FOLLOWING = re.compile(r'\s*(?:和|及|与|、|[,，/]|\band\b|\bor\b)\s*' + _VALUE, re.I)
_CANDIDATE_SELECTION = re.compile(
    r'[ \t]*(?:[,，]|\band\b)[ \t]*select[ \t]+top[ \t]+'
    r'(?:100|[1-9][0-9]?)(?:[ \t]+(?:candidates|molecules))?'
    r'(?:[ \t]*[.!?。！？])?(?u:\s*)', re.I | re.ASCII,
)
_FOR = re.compile(r'\bfor\s+' + _VALUE, re.I)
_IDENTIFIER = re.compile(r'(?<![A-Za-z0-9_-])([A-Za-z][A-Za-z0-9_-]*)(?![A-Za-z0-9_-])')
_ACTION_WORDS = frozenset({
    'generate', 'design', 'screen', 'predict', 'calculate', 'report', 'then', 'do',
    'please', '生成', '设计', '筛选', '预测', '计算',
})
_QUALIFIED = re.compile(
    r'不要针对|不针对|不考虑|并非|不是|排除|而非|改为|换成|选择性|'
    r'\b(?:except|exclude|instead|rather|selective|switch)\b|'
    r'\bnot\s+(?:target(?:ing)?\b|against\b|' + TARGET_PATTERN.pattern + r')', re.I,
)
TARGET_CLARIFICATION = '请明确本次使用的单一靶点标识；多个靶点、未知标识或否定/切换/选择性条件不能自动选择。'


@dataclass(frozen=True)
class TargetRequest:
    targets: tuple[str, ...]
    unknown: tuple[str, ...]
    explicit: bool
    qualified: bool

    @property
    def needs_clarification(self) -> bool:
        return len(self.targets) != 1 or bool(self.unknown) or self.qualified


def _identifier_like(value: str) -> bool:
    """Bounded entity syntax, not arbitrary prose or evidence of availability."""
    if value.casefold() in _ACTION_WORDS:
        return False
    return canonical_target_identifier(value) is not None or (
        _IDENTIFIER.fullmatch(value) is not None
        and len(value) > 1
        and (value.isupper() or any(char.isdigit() for char in value))
    )


def _is_candidate_selection(query: str, following: re.Match[str]) -> bool:
    """Only a complete terminal selection clause ends the target list."""
    return (
        following[1].casefold() == 'select'
        and _CANDIDATE_SELECTION.fullmatch(query, following.start()) is not None
    )


_ANALYSIS_BOUNDARY = re.compile(r'[;；\r\n]|\Z')
_ANALYSIS_SPACE = re.compile(r'[ \t]*')
_ANALYSIS_HEAD = re.compile(
    r'[ \t]*(?:请[ \t]*|please[ \t]+)?'
    r'(?:计算|预测|评估|分析|'
    r'(?:calculate|compute|predict|assess|evaluate|analyse|analyze)(?![A-Za-z0-9_-]))'
    r'[ \t]*', re.I,
)
_ANALYSIS_ADMET = re.compile(r'(?:ADMET|ADME)(?![A-Za-z0-9_-])', re.I)
_ANALYSIS_PROPERTY = re.compile(
    r'(?:分子量|分子性质|分子属性|理化性质|性质|属性|类药性|成药性|'
    r'(?:LogP|TPSA|HBD|HBA|QED|Lipinski|molecular[ \t]+weight|'
    r'properties|property|drug(?:-|[ \t]+)likeness)(?![A-Za-z0-9_-]))', re.I,
)
_ANALYSIS_ACTIVITY = re.compile(
    r'[ \t]*(?:的[ \t]*)?(?:活性|'
    r'(?:activity|potency|pIC50|IC50)(?![A-Za-z0-9_-]))', re.I,
)
_ANALYSIS_JOIN = re.compile(
    r'[ \t]*(?:和|及|与|并|、|[,，]|(?<![A-Za-z0-9_-])and(?![A-Za-z0-9_-]))[ \t]*',
    re.I,
)


def _analysis_item(query: str, position: int, end: int) -> tuple[int, str] | None:
    for pattern, role in ((_ANALYSIS_ADMET, 'admet'), (_ANALYSIS_PROPERTY, 'property')):
        if match := pattern.match(query, position, end):
            return match.end(), role
    target = TARGET_PATTERN.match(query, position, end)
    if target is not None:
        activity = _ANALYSIS_ACTIVITY.match(query, target.end(), end)
        if activity is not None:
            return activity.end(), 'activity'
    return None


def _analysis_clause(query: str, start: int, end: int) -> bool:
    """Recognize one whole analytical clause, never an unknown-text suffix."""
    head = _ANALYSIS_HEAD.match(query, start, end)
    if head is None:
        return False
    position = head.end()
    has_admet = has_activity = False
    while position < end:
        item = _analysis_item(query, position, end)
        if item is None:
            return False
        position, role = item
        has_admet = has_admet or role == 'admet'
        has_activity = has_activity or role == 'activity'
        position = _ANALYSIS_SPACE.match(query, position, end).end()
        if position < end and query[position] in '.!?。！？':
            position = _ANALYSIS_SPACE.match(query, position + 1, end).end()
            return position == end and has_admet and has_activity
        if position == end:
            return has_admet and has_activity
        join = _ANALYSIS_JOIN.match(query, position, end)
        if join is None:
            return False
        position = join.end()
    return False  # No item after an action or a trailing join.


def _analysis_clause_intervals(query: str) -> tuple[tuple[int, int], ...]:
    intervals = []
    start = 0
    for boundary in _ANALYSIS_BOUNDARY.finditer(query):
        end = boundary.start()
        if _analysis_clause(query, start, end):
            intervals.append((start, end))
        start = boundary.end()
    return tuple(intervals)


def analyze_target_request(query: str) -> TargetRequest:
    targets = target_mentions(query)
    values = []
    label_matches = list(_LABEL.finditer(query))
    # "for" is also a purpose marker; require entity syntax, not "for testing".
    for_matches = [match for match in _FOR.finditer(query) if _identifier_like(match[1])]
    # Unlabelled lists may start with unknown identifiers, but need a known
    # target anchor: property lists (ADMET, pIC50) and SMILES are not targets.
    list_matches = []
    list_end = 0
    analysis_intervals = _analysis_clause_intervals(query)
    interval_index = 0
    for match in _IDENTIFIER.finditer(query):
        while (interval_index < len(analysis_intervals)
               and analysis_intervals[interval_index][1] <= match.start()):
            interval_index += 1
        if (interval_index < len(analysis_intervals)
                and analysis_intervals[interval_index][0] <= match.start()
                < analysis_intervals[interval_index][1]):
            continue  # Only inferred list starts; explicit/known scans stay intact.
        if match.start() < list_end or not _identifier_like(match[1]):
            continue
        members = [match[1]]
        list_end = match.end()
        while following := _FOLLOWING.match(query, list_end):
            if not _identifier_like(following[1]):
                break
            members.append(following[1])
            list_end = following.end()
        if len(members) > 1 and any(canonical_target_identifier(value) for value in members):
            list_matches.append(match)
    starts = (*label_matches, *for_matches, *list_matches, *TARGET_PATTERN.finditer(query))
    consumed_end = 0
    for match in sorted(starts, key=lambda match: match.start()):
        if match.end() <= consumed_end:
            continue
        values.append(match[1])
        consumed_end = match.end()
        while following := _FOLLOWING.match(query, consumed_end):
            if following[1].casefold() in _ACTION_WORDS or _is_candidate_selection(query, following):
                break
            values.append(following[1])
            consumed_end = following.end()
    unknown = tuple(dict.fromkeys(v for v in values if canonical_target_identifier(v) is None))
    explicit = bool(label_matches or for_matches or list_matches)
    return TargetRequest(targets, unknown, explicit, bool(_QUALIFIED.search(query)))
