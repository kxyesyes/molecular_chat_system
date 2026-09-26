"""Immutable B1 obligations, not tool authorization or model-authored arguments.

Chemical validation is deliberately deferred to preparation, never import time.
"""
from __future__ import annotations

import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .decision_bindings import _bounded_model_payload
from .task_requirements import MolecularRequirement, Smiles, ToolName


_ERROR = 'invalid_binding_requirements'


class _Requirement(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra='forbid', revalidate_instances='always')


class _AnalysisRequirement(_Requirement):
    exact_molecule_count: Annotated[int, Field(ge=1, le=100)] | None = None
    expected_smiles: Annotated[tuple[Smiles, ...], Field(max_length=100)] = ()

    @model_validator(mode='after')
    def subjects_and_count(self):
        if (len(set(self.expected_smiles)) != len(self.expected_smiles)
                or self.expected_smiles and self.exact_molecule_count is not None
                and len(self.expected_smiles) != self.exact_molecule_count):
            raise ValueError(_ERROR)
        return self


class AdmetRequirement(_AnalysisRequirement):
    tool_name: Literal['admet_predictor']
    scope: Literal['available_methods'] = 'available_methods'


class ActivityRequirement(_AnalysisRequirement):
    tool_name: Literal['activity_predictor']
    target: Annotated[str, StringConstraints(min_length=1, max_length=128)] | None = None


AnalysisRequirement = Annotated[AdmetRequirement | ActivityRequirement, Field(discriminator='tool_name')]


class ReverseRequirement(_Requirement):
    expected_smiles: Smiles


class RetrievalRequirement(_Requirement):
    query: Annotated[str, StringConstraints(min_length=1, max_length=16384)]
    k: Literal[3] = 3
    require_hits: bool = False

    @field_validator('query')
    @classmethod
    def exact_nonblank_utf8(cls, value):
        if not value.strip() or len(value.encode('utf-8')) > 16384:
            raise ValueError(_ERROR)
        return value

    @field_validator('k', mode='before')
    @classmethod
    def native_k(cls, value):
        if type(value) is not int:
            raise ValueError(_ERROR)
        return value


class TargetRequirement(_Requirement):
    input: Literal['user', 'reverse']
    query: Annotated[str, StringConstraints(min_length=1, max_length=8192)] | None = None
    require_resolved: bool = False

    @model_validator(mode='after')
    def user_query_only(self):
        if (self.input == 'user' and (self.query is None or not self.query.strip())
                or self.input == 'reverse' and self.query is not None):
            raise ValueError(_ERROR)
        return self


class BindingRequirements(_Requirement):
    version: Literal['2']
    profile: Literal['ordinary-semantic-b1-v1']
    forbidden_tools: Annotated[tuple[ToolName, ...], Field(max_length=32)] = ()
    molecular_results: Annotated[tuple[MolecularRequirement, ...], Field(max_length=2)] = ()
    analysis_results: Annotated[tuple[AnalysisRequirement, ...], Field(max_length=2)] = ()
    reverse_result: ReverseRequirement | None = None
    retrieval_result: RetrievalRequirement | None = None
    target_result: TargetRequirement | None = None

    @model_validator(mode='after')
    def consistent_tools(self):
        names = [r.tool_name for r in (*self.molecular_results, *self.analysis_results)]
        if (len(set(names)) != len(names)
                or len(set(self.forbidden_tools)) != len(self.forbidden_tools)
                or any(re.fullmatch(r'[a-z][a-z0-9_]{0,63}', name) is None for name in self.forbidden_tools)
                or required_binding_tools(self) & set(self.forbidden_tools)):
            raise ValueError(_ERROR)
        return self


def required_binding_tools(requirements: BindingRequirements, required_tools=()) -> frozenset[str]:
    """Pure union for prepared requirements; membership never grants permission."""
    names = set(required_tools) | {r.tool_name for r in (
        *requirements.molecular_results, *requirements.analysis_results)}
    for field, tool in (('reverse_result', 'reverse_target_predictor'),
                        ('retrieval_result', 'rag_search'), ('target_result', 'target_database_search')):
        if getattr(requirements, field) is not None:
            names.add(tool)
    return frozenset(names)


def parse_binding_requirements(value) -> BindingRequirements:
    """Explicit v2 only, bounded before serialization and after default expansion."""
    models = {
        BindingRequirements: {'forbidden_tools', 'molecular_results', 'analysis_results'},
        MolecularRequirement: {'required_metrics', 'expected_smiles'},
        AdmetRequirement: {'expected_smiles'}, ActivityRequirement: {'expected_smiles'},
        ReverseRequirement: set(), RetrievalRequirement: set(), TargetRequirement: set(),
    }
    try:
        raw = _bounded_model_payload(value, models=models, max_bytes=32768, reason=_ERROR)
        result = BindingRequirements.model_validate_json(
            json.dumps(raw, ensure_ascii=False, allow_nan=False), strict=True)
        # Project only known fields, after validation; no unbounded model_dump.
        _bounded_model_payload(result, models=models, max_bytes=32768, reason=_ERROR)
        return result
    except (TypeError, ValueError, RecursionError):
        raise ValueError(_ERROR) from None
