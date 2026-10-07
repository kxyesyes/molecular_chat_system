"""Typed transport boundary for lead-optimization verification.

The verifier still accepts opaque scientific records produced by the existing
property, ADMET, activity and generation tools.  This module types the
workflow envelope and the verifier's decision fields without pretending that
the nested scientific values are interchangeable or complete.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class _View(BaseModel):
    model_config = ConfigDict(strict=True, extra="allow", revalidate_instances="always")


class LeadOptimizationInput(_View):
    query: str = ""
    metadata: dict[str, Any] | None = None
    outputs: dict[str, Any]


class LeadOptimizationOutput(_View):
    status: Literal["supported", "evaluated_not_improved", "unverified"]
    objectives: list[dict[str, Any]]
    unverifiable_constraints: list[str] = Field(default_factory=list)
    baseline_smiles: str | None = None
    candidates: list[dict[str, Any]]
    supported_candidate_count: int = Field(ge=0)


__all__ = ["LeadOptimizationInput", "LeadOptimizationOutput"]
