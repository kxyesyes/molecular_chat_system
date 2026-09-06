from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from src.agent.persistence import redact_sensitive


@dataclass
class EvaluationCase:
    case_id: str
    version: str
    category: str
    prompt: str
    expected_skill: str | None = None
    expected_tools: list[str] = field(default_factory=list)
    forbidden_tools: list[str] = field(default_factory=list)
    expected_steps: list[str] = field(default_factory=list)
    expected_events: list[str] = field(default_factory=list)
    forbidden_patterns: list[str] = field(default_factory=list)
    requires_external: list[str] = field(default_factory=list)
    allow_mock: bool = False
    pass_criteria: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    scientific_acceptance: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationResult:
    case_id: str
    status: str
    score: float
    actual_skill: str | None = None
    actual_tools: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)


@dataclass
class EvaluationReport:
    mode: str
    metrics: dict[str, Any]
    results: list[EvaluationResult]
    cases: list[EvaluationCase] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return redact_sensitive(
            {
                "mode": self.mode,
                "metrics": self.metrics,
                "results": [asdict(item) for item in self.results],
                "cases": [asdict(item) for item in self.cases],
            }
        )

    def write_json(self, path: str | Path) -> Path:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(self.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return output
