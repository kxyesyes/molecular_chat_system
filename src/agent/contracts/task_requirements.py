"""Immutable service-owned result requirements; not a model decision schema.

These are bounded acceptance criteria, not tool authorization, chemical
validation, evidence verification, or permission for model self-evaluation.
"""
from __future__ import annotations

import json
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


ToolName = Annotated[str, StringConstraints(pattern=r'^[a-z][a-z0-9_]*$', max_length=64)]
Metric = Literal['molecular_weight', 'logp', 'tpsa', 'hbd', 'hba', 'qed', 'lipinski_compliant']
Smiles = Annotated[str, StringConstraints(min_length=1, max_length=8192, pattern=r'^\S+$')]


class MolecularRequirement(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra='forbid')
    tool_name: Literal['property_calculator', 'drug_likeness_assessment']
    exact_molecule_count: Annotated[int, Field(ge=1, le=100)] | None = None
    required_metrics: Annotated[tuple[Metric, ...], Field(max_length=7)] = ()
    expected_smiles: Annotated[tuple[Smiles, ...], Field(max_length=100)] = ()

    @model_validator(mode='after')
    def validate_consistency(self):
        if (len(set(self.required_metrics)) != len(self.required_metrics)
                or len(set(self.expected_smiles)) != len(self.expected_smiles)
                or self.tool_name == 'property_calculator' and 'lipinski_compliant' in self.required_metrics
                or self.expected_smiles and self.exact_molecule_count is not None
                and len(self.expected_smiles) != self.exact_molecule_count):
            raise ValueError('inconsistent molecular requirement')
        return self


class TaskRequirements(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra='forbid')
    version: Literal['1'] = '1'
    forbidden_tools: Annotated[tuple[ToolName, ...], Field(max_length=32)] = ()
    molecular_results: Annotated[tuple[MolecularRequirement, ...], Field(max_length=2)] = ()

    @model_validator(mode='after')
    def validate_consistency(self):
        names = [r.tool_name for r in self.molecular_results]
        if (len(set(names)) != len(names) or len(set(self.forbidden_tools)) != len(self.forbidden_tools)
                or set(names) & set(self.forbidden_tools)):
            raise ValueError('conflicting task requirements')
        return self


def parse_task_requirements(value) -> TaskRequirements:
    """Revalidate even constructed instances and detach caller-owned containers."""
    if value is None:
        return TaskRequirements()
    try:
        if isinstance(value, TaskRequirements):
            # Preserve invalid Python types (notably bytes) until strict
            # validation; JSON-mode serialization could silently coerce them.
            # Constructed data must not leak through serializer warnings.
            value = value.model_dump(mode='python', warnings=False)
        if not isinstance(value, dict):
            raise ValueError('invalid requirements')
        raw = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(raw.encode('utf-8')) > 32768:
            raise ValueError('requirements too large')
        return TaskRequirements.model_validate_json(raw, strict=True)
    except (TypeError, ValueError, RecursionError):
        raise ValueError('invalid_task_requirements') from None
