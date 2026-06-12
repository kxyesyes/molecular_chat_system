from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentContext:
    query: str
    trace_id: str
    user_id: str | None = None
    session_id: str | None = None
    active_skill: str | None = None
    workflow_name: str | None = None
    model_name: str | None = None
    stream: bool = True
    temperature: float = 0.7
    mol_count: int = 5
    metadata: dict[str, Any] = field(default_factory=dict)

    def with_skill(self, skill_name: str) -> "AgentContext":
        return AgentContext(
            query=self.query,
            trace_id=self.trace_id,
            user_id=self.user_id,
            session_id=self.session_id,
            active_skill=skill_name,
            workflow_name=self.workflow_name,
            model_name=self.model_name,
            stream=self.stream,
            temperature=self.temperature,
            mol_count=self.mol_count,
            metadata=dict(self.metadata),
        )
