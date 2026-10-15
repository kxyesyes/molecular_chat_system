"""Small descriptor-bound checks. No inference, calculations or unit conversion.

Only explicit MW (Da) and logP comparisons are supported here. Ambiguous forms
are reported as unverified rather than being treated as a satisfied constraint.
"""
import math
import re

_METRIC = re.compile(r"(?<![A-Za-z0-9_])(?:molecular\s+weight|MW|分子量|logP)(?![A-Za-z0-9_])", re.I)
_OP = re.compile(r"\s*(不超过|不大于|不低于|不小于|小于|低于|大于|高于|<=|>=|≤|≥|<|>)\s*")
_OPS = {"不超过": "<=", "不大于": "<=", "不低于": ">=", "不小于": ">=",
        "小于": "<", "低于": "<", "大于": ">", "高于": ">", "≤": "<=", "≥": ">="}
_NUMBER = re.compile(r"[+-]?(?:\d+(?:\.\d+)?|\.\d+)(?:[eE][+-]?\d+)?")


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
        metric = "logp" if match[0].casefold() == "logp" else "molecular_weight"
        op = _OP.match(query, match.end())
        if op is None:
            continue  # relative optimization objectives are handled elsewhere
        number = _NUMBER.match(query, op.end())
        prefix = re.split(r"[，,。;；\n]", query[:match.start()])[-1]
        ambiguous = bool(re.search(r"不要|无需|禁止|勿|\b(?:not|no|don't)\b|[<>≤≥]\s*$", prefix, re.I))
        value = float(number[0]) if number else None
        tail = query[number.end():] if number else ""
        unit = re.match(r"\s*(?:Da|g/mol|g·mol-1)(?![A-Za-z0-9_/])", tail, re.I) if metric == "molecular_weight" else None
        if unit:
            tail = tail[unit.end():]
        # A threshold must end at a known clause boundary. Unknown units and
        # arithmetic suffixes must not silently become a different constraint.
        bounded_tail = re.match(r"\s*(?:$|[，,。;；\n、]|的(?:约束|要求|限制)(?:$|[，,。;；\n、])|并|且|和|同时)", tail)
        if (ambiguous or not finite_number(value) or (metric == "molecular_weight" and value <= 0)
                or not bounded_tail):
            unknown.append(metric + "_bound")
            continue
        bounds.append({"metric": metric, "operator": _OPS.get(op[1], op[1]),
                       "threshold": value, "unit": "Da" if metric == "molecular_weight" else "dimensionless"})
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
