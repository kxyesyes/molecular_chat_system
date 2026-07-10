"""Goal parsing and scoring helpers for molecular design optimization."""

from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Mapping


GoalSpec = Dict[str, Dict[str, Any]]


DEFAULT_GOALS: GoalSpec = {
    "qed": {
        "metric": "qed",
        "label": "QED >= 0.7",
        "direction": "max",
        "threshold": 0.7,
        "source": "default",
    },
    "mw": {
        "metric": "mw",
        "label": "MW <= 500 Da",
        "direction": "min",
        "threshold": 500,
        "source": "default",
    },
    "logp": {
        "metric": "logp",
        "label": "LogP <= 5",
        "direction": "min",
        "threshold": 5,
        "source": "default",
    },
    "sa_score": {
        "metric": "sa_score",
        "label": "SA Score <= 3.5",
        "direction": "min",
        "threshold": 3.5,
        "source": "default",
    },
}


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
    return {key: value.copy() for key, value in DEFAULT_GOALS.items()}


def _canonical_metric(raw_metric: str) -> str | None:
    normalized = raw_metric.strip().lower()
    return METRIC_ALIASES.get(normalized)


def _goal_label(metric: str, direction: str, threshold: float) -> str:
    comparator = ">=" if direction == "max" else "<="
    value = int(threshold) if float(threshold).is_integer() else threshold
    suffix = " Da" if metric == "mw" else ""
    return f"{LABELS.get(metric, metric)} {comparator} {value}{suffix}"


def _direction_from_operator(operator: str, metric: str) -> str:
    if operator in (">", ">="):
        return "max"
    if operator in ("<", "<="):
        return "min"
    return DEFAULT_GOALS.get(metric, {}).get("direction", "min")


def _iter_numeric_goal_matches(command: str) -> Iterable[tuple[str, str, float]]:
    metric_pattern = r"(QED|MW|LogP|cLogP|TPSA|SA\s*Score|SAScore|SA|分子量)"
    number_pattern = r"([0-9]+(?:\.[0-9]+)?)"
    operator_pattern = r"(<=|>=|<|>|≤|≥|不超过|低于|小于|高于|大于|至少|不低于)"

    # Example: MW < 300, LogP <= 3, QED >= 0.7
    forward = re.compile(
        metric_pattern + r"\s*" + operator_pattern + r"\s*" + number_pattern,
        re.IGNORECASE,
    )
    # Example: 分子量控制在 300 以下, QED 提高到 0.7 以上
    loose = re.compile(
        metric_pattern
        + r".{0,8}?"
        + number_pattern
        + r"\s*(以下|以内|以内|以上|左右)?",
        re.IGNORECASE,
    )

    for metric, operator, value in forward.findall(command or ""):
        normalized_operator = operator.replace("≤", "<=").replace("≥", ">=")
        if normalized_operator in ("不超过", "低于", "小于"):
            normalized_operator = "<="
        elif normalized_operator in ("高于", "大于", "至少", "不低于"):
            normalized_operator = ">="
        yield metric, normalized_operator, float(value)

    for metric, value, suffix in loose.findall(command or ""):
        if forward.search(metric):
            continue
        operator = ">=" if suffix == "以上" else "<="
        canonical = _canonical_metric(metric)
        if canonical == "qed" and suffix not in ("以下", "以内"):
            operator = ">="
        yield metric, operator, float(value)


def parse_optimization_goals(command: str = "") -> GoalSpec:
    """Parse simple numeric optimization goals from a user command.

    The parser intentionally stays conservative: it keeps default demo goals and
    only overrides metrics where users provide clear numeric thresholds.
    """

    goals = _copy_default_goals()
    for raw_metric, operator, threshold in _iter_numeric_goal_matches(command or ""):
        metric = _canonical_metric(raw_metric)
        if not metric:
            continue
        direction = _direction_from_operator(operator, metric)
        goals[metric] = {
            "metric": metric,
            "label": _goal_label(metric, direction, threshold),
            "direction": direction,
            "threshold": threshold,
            "source": "command",
        }
    return goals


def evaluate_goals(properties: Mapping[str, Any], goals: GoalSpec | None = None) -> Dict[str, Any]:
    goals = goals or _copy_default_goals()
    items = []
    for metric, goal in goals.items():
        raw_value = properties.get(metric)
        try:
            value = float(raw_value)
        except (TypeError, ValueError):
            value = None

        passed = False
        if value is not None:
            if goal.get("direction") == "max":
                passed = value >= float(goal["threshold"])
            else:
                passed = value <= float(goal["threshold"])

        items.append(
            {
                "metric": metric,
                "label": goal.get("label") or _goal_label(metric, goal.get("direction", "min"), goal["threshold"]),
                "value": value,
                "threshold": goal.get("threshold"),
                "direction": goal.get("direction"),
                "source": goal.get("source", "default"),
                "passed": passed,
                "available": value is not None,
            }
        )

    passed_count = sum(1 for item in items if item["passed"])
    return {
        "items": items,
        "summary": {
            "passed_count": passed_count,
            "total": len(items),
            "all_passed": passed_count == len(items) and bool(items),
        },
    }


def score_candidate(before: Mapping[str, Any] | None, after: Mapping[str, Any] | None) -> float:
    if not after:
        return 0.0
    before = before or {}
    score = 0.0
    weights = {
        "qed": 30.0,
        "logp": -4.0,
        "mw": -0.02,
        "tpsa": -0.03,
        "sa_score": -5.0,
    }
    for key, weight in weights.items():
        try:
            before_value = float(before.get(key, after.get(key, 0)) or 0)
            after_value = float(after.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
        delta = after_value - before_value
        score += delta * weight
    return round(score, 4)
