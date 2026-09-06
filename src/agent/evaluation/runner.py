from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any, Iterable

from src.agent.routing import HybridSkillRouter

from .models import EvaluationCase, EvaluationReport, EvaluationResult


def load_cases(path: str | Path) -> list[EvaluationCase]:
    cases = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            cases.append(EvaluationCase(**json.loads(line)))
    return cases


def score_workflow(
    expected_tools: list[str],
    actual_tools: list[str],
    forbidden_tools: list[str],
    fabricated: bool,
    structured_output: bool,
) -> dict[str, Any]:
    expected_set = set(expected_tools)
    actual_set = set(actual_tools)
    forbidden_called = bool(actual_set & set(forbidden_tools))
    tool_selection = (
        2
        if expected_set.issubset(actual_set) and not forbidden_called
        else 1
        if expected_set & actual_set and not forbidden_called
        else 0
    )
    expected_order = [tool for tool in expected_tools if tool in actual_set]
    actual_expected_order = [tool for tool in actual_tools if tool in expected_set]
    workflow_order = (
        2
        if expected_order == expected_tools
        and actual_expected_order[: len(expected_tools)] == expected_tools
        else 1
        if actual_expected_order == expected_order and expected_order
        else 0
    )
    completion = (
        2
        if expected_set.issubset(actual_set)
        else 1
        if expected_set & actual_set
        else 0
    )
    anti_hallucination = 0 if fabricated else 2
    output_usability = 2 if structured_output else 0
    score = (
        tool_selection
        + workflow_order
        + completion
        + anti_hallucination
        + output_usability
    )
    return {
        "score": score,
        "tool_selection": tool_selection,
        "workflow_order": workflow_order,
        "completion": completion,
        "anti_hallucination": anti_hallucination,
        "output_usability": output_usability,
        "forbidden_called": forbidden_called,
    }


def chemistry_metrics(
    smiles: list[str], requested_count: int | None = None
) -> dict[str, Any]:
    try:
        from rdkit import Chem
    except ImportError:
        Chem = None

    valid = []
    for value in smiles:
        is_valid = bool(value)
        if Chem is not None:
            try:
                is_valid = Chem.MolFromSmiles(value) is not None
            except Exception:
                is_valid = False
        if is_valid:
            valid.append(value)
    total = len(smiles)
    return {
        "valid_smiles_rate": len(valid) / total if total else 0.0,
        "unique_valid_smiles": len(set(valid)),
        "requested_count": requested_count,
        "actual_count": total,
        "requested_count_compliance": (
            total == requested_count if requested_count is not None else True
        ),
    }


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return float(ordered[lower])
    weight = position - lower
    return float(ordered[lower] * (1 - weight) + ordered[upper] * weight)


def latency_metrics(elapsed_ms: Iterable[float]) -> dict[str, float]:
    values = [float(value) for value in elapsed_ms]
    return {
        "p50_ms": _percentile(values, 0.5),
        "p95_ms": _percentile(values, 0.95),
    }


class EvaluationRunner:
    def __init__(self, router: HybridSkillRouter | None = None):
        self.router = router or HybridSkillRouter()

    def evaluate_routing(
        self, cases: list[EvaluationCase]
    ) -> EvaluationReport:
        results = []
        top1_hits = 0
        top3_hits = 0
        abstention_cases = 0
        correct_abstentions = 0
        confusion: dict[str, dict[str, int]] = {}

        for case in cases:
            decision = self.router.decide(case.prompt)
            top_three = [item.skill_name for item in decision.candidates[:3]]
            top1 = decision.selected_skill == case.expected_skill
            top3 = (
                case.expected_skill in top_three
                if case.expected_skill is not None
                else decision.selected_skill is None
            )
            top1_hits += int(top1)
            top3_hits += int(top3)
            if case.expected_skill is None:
                abstention_cases += 1
                correct_abstentions += int(decision.selected_skill is None)
            expected_label = case.expected_skill or "none"
            actual_label = decision.selected_skill or "none"
            confusion.setdefault(expected_label, {})
            confusion[expected_label][actual_label] = (
                confusion[expected_label].get(actual_label, 0) + 1
            )
            results.append(
                EvaluationResult(
                    case_id=case.case_id,
                    status="passed" if top1 else "failed",
                    score=10 if top1 else 5 if top3 else 0,
                    actual_skill=decision.selected_skill,
                    details={
                        "confidence": decision.confidence,
                        "source": decision.source,
                        "top_three": top_three,
                        "reasons": decision.reasons,
                    },
                )
            )

        count = len(cases)
        metrics = {
            "case_count": count,
            "top1_accuracy": top1_hits / count if count else 0.0,
            "top3_accuracy": top3_hits / count if count else 0.0,
            "correct_abstention_rate": (
                correct_abstentions / abstention_cases
                if abstention_cases
                else 1.0
            ),
            "confusion_matrix": confusion,
        }
        return EvaluationReport(
            mode="routing",
            metrics=metrics,
            results=results,
            cases=cases,
        )
