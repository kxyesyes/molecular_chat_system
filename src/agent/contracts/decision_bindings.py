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


# A-contract syntax only. These records neither issue a trusted token grant nor
# authorize dispatch, persistence, credit release or backend usage normalization.
_GeneratorStopCode = Literal[
    'generation_control_invalid', 'generator_token_bound_unavailable',
    'generation_cancelled', 'generation_deadline_exceeded',
    'generation_request_budget_exhausted', 'generation_token_budget_exhausted',
    'generation_context_exceeded', 'generation_journal_unavailable',
    'generation_dispatch_uncertain', 'generation_usage_invalid',
    'generation_token_bound_violated', 'generation_cleanup_unsettled',
    'generation_slot_consumed',
]
_GENERATOR_OVERFLOW_ISSUES = frozenset((
    'usage_scalar_overflow', 'usage_wire_overflow', 'usage_scalar_and_wire_overflow',
))
_GENERATOR_SCALAR_OVERFLOW_ISSUES = frozenset((
    'usage_scalar_overflow', 'usage_scalar_and_wire_overflow',
))


def _generator_integer(value):
    # Match the existing validate_json scalar guard even for direct construction.
    # This is a representation limit, not a business-token allowance.
    if type(value) is not int or value.bit_length() > 4096:
        raise ValueError('invalid_generator_integer')
    return value


def _generator_evidence_id(value):
    if value is not None and re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', value) is None:
        raise ValueError('invalid_generator_identity')
    return value


def _generator_hex_id(value):
    # Length is enforced by the existing GenerationId/Sha256 annotations.
    if value is not None and re.fullmatch(r'[a-f0-9]+', value) is None:
        raise ValueError('invalid_generator_identity')
    return value


class GeneratorTokenGrant(_ProofView):
    """Closed claimed grant; backend identity/enforcement remain external gates."""
    version: Literal['1']
    generator_generation: GenerationId
    backend_revision: EvidenceId
    model_artifact_digest: Sha256
    tokenizer_digest: Sha256
    template_options_digest: Sha256
    bound_policy_revision: EvidenceId
    total_reserved_tokens: Annotated[int, Field(gt=0)]
    prompt_token_ceiling: Annotated[int, Field(gt=0)]
    context_token_limit: Annotated[int, Field(gt=0)]
    completion_token_limit: Literal[1000]

    @field_validator('total_reserved_tokens', 'prompt_token_ceiling',
                     'context_token_limit', 'completion_token_limit', mode='before')
    @classmethod
    def native_token_counts(cls, value):
        return _generator_integer(value)

    @field_validator('backend_revision', 'bound_policy_revision')
    @classmethod
    def whole_revision_ids(cls, value):
        return _generator_evidence_id(value)

    @field_validator('generator_generation', 'model_artifact_digest',
                     'tokenizer_digest', 'template_options_digest')
    @classmethod
    def whole_grant_hex_ids(cls, value):
        return _generator_hex_id(value)

    @model_validator(mode='after')
    def context_fits_ceiling(self):
        if self.prompt_token_ceiling + self.completion_token_limit > self.context_token_limit:
            raise ValueError('invalid_generator_token_grant')
        return self


class GeneratorUsage(_ProofView):
    """Nullable observed counts, never a fallback estimate or a refundable debit."""
    prompt_tokens: Annotated[int, Field(ge=0)] | None
    completion_tokens: Annotated[int, Field(ge=0)] | None

    @field_validator('prompt_tokens', 'completion_tokens', mode='before')
    @classmethod
    def native_usage_counts(cls, value):
        return None if value is None else _generator_integer(value)


class GeneratorRequestReceipt(_ProofView):
    version: Literal['1']
    root_id: EvidenceId
    logical_slot_id: GenerationId
    reservation_id: GenerationId
    round_index: Annotated[int, Field(ge=1, le=5)]
    input_sha256: Sha256
    proof_sha256: Sha256
    generator_generation: GenerationId
    grant_sha256: Sha256
    reserved_prompt_tokens: Annotated[int, Field(gt=0)]
    reserved_completion_tokens: Literal[1000]
    phase: Literal['reserved', 'dispatched', 'settled', 'uncertain', 'not_dispatched']
    dispatch_marker_id: GenerationId | None
    client_dispatch_id: GenerationId | None
    backend_request_id: EvidenceId | None
    never_dispatched_evidence_id: EvidenceId | None
    drained: bool
    outcome: Literal['pending', 'success', 'backend_failed', 'stopped',
                     'uncertain', 'bound_violation']
    stop_code: _GeneratorStopCode | None
    usage_status: Literal['not_observed', 'missing', 'valid', 'malformed', 'over_bound']
    usage: GeneratorUsage | None
    usage_issue: Literal['invalid_prompt', 'invalid_completion', 'invalid_both',
                         'usage_scalar_overflow', 'usage_wire_overflow',
                         'usage_scalar_and_wire_overflow'] | None

    @field_validator('round_index', 'reserved_prompt_tokens',
                     'reserved_completion_tokens', mode='before')
    @classmethod
    def native_receipt_counts(cls, value):
        return _generator_integer(value)

    @field_validator('root_id', 'backend_request_id', 'never_dispatched_evidence_id')
    @classmethod
    def whole_receipt_evidence_ids(cls, value):
        return _generator_evidence_id(value)

    @field_validator('logical_slot_id', 'reservation_id', 'input_sha256', 'proof_sha256',
                     'generator_generation', 'grant_sha256', 'dispatch_marker_id',
                     'client_dispatch_id')
    @classmethod
    def whole_receipt_hex_ids(cls, value):
        return _generator_hex_id(value)

    def _check_usage_shape(self):
        error = 'invalid_generator_request_receipt'
        if self.usage_status == 'not_observed':
            if self.usage is not None or self.usage_issue is not None:
                raise ValueError(error)
            return
        if self.usage is None:
            raise ValueError(error)
        prompt, completion = self.usage.prompt_tokens, self.usage.completion_tokens
        missing = prompt is None or completion is None
        over = ((prompt is not None and prompt > self.reserved_prompt_tokens)
                or (completion is not None and completion > self.reserved_completion_tokens))
        issue = self.usage_issue
        if ((issue == 'invalid_prompt' and prompt is not None)
                or (issue == 'invalid_completion' and completion is not None)
                or (issue == 'invalid_both' and (prompt is not None or completion is not None))
                or (issue in _GENERATOR_OVERFLOW_ISSUES and not missing)):
            raise ValueError(error)
        if issue in _GENERATOR_SCALAR_OVERFLOW_ISSUES and self.usage_status != 'over_bound':
            raise ValueError(error)
        if self.usage_status == 'valid':
            valid = not missing and not over and issue is None
        elif self.usage_status == 'missing':
            valid = missing and not over and issue is None
        elif self.usage_status == 'malformed':
            valid = issue is not None and not over and issue not in _GENERATOR_SCALAR_OVERFLOW_ISSUES
        else:  # over_bound: retain a count or an explicitly claimed bounded fact.
            valid = over or issue in _GENERATOR_OVERFLOW_ISSUES
        if not valid:
            raise ValueError(error)

    @model_validator(mode='after')
    def consistent_receipt_facts(self):
        error = 'invalid_generator_request_receipt'
        self._check_usage_shape()
        if ((self.client_dispatch_id is not None and self.dispatch_marker_id is None)
                or (self.backend_request_id is not None and self.client_dispatch_id is None)
                or (self.never_dispatched_evidence_id is not None and self.phase != 'not_dispatched')):
            raise ValueError(error)
        if self.phase in ('reserved', 'dispatched'):
            if (self.outcome != 'pending' or self.drained or self.stop_code is not None
                    or self.usage_status != 'not_observed'
                    or (self.phase == 'reserved' and self.dispatch_marker_id is not None)
                    or (self.phase == 'dispatched' and self.dispatch_marker_id is None)):
                raise ValueError(error)
        elif self.phase == 'not_dispatched':
            if (not self.drained or self.outcome != 'stopped' or self.stop_code is None
                    or self.never_dispatched_evidence_id is None
                    or self.client_dispatch_id is not None or self.backend_request_id is not None
                    or self.usage_status != 'not_observed'):
                raise ValueError(error)
        elif self.phase == 'uncertain':
            # Local drain may later become known while dispatch/settlement is
            # still uncertain. Observed usage is retained, never a success claim.
            if self.outcome != 'uncertain' or self.stop_code is None:
                raise ValueError(error)
        else:  # settled
            if not self.drained or self.client_dispatch_id is None:
                raise ValueError(error)
            if self.outcome == 'success':
                valid = self.stop_code is None and self.usage_status in ('valid', 'missing')
            elif self.outcome == 'backend_failed':
                valid = self.stop_code is None and self.usage_status in ('not_observed', 'valid', 'missing')
            elif self.outcome == 'bound_violation':
                valid = (self.usage_status == 'over_bound'
                         and self.stop_code == 'generation_token_bound_violated')
            elif self.outcome == 'stopped':
                valid = (self.stop_code is not None and self.usage_status != 'over_bound'
                         and (self.usage_status != 'malformed'
                              or self.stop_code == 'generation_usage_invalid'))
            else:
                valid = False
            if not valid:
                raise ValueError(error)
        return self


class BRootBudget(_ProofView):
    """Bounded journal projection; release claims here have no owner authority."""
    version: Literal['1']
    policy: Literal['b-root-inclusive-16-v1']
    root_id: EvidenceId
    input_sha256: Sha256
    proof_sha256: Sha256
    logical_slot_id: GenerationId | None
    slot_state: Literal['unused', 'claimed', 'running', 'complete', 'partial',
                        'failed', 'cancelled', 'uncertain']
    grant: GeneratorTokenGrant
    grant_sha256: Sha256
    main_limit: Annotated[int, Field(ge=1, le=16)]
    root_limit: Literal[16]
    intent_requests: Annotated[int, Field(ge=0, le=1)]
    decision_requests: Annotated[int, Field(ge=0, le=16)]
    main_requests: Annotated[int, Field(ge=0, le=16)]
    generator_request_debits: Annotated[int, Field(ge=0, le=5)]
    root_request_debits: Annotated[int, Field(ge=0, le=16)]
    reserved_tokens: Annotated[int, Field(ge=0)]
    generator_requests: Annotated[tuple[GeneratorRequestReceipt, ...], Field(max_length=5)]

    @field_validator('main_limit', 'root_limit', 'intent_requests', 'decision_requests',
                     'main_requests', 'generator_request_debits', 'root_request_debits',
                     'reserved_tokens', mode='before')
    @classmethod
    def native_budget_counts(cls, value):
        return _generator_integer(value)

    @field_validator('root_id')
    @classmethod
    def whole_budget_evidence_id(cls, value):
        return _generator_evidence_id(value)

    @field_validator('input_sha256', 'proof_sha256', 'logical_slot_id', 'grant_sha256')
    @classmethod
    def whole_budget_hex_ids(cls, value):
        return _generator_hex_id(value)

    @model_validator(mode='after')
    def consistent_root_arithmetic(self):
        from src.agent.evidence.ledger import EvidenceLedger

        error = 'invalid_b_root_budget'
        # Bound the nested grant separately before canonical hashing. The shared
        # digest function is pure; no ledger instance or evidence lookup occurs.
        grant_payload = _bounded_model_payload(
            self.grant, models={GeneratorTokenGrant: set()}, max_bytes=4096, reason=error)
        if self.grant_sha256 != EvidenceLedger.output_digest(grant_payload):
            raise ValueError(error)
        if ((self.slot_state == 'unused') != (self.logical_slot_id is None)
                or (self.slot_state in ('unused', 'claimed') and self.generator_requests)):
            raise ValueError(error)
        rows = self.generator_requests
        for index, row in enumerate(rows, 1):
            if (row.round_index != index or row.root_id != self.root_id
                    or row.logical_slot_id != self.logical_slot_id
                    or row.input_sha256 != self.input_sha256 or row.proof_sha256 != self.proof_sha256
                    or row.generator_generation != self.grant.generator_generation
                    or row.grant_sha256 != self.grant_sha256
                    or row.reserved_prompt_tokens > self.grant.prompt_token_ceiling
                    or row.reserved_prompt_tokens + row.reserved_completion_tokens > self.grant.context_token_limit):
                raise ValueError(error)
        for field in ('reservation_id', 'dispatch_marker_id', 'client_dispatch_id'):
            identities = [getattr(row, field) for row in rows if getattr(row, field) is not None]
            if len(identities) != len(set(identities)):
                raise ValueError(error)
        debited = [row for row in rows if row.phase != 'not_dispatched']
        reserved = sum(row.reserved_prompt_tokens + row.reserved_completion_tokens for row in debited)
        if (self.main_requests != self.intent_requests + self.decision_requests
                or self.main_requests > self.main_limit
                or self.generator_request_debits != len(debited)
                or self.root_request_debits != self.main_requests + len(debited)
                or self.reserved_tokens != reserved or reserved > self.grant.total_reserved_tokens):
            raise ValueError(error)
        if self.slot_state in ('complete', 'partial'):
            if (not any(row.phase == 'settled' and row.outcome == 'success' for row in rows)
                    or any(row.phase not in ('settled', 'not_dispatched') or not row.drained
                           or row.stop_code is not None for row in rows)):
                raise ValueError(error)
        if self.slot_state in ('failed', 'cancelled') and any(not row.drained for row in rows):
            raise ValueError(error)
        return self


def _parse_generator_record(value, *, model, models, max_bytes, error):
    """Use the existing bounded codec; never normalize raw overflowing usage."""
    try:
        if type(value) not in (dict, model):
            raise ValueError(error)
        raw = _bounded_model_payload(value, models=models, max_bytes=max_bytes, reason=error)
        result = model.model_validate_json(
            json.dumps(raw, ensure_ascii=False, allow_nan=False), strict=True)
        _bounded_model_payload(result, models=models, max_bytes=max_bytes, reason=error)
        return result
    except (TypeError, ValueError, RecursionError):
        raise ValueError(error) from None


def parse_generator_token_grant(value: object) -> GeneratorTokenGrant:
    return _parse_generator_record(
        value, model=GeneratorTokenGrant, models={GeneratorTokenGrant: set()},
        max_bytes=4096, error='invalid_generator_token_grant')


def parse_generator_request_receipt(value: object) -> GeneratorRequestReceipt:
    return _parse_generator_record(
        value, model=GeneratorRequestReceipt,
        models={GeneratorRequestReceipt: set(), GeneratorUsage: set()},
        max_bytes=16384, error='invalid_generator_request_receipt')


def parse_b_root_budget(value: object) -> BRootBudget:
    return _parse_generator_record(
        value, model=BRootBudget,
        models={BRootBudget: {'generator_requests'}, GeneratorTokenGrant: set(),
                GeneratorRequestReceipt: set(), GeneratorUsage: set()},
        max_bytes=16384, error='invalid_b_root_budget')


_GENERATOR_RECEIPT_IDENTITIES = (
    'version', 'root_id', 'logical_slot_id', 'reservation_id', 'round_index',
    'input_sha256', 'proof_sha256', 'generator_generation', 'grant_sha256',
    'reserved_prompt_tokens', 'reserved_completion_tokens',
)
_GENERATOR_RECEIPT_EDGES = frozenset((
    ('reserved', 'dispatched'), ('reserved', 'not_dispatched'), ('reserved', 'uncertain'),
    ('dispatched', 'dispatched'), ('dispatched', 'settled'), ('dispatched', 'uncertain'),
    ('dispatched', 'not_dispatched'), ('uncertain', 'uncertain'),
    ('uncertain', 'settled'), ('uncertain', 'not_dispatched'),
))
_GENERATOR_SLOT_EDGES = frozenset((
    ('unused', 'claimed'), ('claimed', 'running'), ('claimed', 'failed'),
    ('claimed', 'cancelled'), ('claimed', 'uncertain'), ('running', 'complete'),
    ('running', 'partial'), ('running', 'failed'), ('running', 'cancelled'),
    ('running', 'uncertain'), ('uncertain', 'failed'), ('uncertain', 'cancelled'),
))


def _generator_unchanged_except(before, after, changed_fields):
    return all(before[key] == after[key] for key in before.keys() - set(changed_fields))


def _generator_usage_monotonic(before, after):
    if before.usage is None:
        return True
    if after.usage is None:
        return False
    # Once an invalid/overflow fact is observed it cannot be replaced by a
    # plausible numeric usage or silently downgraded while settling uncertainty.
    if before.usage_issue is not None:
        return (before.usage_issue == after.usage_issue and before.usage == after.usage
                and before.usage_status == after.usage_status)
    return all(old is None or old == new for old, new in (
        (before.usage.prompt_tokens, after.usage.prompt_tokens),
        (before.usage.completion_tokens, after.usage.completion_tokens),
    ))


def transition_generator_request(old: object | None, new: object) -> GeneratorRequestReceipt:
    """Pure claimed-state transition; an evidence ID never proves no dispatch."""
    error = 'invalid_generator_request_transition'
    try:
        before = None if old is None else parse_generator_request_receipt(old)
        after = parse_generator_request_receipt(new)
        if before is None:
            if after.phase != 'reserved':
                raise ValueError(error)
            return after
        previous, proposed = before.model_dump(mode='json'), after.model_dump(mode='json')
        if previous == proposed:
            return after
        if (before.phase, after.phase) not in _GENERATOR_RECEIPT_EDGES:
            raise ValueError(error)
        if any(previous[key] != proposed[key] for key in _GENERATOR_RECEIPT_IDENTITIES):
            raise ValueError(error)
        for key in ('dispatch_marker_id', 'client_dispatch_id', 'backend_request_id'):
            if previous[key] is not None and proposed[key] != previous[key]:
                raise ValueError(error)
        if (before.drained and not after.drained) or not _generator_usage_monotonic(before, after):
            raise ValueError(error)
        if after.phase == 'not_dispatched':
            # Only syntax is checked. An authenticated journal owner must prove
            # never-entered + drain + durable checkpoint before exposing credit.
            allowed = {'phase', 'outcome', 'stop_code', 'drained', 'never_dispatched_evidence_id'}
            if not _generator_unchanged_except(previous, proposed, allowed):
                raise ValueError(error)
        elif before.phase == 'reserved':
            if after.phase == 'dispatched':
                allowed = {'phase', 'dispatch_marker_id'}
            else:
                allowed = {'phase', 'outcome', 'stop_code'}
                if after.stop_code not in ('generation_journal_unavailable', 'generation_dispatch_uncertain'):
                    raise ValueError(error)
            if not _generator_unchanged_except(previous, proposed, allowed):
                raise ValueError(error)
        elif before.phase == after.phase == 'dispatched':
            if not _generator_unchanged_except(previous, proposed, {'client_dispatch_id', 'backend_request_id'}):
                raise ValueError(error)
        elif before.phase == after.phase == 'uncertain':
            allowed = {'dispatch_marker_id', 'client_dispatch_id', 'backend_request_id',
                       'usage_status', 'usage', 'usage_issue', 'drained'}
            if not _generator_unchanged_except(previous, proposed, allowed):
                raise ValueError(error)
        elif before.phase == 'dispatched' and after.phase == 'uncertain' and after.drained:
            raise ValueError(error)
        return after
    except (TypeError, ValueError, RecursionError):
        raise ValueError(error) from None


def transition_b_root_budget(old: object, new: object) -> BRootBudget:
    """One pure monotonic operation, not a CAS, journal write or replay grant."""
    error = 'invalid_b_root_budget_transition'
    try:
        before, after = parse_b_root_budget(old), parse_b_root_budget(new)
        previous, proposed = before.model_dump(mode='json'), after.model_dump(mode='json')
        if previous == proposed:
            return after
        identity = ('version', 'policy', 'root_id', 'input_sha256', 'proof_sha256',
                    'grant', 'grant_sha256', 'main_limit', 'root_limit')
        if any(previous[key] != proposed[key] for key in identity):
            raise ValueError(error)
        if before.slot_state != after.slot_state:
            if (before.slot_state, after.slot_state) not in _GENERATOR_SLOT_EDGES:
                raise ValueError(error)
            claiming = before.slot_state == 'unused' and after.slot_state == 'claimed'
            allowed = {'slot_state', 'logical_slot_id'} if claiming else {'slot_state'}
            if not _generator_unchanged_except(previous, proposed, allowed):
                raise ValueError(error)
            return after
        if before.logical_slot_id != after.logical_slot_id:
            raise ValueError(error)
        main_delta = (after.intent_requests - before.intent_requests,
                      after.decision_requests - before.decision_requests)
        if main_delta != (0, 0):
            if main_delta not in ((1, 0), (0, 1)):
                raise ValueError(error)
            allowed = {'intent_requests', 'decision_requests', 'main_requests', 'root_request_debits'}
            if not _generator_unchanged_except(previous, proposed, allowed):
                raise ValueError(error)
            return after
        # Aggregates are already revalidated; only one receipt may change and
        # only a not_dispatched transition can reduce its reservation/debit.
        allowed = {'generator_requests', 'generator_request_debits', 'root_request_debits', 'reserved_tokens'}
        if not _generator_unchanged_except(previous, proposed, allowed):
            raise ValueError(error)
        old_rows, new_rows = before.generator_requests, after.generator_requests
        if len(new_rows) == len(old_rows) + 1:
            if (before.slot_state != 'running' or new_rows[:-1] != old_rows
                    or any(row.phase != 'settled' or row.outcome not in ('success', 'backend_failed')
                           or row.stop_code is not None for row in old_rows)):
                raise ValueError(error)
            transition_generator_request(None, new_rows[-1])
        elif len(new_rows) == len(old_rows):
            changed = [(a, b) for a, b in zip(old_rows, new_rows) if a != b]
            if len(changed) != 1:
                raise ValueError(error)
            first, second = changed[0]
            if before.slot_state != 'running' and first.phase == 'reserved' and second.phase == 'dispatched':
                raise ValueError(error)
            transition_generator_request(first, second)
        else:
            raise ValueError(error)
        return after
    except (TypeError, ValueError, RecursionError):
        raise ValueError(error) from None
