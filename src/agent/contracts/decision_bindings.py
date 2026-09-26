"""Closed B reference syntax, not evidence resolution or execution authority.

Handles (including identifier-shaped CCO) require a future same-run resolver to
verify existence, ownership, producer roles and receipts. No chemistry or I/O.
"""
import json
import re
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator, model_validator

from .decision import EvidenceId


B1_PROFILE_REVISION = "ordinary-semantic-b1-v1"
B2_PROFILE_REVISION = "ordinary-semantic-b2-v1"
TOOL_FEATURE_PAIRS = (
    ("property_calculator", "property_calculator"),
    ("drug_likeness_assessment", "drug_likeness_assessment"),
    ("activity_predictor", "activity_predictor"),
    ("target_database_search", "target_database_search"),
    ("admet_predictor", "admet_prediction"),
    ("reverse_target_predictor", "reverse_target"),
    ("rag_search", "rag_retrieval"),
    ("llm_molecular_generator", "molecule_generation"),
    ("candidate_ranker", "molecule_ranking"),
)
B1_TOOLS = frozenset(tool for tool, _ in TOOL_FEATURE_PAIRS[:7])
B2_TOOLS = frozenset(tool for tool, _ in TOOL_FEATURE_PAIRS)

_ERROR = 'invalid_binding_arguments'


class _BindingArguments(BaseModel):
    model_config = ConfigDict(strict=True, extra='forbid', frozen=True)

    @field_validator('*', mode='before')
    @classmethod
    def check_present_reference(cls, value):
        # Defaults may be None internally; explicitly supplied null never is.
        # EvidenceId's shared terminal-$ pattern alone allows a final newline.
        if value is None or (type(value) is str and
                re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value) is None):
            raise ValueError(_ERROR)
        return value


class MolecularBindingArguments(_BindingArguments):
    input_ref: EvidenceId


class RagBindingArguments(_BindingArguments):
    input_ref: Literal['user']


class TargetBindingArguments(_BindingArguments):
    input_ref: EvidenceId
    record_ref: EvidenceId | None = None

    @model_validator(mode='after')
    def check_target_shape(self):
        if ((self.input_ref == 'user') != (self.record_ref is None)
                or self.record_ref == 'user'):
            raise ValueError(_ERROR)
        return self


class GenerationBindingArguments(_BindingArguments):
    input_ref: Literal['user']
    target_ref: EvidenceId | None = None

    @model_validator(mode='after')
    def check_target_reference(self):
        if self.target_ref == 'user':
            raise ValueError(_ERROR)
        return self


class RankingEvidenceReferences(_BindingArguments):
    properties: EvidenceId
    admet: EvidenceId | None = None
    activity: EvidenceId | None = None


class RankingBindingArguments(_BindingArguments):
    input_ref: EvidenceId
    evidence_refs: RankingEvidenceReferences

    @model_validator(mode='after')
    def check_distinct_sources(self):
        sources = [value for value in (self.input_ref, self.evidence_refs.properties,
            self.evidence_refs.admet, self.evidence_refs.activity) if value is not None]
        if 'user' in sources or len(set(sources)) != len(sources):
            raise ValueError(_ERROR)
        return self


def parse_binding_arguments(tool_name, arguments, *, profile_revision=None):
    """Bound plain JSON first, then return a detached, immutable syntax value.

    Omitted/None profiles are payload errors, never an implicit B upgrade.
    The old decision and v1 requirement parsers remain independent and closed.
    """
    try:
        # Lazy: harness initialization also imports Web capability contracts.
        from src.agent.harness.decision_bounds import validate_json
        from src.agent.persistence.redaction import contains_secret_material

        validate_json(arguments, max_bytes=4096, max_depth=8, max_nodes=64,
                      reason=_ERROR)
        if type(arguments) is not dict:
            raise ValueError(_ERROR)
        if any(type(value) is not str or not 1 <= len(value) <= 64
               for value in (profile_revision, tool_name)):
            raise ValueError(_ERROR)
        if profile_revision not in (B1_PROFILE_REVISION, B2_PROFILE_REVISION):
            raise ValueError(_ERROR)
        tools = B1_TOOLS if profile_revision == B1_PROFILE_REVISION else B2_TOOLS
        if tool_name not in tools or contains_secret_material(arguments):
            raise ValueError(_ERROR)
        model = {
            'rag_search': RagBindingArguments,
            'target_database_search': TargetBindingArguments,
            'llm_molecular_generator': GenerationBindingArguments,
            'candidate_ranker': RankingBindingArguments,
        }.get(tool_name, MolecularBindingArguments)
        return model.model_validate(arguments, strict=True)
    except (ValueError, TypeError, RecursionError):
        raise ValueError(_ERROR) from None


class _ProofView(BaseModel):
    model_config = ConfigDict(strict=True, frozen=True, extra='forbid', revalidate_instances='always')


Sha256 = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{64}$', min_length=64, max_length=64)]
GenerationId = Annotated[str, StringConstraints(pattern=r'^[a-f0-9]{32}$', min_length=32, max_length=32)]


class RagBindingSource(_ProofView):
    kind: Literal['rag']
    generation_id: GenerationId
    epoch: Annotated[int, Field(ge=0)]
    configuration_sha256: Sha256
    source_identity_sha256: Sha256


class ReverseBindingSource(_ProofView):
    kind: Literal['reverse']
    generation_id: GenerationId
    source_sha256: Sha256
    configuration_sha256: Sha256


class BindingRole(_ProofView):
    role: Literal['molecules', 'reverse_record']
    evidence_id: EvidenceId
    output_sha256: Sha256

    @field_validator('evidence_id')
    @classmethod
    def whole_evidence_id(cls, value):
        if re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value) is None:
            raise ValueError('invalid_binding_proof')
        return value


class BindingProof(_ProofView):
    """Syntax/consistency only; source eligibility belongs to the future resolver.

    Future binding hashes use EvidenceLedger.output_digest's default-separator
    representation. Producer receipt codecs and observation ceilings are unchanged.
    """
    version: Literal['1']
    profile: Literal['ordinary-semantic-b1-v1']
    requirements_sha256: Sha256
    action_sha256: Sha256
    input_sha256: Sha256
    roles: Annotated[tuple[BindingRole, ...], Field(max_length=5)]
    own_source: Annotated[RagBindingSource | ReverseBindingSource, Field(discriminator='kind')] | None
    selection_sha256: Sha256 | None
    policy: Literal['b1-v1']

    @model_validator(mode='after')
    def b1_roles(self):
        # Five is a wire ceiling, never B1 permission to combine sources.
        if len(self.roles) > 1 or ((self.selection_sha256 is not None) !=
                bool(self.roles and self.roles[0].role == 'reverse_record')):
            raise ValueError('invalid_binding_proof')
        return self


def _bounded_model_payload(value, *, models, max_bytes, reason):
    """Explicit known-model projection, never model_dump on unvalidated data.

    Raw callers get native JSON only. For server models only named tuple fields
    become arrays; arbitrary tuples/Enums/bytes or serializer hooks cannot enter.
    Depth/work are bounded even for model_construct cycles before any JSON dump.
    The native walk then bounds the complete default-separator representation.
    """
    from src.agent.harness.decision_bounds import validate_json

    if type(value) is dict:
        validate_json(value, max_bytes=max_bytes, reason=reason)
        return value
    if type(value) not in models:
        raise ValueError(reason)
    active = set()
    nodes = 0

    def project(item, depth=0, tuple_field=False):
        nonlocal nodes
        nodes += 1
        if depth > 32 or nodes > 16384:
            raise ValueError(reason)
        kind = type(item)
        if kind in models or kind in (dict, list, tuple):
            identity = id(item)
            if identity in active:
                raise ValueError(reason)
            active.add(identity)
            try:
                if kind in models:
                    fields = kind.model_fields
                    values = vars(item)
                    if (set(values) != set(fields) or item.__pydantic_extra__):
                        raise ValueError(reason)
                    return {name: project(values[name], depth + 1, name in models[kind])
                            for name in fields}
                if len(item) > 16384 - nodes:
                    raise ValueError(reason)
                if kind is dict:
                    if any(type(key) is not str for key in item):
                        raise ValueError(reason)
                    return {key: project(child, depth + 1) for key, child in item.items()}
                if kind is tuple and not tuple_field:
                    raise ValueError(reason)
                return [project(child, depth + 1) for child in item]
            finally:
                active.remove(identity)
        # Native validation below rejects invalid scalars without coercing them.
        return item

    projected = project(value)
    validate_json(projected, max_bytes=max_bytes, reason=reason)
    return projected


def parse_binding_proof(value) -> BindingProof:
    """Return a bounded detached proof, including revalidation of constructed views."""
    error = 'invalid_binding_proof'
    models = {BindingProof: {'roles'}, BindingRole: set(),
              RagBindingSource: set(), ReverseBindingSource: set()}
    try:
        raw = _bounded_model_payload(value, models=models, max_bytes=8192, reason=error)
        result = BindingProof.model_validate_json(json.dumps(raw, ensure_ascii=False, allow_nan=False), strict=True)
        _bounded_model_payload(result, models=models, max_bytes=8192, reason=error)
        return result
    except (TypeError, ValueError, RecursionError):
        raise ValueError(error) from None
