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
_SWITCH = r'(?:改(?:为|用)|换成|转而\s*使用)'
_LABEL = re.compile(
    r'(?:针对|靶点(?:\s*[:：]|\s+|(?=[A-Za-z]))|' + _SWITCH + r'|'
    r'\b(?:against|target(?:ing)?)\s*[:=]?)\s*' + _VALUE, re.I,
)
# Delimiters cannot occur in the body: failed starts never rescan another
# "以" or backtrack across "为". Keep the whole body, including unknown prose.
_TARGET_ROLE = re.compile(r'以([^以为。！？!?;；\r\n]+)为\s*(靶点)')
_ROLE_DESIGN = re.compile(r'\s*(?:[,，]\s*)?(?:设计|生成)')
_ROLE_DESIGN_BOUNDARY = re.compile(r'[,，.。;；!！?？\r\n]')
_ROLE_DESIGN_OBJECT = re.compile(
    r'(?:[0-9零〇一二两三四五六七八九十百千万]+个?)?'
    r'(?:类药)?(?:候选)?(?:分子|化合物|结构)',
)
_ROLE_WRAPPERS = {'“': '”', '‘': '’', '"': '"', "'": "'", '（': '）', '(': ')'}
_ROLE_TAIL_SEPARATOR = re.compile(r'[\s,，.。;；!！?？]+')
_ROLE_TAIL_CONNECTOR = re.compile(
    r'(?:并且|同时|然后|之后|接着|且|并|和|及|与|、|再|\band\b)\s*', re.I,
)
_ROLE_TARGET_ACTION = re.compile(r'(?:抑制|' + _SWITCH + r')\s*')
_ROLE_TARGET_VALUE = re.compile(_VALUE, re.I)
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
    r'不要针对|不针对|(?:不(?:要|能)?|别|勿)(?:\s*再)?\s*以|'
    r'不考虑|并非|不是|排除|而非|选择性|' + _SWITCH + r'|'
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


def _role_value(value: str) -> str:
    """Unwrap only one balanced pair around a complete known identifier."""
    value = value.strip()
    if len(value) >= 2 and _ROLE_WRAPPERS.get(value[0]) == value[-1]:
        inner = value[1:-1].strip()
        if canonical_target_identifier(inner) is not None:
            return inner
    return value


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


def _role_tail(query: str, start: int, end: int) -> tuple[list[str], list[tuple[int, int]], bool]:
    """Consume every tail clause; unknown remainder can never earn an exemption.

    Reuse analytical tokens without changing the standalone analysis grammar.
    Target-bearing clauses keep their values; switching stays qualified even
    when the new identifier is known or repeats the original target.
    """
    values = []
    analysis_intervals = []
    position = start
    while position < end:
        separator = _ROLE_TAIL_SEPARATOR.match(query, position, end)
        if separator is not None:
            position = separator.end()
        if position == end:
            return values, analysis_intervals, True
        connector = _ROLE_TAIL_CONNECTOR.match(query, position, end)
        if connector is not None:
            position = connector.end()
            if position == end:
                position = connector.start()
                break  # A conjunction requires a following action.
        elif separator is None:
            break
        clause_start = position
        if head := _ANALYSIS_HEAD.match(query, position, end):
            item = _analysis_item(query, head.end(), end)
            if item is None:
                break
            position = item[0]
            while join := _ANALYSIS_JOIN.match(query, position, end):
                item = _analysis_item(query, join.end(), end)
                if item is None:
                    break  # The join may introduce a new action, not an item.
                position = item[0]
            analysis_intervals.append((clause_start, position))
        elif action := _ROLE_TARGET_ACTION.match(query, position, end):
            target = _ROLE_TARGET_VALUE.match(query, action.end(), end)
            if target is None:
                break
            values.append(target[1])
            position = target.end()
        else:
            break
    else:
        return values, analysis_intervals, True
    values.append(query[position:end].strip())
    return values, analysis_intervals, False


def analyze_target_request(query: str) -> TargetRequest:
    targets = target_mentions(query)
    role_matches = list(_TARGET_ROLE.finditer(query))
    values = [_role_value(match[1]) for match in role_matches]
    role_labels = set()
    role_analysis_intervals = []
    for index, match in enumerate(role_matches):
        # Each body ends before the next role, so scans never overlap.
        limit = role_matches[index + 1].start() if index + 1 < len(role_matches) else len(query)
        design = _ROLE_DESIGN.match(query, match.end(), limit)
        if design is None:
            # A repeated role may omit the design verb, but must not hide the
            # remaining target-bearing clauses behind an earlier exemption.
            tail_values, tail_intervals, _ = _role_tail(query, match.end(), limit)
            values.extend(tail_values)
            role_analysis_intervals.extend(tail_intervals)
            continue
        boundary = _ROLE_DESIGN_BOUNDARY.search(query, design.end(), limit)
        end = boundary.start() if boundary else limit
        body = query[design.end():end]
        # Validate the whole object, not merely the action prefix. Numbers are
        # only syntax here; the planner retains count validation and limits.
        if _ROLE_DESIGN_OBJECT.fullmatch(''.join(body.split())) is not None:
            tail_values, tail_intervals, complete = _role_tail(query, end, limit)
            values.extend(tail_values)
            role_analysis_intervals.extend(tail_intervals)
            if complete:
                role_labels.add(match.start(2))
        else:
            # Preserve unrecognized text even without whitespace after 靶点,
            # where the forward-label scan cannot provide a fallback guard.
            values.append(query[design.start():end].strip())
    # Only a complete role, design clause and fully consumed tail are exempt;
    # other labels (including "靶点: UNKNOWN") retain their original authority.
    label_matches = [match for match in _LABEL.finditer(query) if match.start() not in role_labels]
    # "for" is also a purpose marker; require entity syntax, not "for testing".
    for_matches = [match for match in _FOR.finditer(query) if _identifier_like(match[1])]
    # Unlabelled lists may start with unknown identifiers, but need a known
    # target anchor: property lists (ADMET, pIC50) and SMILES are not targets.
    list_matches = []
    list_end = 0
    analysis_intervals = _analysis_clause_intervals(query)
    if role_analysis_intervals:
        analysis_intervals = tuple(sorted((*analysis_intervals, *role_analysis_intervals)))
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
    explicit = bool(role_matches or label_matches or for_matches or list_matches)
    return TargetRequest(targets, unknown, explicit, bool(_QUALIFIED.search(query)))
