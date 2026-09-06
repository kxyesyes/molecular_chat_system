from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from src.agent.persistence.redaction import looks_like_credential


MIN_GENERATION_COUNT = 1
MAX_GENERATION_COUNT = 10
DEFAULT_GENERATION_COUNT = 1
MAX_TARGET_RECORDS = 3
MAX_TARGET_STRUCTURES = 2
MAX_EVIDENCE_ITEMS = 3
MAX_EVIDENCE_CHARS = 4096
MAX_GENERATION_REQUEST_CHARS = 16384
MAX_GENERATION_OCCURRENCES = 64
MAX_GENERATION_COUNT_TOKEN_DIGITS = 32
_COUNT_UNSET = object()


class GenerationRequestError(ValueError):
    """A caller-supplied generation request violates the public contract."""

    def __init__(
        self,
        message: str,
        *,
        value: Any = None,
        reason: str = "malformed_requested_count",
    ):
        super().__init__(message)
        self.value = value
        self.reason = reason


def generation_request_error_details(
    exc: GenerationRequestError,
    **extra: Any,
) -> dict[str, Any]:
    """Return one stable public classification for every generation boundary."""
    details = {"reason": exc.reason, **extra}
    if exc.value is not None:
        details["rejected_value"] = deepcopy(exc.value)
    return details


class TargetEvidenceError(GenerationRequestError):
    """Target evidence is unsafe or cannot satisfy the canonical schema."""


_PLACEHOLDERS = frozenset({"", "none", "null", "n/a", "unknown"})
_UNTRUSTED_TRUE_FLAGS = frozenset(
    {"demo_mode", "fallback_used", "is_demo", "fallback", "untrusted"}
)
_UNTRUSTED_FALSE_FLAGS = frozenset({"trusted", "authoritative"})
_TRUST_QUALITY_FIELDS = _UNTRUSTED_TRUE_FLAGS | _UNTRUSTED_FALSE_FLAGS
_TRUSTED_EVIDENCE_SOURCES = frozenset(
    {
        "alphafold",
        "local",
        "local_target_db",
        "official",
        "pdb",
        "rcsb",
        "rcsb_pdb",
        "target_database",
        "uniprot",
    }
)
_TARGET_TEXT_FIELDS = (
    "gene_symbol",
    "target_identifier",
    "uniprot_id",
    "target_name",
    "target_id",
    "source_record_id",
    "source",
    "database",
    "source_url",
)
_STRUCTURE_TEXT_FIELDS = (
    "structure_id",
    "source",
    "database",
    "source_record_id",
    "source_url",
    "download_url",
)
_EVIDENCE_TEXT_FIELDS = (
    "source",
    "database",
    "id",
    "source_record_id",
    "structure_id",
    "uniprot_id",
    "source_url",
)
_PROVENANCE_FIELDS = frozenset(
    {
        "source",
        "database",
        "source_url",
        "recommended_structures",
        "evidence",
        "artifacts",
    }
)
_CREDENTIAL_PATTERN = re.compile(
    r"(?i)(?:\bsk-[A-Za-z0-9_-]+|\b(?:api[_ -]?key|access[_ -]?token|"
    r"secret|password)\b(?:\s*[:=]|\Z)|(?:^|[_ -])(?:token|secret|password)"
    r"(?:$|[_ -]))"
)
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_SAFE_GENE_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,19}\Z")
_INSTRUCTION_IDENTIFIER_LEXICAL_COMBINATIONS = (
    ("follow", "instruction"),
    ("ignore", "instruction"),
    ("output", "only"),
    ("override", "prompt"),
    ("override", "system"),
    ("override", "task"),
    ("respond", "with"),
    ("return", "only"),
    ("system", "prompt"),
)
_INSTRUCTION_ACTION_PATTERN = re.compile(
    r"(?:bypass(?:ed|es|ing)?|circumvent(?:ed|s|ing)?|"
    r"disabl(?:e|ed|es|ing)|disregard(?:ed|s|ing)?|"
    r"evad(?:e|ed|es|ing)|follow(?:ed|s|ing)?|"
    r"ignor(?:e|ed|es|ing)|obey(?:ed|s|ing)?|"
    r"overrid(?:e|den|es|ing)|remov(?:e|ed|es|ing))"
)
_INSTRUCTION_OBJECT_PATTERN = re.compile(
    r"(?:guardrails?|instructions?|polic(?:y|ies)|prompts?|rules?|"
    r"safeguards?|safet(?:y|ies)|tasks?)"
)
_CREDENTIAL_TEXT_PATTERN = re.compile(
    r"(?:bearer[a-z0-9._~+/=-]{8,}|accesstoken|apikey|authtoken|"
    r"credential|githubpat|password|"
    r"secret(?:key|token|[0-9]|$))"
)
_UNIPROT_ACCESSION = re.compile(
    r"(?:[OPQ][0-9][A-Z0-9]{3}[0-9]|[A-NR-Z][0-9](?:[A-Z][A-Z0-9]{2}[0-9]){1,2})\Z",
    re.IGNORECASE,
)
_RCSB_STRUCTURE_ID = re.compile(r"[0-9][A-Z0-9]{3}\Z", re.IGNORECASE)
_ALPHAFOLD_STRUCTURE_ID = re.compile(
    rf"AF-(?:{_UNIPROT_ACCESSION.pattern[:-2]})-F[0-9]+\Z",
    re.IGNORECASE,
)
_LOCAL_RECORD_ID = re.compile(
    r"(?:LOCAL|TARGETDB|TARGET_DATABASE)[._-](?P<suffix>[A-Za-z0-9]{1,64})\Z",
    re.IGNORECASE,
)
_LOCAL_RECORD_PREFIX = re.compile(
    r"(?:LOCAL|TARGETDB|TARGET_DATABASE)[._-]+(?P<suffix>.*)\Z",
    re.IGNORECASE,
)
_OFFICIAL_EVIDENCE_HOSTS = frozenset(
    {
        "alphafold.ebi.ac.uk",
        "files.rcsb.org",
        "www.rcsb.org",
        "www.uniprot.org",
    }
)

_ARABIC_COUNT = r"(?<![\w.])[-+\u2212]?\d+(?:\.\d+)?(?![\w.])"
_CHINESE_ARABIC_COUNT = r"[-+\u2212]?\d+(?:\.\d+)?"
_CHINESE_COUNT = r"[零〇一二两三四五六七八九十百]+"
_CHINESE_REQUEST_COUNT = (
    rf"(?:{_CHINESE_ARABIC_COUNT}|{_CHINESE_COUNT})"
    rf"(?![.\d零〇一二两三四五六七八九十百])"
)
_ENGLISH_COUNT_WORD = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty)"
)
_ENGLISH_COUNT = rf"\b{_ENGLISH_COUNT_WORD}\b"
_ENGLISH_DECIMAL_PART = rf"(?:{_ENGLISH_COUNT_WORD}|\d+)"
_CHINESE_DECIMAL_PART = rf"(?:{_CHINESE_COUNT}|\d+)"
_COUNT_TOKEN = rf"(?:{_ARABIC_COUNT}|{_CHINESE_COUNT}|{_ENGLISH_COUNT})"
_ENGLISH_NOUN = r"(?:candidates?|molecules?|compounds?|structures?)"
_GENERATION_VERB = r"(?:generate|design|create|produce|make|build|synthesize)"
_GENERATION_CONNECTOR = (
    r"(?:and[ \t]+(?:(?:then|also)[ \t]+)?|then[ \t]+|plus[ \t]+)"
)
_ENGLISH_COUNT_FOLLOW = (
    rf"(?:{_ENGLISH_NOUN}\b|for\b|"
    rf"{_GENERATION_CONNECTOR}{_GENERATION_VERB}\b)"
)
_ENGLISH_COUNT_BOUNDARY = (
    rf"(?:[ \t]+{_ENGLISH_COUNT_FOLLOW}|"
    rf"[ \t]*(?:[+;,!?]|\b(?:but|instead)\b)|[ \t]*$)"
)
_MAX_ENGLISH_COUNT_MODIFIER_TOKENS = 6
_ENGLISH_COUNT_MODIFIER_TOKEN = (
    rf"(?!(?:{_ENGLISH_NOUN}|{_GENERATION_VERB}|but|instead|for)\b)"
    rf"(?!{_GENERATION_CONNECTOR}{_GENERATION_VERB}\b)"
    r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*"
)
_ENGLISH_COUNT_TAIL = (
    rf"(?:[ \t]+{_ENGLISH_COUNT_MODIFIER_TOKEN})"
    rf"{{0,{_MAX_ENGLISH_COUNT_MODIFIER_TOKENS}}}"
    rf"(?={_ENGLISH_COUNT_BOUNDARY})"
)
_CHINESE_REFERENCE = r"[A-Za-z0-9\u3400-\u9fff-]{1,24}"
_CHINESE_MOLECULAR_NOUN = (
    r"(?:分子|化合物|结构|候选|配体|骨架|类似物|抑制剂|激动剂|拮抗剂|药物)"
)
_CHINESE_COUNT_OBJECT_FOLLOW = (
    rf"(?=[^,，。；;、!?！？\r\n]{{0,24}}{_CHINESE_MOLECULAR_NOUN}|用于|给|$)"
)
_CHINESE_TARGET_PREFIX = r"(?:[A-Za-z][A-Za-z0-9-]{0,19}\s*)?"
_CHINESE_REPEAT_PREFIX = (
    rf"(?:生成|创建|设计|制作|构建|合成)\s*{_CHINESE_REQUEST_COUNT}\s*次"
    rf"\s*[、,，]?\s*每次\s*"
)
_COUNT_PATTERNS = (
    re.compile(
        rf"{_CHINESE_REPEAT_PREFIX}(?P<count>{_CHINESE_REQUEST_COUNT})"
        rf"\s*(?:个|种|份)?\s*{_CHINESE_TARGET_PREFIX}"
        rf"{_CHINESE_COUNT_OBJECT_FOLLOW}"
    ),
    re.compile(
        rf"(?i)\b{_GENERATION_VERB}\b[ \t]*(?P<count>{_COUNT_TOKEN})"
        rf"{_ENGLISH_COUNT_TAIL}"
    ),
    re.compile(
        rf"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*"
        rf"(?P<count>{_CHINESE_REQUEST_COUNT})\s*(?:个|种|份)?\s*"
        rf"{_CHINESE_COUNT_OBJECT_FOLLOW}"
    ),
)
_CLAUSE_TERMINATED_COUNT_PATTERNS = (
    re.compile(
        rf"(?i)\b{_GENERATION_VERB}\b[ \t]*"
        rf"(?P<count>(?<![\w.])[-+\u2212]?\d+(?:\.\d+)?)"
        rf"(?=[ \t]*\.[ \t]+{_GENERATION_VERB}\b)"
    ),
)
_COUNT_SLOT_PATTERNS = (
    re.compile(
        rf"{_CHINESE_REPEAT_PREFIX}"
        rf"(?P<count>[^\s个种份，。；、！？\"']+)\s*(?:个|种|份)?\s*"
        rf"{_CHINESE_TARGET_PREFIX}"
        rf"{_CHINESE_COUNT_OBJECT_FOLLOW}"
    ),
    re.compile(
        rf"(?i)\b{_GENERATION_VERB}\b[ \t]*(?P<count>[^\s;!?\"']+)"
        rf"{_ENGLISH_COUNT_TAIL}"
    ),
    re.compile(
        rf"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*"
        rf"(?P<count>[^\s个种份，。；、！？\"']+)\s*(?:个|种|份)?\s*"
        rf"{_CHINESE_COUNT_OBJECT_FOLLOW}"
    ),
)
_GROUPED_COUNT_SEPARATOR = r"(?:[ \t]*[,，][ \t]*|[ \t]+)"
_GROUPED_ARABIC_COUNT = (
    rf"[-+\u2212]?\d+(?:{_GROUPED_COUNT_SEPARATOR}\d+)+"
)
_MALFORMED_GROUPED_COUNT_PATTERNS = (
    re.compile(
        rf"(?i)\b{_GENERATION_VERB}\b[ \t]*"
        rf"(?P<count>{_GROUPED_ARABIC_COUNT})"
        rf"{_ENGLISH_COUNT_TAIL}"
    ),
    re.compile(
        rf"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*"
        rf"(?P<count>{_GROUPED_ARABIC_COUNT})\s*(?:个|种|份)?\s*"
        rf"{_CHINESE_COUNT_OBJECT_FOLLOW}"
    ),
)
_CHINESE_ARABIC_SEPARATOR = r"[ \t]*(?:[,，][ \t]*)?"
_CHINESE_ARABIC_HYBRID_COUNT = (
    rf"(?:{_CHINESE_COUNT}{_CHINESE_ARABIC_SEPARATOR}\d+|"
    rf"\d+{_CHINESE_ARABIC_SEPARATOR}{_CHINESE_COUNT})"
    rf"(?:{_CHINESE_ARABIC_SEPARATOR}(?:{_CHINESE_COUNT}|\d+))*"
)
_MALFORMED_HYBRID_COUNT_PATTERNS = (
    re.compile(
        rf"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*"
        rf"(?P<count>{_CHINESE_ARABIC_HYBRID_COUNT})"
        rf"\s*(?:个|种|份)?\s*"
        rf"{_CHINESE_COUNT_OBJECT_FOLLOW}"
    ),
)
_SPACED_SIGNED_ARABIC_COUNT = r"[-+\u2212][ \t]+\d+(?:\.\d+)?"
_MALFORMED_SPACED_SIGN_COUNT_PATTERNS = (
    re.compile(
        rf"(?i)\b{_GENERATION_VERB}\b[ \t]*"
        rf"(?P<count>{_SPACED_SIGNED_ARABIC_COUNT})"
        rf"{_ENGLISH_COUNT_TAIL}"
    ),
    re.compile(
        rf"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*"
        rf"(?P<count>{_SPACED_SIGNED_ARABIC_COUNT})"
        rf"\s*(?:个|种|份)?\s*"
        rf"{_CHINESE_COUNT_OBJECT_FOLLOW}"
    ),
)
_MALFORMED_PREMASK_COUNT_PATTERNS = (
    *_MALFORMED_GROUPED_COUNT_PATTERNS,
    *_MALFORMED_HYBRID_COUNT_PATTERNS,
    *_MALFORMED_SPACED_SIGN_COUNT_PATTERNS,
)
_MALFORMED_WORD_COUNT_PATTERNS = (
    re.compile(
        rf"(?i)\b{_GENERATION_VERB}\b[ \t]*{_ENGLISH_DECIMAL_PART}"
        rf"(?:[ \t]+|-)point(?:[ \t]+|-){_ENGLISH_DECIMAL_PART}"
        rf"{_ENGLISH_COUNT_TAIL}"
    ),
    re.compile(
        rf"(?i)\b{_GENERATION_VERB}\b[ \t]*{_ENGLISH_DECIMAL_PART}"
        rf"[ \t]+{_ENGLISH_DECIMAL_PART}"
        rf"{_ENGLISH_COUNT_TAIL}"
    ),
    re.compile(
        rf"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*"
        rf"{_CHINESE_DECIMAL_PART}\s*点\s*{_CHINESE_DECIMAL_PART}"
        rf"\s*(?:个|种|份)?\s*"
        rf"{_CHINESE_COUNT_OBJECT_FOLLOW}"
    ),
)
_FUZZY_COUNTS = (
    (
        re.compile(
            rf"(?i)\b{_GENERATION_VERB}\b[ \t]*(?:several|some|a[ \t]+few)"
            rf"{_ENGLISH_COUNT_TAIL}"
        ),
        3,
    ),
    (
        re.compile(
            rf"(?i)\b{_GENERATION_VERB}\b[ \t]*(?:multiple|many)"
            rf"{_ENGLISH_COUNT_TAIL}"
        ),
        5,
    ),
    (
        re.compile(
            rf"(?i)\b{_GENERATION_VERB}\b[ \t]*(?:one|a)"
            rf"{_ENGLISH_COUNT_TAIL}"
        ),
        1,
    ),
    (
        re.compile(
            r"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*(?:几个|若干)"
            r"\s*(?:个|种|份)?\s*(?=(?:类药)?(?:候选)?(?:分子|化合物|结构|候选))"
        ),
        3,
    ),
    (
        re.compile(
            r"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*(?:多个|很多)"
            r"\s*(?:个|种|份)?\s*(?=(?:类药)?(?:候选)?(?:分子|化合物|结构|候选))"
        ),
        5,
    ),
    (
        re.compile(
            r"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*(?:一个|单个)"
            r"\s*(?:个|种|份)?\s*(?=(?:类药)?(?:候选)?(?:分子|化合物|结构|候选))"
        ),
        1,
    ),
)


def validate_generation_count(value: Any, *, field: str = "requested_count") -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise GenerationRequestError(
            f"{field} must be an integer",
            value=value,
            reason="invalid_requested_count_type",
        )
    if not MIN_GENERATION_COUNT <= value <= MAX_GENERATION_COUNT:
        raise GenerationRequestError(
            f"{field} must be between {MIN_GENERATION_COUNT} and "
            f"{MAX_GENERATION_COUNT}",
            value=value,
            reason="requested_count_out_of_range",
        )
    return value


def validate_generation_request_length(query: Any) -> str:
    """Normalize and bound a public generation query before other validation."""
    normalized = str(query)
    if len(normalized) > MAX_GENERATION_REQUEST_CHARS:
        _raise_request_too_long(normalized)
    return normalized


def preflight_generation_request(
    query: str,
    authoritative_count: Any = None,
    *,
    count_supplied: bool = False,
    field: str = "requested_count",
    active_molecular_skill: bool = False,
) -> int | None:
    """Validate one generation boundary without reparsing authoritative counts."""
    query = validate_generation_request_length(query)
    if count_supplied:
        return validate_generation_count(authoritative_count, field=field)
    analysis = _build_generation_analysis(query)
    if analysis.overlength:
        _raise_request_too_long(analysis.query)
    if _analysis_has_generation_intent(
        analysis,
        active_molecular_skill=active_molecular_skill,
    ):
        return validate_generation_count(
            _parse_generation_analysis(analysis, DEFAULT_GENERATION_COUNT),
            field=field,
        )
    return None


def parse_generation_count(query: str, *, default: int = DEFAULT_GENERATION_COUNT) -> int:
    return _parse_generation_analysis(
        _build_generation_analysis(query),
        default,
    )


def _parse_generation_analysis(
    analysis: _GenerationAnalysis,
    default: int,
) -> int:
    if analysis.overlength:
        _raise_request_too_long(analysis.query)
    counts: list[int] = []
    for occurrence in analysis.occurrences:
        count = _parse_generation_occurrence_count(occurrence)
        if count is not None:
            counts.append(count)
    if len(set(counts)) > 1:
        raise GenerationRequestError(
            "conflicting generation counts",
            value=tuple(dict.fromkeys(counts)),
        )
    return counts[0] if counts else default


@dataclass(frozen=True)
class _GenerationPatternMatch:
    category: str
    order: int
    match: re.Match[str]
    fuzzy_count: int | None = None


@dataclass(frozen=True)
class _GenerationOccurrence:
    start: int
    end: int
    matches: tuple[_GenerationPatternMatch, ...]


@dataclass(frozen=True)
class _GenerationAnalysis:
    query: str
    text: str
    occurrences: tuple[_GenerationOccurrence, ...]
    overlength: bool = False


def _raise_request_too_long(query: str) -> None:
    raise GenerationRequestError(
        "generation request is too long",
        value={"length": len(query)},
        reason="request_too_long",
    )


def _build_generation_analysis(query: str) -> _GenerationAnalysis:
    query = str(query)
    if len(query) > MAX_GENERATION_REQUEST_CHARS:
        return _GenerationAnalysis(
            query=query,
            text="",
            occurrences=(),
            overlength=True,
        )
    text = _strip_generation_literals(query)
    occurrences = _collect_generation_occurrences(text)
    masked = _mask_non_actionable_generation_occurrences(
        text,
        occurrences=occurrences,
    )
    actionable = tuple(
        occurrence
        for occurrence in occurrences
        if masked[occurrence.start : occurrence.end].strip()
    )
    return _GenerationAnalysis(
        query=query,
        text=text,
        occurrences=actionable,
    )


def _collect_generation_occurrences(value: str) -> list[_GenerationOccurrence]:
    """Scan each grammar pattern once, then group overlapping matches."""
    found: list[_GenerationPatternMatch] = []
    pattern_groups = (
        ("malformed", _MALFORMED_PREMASK_COUNT_PATTERNS),
        ("malformed", _MALFORMED_WORD_COUNT_PATTERNS),
        ("count", _CLAUSE_TERMINATED_COUNT_PATTERNS),
        ("count", _COUNT_PATTERNS),
        ("slot", _COUNT_SLOT_PATTERNS),
    )
    order = 0
    for category, patterns in pattern_groups:
        for pattern in patterns:
            found.extend(
                _GenerationPatternMatch(category, order, match)
                for match in pattern.finditer(value)
            )
            order += 1
    for pattern, fuzzy_count in _FUZZY_COUNTS:
        found.extend(
            _GenerationPatternMatch("fuzzy", order, match, fuzzy_count)
            for match in pattern.finditer(value)
        )
        order += 1
    if not found:
        return []

    occurrences: list[_GenerationOccurrence] = []
    for item in sorted(
        found,
        key=lambda candidate: (
            candidate.match.start(),
            candidate.match.end(),
            candidate.order,
        ),
    ):
        start, end = item.match.span()
        if occurrences and start < occurrences[-1].end:
            previous = occurrences[-1]
            occurrences[-1] = _GenerationOccurrence(
                previous.start,
                max(previous.end, end),
                (*previous.matches, item),
            )
        else:
            occurrences.append(_GenerationOccurrence(start, end, (item,)))
        if len(occurrences) > MAX_GENERATION_OCCURRENCES:
            raise GenerationRequestError(
                "too many generation occurrences",
                value={"limit": MAX_GENERATION_OCCURRENCES},
            )
    return occurrences


def _parse_generation_occurrence_count(
    occurrence: _GenerationOccurrence,
) -> int | None:
    ordered = sorted(occurrence.matches, key=lambda item: item.order)
    malformed = next(
        (item for item in ordered if item.category == "malformed"),
        None,
    )
    if malformed is not None:
        raise GenerationRequestError(
            "generation count must be an integer",
            value=_safe_count_error_value(malformed.match.groupdict().get("count")),
        )
    for item in ordered:
        if item.category != "count":
            continue
        count = _parse_count_token(item.match.group("count"))
        if count is not None:
            return count
    for item in ordered:
        if item.category == "slot" and _looks_numeric_like_count_token(
            item.match.group("count")
        ):
            token = item.match.group("count")
            raise GenerationRequestError(
                "generation count must be an integer",
                value=_safe_count_error_value(token),
            )
    fuzzy = next(
        (item for item in ordered if item.category == "fuzzy"),
        None,
    )
    if fuzzy is not None:
        return fuzzy.fuzzy_count
    return None


def has_generation_intent(
    query: str,
    *,
    active_molecular_skill: bool = False,
) -> bool:
    """Recognize actionable generation grammar, excluding examples and code."""
    return _analysis_has_generation_intent(
        _build_generation_analysis(query),
        active_molecular_skill=active_molecular_skill,
    )


def _analysis_has_generation_intent(
    analysis: _GenerationAnalysis,
    *,
    active_molecular_skill: bool = False,
) -> bool:
    if analysis.overlength:
        return False
    return any(
        active_molecular_skill
        or _occurrence_has_molecular_scope(analysis.text, occurrence)
        for occurrence in analysis.occurrences
    )

def _matches_generation_grammar(
    value: str,
    *,
    active_molecular_skill: bool = False,
) -> bool:
    occurrences = _collect_generation_occurrences(value)
    return any(
        active_molecular_skill
        or _occurrence_has_molecular_scope(value, occurrence)
        for occurrence in occurrences
    )


_GENERIC_CREATION_VERB = re.compile(
    rf"(?i)\b{_GENERATION_VERB}\b"
)
_MOLECULAR_ENTITY = re.compile(
    r"(?i)(?:molecules?|compounds?|structures?|candidates?|ligands?|"
    r"scaffolds?|analog(?:ue)?s?|inhibitors?|agonists?|antagonists?|drugs?|"
    r"chemotypes?|smiles|chemicals?)\Z"
)
_OBJECT_WORD = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:-[A-Za-z0-9]+)*")
_MAX_OBJECT_MODIFIERS = 6
_RELATIVE_CLAUSE_MARKERS = frozenset(
    {"that", "which", "who", "whom", "whose"}
)
_NON_MOLECULAR_OUTPUT_HEAD = re.compile(
    r"(?i)(?:presentations?|reports?|dashboards?|charts?|tables?|summaries?|"
    r"slides?)\Z"
)
_CHINESE_GENERATION_VERB_PATTERN = re.compile(
    r"(?:生成|创建|设计|制作|构建|合成|给我|提供)"
)
_CHINESE_MOLECULAR_ENTITY = re.compile(
    _CHINESE_MOLECULAR_NOUN
)
_CHINESE_NON_MOLECULAR_OUTPUT_HEAD = re.compile(
    r"(?:报告|报表|图表|仪表盘|表格|摘要|演示文稿|幻灯片)"
)
_OBJECT_BOUNDARY = re.compile(
    r"(?i)(?:[.;!?]|\b(?:about|based\s+on|concerning|containing|contains?|"
    r"covering|describing|featuring|for|from|including|includes?|of|on|"
    r"regarding|that\s+(?:contain|contains|include|includes)|with|without)\b|"
    r"\b(?:and|but|or)\s+(?:(?:also|then)\s+)?(?:analy(?:s|z)(?:e|es|ed|ing)|"
    r"assess(?:es|ed|ing)?|calculat(?:e|es|ed|ing)|compar(?:e|es|ed|ing)|"
    r"comput(?:e|es|ed|ing)|discuss(?:es|ed|ing)?|evaluat(?:e|es|ed|ing)|"
    r"explain(?:s|ed|ing)?|predict(?:s|ed|ing)?|review(?:s|ed|ing)?|"
    r"screen(?:s|ed|ing)?|search(?:es|ed|ing)?|summari(?:ze|zes|zed|zing))\b)"
)


def _has_immediate_molecular_head(object_phrase: str) -> bool:
    """Recognize a molecular head after only a bounded modifier sequence.

    Relative clauses and relational predicates start subordinate descriptions,
    so their molecular terms cannot retroactively change the generated head.
    """
    modifiers = 0
    position = 0
    for match in _OBJECT_WORD.finditer(object_phrase):
        if object_phrase[position : match.start()].strip():
            return False
        token = match.group(0)
        normalized = token.casefold()
        if _MOLECULAR_ENTITY.fullmatch(token):
            return True
        if _NON_MOLECULAR_OUTPUT_HEAD.fullmatch(token):
            return False
        if normalized in _RELATIVE_CLAUSE_MARKERS:
            return False
        modifiers += 1
        if modifiers > _MAX_OBJECT_MODIFIERS:
            return False
        position = match.end()
    return False


def _has_chinese_molecular_head(object_phrase: str) -> bool:
    """Classify the first direct head in the final Chinese noun phrase."""
    head_phrase = object_phrase.rsplit("的", maxsplit=1)[-1]
    molecular = _CHINESE_MOLECULAR_ENTITY.search(head_phrase)
    non_molecular = _CHINESE_NON_MOLECULAR_OUTPUT_HEAD.search(head_phrase)
    if molecular is None:
        return False
    return non_molecular is None or molecular.start() < non_molecular.start()


def _occurrence_has_molecular_scope(
    value: str,
    occurrence: _GenerationOccurrence,
) -> bool:
    occurrence_text = value[occurrence.start : occurrence.end]
    verb = re.search(rf"(?i)\b{_GENERATION_VERB}\b", occurrence_text)
    if verb is None:
        chinese_verb = _CHINESE_GENERATION_VERB_PATTERN.search(occurrence_text)
        if chinese_verb is None:
            return False
        object_phrase = value[
            occurrence.start + chinese_verb.end() : min(
                len(value), occurrence.end + 200
            )
        ]
        object_phrase = re.split(
            r"(?:[,，](?!\s*[0-9])|[。；;!?！？]|关于|有关|描述|涉及|包含|含有|带有|"
            r"并且|然后|同时)",
            object_phrase,
            maxsplit=1,
        )[0]
        return _has_chinese_molecular_head(object_phrase)
    if not _GENERIC_CREATION_VERB.fullmatch(verb.group(0)):
        return False
    window = value[
        occurrence.start : min(len(value), occurrence.end + 200)
    ]
    connector = re.search(
        rf"(?i)\b(?:and(?:\s+(?:then|also))?|then|plus)\s+{_GENERATION_VERB}\b",
        window[1:],
    )
    if connector:
        window = window[: connector.start() + 1]
    malformed_end = max(
        (
            item.match.end() - occurrence.start
            for item in occurrence.matches
            if item.category == "malformed"
        ),
        default=None,
    )
    if malformed_end is not None:
        object_phrase = window[malformed_end:].strip()
    else:
        object_phrase = window[verb.end() :].strip()
        object_phrase = re.sub(
            rf"(?i)^(?:{_COUNT_TOKEN}|several|some|a\s+few|multiple|many|one|a)\b\s*",
            "",
            object_phrase,
            count=1,
        )
    object_phrase = _OBJECT_BOUNDARY.split(object_phrase, maxsplit=1)[0]
    return bool(
        _has_immediate_molecular_head(object_phrase)
        or re.match(
            r"[BCNOFPSI][BCNOFPSIcnops0-9@+\-\[\]()=#\\/.]{1,}\b",
            object_phrase,
        )
    )


def _actionable_generation_clauses(query: str) -> list[str]:
    unquoted = _strip_generation_literals(query)
    return [
        _mask_non_actionable_generation_occurrences(clause)
        for clause in _split_generation_clauses(unquoted)
    ]


def _strip_generation_literals(query: str) -> str:
    normalized = unicodedata.normalize("NFKC", str(query))
    normalized = re.sub(r"(?i)\be\.g\.", "for example", normalized)
    without_code = re.sub(
        r"```[\s\S]*?(?:```|\Z)|~~~[\s\S]*?(?:~~~|\Z)",
        " ",
        normalized,
    )
    without_code = re.sub(r"(?<!`)`[^`\r\n]+`(?!`)", " ", without_code)
    unquoted = re.sub(
        r'"[^"\r\n]*"|“[^”\r\n]*”|\'[^\'\r\n]*\'|‘[^’\r\n]*’',
        " ",
        without_code,
    )
    return unquoted


def _split_generation_clauses(value: str) -> list[str]:
    protected_spans = [
        (occurrence.start, occurrence.end)
        for occurrence in _collect_generation_occurrences(value)
    ]
    protected_index = 0

    separated: list[str] = []
    for index, character in enumerate(value):
        while (
            protected_index < len(protected_spans)
            and index >= protected_spans[protected_index][1]
        ):
            protected_index += 1
        protected = (
            protected_index < len(protected_spans)
            and protected_spans[protected_index][0]
            <= index
            < protected_spans[protected_index][1]
        )
        if protected:
            separated.append(character)
            continue
        if character == "+":
            suffix = value[index + 1 :].lstrip()
            is_numeric_sign = bool(re.match(r"(?:\d|\.)", suffix))
            separated.append(character if is_numeric_sign else "\n")
            continue
        if character in ";；!?。！？\r\n、":
            separated.append("\n")
            continue
        if character in ",，":
            before = value[index - 1] if index else ""
            after = value[index + 1] if index + 1 < len(value) else ""
            prefix = value[:index].rstrip().casefold()
            introduces_example = bool(
                re.search(
                    r"(?:for example|as an? example|such as|例如|比如)$",
                    prefix,
                )
            )
            separated.append(
                character
                if (
                    before.isdigit() and after.isdigit()
                ) or introduces_example
                else "\n"
            )
            continue
        if character == ".":
            separated.append("\n")
            continue
        separated.append(character)

    return re.split(
        r"(?i)\n+|\b(?:but|instead)\b|(?:但是|而是|但|只)",
        "".join(separated),
    )


def _generation_occurrence_spans(value: str) -> list[tuple[int, int]]:
    return [
        (occurrence.start, occurrence.end)
        for occurrence in _collect_generation_occurrences(value)
    ]


def _mask_non_actionable_generation_occurrences(
    clause: str,
    *,
    occurrences: Sequence[_GenerationOccurrence] | None = None,
) -> str:
    resolved_occurrences = (
        list(occurrences)
        if occurrences is not None
        else _collect_generation_occurrences(clause)
    )
    if not resolved_occurrences:
        return clause
    spans = [
        (occurrence.start, occurrence.end)
        for occurrence in resolved_occurrences
    ]

    masked = list(clause)
    previous_end = 0
    for index, (start, end) in enumerate(spans):
        next_start = (
            spans[index + 1][0]
            if index + 1 < len(spans)
            else len(clause)
        )
        bridge = clause[previous_end:start]
        boundaries = (
            list(
                re.finditer(
                    r"(?i)\b(?:and|then)\b|(?:然后|并且|并|再|且)",
                    bridge,
                )
            )
            if index
            else []
        )
        prefix_start = (
            previous_end + boundaries[-1].end()
            if boundaries
            else previous_end
        )
        blank_lines = list(re.finditer(r"\r?\n[ \t]*\r?\n", bridge))
        if blank_lines:
            prefix_start = previous_end + blank_lines[-1].end()
        prefix_window = clause[prefix_start:end]
        suffix_window = clause[start:next_start]
        suffix_boundary = re.search(
            r"(?i)\b(?:and|then)\b|(?:然后|并且|并|再|且)",
            suffix_window,
        )
        if suffix_boundary:
            suffix_window = suffix_window[: suffix_boundary.start()]
        if (
            _has_generation_negation(prefix_window)
            or _has_generation_example_prefix(prefix_window)
            or _has_generation_example_suffix(suffix_window)
        ):
            masked[start:end] = " " * (end - start)
        previous_end = end
    return "".join(masked)


def _has_generation_negation(value: str) -> bool:
    english_negation = (
        r"(?:do\s+not|don['’]t|dont|can(?:not|['’]t)|never|"
        r"not(?:\s+to)?|without)"
    )
    if re.search(
        rf"(?i)\b{english_negation}\b"
        rf"(?:[ \t]+[A-Za-z0-9][A-Za-z0-9'’_-]*){{0,5}}"
        rf"[ \t]+\b{_GENERATION_VERB}\b",
        value,
    ):
        return True
    chinese_verb = r"(?:生成|创建|设计|制作|构建|合成|给我|提供)"
    chinese_target_scope = (
        rf"(?:(?:为|针对|给)\s*{_CHINESE_REFERENCE}\s*)"
    )
    return bool(
        re.search(
            rf"(?:请勿|勿|不要|无需|不需要|禁止|别|不可|不能|不"
            rf")\s*(?:(?:再|要)\s*|{chinese_target_scope}){{0,2}}"
            rf"{chinese_verb}",
            value,
        )
    )


def _has_generation_example_prefix(value: str) -> bool:
    english_example = (
        r"(?:for\s+example|as\s+an?\s+example|such\s+as|"
        r"explain(?:ing)?|illustrat(?:e|ing)|example|phrase|instruction|command)"
    )
    if re.search(
        rf"(?i)\b{english_example}\b[^;.!?]{{0,80}}\b{_GENERATION_VERB}\b",
        value,
    ):
        return True
    chinese_verb = r"(?:生成|创建|设计|制作|构建|合成|给我|提供)"
    return bool(
        re.search(
            rf"(?:例如|比如|示例|例子|解释|说明)"
            rf"[^；。！？]{{0,48}}{chinese_verb}",
            value,
        )
    )


def _has_generation_example_suffix(value: str) -> bool:
    english_suffix = r"(?:as\s+an?\s+example|for\s+example)"
    if re.search(
        rf"(?i)\b{_GENERATION_VERB}\b[^;.!?]{{0,80}}\b{english_suffix}\b",
        value,
    ):
        return True
    chinese_verb = r"(?:生成|创建|设计|制作|构建|合成|给我|提供)"
    return bool(
        re.search(
            rf"{chinese_verb}[^；。！？]{{0,48}}(?:作为|当作)(?:一个)?示例",
            value,
        )
    )


def _looks_numeric_like_count_token(token: str) -> bool:
    normalized = (
        unicodedata.normalize("NFKC", token)
        .strip()
        .casefold()
        .replace("\u2212", "-")
    )
    if re.fullmatch(r"[-+]?(?:nan|inf(?:inity)?|∞)", normalized):
        return True
    if re.match(r"^(?:[-+\u2212.]*)\d", normalized):
        return True
    english_number = (
        r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty)"
    )
    if re.match(rf"^{english_number}(?:\.|-|_?point(?:-|_)?)", normalized):
        return True
    return bool(
        re.match(
            rf"^(?:{_CHINESE_COUNT}|\d+)(?:点|\.)(?:{_CHINESE_COUNT}|\d+)",
            normalized,
        )
    )


def _safe_count_error_value(token: Any) -> Any:
    if not isinstance(token, str):
        return token
    normalized = unicodedata.normalize("NFKC", token).strip()
    digit_count = sum(character.isdigit() for character in normalized)
    if digit_count > MAX_GENERATION_COUNT_TOKEN_DIGITS:
        return {"numeric_digits": digit_count}
    return normalized


def _parse_count_token(token: str) -> int | None:
    normalized = unicodedata.normalize("NFKC", token).strip().lower()
    normalized = normalized.replace("\u2212", "-")
    if re.fullmatch(r"[-+]?\d+", normalized):
        digit_count = len(normalized.lstrip("-+"))
        if digit_count > MAX_GENERATION_COUNT_TOKEN_DIGITS:
            raise GenerationRequestError(
                "generation count numeric token is too long",
                value={"numeric_digits": digit_count},
            )
        return int(normalized)
    if re.fullmatch(r"[-+]?\d+\.\d+", normalized):
        raise GenerationRequestError(
            "generation count must be an integer",
            value=_safe_count_error_value(token),
        )
    english = {
        "zero": 0,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
        "ten": 10,
        "eleven": 11,
        "twelve": 12,
        "thirteen": 13,
        "fourteen": 14,
        "fifteen": 15,
        "sixteen": 16,
        "seventeen": 17,
        "eighteen": 18,
        "nineteen": 19,
        "twenty": 20,
    }
    if normalized in english:
        return english[normalized]
    return parse_chinese_integer(normalized)


def parse_chinese_integer(text: str) -> int | None:
    digits = {
        "零": 0,
        "〇": 0,
        "一": 1,
        "二": 2,
        "两": 2,
        "三": 3,
        "四": 4,
        "五": 5,
        "六": 6,
        "七": 7,
        "八": 8,
        "九": 9,
    }
    if text in digits:
        return digits[text]
    if text == "十":
        return 10
    if "百" in text:
        left, right = text.split("百", 1)
        hundreds = digits.get(left, 1 if not left else None)
        rest = parse_chinese_integer(right) if right else 0
        return None if hundreds is None or rest is None else hundreds * 100 + rest
    if "十" in text:
        left, right = text.split("十", 1)
        tens = digits.get(left, 1 if not left else None)
        ones = digits.get(right, 0 if not right else None)
        return None if tens is None or ones is None else tens * 10 + ones
    return None


def sanitize_target_evidence(
    value: Any,
    *,
    max_records: int = MAX_TARGET_RECORDS,
    max_structures: int = MAX_TARGET_STRUCTURES,
) -> list[dict[str, Any]]:
    if value is None:
        return []
    if _contains_untrusted_mode(value):
        raise TargetEvidenceError("target evidence is demo, fallback, or untrusted")
    if isinstance(value, Mapping) and all(
        key in value for key in ("query", "metadata", "outputs")
    ):
        outputs = value.get("outputs")
        value = outputs.get("target") if isinstance(outputs, Mapping) else None

    sanitized: list[dict[str, Any]] = []
    for raw_record in _target_records(value):
        record = _sanitize_target_record(raw_record, max_structures=max_structures)
        if record:
            sanitized.append(record)
    return sanitized[:max_records]


class _ImmutableQuality(dict[str, Any]):
    def _readonly(self, *_args: Any, **_kwargs: Any) -> None:
        raise TypeError("target quality envelope is immutable")

    __setitem__ = _readonly
    __delitem__ = _readonly
    clear = _readonly
    pop = _readonly
    popitem = _readonly
    setdefault = _readonly
    update = _readonly
    __ior__ = _readonly

    def __deepcopy__(self, _memo: dict[int, Any]) -> "_ImmutableQuality":
        return self


def build_generation_request(
    query: str,
    requested_count: Any = _COUNT_UNSET,
    *,
    outputs: Mapping[str, Any] | None = None,
    trust_envelope: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    count = validate_generation_count(
        parse_generation_count(query)
        if requested_count is _COUNT_UNSET
        else requested_count
    )
    target_outputs = (
        {"target": deepcopy(outputs["target"])}
        if isinstance(outputs, Mapping) and "target" in outputs
        else {}
    )
    request = {
        "query": str(query),
        "metadata": {"requested_count": count},
        "outputs": target_outputs,
    }
    preserved_trust = preserve_generation_trust_envelope(trust_envelope)
    metadata_trust = preserved_trust.get("metadata")
    if isinstance(metadata_trust, Mapping):
        request["metadata"].update(metadata_trust)
    outputs_trust = preserved_trust.get("outputs")
    if isinstance(outputs_trust, Mapping):
        request["outputs"].update(outputs_trust)
    if "quality" in preserved_trust:
        request["quality"] = preserved_trust["quality"]
    if "trust_envelope" in preserved_trust:
        request["trust_envelope"] = preserved_trust["trust_envelope"]
    return request


def preserve_generation_trust_envelope(
    value: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Keep only canonical recursive trust signals from request wrappers."""
    if not isinstance(value, Mapping):
        return {}
    envelope: dict[str, Any] = {}
    metadata_trust = preserve_trust_signals(value.get("metadata"))
    if metadata_trust is not None:
        envelope["metadata"] = metadata_trust
    outputs = value.get("outputs")
    if isinstance(outputs, Mapping):
        outputs_quality = preserve_trust_signals(outputs.get("quality"))
        if outputs_quality is not None:
            envelope["outputs"] = _ImmutableQuality(
                {"quality": outputs_quality}
            )
    request_quality = preserve_trust_signals(value.get("quality"))
    if request_quality is not None:
        envelope["quality"] = request_quality
    extra_signals = _collect_discarded_trust_signals(value)
    if extra_signals:
        envelope["trust_envelope"] = _ImmutableQuality(
            {"signals": tuple(extra_signals)}
        )
    return envelope


def _collect_discarded_trust_signals(value: Mapping[str, Any]) -> list[Any]:
    """Retain trust flags that canonical allowlisting would otherwise discard."""
    signals: list[Any] = []

    def retained_path(path: tuple[str, ...]) -> bool:
        if path and path[0] in {"metadata", "quality"}:
            return True
        return len(path) >= 2 and path[:2] in {
            ("outputs", "quality"),
            ("outputs", "target"),
        }

    def collect(item: Any, path: tuple[str, ...]) -> None:
        if isinstance(item, Mapping):
            for key, nested in item.items():
                normalized_key = str(key).strip().lower()
                if (
                    normalized_key in _TRUST_QUALITY_FIELDS
                    and not retained_path(path)
                ):
                    signals.append(
                        _ImmutableQuality(
                            {normalized_key: _freeze_quality_value(nested)}
                        )
                    )
                collect(nested, (*path, normalized_key))
        elif _is_sequence(item):
            for nested in item:
                collect(nested, path)

    collect(value, ())
    return signals


def preserve_target_quality(value: Any, quality: Any) -> Any:
    trust = preserve_trust_signals(quality)
    if trust is None:
        return value
    return {
        "data": deepcopy(value),
        "quality": trust,
    }


def preserve_trust_signals(value: Any) -> Any | None:
    if isinstance(value, Mapping):
        trust: dict[str, Any] = {}
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in _TRUST_QUALITY_FIELDS:
                trust[normalized_key] = _freeze_quality_value(item)
                continue
            nested = preserve_trust_signals(item)
            if nested is not None:
                trust[str(key)] = nested
        return _ImmutableQuality(trust) if trust else None
    if _is_sequence(value):
        nested_items = tuple(
            nested
            for item in value
            if (nested := preserve_trust_signals(item)) is not None
        )
        return nested_items or None
    return None


def _freeze_quality_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        return _ImmutableQuality(
            {str(key): _freeze_quality_value(item) for key, item in value.items()}
        )
    if _is_sequence(value):
        return tuple(_freeze_quality_value(item) for item in value)
    return deepcopy(value)


def serialize_target_evidence(value: Any) -> str | None:
    sanitized = sanitize_target_evidence(value)
    if not sanitized:
        return None
    serialized = _compact_json(sanitized)
    while len(serialized) > MAX_EVIDENCE_CHARS and sanitized:
        sanitized.pop()
        serialized = _compact_json(sanitized)
    return serialized if sanitized else None


def _target_records(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, Mapping):
        for key in ("targets", "records", "results", "data"):
            nested = value.get(key)
            if isinstance(nested, Mapping):
                return _target_records(nested)
            if _is_sequence(nested):
                return [item for item in nested if isinstance(item, Mapping)]
        return [value]
    if _is_sequence(value):
        return [item for item in value if isinstance(item, Mapping)]
    return []


def _sanitize_target_record(
    value: Mapping[str, Any], *, max_structures: int
) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field in _TARGET_TEXT_FIELDS:
        safe = _safe_text(
            value.get(field),
            field=field,
        )
        if safe is not None:
            record[field] = safe
    for field in ("has_experimental_structure", "has_alphafold_structure"):
        if isinstance(value.get(field), bool):
            record[field] = value[field]
    count = value.get("structure_count")
    if isinstance(count, int) and not isinstance(count, bool) and 0 <= count <= 10000:
        record["structure_count"] = count

    structures = value.get("recommended_structures", value.get("structures"))
    safe_structures = _sanitize_nested_records(
        structures,
        _sanitize_structure_record,
        limit=max_structures,
    )
    if safe_structures:
        record["recommended_structures"] = safe_structures
    safe_evidence = _sanitize_nested_records(
        value.get("evidence"), _sanitize_evidence_record, limit=MAX_EVIDENCE_ITEMS
    )
    if safe_evidence:
        record["evidence"] = safe_evidence
    safe_artifacts = _sanitize_nested_records(
        value.get("artifacts"), _sanitize_evidence_record, limit=MAX_EVIDENCE_ITEMS
    )
    if safe_artifacts:
        record["artifacts"] = safe_artifacts

    if not _filter_source_specific_identifiers(
        record,
        fields=(
            "target_identifier",
            "uniprot_id",
            "target_id",
            "source_record_id",
        ),
        allow_nested_source=True,
        require_identity=False,
    ):
        return {}

    has_identifier = _has_source_specific_identity(
        record,
        fields=(
            "target_identifier",
            "uniprot_id",
            "target_id",
            "source_record_id",
        ),
        allow_nested_source=True,
    ) or any(
        record.get(field)
        for field in ("recommended_structures", "evidence", "artifacts")
    )
    if not has_identifier:
        record.pop("target_name", None)
    has_provenance = any(field in record for field in _PROVENANCE_FIELDS)
    return record if has_identifier and has_provenance else {}


def _sanitize_structure_record(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _sanitize_text_fields(value, _STRUCTURE_TEXT_FIELDS)
    if not _filter_source_specific_identifiers(
        record,
        fields=("structure_id", "source_record_id"),
    ):
        return {}
    resolution = value.get("resolution")
    if (
        isinstance(resolution, (int, float))
        and not isinstance(resolution, bool)
        and 0 <= float(resolution) <= 100
    ):
        record["resolution"] = float(resolution)
    for field in ("docking_recommended", "is_preferred", "stale"):
        if isinstance(value.get(field), bool):
            record[field] = value[field]
    has_provenance = any(
        key in record
        for key in ("source", "database", "source_url", "download_url")
    )
    return record if has_provenance else {}


def _sanitize_evidence_record(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _sanitize_text_fields(
        value,
        _EVIDENCE_TEXT_FIELDS,
    )
    if isinstance(value.get("stale"), bool):
        record["stale"] = value["stale"]
    if not _filter_source_specific_identifiers(
        record,
        fields=("id", "source_record_id", "structure_id", "uniprot_id"),
    ):
        return {}
    has_provenance = any(
        key in record for key in ("source", "database", "source_url")
    )
    return record if has_provenance else {}


def _sanitize_nested_records(value: Any, sanitizer, *, limit: int) -> list[dict[str, Any]]:
    if not _is_sequence(value):
        return []
    sanitized = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        record = sanitizer(item)
        if record:
            sanitized.append(record)
    return sanitized[:limit]


def _sanitize_text_fields(
    value: Mapping[str, Any],
    fields: Sequence[str],
) -> dict[str, Any]:
    record: dict[str, Any] = {}
    for field in fields:
        safe = _safe_text(
            value.get(field),
            field=field,
        )
        if safe is not None:
            record[field] = safe
    return record


def _safe_text(
    value: Any,
    *,
    field: str,
) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if normalized.lower() in _PLACEHOLDERS:
        return None
    if _looks_credential_shaped_text(normalized):
        raise TargetEvidenceError("target evidence contains unsafe text")
    if _looks_instruction_shaped_identifier(normalized):
        return None
    if field in {"source", "database"}:
        if normalized.casefold() not in _TRUSTED_EVIDENCE_SOURCES:
            return None
        return normalized
    if field in {"source_url", "download_url"}:
        return normalized if _is_official_evidence_url(normalized) else None
    pattern = (
        _SAFE_GENE_SYMBOL
        if field in {"gene_symbol", "target_name"}
        else _SAFE_IDENTIFIER
    )
    return normalized if pattern.fullmatch(normalized) else None


def _looks_instruction_shaped_identifier(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    prefix_match = _LOCAL_RECORD_PREFIX.fullmatch(normalized)
    identifier_text = (
        prefix_match.group("suffix") if prefix_match else normalized
    )
    lexical_text = re.sub(r"[^a-z0-9]+", "", identifier_text)
    has_instruction_action = bool(
        _INSTRUCTION_ACTION_PATTERN.search(lexical_text)
    )
    has_instruction_object = bool(
        _INSTRUCTION_OBJECT_PATTERN.search(lexical_text)
    )
    if has_instruction_action and has_instruction_object:
        return True
    return any(
        all(lexeme in lexical_text for lexeme in combination)
        for combination in _INSTRUCTION_IDENTIFIER_LEXICAL_COMBINATIONS
    )


def _looks_credential_shaped_text(value: str) -> bool:
    normalized = unicodedata.normalize("NFKC", value)
    prefix_match = _LOCAL_RECORD_PREFIX.fullmatch(normalized)
    candidates = [normalized]
    if prefix_match:
        candidates.append(prefix_match.group("suffix"))
    for candidate in candidates:
        folded = candidate.casefold()
        lexical_text = re.sub(r"[^a-z0-9]+", "", folded)
        if (
            looks_like_credential(candidate)
            or _CREDENTIAL_PATTERN.search(folded)
            or _CREDENTIAL_TEXT_PATTERN.search(lexical_text)
        ):
            return True
    return False


def _filter_source_specific_identifiers(
    record: dict[str, Any],
    *,
    fields: Sequence[str],
    allow_nested_source: bool = False,
    require_identity: bool = True,
) -> bool:
    source_kind, conflict = _record_source_kind(record)
    if conflict:
        return False
    if source_kind is None and allow_nested_source:
        nested_kinds = _nested_source_kinds(record)
        if len(nested_kinds) == 1:
            source_kind = next(iter(nested_kinds))

    for field in fields:
        identifier = record.get(field)
        if not isinstance(identifier, str):
            continue
        if not _identifier_matches_source(field, identifier, source_kind):
            record.pop(field, None)
    if not _record_identifiers_match_urls(record, fields, source_kind):
        return False
    return not require_identity or _has_source_specific_identity(
        record,
        fields=fields,
        allow_nested_source=allow_nested_source,
    )


def _has_source_specific_identity(
    record: Mapping[str, Any],
    *,
    fields: Sequence[str],
    allow_nested_source: bool = False,
) -> bool:
    source_kind, conflict = _record_source_kind(record)
    if conflict:
        return False
    if source_kind is None and allow_nested_source:
        nested_kinds = _nested_source_kinds(record)
        if len(nested_kinds) == 1:
            source_kind = next(iter(nested_kinds))
    if source_kind not in {"uniprot", "rcsb", "alphafold", "local"}:
        return False
    identifiers, url_identifiers = _source_identity_values(
        record, fields, source_kind
    )
    if source_kind == "alphafold":
        identifiers = {
            canonical
            for field in fields
            if field != "uniprot_id"
            and isinstance(record.get(field), str)
            and (
                canonical := _canonical_source_identifier(
                    str(record[field]), source_kind, field=field
                )
            )
        }
    return bool(identifiers or url_identifiers)


def _record_source_kind(record: Mapping[str, Any]) -> tuple[str | None, bool]:
    kinds: set[str] = set()
    for field in ("source", "database"):
        value = record.get(field)
        if not isinstance(value, str):
            continue
        normalized = value.casefold()
        if normalized in {"uniprot"}:
            kinds.add("uniprot")
        elif normalized in {"pdb", "rcsb", "rcsb_pdb"}:
            kinds.add("rcsb")
        elif normalized == "alphafold":
            kinds.add("alphafold")
        elif normalized in {"local", "local_target_db", "target_database"}:
            kinds.add("local")
        elif normalized == "official":
            kinds.add("official")
    for field in ("source_url", "download_url"):
        value = record.get(field)
        if not isinstance(value, str):
            continue
        host = urlsplit(value).hostname
        if host == "www.uniprot.org":
            kinds.add("uniprot")
        elif host in {"www.rcsb.org", "files.rcsb.org"}:
            kinds.add("rcsb")
        elif host == "alphafold.ebi.ac.uk":
            kinds.add("alphafold")
    if "official" in kinds and len(kinds) > 1:
        kinds.remove("official")
    return (next(iter(kinds)), False) if len(kinds) == 1 else (None, len(kinds) > 1)


def _nested_source_kinds(record: Mapping[str, Any]) -> set[str]:
    kinds: set[str] = set()
    for field in ("recommended_structures", "evidence", "artifacts"):
        value = record.get(field)
        if not _is_sequence(value):
            continue
        for item in value:
            if not isinstance(item, Mapping):
                continue
            kind, conflict = _record_source_kind(item)
            if not conflict and kind not in {None, "official"}:
                kinds.add(kind)
    return kinds


def _identifier_matches_source(
    field: str,
    identifier: str,
    source_kind: str | None,
) -> bool:
    if field == "uniprot_id":
        return source_kind in {None, "official", "uniprot", "alphafold"} and bool(
            _UNIPROT_ACCESSION.fullmatch(identifier)
        )
    if source_kind == "uniprot":
        return bool(_UNIPROT_ACCESSION.fullmatch(identifier))
    if source_kind == "rcsb":
        return bool(_RCSB_STRUCTURE_ID.fullmatch(identifier))
    if source_kind == "alphafold":
        return bool(_ALPHAFOLD_STRUCTURE_ID.fullmatch(identifier))
    if source_kind == "local":
        return bool(_LOCAL_RECORD_ID.fullmatch(identifier))
    if source_kind in {None, "official"}:
        return any(
            pattern.fullmatch(identifier)
            for pattern in (
                _UNIPROT_ACCESSION,
                _RCSB_STRUCTURE_ID,
                _ALPHAFOLD_STRUCTURE_ID,
            )
        )
    return False


def _record_identifiers_match_urls(
    record: Mapping[str, Any],
    fields: Sequence[str],
    source_kind: str | None,
) -> bool:
    if source_kind not in {"uniprot", "rcsb", "alphafold"}:
        return True
    identifiers, url_identifiers = _source_identity_values(
        record, fields, source_kind
    )
    return (
        len(identifiers) <= 1
        and len(url_identifiers) <= 1
        and (not identifiers or not url_identifiers or identifiers == url_identifiers)
    )


def _source_identity_values(
    record: Mapping[str, Any],
    fields: Sequence[str],
    source_kind: str,
) -> tuple[set[str], set[str]]:
    identifiers = {
        canonical
        for field in fields
        if isinstance(record.get(field), str)
        and (
            canonical := _canonical_source_identifier(
                str(record[field]), source_kind, field=field
            )
        )
    }
    url_identifiers = {
        canonical
        for field in ("source_url", "download_url")
        if isinstance(record.get(field), str)
        and (
            canonical := _canonical_url_identifier(
                str(record[field]), source_kind
            )
        )
    }
    return identifiers, url_identifiers


def _canonical_source_identifier(
    identifier: str,
    source_kind: str,
    *,
    field: str,
) -> str | None:
    normalized = identifier.upper()
    if source_kind == "alphafold":
        if field == "uniprot_id" and _UNIPROT_ACCESSION.fullmatch(normalized):
            return normalized
        match = re.fullmatch(r"AF-(.+)-F[0-9]+", normalized)
        return match.group(1) if match else None
    return normalized


def _canonical_url_identifier(value: str, source_kind: str) -> str | None:
    parsed = urlsplit(value)
    if source_kind == "uniprot":
        match = re.fullmatch(r"/uniprotkb/([^/]+)/entry", parsed.path)
        return match.group(1).upper() if match else None
    if source_kind == "rcsb":
        match = re.fullmatch(r"/structure/([^/]+)", parsed.path)
        if match:
            return match.group(1).upper()
        match = re.fullmatch(r"/download/([^/]+)\.(?:cif|pdb)", parsed.path, re.I)
        if match:
            return match.group(1).upper()
        match = re.fullmatch(r"request=([^&]+)", parsed.query)
        return match.group(1).upper() if match else None
    match = re.fullmatch(r"/entry/([^/]+)", parsed.path)
    if match:
        return match.group(1).upper()
    match = re.fullmatch(
        r"/files/AF-(.+)-F[0-9]+-model_v[1-9][0-9]*\.(?:cif|pdb)",
        parsed.path,
        re.I,
    )
    return match.group(1).upper() if match else None


def _is_official_evidence_url(value: str) -> bool:
    if len(value) > 256:
        return False
    try:
        parsed = urlsplit(value)
        if (
            parsed.scheme != "https"
            or parsed.hostname not in _OFFICIAL_EVIDENCE_HOSTS
            or parsed.username is not None
            or parsed.password is not None
            or parsed.port is not None
            or parsed.fragment
        ):
            return False
    except ValueError:
        return False
    if parsed.hostname == "www.uniprot.org":
        return bool(
            not parsed.query
            and re.fullmatch(
                rf"/uniprotkb/{_UNIPROT_ACCESSION.pattern[:-2]}/entry",
                parsed.path,
                re.IGNORECASE,
            )
        )
    if parsed.hostname == "www.rcsb.org":
        return bool(
            (
                not parsed.query
                and re.fullmatch(
                    rf"/structure/{_RCSB_STRUCTURE_ID.pattern[:-2]}",
                    parsed.path,
                    re.IGNORECASE,
                )
            )
            or (
                parsed.path == "/search"
                and re.fullmatch(
                    rf"request={_RCSB_STRUCTURE_ID.pattern[:-2]}",
                    parsed.query,
                    re.IGNORECASE,
                )
            )
        )
    if parsed.hostname == "files.rcsb.org":
        return bool(
            not parsed.query
            and re.fullmatch(
                rf"/download/{_RCSB_STRUCTURE_ID.pattern[:-2]}\.(?:cif|pdb)",
                parsed.path,
                re.IGNORECASE,
            )
        )
    return bool(
        not parsed.query
        and (
            re.fullmatch(
                rf"/entry/{_UNIPROT_ACCESSION.pattern[:-2]}",
                parsed.path,
                re.IGNORECASE,
            )
            or re.fullmatch(
                rf"/files/AF-{_UNIPROT_ACCESSION.pattern[:-2]}-F1-model_v[1-9][0-9]*\.(?:cif|pdb)",
                parsed.path,
                re.IGNORECASE,
            )
        )
    )


def _contains_untrusted_mode(value: Any) -> bool:
    if isinstance(value, Mapping):
        for key, item in value.items():
            normalized_key = str(key).strip().lower()
            if normalized_key in _UNTRUSTED_TRUE_FLAGS:
                parsed = _parse_trust_flag(item)
                if parsed is None or parsed:
                    return True
                continue
            if normalized_key in _UNTRUSTED_FALSE_FLAGS:
                parsed = _parse_trust_flag(item)
                if parsed is None or not parsed:
                    return True
                continue
            if _contains_untrusted_mode(item):
                return True
    elif _is_sequence(value):
        return any(_contains_untrusted_mode(item) for item in value)
    return False


def _parse_trust_flag(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if not isinstance(value, str):
        return None
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _is_sequence(value: Any) -> bool:
    return isinstance(value, (list, tuple))


def _compact_json(value: Any) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


__all__ = [
    "DEFAULT_GENERATION_COUNT",
    "GenerationRequestError",
    "MAX_GENERATION_COUNT",
    "MIN_GENERATION_COUNT",
    "TargetEvidenceError",
    "build_generation_request",
    "generation_request_error_details",
    "has_generation_intent",
    "parse_chinese_integer",
    "parse_generation_count",
    "preflight_generation_request",
    "preserve_generation_trust_envelope",
    "preserve_target_quality",
    "preserve_trust_signals",
    "sanitize_target_evidence",
    "serialize_target_evidence",
    "validate_generation_count",
    "validate_generation_request_length",
]
