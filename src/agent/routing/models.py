from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class RouteCandidate(BaseModel):
    skill_name: str
    score: float = Field(ge=0.0, le=1.0)
    reasons: list[str] = Field(default_factory=list)


class RouteDecision(BaseModel):
    selected_skill: str | None
    confidence: float = Field(ge=0.0, le=1.0)
    candidates: list[RouteCandidate] = Field(default_factory=list)
    reasons: list[str] = Field(default_factory=list)
    source: Literal["rule", "scoring", "llm", "fallback"]
    requires_confirmation: bool = False
    allowed_tools: list[str] = Field(default_factory=list)
