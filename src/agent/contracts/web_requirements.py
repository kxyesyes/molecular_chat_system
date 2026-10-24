"""Closed, immutable compiled Web plans; these values grant no execution authority.

Nested JSON is frozen after validation. Tuple paths preserve the existing
compiler's Python metadata contract through JSON without accepting Python
objects from a wire caller. B1 codecs and permissions remain independent.
"""
from __future__ import annotations

import json
import re
from types import MappingProxyType
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_serializer, field_validator, model_validator

from .decision import decode_protocol_json
from .decision_bindings import RagBindingSource, ReverseBindingSource, Sha256

WEB_PROFILE_REVISION = 'medchat-web-v1'
WEB_TOOLS = frozenset({
    'property_calculator', 'drug_likeness_assessment', 'activity_predictor',
    'target_database_search', 'admet_predictor', 'reverse_target_predictor',
    'rag_search', 'llm_molecular_generator', 'candidate_ranker', 'lead_optimization_verifier',
})
_ERROR = 'invalid_web_requirements'
WEB_REQUIREMENTS_MAX_BYTES = 32 * 1024
_LIMIT = WEB_REQUIREMENTS_MAX_BYTES
StepRef = Annotated[str, StringConstraints(min_length=1, max_length=128, pattern=r'^[A-Za-z0-9][A-Za-z0-9_.:-]*$')]


def frozen_json(value):
    if type(value) is dict:
        return MappingProxyType({k: frozen_json(v) for k, v in value.items()})
    if type(value) is list:
        return tuple(frozen_json(v) for v in value)
    return value


def plain_json(value, *, _depth=0, _count=None):
    """Bound known immutable containers before invoking any serializer hooks."""
    count = [0] if _count is None else _count
    count[0] += 1
    if _depth > 32 or count[0] > 16384:
        raise ValueError(_ERROR)
    if type(value) in (dict, MappingProxyType):
        if any(type(k) is not str or len(k) > _LIMIT for k in value):
            raise ValueError(_ERROR)
        return {k: plain_json(v, _depth=_depth+1, _count=count) for k, v in value.items()}
    if type(value) in (list, tuple):
        return [plain_json(v, _depth=_depth+1, _count=count) for v in value]
    if type(value) in (str, int, float, bool, type(None)):
        if type(value) is str and len(value) > _LIMIT:
            raise ValueError(_ERROR)
        return value
    if type(value) in _MODELS:
        if set(vars(value)) != set(type(value).model_fields):
            raise ValueError(_ERROR)
        return {k: plain_json(v, _depth=_depth+1, _count=count) for k, v in vars(value).items()}
    raise ValueError(_ERROR)


def _bounded(value, *, limit=_LIMIT):
    from src.agent.scientific_session.decision_bounds import validate_json
    validate_json(value, max_bytes=limit, reason=_ERROR)
    return value


def _frozen_object(value):
    _bounded(value)
    if type(value) is not dict:
        raise ValueError(_ERROR)
    return frozen_json(value)


class _Closed(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True, revalidate_instances='always')


class WebActionArguments(_Closed):
    step_ref: StepRef

    @field_validator('step_ref')
    @classmethod
    def whole_reference(cls, value):
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value) is None:
            raise ValueError(_ERROR)
        return value


class WebCompiledStep(_Closed):
    name: StepRef
    tool_name: str
    input_data: Any = None
    input_from: str | None = None
    input_template: str | None = None
    continue_on_error: bool | None = None
    required: bool = True
    timeout_seconds: Annotated[float, Field(gt=0, le=300, allow_inf_nan=False)] | None = None
    output_key: str | None = None
    metadata: Any = Field(default_factory=dict)
    capability: str | None = None
    input_binding: str | None = None
    input_transform: str = 'identity'
    output_contract: str | None = None
    preconditions: tuple[str, ...] = ()
    tuple_paths: tuple[str, ...] = ()

    @field_validator('input_data')
    @classmethod
    def freeze_input(cls, value):
        return frozen_json(_bounded(value))

    @field_validator('metadata')
    @classmethod
    def freeze_metadata(cls, value):
        return _frozen_object(value)

    @field_serializer('input_data', 'metadata')
    def serialize_container(self, value):
        return plain_json(value)

    @model_validator(mode='after')
    def known_step(self):
        WebActionArguments(step_ref=self.name)
        if (self.tool_name not in WEB_TOOLS or self.input_transform not in ('identity', 'smiles_text', 'molecule_batch')
                or set(self.preconditions) - {'target_evidence'}
                or len(self.preconditions) != len(set(self.preconditions))
                or len(self.tuple_paths) > 256 or len(self.tuple_paths) != len(set(self.tuple_paths))):
            raise ValueError(_ERROR)
        for path in self.tuple_paths:
            if not path.startswith(('/metadata/', '/input_data/')) and path not in ('/metadata', '/input_data'):
                raise ValueError(_ERROR)
            current = {'metadata': self.metadata, 'input_data': self.input_data}
            try:
                for part in path[1:].split('/'):
                    key = part.replace('~1', '/').replace('~0', '~')
                    current = current[int(key)] if type(current) is tuple else current[key]
                if type(current) is not tuple:
                    raise ValueError(_ERROR)
            except (KeyError, IndexError, TypeError):
                raise ValueError(_ERROR) from None
        return self


class WebTaskRequirements(_Closed):
    version: Literal['1']
    profile: Literal['medchat-web-v1']
    workflow_name: Annotated[str, StringConstraints(min_length=1, max_length=128)]
    request: Any
    steps: Annotated[tuple[WebCompiledStep, ...], Field(max_length=12)]
    dependencies: dict[str, tuple[str, ...]]
    metadata: Any
    forbidden_tools: Annotated[tuple[str, ...], Field(max_length=32)] = ()
    plan_sha256: Sha256

    @field_validator('request', 'metadata')
    @classmethod
    def freeze_objects(cls, value):
        return _frozen_object(value)

    @field_validator('dependencies')
    @classmethod
    def freeze_dependencies(cls, value):
        return MappingProxyType(value)

    @field_serializer('request', 'metadata', 'dependencies')
    def serialize_objects(self, value):
        return plain_json(value)

    @model_validator(mode='after')
    def consistent_plan(self):
        names = [s.name for s in self.steps]
        if (len(names) != len(set(names)) or set(self.dependencies) != set(names)
                or len(self.forbidden_tools) != len(set(self.forbidden_tools))
                or set(self.forbidden_tools) - WEB_TOOLS
                or {s.tool_name for s in self.steps} & set(self.forbidden_tools)
                or (not self.steps and self.workflow_name != 'ordinary_chat')):
            raise ValueError(_ERROR)
        seen = set()
        for step in self.steps:
            deps = self.dependencies[step.name]
            if len(deps) != len(set(deps)) or not set(deps) <= seen:
                raise ValueError(_ERROR)
            seen.add(step.name)
        raw = plain_json(self)
        digest = raw.pop('plan_sha256')
        if digest != web_plan_digest(raw):
            raise ValueError(_ERROR)
        return self


class WebBindingRole(_Closed):
    role: Literal['dependency']
    step_ref: StepRef
    evidence_id: StepRef
    output_sha256: Sha256

    @field_validator('step_ref', 'evidence_id')
    @classmethod
    def whole_reference(cls, value):
        return WebActionArguments(step_ref=value).step_ref


class WebBindingProof(_Closed):
    version: Literal['1']
    profile: Literal['medchat-web-v1']
    requirements_sha256: Sha256
    action_sha256: Sha256
    input_sha256: Sha256
    roles: Annotated[tuple[WebBindingRole, ...], Field(max_length=12)]
    own_source: Annotated[RagBindingSource | ReverseBindingSource, Field(discriminator='kind')] | None
    selection_sha256: Sha256 | None
    policy: Literal['web-v1']
    step_ref: StepRef
    plan_sha256: Sha256

    @model_validator(mode='after')
    def distinct_dependencies(self):
        WebActionArguments(step_ref=self.step_ref)
        if (len({r.step_ref for r in self.roles}) != len(self.roles)
                or len({r.evidence_id for r in self.roles}) != len(self.roles)
                or any(r.step_ref == self.step_ref for r in self.roles)):
            raise ValueError('invalid_web_binding_proof')
        return self


_MODELS = {WebTaskRequirements, WebCompiledStep, WebActionArguments, WebBindingProof,
           WebBindingRole, RagBindingSource, ReverseBindingSource}


def web_plan_digest(value):
    from src.agent.evidence.ledger import EvidenceLedger
    return EvidenceLedger.output_digest(_bounded(value))


def _parse(value, model, error, limit):
    try:
        if type(value) is str:
            raw = decode_protocol_json(value, max_bytes=limit, max_depth=32)
        elif type(value) is model:
            raw = plain_json(value)
        elif type(value) is dict:
            raw = value
        else:
            raise ValueError(error)
        _bounded(raw, limit=limit)
        result = model.model_validate_json(json.dumps(raw, ensure_ascii=False, allow_nan=False), strict=True)
        _bounded(plain_json(result), limit=limit)
        return result
    except (ValueError, TypeError, RecursionError, OverflowError):
        raise ValueError(error) from None


def parse_web_requirements(value):
    return _parse(value, WebTaskRequirements, _ERROR, _LIMIT)


def parse_web_action_arguments(value):
    return _parse(value, WebActionArguments, 'invalid_web_action_arguments', 4096)


def parse_web_binding_proof(value):
    return _parse(value, WebBindingProof, 'invalid_web_binding_proof', 8192)
