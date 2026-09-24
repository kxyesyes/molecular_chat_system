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
    for match in _IDENTIFIER.finditer(query):
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
