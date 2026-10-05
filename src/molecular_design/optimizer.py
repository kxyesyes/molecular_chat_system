"""Explicit optimization-goal parsing for molecular design."""

from __future__ import annotations

import html
import math
import operator as comparisons
import re
from typing import Any, Dict, Iterable, Mapping


GoalSpec = Dict[str, Dict[str, Any]]

# Compatibility symbol only. A design request must explicitly provide a target.
DEFAULT_GOALS: GoalSpec = {}

METRIC_ALIASES = {
    "qed": "qed",
    "mw": "mw",
    "分子量": "mw",
    "molecular weight": "mw",
    "mol wt": "mw",
    "logp": "logp",
    "clogp": "logp",
    "tpsa": "tpsa",
    "sa": "sa_score",
    "sa score": "sa_score",
    "sascore": "sa_score",
}

LABELS = {
    "qed": "QED",
    "mw": "MW",
    "logp": "LogP",
    "tpsa": "TPSA",
    "sa_score": "SA Score",
}


def _copy_default_goals() -> GoalSpec:
    return {}


def _canonical_metric(raw_metric: str) -> str | None:
    return METRIC_ALIASES.get(raw_metric.strip().lower())


def _goal_label(metric: str, direction: str, threshold: float | None, operator: str | None = None) -> str:
    label = LABELS.get(metric, metric)
    if threshold is None:
        return f"{label} {'提高' if direction == 'max' else '降低'}"
    comparator = operator or (">=" if direction == "max" else "<=")
    value = int(threshold) if float(threshold).is_integer() else threshold
    suffix = " Da" if metric == "mw" else ""
    return f"{label} {comparator} {value}{suffix}"


def _direction_from_operator(operator: str) -> str:
    return "max" if operator in (">", ">=") else "min"


def _iter_numeric_goal_matches(command: str) -> Iterable[tuple[str, str, float | None]]:
    """Yield explicit constraints in source order without loose duplicates."""

    command = html.unescape(command or "")
    metric_pattern = r"(QED|MW|LogP|cLogP|TPSA|SA\s*Score|SAScore|SA|分子量)"
    number_pattern = r"([+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+))"
    operator_pattern = r"(<=|>=|<|>|≤|≥|不超过|不高于|不大于|不低于|不少于|不小于|低于|小于|高于|大于|至少)"

    forward = re.compile(
        metric_pattern + r"\s*" + operator_pattern + r"\s*" + number_pattern,
        re.IGNORECASE,
    )
    forward_matches: list[tuple[tuple[int, int], str, str, float]] = []
    for match in forward.finditer(command):
        raw_metric, operator, value = match.groups()
        normalized = operator.replace("≤", "<=").replace("≥", ">=")
        if normalized in ("不超过", "不高于", "不大于"):
            normalized = "<="
        elif normalized in ("至少", "不低于", "不少于", "不小于"):
            normalized = ">="
        elif normalized in ("低于", "小于"):
            normalized = "<"
        elif normalized in ("高于", "大于"):
            normalized = ">"
        parsed = (match.span(), raw_metric, normalized, float(value))
        forward_matches.append(parsed)
        yield raw_metric, normalized, float(value)

    # Bound the natural-language match so a later metric is never consumed.
    loose = re.compile(
        metric_pattern
        + r"[^\n,，;；。]{0,8}?"
        + number_pattern
        + r"\s*(以下|以内|以上)",
        re.IGNORECASE,
    )
    for match in loose.finditer(command):
        if any(
            match.start() < span[1] and match.end() > span[0]
            for span, *_ in forward_matches
        ):
            continue
        raw_metric, value, suffix = match.groups()
        yield raw_metric, (">=" if suffix == "以上" else "<="), float(value)

    direction_only = re.compile(
        r"(提高|增加|提升|改善|降低|减少|控制|减小)\s*" + metric_pattern,
        re.IGNORECASE,
    )
    for match in direction_only.finditer(command):
        verb, raw_metric = match.groups()
        direction = "max" if verb in ("提高", "增加", "提升", "改善") else "min"
        yield raw_metric, direction, None


def parse_optimization_goals(command: str = "") -> GoalSpec:
    """Return only targets explicitly stated by the user."""

    goals: GoalSpec = {}
    seen_numeric: set[tuple[str, str, float]] = set()
    numeric_metrics: set[str] = set()
    directions: dict[str, str] = {}
    for raw_metric, operator, threshold in _iter_numeric_goal_matches(command):
        metric = _canonical_metric(raw_metric)
        if not metric:
            continue
        if threshold is not None:
            numeric_metrics.add(metric)
            identity = (metric, operator, float(threshold))
            if identity in seen_numeric:
                continue
            seen_numeric.add(identity)
            direction = _direction_from_operator(operator)
            key = metric if metric not in goals else f"{metric}#{sum(1 for item in goals.values() if item['metric'] == metric) + 1}"
        else:
            direction = operator
            if metric in numeric_metrics:
                continue
            previous = directions.get(metric)
            if previous is not None and previous != direction:
                raise ValueError(f"优化目标冲突：{LABELS.get(metric, metric)}同时要求提高和降低")
            if previous is not None:
                continue
            directions[metric] = direction
            key = metric
        goals[key] = {
            "metric": metric,
            "label": _goal_label(metric, direction, threshold, operator if threshold is not None else None),
            "operator": operator if threshold is not None else None,
            "direction": direction,
            "threshold": threshold,
            "source": "command",
        }
    return goals


def evaluate_goals(properties: Mapping[str, Any], goals: GoalSpec | None = None) -> Dict[str, Any]:
    goals = _copy_default_goals() if goals is None else goals
    items = []
    for _key, goal in goals.items():
        metric = goal["metric"]
        raw_value = properties.get(metric)
        try:
            value = float(raw_value)
            if not math.isfinite(value):
                value = None
        except (TypeError, ValueError):
            value = None

        threshold = goal.get("threshold")
        evaluated = value is not None and threshold is not None
        passed = None
        if evaluated:
            operator = goal.get("operator") or (">=" if goal.get("direction") == "max" else "<=")
            compare = {"<": comparisons.lt, "<=": comparisons.le, ">": comparisons.gt, ">=": comparisons.ge}[operator]
            passed = compare(value, float(threshold))

        items.append(
            {
                "metric": metric,
                "label": goal.get("label") or _goal_label(metric, goal.get("direction", "min"), threshold),
                "value": value,
                "threshold": threshold,
                "direction": goal.get("direction"),
                "source": goal.get("source", "command"),
                "passed": passed,
                "available": evaluated,
                "requires_comparison": threshold is None,
            }
        )

    passed_count = sum(1 for item in items if item["passed"] is True)
    evaluated_count = sum(1 for item in items if item["available"])
    return {
        "items": items,
        "summary": {
            "passed_count": passed_count,
            "evaluated": evaluated_count,
            "total": len(items),
            "all_passed": bool(items) and evaluated_count == len(items) and passed_count == evaluated_count,
        },
    }
