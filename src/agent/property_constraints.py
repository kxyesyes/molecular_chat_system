"""Small descriptor-bound checks. No inference, calculations or unit conversion.

Only explicit MW, logP, TPSA, QED, HBA and HBD comparisons are supported here.
Ambiguous forms are unverified rather than treated as a satisfied constraint.
"""
import math
import re

_METRIC = re.compile(r"(?<![A-Za-z0-9_])(?:molecular\s+weight|MW|分子量|logP|TPSA|QED|HBA|HBD)(?![A-Za-z0-9_])", re.I)
_UNITS = {"molecular_weight": "Da", "logp": "dimensionless", "tpsa": "Å²",
          "qed": "dimensionless", "hba": "count", "hbd": "count"}
_EXPLICIT_UNITS = {
    "molecular_weight": re.compile(r"\s*(?:Da|g/mol|g·mol-1)(?![A-Za-z0-9_/])", re.I),
    "tpsa": re.compile(r"\s*(?:Å²|A²|A2)(?![A-Za-z0-9_/])", re.I),
}
_OP = re.compile(r"\s*(不超过|不大于|不低于|不小于|小于|低于|大于|高于|<=|>=|≤|≥|<|>)\s*")
_OPS = {"不超过": "<=", "不大于": "<=", "不低于": ">=", "不小于": ">=",
        "小于": "<", "低于": "<", "大于": ">", "高于": ">", "≤": "<=", "≥": ">="}
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?")
# These indicate an explicit numeric condition, but do not authorize evaluating
# an unsupported operator as equality or rounding it to a supported bound.
_NUMERIC_PREFIX = re.compile(
    r"\s*(?:约为|约等于|等于|接近|介于|约|为|"
    r"\b(?:equals?|approximately|approx|about|around|between|is|"
    r"(?:less|greater|more|lower|higher)\s*than|below|above|at\s*(?:most|least)|"
    r"no\s*more\s*than|not\s*greater\s*than)(?![A-Za-z_]))\s*", re.I)
_UNSUPPORTED_NUMERIC = re.compile(
    r"\s*(?:[=~≈≃≅]|" + _OP.pattern
    + r"|[+-]?(?:\d|\.\d|NaN\b|inf(?:inity)?\b))", re.I)


def finite_number(value):
    try:
        return type(value) in (int, float) and math.isfinite(value)
    except OverflowError:
        return False


def requested_property_bounds(query):
    bounds, unknown = [], []
    if not isinstance(query, str):
        return bounds, unknown
    for match in _METRIC.finditer(query):
        name = match[0].casefold()
        metric = name if name in _UNITS else "molecular_weight"
        op = _OP.match(query, match.end())
        if op is None:
            # Consume prefixes iteratively: "is approximately" must not drop
            # a numeric goal, but "is lower" remains a relative objective.
            end = match.end()
            while prefix := _NUMERIC_PREFIX.match(query, end):
                end = prefix.end()
            if _UNSUPPORTED_NUMERIC.match(query, end):
                unknown.append(metric + "_bound")
            continue  # relative optimization objectives are handled elsewhere
        number = _NUMBER.match(query, op.end())
        prefix = re.split(r"[，,。;；\n]", query[:match.start()])[-1]
        ambiguous = bool(re.search(r"不要|无需|禁止|勿|\b(?:not|no|don't)\b|[<>≤≥]\s*$", prefix, re.I))
        value = float(number[0]) if number else None
        tail = query[number.end():] if number else ""
        unit_pattern = _EXPLICIT_UNITS.get(metric)
        unit = unit_pattern.match(tail) if unit_pattern else None
        if unit:
            tail = tail[unit.end():]
        # A threshold must end at a known clause boundary. Unknown units and
        # arithmetic suffixes must not silently become a different constraint.
        bounded_tail = re.match(
            r"\s*(?:$|[，,。;；\n、]|的(?:约束|要求|限制)(?:$|[，,。;；\n、])|并|且|和|同时"
            + r"|\band\b(?=\s*" + _METRIC.pattern + _OP.pattern + r"))", tail, re.I)
        continuation = re.match(r"\s*(?:[，,。;；、]\s*)*(?:(?:并且|同时|并|且|和|and)\s*)?", tail, re.I)
        implicit_bound = continuation and (
            _OP.match(tail, continuation.end()) or tail[continuation.end():].startswith("="))
        if (ambiguous or not finite_number(value) or (metric == "molecular_weight" and value <= 0)
                or not bounded_tail or implicit_bound):
            unknown.append(metric + "_bound")
            continue
        bounds.append({"metric": metric, "operator": _OPS.get(op[1], op[1]),
                       "threshold": value, "unit": _UNITS[metric]})
    return bounds, list(dict.fromkeys(unknown))


def check_property_bounds(bounds, properties):
    properties = properties if isinstance(properties, dict) else {}
    checks = []
    for bound in bounds:
        value = properties.get(bound["metric"])
        passed = None
        if finite_number(value):
            threshold = bound["threshold"]
            passed = {"<": value < threshold, "<=": value <= threshold,
                      ">": value > threshold, ">=": value >= threshold}[bound["operator"]]
        checks.append({**bound, "value": value if finite_number(value) else None, "satisfied": passed})
    return checks
