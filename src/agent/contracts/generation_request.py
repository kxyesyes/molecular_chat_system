from __future__ import annotations

import json
import re
import unicodedata
from collections.abc import Mapping, Sequence
from copy import deepcopy
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
_COUNT_UNSET = object()


class GenerationRequestError(ValueError):
    """A caller-supplied generation request violates the public contract."""


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
_IDENTIFIER_FIELDS = frozenset(
    {"gene_symbol", "target_identifier", "uniprot_id", "target_name", "target_id"}
)
_PROVENANCE_FIELDS = frozenset(
    {
        "source",
        "database",
        "source_url",
        "uniprot_id",
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
_SAFE_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9.:-]{0,79}\Z")
_SAFE_GENE_SYMBOL = re.compile(r"[A-Za-z][A-Za-z0-9-]{0,19}\Z")
_OFFICIAL_EVIDENCE_HOSTS = frozenset(
    {
        "alphafold.ebi.ac.uk",
        "files.rcsb.org",
        "www.rcsb.org",
        "www.uniprot.org",
    }
)

_ARABIC_COUNT = r"(?<![\w.])[-+\u2212]?\d+(?:\.\d+)?(?![\w.])"
_CHINESE_COUNT = r"[零〇一二两三四五六七八九十百]+"
_ENGLISH_COUNT_WORD = (
    r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|eleven|"
    r"twelve|thirteen|fourteen|fifteen|sixteen|seventeen|eighteen|nineteen|"
    r"twenty)"
)
_ENGLISH_COUNT = rf"\b{_ENGLISH_COUNT_WORD}\b"
_COUNT_TOKEN = rf"(?:{_ARABIC_COUNT}|{_CHINESE_COUNT}|{_ENGLISH_COUNT})"
_ENGLISH_NOUN = r"(?:candidates?|molecules?|compounds?|structures?)"
_GENERATION_VERB = r"(?:generate|design|create|produce|make|build)"
_ENGLISH_COUNT_MODIFIER = r"(?:new|novel|candidate|drug-like)"
_CHINESE_COUNT_MODIFIER = r"(?:新(?:的)?|全新(?:的)?|新颖(?:的)?)"
_COUNT_PATTERNS = (
    re.compile(
        rf"(?i)\b{_GENERATION_VERB}\b[ \t]*(?P<count>{_COUNT_TOKEN})"
        rf"(?:[ \t]+{_ENGLISH_COUNT_MODIFIER}){{0,3}}"
        rf"(?=[ \t]+(?:{_ENGLISH_NOUN}\b|for\b)|[ \t]*$)"
    ),
    re.compile(
        rf"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*"
        rf"(?P<count>{_COUNT_TOKEN})\s*(?:个|种|份)?\s*"
        rf"(?:{_CHINESE_COUNT_MODIFIER}\s*){{0,2}}"
        rf"(?=(?:类药)?(?:候选)?(?:分子|化合物|结构|候选)|用于|给|$)"
    ),
)
_COUNT_SLOT_PATTERNS = (
    re.compile(
        rf"(?i)\b{_GENERATION_VERB}\b[ \t]*(?P<count>[^\s;!?\"']+)"
        rf"(?:[ \t]+{_ENGLISH_COUNT_MODIFIER}){{0,3}}"
        rf"(?=[ \t]+(?:{_ENGLISH_NOUN}\b|for\b)|[ \t]*$)"
    ),
    re.compile(
        rf"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*"
        rf"(?P<count>[^\s个种份，。；、！？\"']+)\s*(?:个|种|份)?\s*"
        rf"(?:{_CHINESE_COUNT_MODIFIER}\s*){{0,2}}"
        rf"(?=(?:类药)?(?:候选)?(?:分子|化合物|结构|候选)|用于|给|$)"
    ),
)
_MALFORMED_WORD_COUNT_PATTERNS = (
    re.compile(
        rf"(?i)\b{_GENERATION_VERB}\b[ \t]*{_ENGLISH_COUNT_WORD}"
        rf"[ \t]+point[ \t]+{_ENGLISH_COUNT_WORD}"
        rf"(?:[ \t]+{_ENGLISH_COUNT_MODIFIER}){{0,3}}"
        rf"(?=[ \t]+(?:{_ENGLISH_NOUN}\b|for\b)|[ \t]*$)"
    ),
    re.compile(
        rf"(?:生成|创建|设计|制作|构建|合成|给我|提供)\s*"
        rf"{_CHINESE_COUNT}\s*点\s*{_CHINESE_COUNT}\s*(?:个|种|份)?\s*"
        rf"(?:{_CHINESE_COUNT_MODIFIER}\s*){{0,2}}"
        rf"(?=(?:类药)?(?:候选)?(?:分子|化合物|结构|候选)|用于|给|$)"
    ),
)
_FUZZY_COUNTS = (
    (
        re.compile(
            rf"(?i)\b{_GENERATION_VERB}\b[ \t]*(?:several|some|a[ \t]+few)"
            rf"(?:[ \t]+{_ENGLISH_COUNT_MODIFIER}){{0,3}}"
            rf"(?=[ \t]+{_ENGLISH_NOUN}\b)"
        ),
        3,
    ),
    (
        re.compile(
            rf"(?i)\b{_GENERATION_VERB}\b[ \t]*(?:multiple|many)"
            rf"(?:[ \t]+{_ENGLISH_COUNT_MODIFIER}){{0,3}}"
            rf"(?=[ \t]+{_ENGLISH_NOUN}\b)"
        ),
        5,
    ),
    (
        re.compile(
            rf"(?i)\b{_GENERATION_VERB}\b[ \t]*(?:one|a)"
            rf"(?:[ \t]+{_ENGLISH_COUNT_MODIFIER}){{0,3}}"
            rf"(?=[ \t]+{_ENGLISH_NOUN}\b)"
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
        raise GenerationRequestError(f"{field} must be an integer")
    if not MIN_GENERATION_COUNT <= value <= MAX_GENERATION_COUNT:
        raise GenerationRequestError(
            f"{field} must be between {MIN_GENERATION_COUNT} and "
            f"{MAX_GENERATION_COUNT}"
        )
    return value


def parse_generation_count(query: str, *, default: int = DEFAULT_GENERATION_COUNT) -> int:
    query = unicodedata.normalize("NFKC", str(query))
    if any(pattern.search(query) for pattern in _MALFORMED_WORD_COUNT_PATTERNS):
        raise GenerationRequestError("generation count must be an integer")
    for pattern in _COUNT_PATTERNS:
        match = pattern.search(query)
        if not match:
            continue
        token = match.group("count")
        value = _parse_count_token(token)
        if value is None:
            continue
        return value
    for pattern in _COUNT_SLOT_PATTERNS:
        match = pattern.search(query)
        if match and _looks_numeric_like_count_token(match.group("count")):
            raise GenerationRequestError("generation count must be an integer")
    for pattern, value in _FUZZY_COUNTS:
        if pattern.search(query):
            return value
    return default


def has_generation_intent(query: str) -> bool:
    """Recognize generation grammar without treating quoted examples as requests."""
    normalized = unicodedata.normalize("NFKC", str(query))
    unquoted = re.sub(
        r'"[^"\r\n]*"|“[^”\r\n]*”|\'[^\'\r\n]*\'|‘[^’\r\n]*’',
        " ",
        normalized,
    )
    return (
        any(pattern.search(unquoted) for pattern in _COUNT_SLOT_PATTERNS)
        or any(
            pattern.search(unquoted)
            for pattern in _MALFORMED_WORD_COUNT_PATTERNS
        )
        or any(pattern.search(unquoted) for pattern, _value in _FUZZY_COUNTS)
    )


def _looks_numeric_like_count_token(token: str) -> bool:
    normalized = unicodedata.normalize("NFKC", token).strip().casefold()
    if re.match(r"^(?:[-+\u2212.]*)\d", normalized):
        return True
    english_number = (
        r"(?:zero|one|two|three|four|five|six|seven|eight|nine|ten|"
        r"eleven|twelve|thirteen|fourteen|fifteen|sixteen|seventeen|"
        r"eighteen|nineteen|twenty)"
    )
    if re.match(rf"^{english_number}(?:\.|-|_?point(?:-|_)?)", normalized):
        return True
    return bool(re.match(rf"^{_CHINESE_COUNT}点", normalized))


def _parse_count_token(token: str) -> int | None:
    normalized = unicodedata.normalize("NFKC", token).strip().lower()
    normalized = normalized.replace("\u2212", "-")
    if re.fullmatch(r"[-+]?\d+", normalized):
        return int(normalized)
    if re.fullmatch(r"[-+]?\d+\.\d+", normalized):
        raise GenerationRequestError("generation count must be an integer")
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

    has_identifier = any(field in record for field in _IDENTIFIER_FIELDS)
    has_provenance = any(field in record for field in _PROVENANCE_FIELDS)
    return record if has_identifier and has_provenance else {}


def _sanitize_structure_record(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _sanitize_text_fields(value, _STRUCTURE_TEXT_FIELDS)
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
    return record if any(
        key in record for key in ("structure_id", "source_record_id")
    ) else {}


def _sanitize_evidence_record(value: Mapping[str, Any]) -> dict[str, Any]:
    record = _sanitize_text_fields(
        value,
        _EVIDENCE_TEXT_FIELDS,
    )
    if isinstance(value.get("stale"), bool):
        record["stale"] = value["stale"]
    return record if any(
        key in record
        for key in ("source", "database", "id", "source_record_id", "structure_id")
    ) else {}


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
    if (
        looks_like_credential(normalized)
        or _CREDENTIAL_PATTERN.search(normalized)
    ):
        raise TargetEvidenceError("target evidence contains unsafe text")
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
    identifier = r"[A-Za-z0-9][A-Za-z0-9.:-]{0,79}"
    if parsed.hostname == "www.uniprot.org":
        return bool(
            not parsed.query
            and re.fullmatch(rf"/uniprotkb/{identifier}/entry", parsed.path)
        )
    if parsed.hostname == "www.rcsb.org":
        return bool(
            (
                not parsed.query
                and re.fullmatch(rf"/structure/{identifier}", parsed.path)
            )
            or (
                parsed.path == "/search"
                and re.fullmatch(rf"request={identifier}", parsed.query)
            )
        )
    if parsed.hostname == "files.rcsb.org":
        return bool(
            not parsed.query
            and re.fullmatch(
                rf"/download/{identifier}\.(?:cif|pdb)",
                parsed.path,
                re.IGNORECASE,
            )
        )
    return bool(
        not parsed.query
        and (
            re.fullmatch(rf"/entry/{identifier}", parsed.path)
            or re.fullmatch(
                rf"/files/AF-{identifier}-F1-model_v[1-9][0-9]*\.(?:cif|pdb)",
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
    "has_generation_intent",
    "parse_chinese_integer",
    "parse_generation_count",
    "preserve_generation_trust_envelope",
    "preserve_target_quality",
    "preserve_trust_signals",
    "sanitize_target_evidence",
    "serialize_target_evidence",
    "validate_generation_count",
]
