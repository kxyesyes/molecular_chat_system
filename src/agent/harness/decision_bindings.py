"""B1 obligation preparation and server-owned binding resolution.

The caller must supply the ORIGINAL admitted context, including on continuation.
Clarification/resolution must not replace this snapshot or weaken its obligations.
No model/scientific execution or worker lifecycle management. Resolver methods
which check sources/references must run via settle_owned_call (or Session's
owned action); the caller checks its root deadline on both sides.
"""
from types import SimpleNamespace

from src.agent.contracts.binding_requirements import (
    parse_binding_requirements, required_binding_tools,
)
from src.agent.contracts.decision_bindings import B1_TOOLS


_ERROR = 'invalid_binding_requirements'


def _tool_names(values):
    if type(values) not in (tuple, list, set, frozenset) or len(values) > 32:
        raise ValueError(_ERROR)
    if any(type(name) is not str or not name or len(name) > 64 for name in values):
        raise ValueError(_ERROR)
    return frozenset(values)


def _canonical_subjects(values, parser):
    from src.agent.tools.molecular_input import parse_molecular_smiles
    from .decision_requirements import _canonical

    subjects = []
    for value in values:
        # A requirement denotes exactly this complete structure, not a prompt.
        if (type(value) is not str or not value.isascii()
                or parse_molecular_smiles('SMILES: ' + value, parser) != [value]):
            raise ValueError(_ERROR)
        canonical = _canonical(value)
        if canonical is None or canonical in subjects:
            raise ValueError(_ERROR)
        subjects.append(canonical)
    return frozenset(subjects)


def prepare_binding_requirements(value, *, context, request_kind, allowed_tools, required_tools):
    """Validate original obligations and effective permissions before any actions.

    Missing molecules may be clarified later. Supplied invalid/duplicate/conflicting
    molecules cannot be replaced by fragments. The returned value retains original
    spelling, order, counts and targets; preparation never rewrites user intent.
    """
    try:
        requirements = parse_binding_requirements(value)
        from src.agent.contracts import AgentContext
        from .decision_bounds import validate_json
        if (type(context) is not AgentContext or type(context.query) is not str
                or type(context.metadata) is not dict):
            raise ValueError(_ERROR)
        # Bound only preparation's native input view. Lifecycle admission owns
        # the rest of AgentContext. Its legacy 16KiB JSON-string limit is not
        # the v2 retrieval query's 16KiB UTF-8 CONTENT limit (quotes/escapes differ).
        validate_json({'query': context.query, 'metadata': context.metadata},
                      max_bytes=65536, reason=_ERROR)
        if type(request_kind) is not str or request_kind not in ('chat', 'scientific'):
            raise ValueError(_ERROR)
        allowed = _tool_names(allowed_tools) & B1_TOOLS
        required = required_binding_tools(requirements, _tool_names(required_tools))
        capabilities = context.metadata.get('capabilities', {})
        if type(capabilities) is not dict:
            raise ValueError(_ERROR)
        scientific = capabilities.get('scientific_tools', True)
        rag = capabilities.get('rag', True)
        if type(scientific) is not bool or type(rag) is not bool:
            raise ValueError(_ERROR)
        effective = allowed - set(requirements.forbidden_tools)
        if not scientific:
            effective &= {'rag_search'}
        if not rag:
            effective -= {'rag_search'}
        if request_kind == 'chat':
            effective = frozenset()
        if not required <= effective:
            raise ValueError(_ERROR)

        retrieval = requirements.retrieval_result
        if retrieval is not None and retrieval.query != context.query:
            raise ValueError(_ERROR)
        target = requirements.target_result
        if target is not None and target.input == 'user' and target.query != context.query:
            from src.agent.contracts.target_request import analyze_target_request
            original_target = analyze_target_request(context.query)
            if original_target.needs_clarification or target.query not in original_target.targets:
                raise ValueError(_ERROR)

        groups = (*requirements.molecular_results, *requirements.analysis_results)
        molecular_tools = B1_TOOLS - {'rag_search', 'target_database_search'}
        if not required & molecular_tools:
            return requirements

        # Existing parsers/RDKit are loaded only when molecular obligations need
        # checking. They perform no scientific prediction or asset discovery.
        from src.agent.tools.base_tool import BaseMolecularTool
        from src.agent.tools.molecular_input import parse_molecular_smiles
        from .decision_inputs import has_explicit_molecule, effective_molecule, activity_input_target
        parser = BaseMolecularTool('binding_requirements', '')

        if 'activity_predictor' in required:
            from src.target_identifiers import canonical_target_identifier
            original_target = activity_input_target(SimpleNamespace(
                context=context, input_queries=[context.query]))
            for group in requirements.analysis_results:
                if group.tool_name == 'activity_predictor' and group.target is not None:
                    if original_target is None:
                        raise ValueError(_ERROR)
                    # The family resolver accepts surrounding text. Obligations
                    # require a whole supported identifier, not an embedded alias.
                    target_id = canonical_target_identifier(group.target)
                    if target_id is None or target_id != canonical_target_identifier(original_target):
                        raise ValueError(_ERROR)

        counts, subjects = set(), []
        for group in groups:
            if group.exact_molecule_count is not None:
                counts.add(group.exact_molecule_count)
            if group.expected_smiles:
                subjects.append(_canonical_subjects(group.expected_smiles, parser))
        if requirements.reverse_result is not None:
            subjects.append(_canonical_subjects((requirements.reverse_result.expected_smiles,), parser))
        if 'reverse_target_predictor' in required:
            counts.add(1)
        # Only explicit original input (or the server-selected whole structure)
        # can constrain these obligations. Missing input leaves them unchanged.
        selected = effective_molecule(context)
        if selected is not None:
            from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
            if type(selected) is not ResolvedScientificMolecule:
                raise ValueError(_ERROR)
            subjects.append(_canonical_subjects((selected.canonical_smiles,), parser))
        elif has_explicit_molecule(context.query):
            if 'activity_predictor' in required:
                from src.agent.tools.activity_input import parse_activity_input
                values = parse_activity_input(context.query, parser)[1]
            else:
                values = parse_molecular_smiles(context.query, parser)
            subjects.append(_canonical_subjects(values, parser))
        counts.update(len(subject) for subject in subjects)
        if len(counts) > 1 or subjects and any(subject != subjects[0] for subject in subjects):
            raise ValueError(_ERROR)
        return requirements
    except (TypeError, ValueError, RecursionError, ImportError):
        raise ValueError(_ERROR) from None


# These APIs are deliberately unconnected to ModelDecisionLoop. A caller owns
# admission, deadlines, workers, Session creation and explicit restore authority.
import json
from dataclasses import dataclass, replace

from src.agent.contracts.decision import ToolDecision
from src.agent.contracts.decision_bindings import (
    B1_PROFILE_REVISION, parse_binding_arguments, parse_binding_proof,
)
from src.agent.evidence import EvidenceLedger
from .decision_bounds import context_value, observation_value, validate_json
from .decision_inputs import effective_molecule, activity_input_target, verify_observation_integrity
from .decision_policy import DecisionBoundaryError, usable


_MOLECULAR = B1_TOOLS - {'rag_search', 'target_database_search', 'reverse_target_predictor'}
_TARGET_FIELDS = ('gene_symbol', 'target_gene', 'uniprot_id', 'target_name', 'protein_name')
_BINDING_ERROR = 'invalid_dynamic_binding'
_RECORD_BYTES = 512 * 1024


def _native(value, ceiling=65536):
    validate_json(value, max_bytes=ceiling, reason=_BINDING_ERROR)
    return value


def _wire(value, ceiling=65536):
    return json.dumps(_native(value, ceiling), sort_keys=True, ensure_ascii=False, allow_nan=False)


def _digest(value):
    return EvidenceLedger.output_digest(_native(value))


def _record_digest(value):
    # Original action/continuation records have a separate bounded envelope;
    # scientific inputs, observations and their hashes retain the 64KiB cap.
    return EvidenceLedger.output_digest(_native(value, _RECORD_BYTES))


def _initial_operation_key(record):
    return _digest(dict(kind='b1-preparation-input', record_sha256=_record_digest(record)))


def _context_snapshot(context):
    # Keep the full context contract, but apply B's content-byte query limit.
    if type(context.query) is not str or len(context.query.encode('utf-8')) > 16384:
        raise DecisionBoundaryError(_BINDING_ERROR)
    value = context_value(replace(context, query=''))
    value['query'] = context.query
    # Memory/model history is never authority, nor necessary for input replay.
    return json.loads(_wire({key: value[key] for key in (
        'query', 'metadata', 'resolved_molecule', 'trace_id', 'session_id', 'user_id')}))


def _snapshot_context(value):
    from src.agent.contracts import AgentContext
    from src.agent.contracts.resolved_molecule import ResolvedScientificMolecule
    _native(value)
    selected = value['resolved_molecule']
    return AgentContext(**{**value, 'resolved_molecule': (
        ResolvedScientificMolecule(**selected) if selected is not None else None)})


@dataclass(frozen=True)
class ResolvedBindingAction:
    """Detached native projections backed by an immutable server-issued record.

    Do not construct from model output. register_action checks issuance; Task5
    may restore only explicit caller-owned records, never conversation history.
    """
    record_json: str

    @property
    def input_data(self):
        return json.loads(self.record_json)['input_data']

    @property
    def action_sha256(self):
        return json.loads(self.record_json)['action_sha256']


class B1BindingResolver:
    """One run's inputs, action records, proof preparation and current closure.

    resolve(ToolDecision) -> ResolvedBindingAction; register_action(step_id,
    action) -> detached initial metadata. Use action.input_data unchanged as the
    WorkflowStep input, identity transform, step_id as output_key. Install
    prepare_observation as Session observation_prepare and the existing
    seal_observation as observation_capture. export_records() returns bounded
    native original inputs for Task5's explicitly authorized snapshot boundary.
    """
    def __init__(self, *, session, requirements, original_context, adapters):
        from src.agent.runtime.run_session import WorkflowRunSession
        from src.agent.tooling.adapters import ToolAdapter
        if type(session) is not WorkflowRunSession or not session.dynamic:
            raise DecisionBoundaryError(_BINDING_ERROR)
        self.session = session
        self._original = _wire(_context_snapshot(original_context))
        self._owner = tuple(json.loads(self._original)[key] for key in ('trace_id', 'session_id', 'user_id'))
        self.adapters = dict(adapters)
        if any(name not in B1_TOOLS or not isinstance(adapter, ToolAdapter)
               or adapter.spec.name != name for name, adapter in self.adapters.items()):
            raise DecisionBoundaryError(_BINDING_ERROR)
        self.requirements = prepare_binding_requirements(requirements,
            context=original_context, request_kind='scientific',
            allowed_tools=set(adapters), required_tools=set())
        self.requirements_sha256 = _digest(self.requirements.model_dump(mode='json'))
        self._records = {}
        self._issued = set()
        self._check_owner()

    def _check_owner(self):
        if tuple(getattr(self.session.context, key) for key in (
                'trace_id', 'session_id', 'user_id')) != self._owner:
            raise DecisionBoundaryError(_BINDING_ERROR)
        requirements = parse_binding_requirements(self.requirements)
        if _digest(requirements.model_dump(mode='json')) != self.requirements_sha256:
            raise DecisionBoundaryError(_BINDING_ERROR)

    def _adapter(self, name):
        from src.agent.tooling.factory import TOOL_AGENT_OWNERS
        adapter = self.adapters.get(name)
        if (adapter is None or name in self.requirements.forbidden_tools
                or not adapter.spec.idempotent or adapter.spec.side_effects != 'none'
                or TOOL_AGENT_OWNERS[name] not in adapter.spec.owner_agents):
            raise DecisionBoundaryError(_BINDING_ERROR)
        capabilities = json.loads(self._original)['metadata'].get('capabilities', {})
        if not capabilities.get('rag' if name == 'rag_search' else 'scientific_tools', True):
            raise DecisionBoundaryError(_BINDING_ERROR)
        _native([adapter.spec.version, adapter.adapter_version])
        return adapter

    def _molecules(self, values):
        from src.agent.tools.base_tool import BaseMolecularTool
        from .decision_requirements import _canonical
        if type(values) not in (list, tuple) or not 1 <= len(values) <= 100:
            raise DecisionBoundaryError(_BINDING_ERROR)
        parser = BaseMolecularTool('binding', '')
        _canonical_subjects(values, parser)  # complete valid, canonical unique
        canonical = [_canonical(value) for value in values]
        req = self.requirements
        for group in (*req.molecular_results, *req.analysis_results):
            if group.exact_molecule_count is not None and len(values) != group.exact_molecule_count:
                raise DecisionBoundaryError(_BINDING_ERROR)
            if group.expected_smiles and set(canonical) != _canonical_subjects(group.expected_smiles, parser):
                raise DecisionBoundaryError(_BINDING_ERROR)
        if req.reverse_result and canonical != [_canonical(req.reverse_result.expected_smiles)]:
            raise DecisionBoundaryError(_BINDING_ERROR)
        return list(values)

    def _molecular_context(self, snapshot, admitted=None):
        from .decision_inputs import has_explicit_molecule
        context = _snapshot_context(snapshot)
        # A target-only reply is not a new molecular subject. Retain the whole
        # ORIGINAL admitted input, not fragments or model conversation history.
        # Explicit malformed structures still take precedence and fail parsing.
        if effective_molecule(context) is None and not has_explicit_molecule(context.query):
            original = _snapshot_context(json.loads(self._original))
            if (effective_molecule(original) is not None or has_explicit_molecule(original.query)
                    or admitted is None):
                return original
            return _snapshot_context(admitted)
        return context

    def _user_molecules(self, snapshot, admitted=None):
        from src.agent.tools.base_tool import BaseMolecularTool
        from src.agent.tools.activity_input import parse_activity_input
        from src.agent.tools.molecular_input import parse_molecular_smiles
        context = self._molecular_context(snapshot, admitted)
        selected = effective_molecule(context)
        if selected is not None:
            if not selected.revalidate(self.session.orchestrator.state_store, context.session_id):
                raise DecisionBoundaryError('scientific_reference_unavailable')
            values = [selected.canonical_smiles]
        else:
            parser = BaseMolecularTool('binding', '')
            try:
                # Whole molecular fields do not require an activity-supported
                # target (e.g. EGFR property requests). Activity authorization
                # remains a separate, strict check in _molecular_payload.
                values = parse_molecular_smiles(context.query, parser)
            except ValueError:
                # Preserve the existing supported activity prose grammar; its
                # whole-field validator cannot truncate or repair a structure.
                values = parse_activity_input(context.query, parser)[1]
        return self._molecules(values)

    def _subject_origin(self, values, snapshot, admitted=None):
        from .decision_inputs import has_explicit_molecule
        from .decision_requirements import _canonical
        # Original explicit subjects constrain every later clarification/action.
        for source in (json.loads(self._original), snapshot, admitted):
            if source is None:
                continue
            context = _snapshot_context(source)
            if effective_molecule(context) is not None or has_explicit_molecule(context.query):
                expected = self._user_molecules(source)
                if {_canonical(v) for v in values} != {_canonical(v) for v in expected}:
                    raise DecisionBoundaryError(_BINDING_ERROR)

    def _target(self, snapshot, admitted_queries=()):
        original = _snapshot_context(json.loads(self._original))
        current = _snapshot_context(snapshot)
        target = activity_input_target(SimpleNamespace(context=original,
            input_queries=[original.query, *admitted_queries, current.query]))
        for group in self.requirements.analysis_results:
            if group.tool_name == 'activity_predictor' and group.target is not None:
                return group.target  # preparation validated against original parser
        return target

    def _observations(self):
        self._check_owner()
        if len(self.session.results) > 128:
            raise DecisionBoundaryError(_BINDING_ERROR)
        found = {}
        for result in self.session.results:
            observation_value(result)
            identity = result.quality.get('evidence_id')
            if type(identity) is not str or identity in found:
                raise DecisionBoundaryError(_BINDING_ERROR)
            found[identity] = result
        return found

    def _raw_descriptors(self, source):
        """Selection digest payload is exactly {row_index: int, row: raw_row}.

        Handle payload is {evidence_id, row_index, row_sha256}; both use the
        ledger codec, never the producer receipt's separate compact codec.
        """
        entries = [entry for entry in source.evidence
                   if type(entry) is dict and 'prediction_receipt' in entry]
        if len(entries) != 1 or type(entries[0].get('records')) is not list:
            raise DecisionBoundaryError(_BINDING_ERROR)
        descriptors = []
        for index, row in enumerate(entries[0]['records']):
            _native(row)
            descriptors.append(dict(record_ref='record-' + _digest(dict(
                evidence_id=source.quality['evidence_id'], row_index=index, row_sha256=_digest(row))),
                row_index=index, selection_sha256=_digest(dict(row_index=index, row=row))))
        return entries[0]['records'], descriptors

    def _target_row(self, row):
        # Same precedence as TargetDatabaseTool. Reject conflicting aliases;
        # identifiers in other namespaces never become an earlier-priority gene.
        from src.target_identifiers import canonical_target_identifier

        def identifier(value):
            return canonical_target_identifier(value) or value.strip().casefold()

        projected = {}
        for key in _TARGET_FIELDS:
            value = row.get(key)
            if value is None:
                continue
            if type(value) is not str or not value.strip() or len(value) > 8192:
                raise DecisionBoundaryError(_BINDING_ERROR)
            projected[key] = value
        if not projected:
            raise DecisionBoundaryError(_BINDING_ERROR)
        for left, right in (('gene_symbol', 'target_gene'), ('target_name', 'protein_name')):
            if left in projected and right in projected and identifier(projected[left]) != identifier(projected[right]):
                raise DecisionBoundaryError(_BINDING_ERROR)
        recognized = {canonical_target_identifier(value) for value in projected.values()}
        recognized.discard(None)
        if len(recognized) > 1:
            raise DecisionBoundaryError(_BINDING_ERROR)
        for key in ('gene_symbol', 'target_gene', 'uniprot_id'):
            value = projected.get(key, '')
            if value and (value != value.strip() or any(c.isspace() for c in value)
                          or value.upper().startswith('CHEMBL') or value.startswith('name-sha256:')):
                raise DecisionBoundaryError(_BINDING_ERROR)
        return projected

    def _resolve(self, name, arguments, snapshot, prior, admitted=None, admitted_queries=()):
        if tuple(snapshot[key] for key in ('trace_id', 'session_id', 'user_id')) != self._owner:
            raise DecisionBoundaryError(_BINDING_ERROR)
        adapter = self._adapter(name)
        args = parse_binding_arguments(name, arguments, profile_revision=B1_PROFILE_REVISION)
        target = self._target(snapshot, admitted_queries) if name == 'activity_predictor' else None
        roles, selection = [], None
        if args.input_ref == 'user':
            if name == 'rag_search':
                query = json.loads(self._original)['query']
                required = self.requirements.retrieval_result
                if required and required.query != query:
                    raise DecisionBoundaryError(_BINDING_ERROR)
                payload = {'query': query}
            elif name == 'target_database_search':
                required = self.requirements.target_result
                if required and required.input != 'user':
                    raise DecisionBoundaryError(_BINDING_ERROR)
                payload = {'query': required.query if required else json.loads(self._original)['query']}
            else:
                values = self._user_molecules(snapshot, admitted)
                self._subject_origin(values, snapshot, admitted)
                payload = self._molecular_payload(name, values, target)
        else:
            source = prior.get(args.input_ref)
            if source is None or not usable(source):
                raise DecisionBoundaryError(_BINDING_ERROR)
            role = 'reverse_record' if name == 'target_database_search' else 'molecules'
            if role == 'reverse_record':
                required = self.requirements.target_result
                if source.tool_name != 'reverse_target_predictor' or required and required.input != 'reverse':
                    raise DecisionBoundaryError(_BINDING_ERROR)
                rows, descriptors = self._raw_descriptors(source)
                match = [desc for desc in descriptors if desc['record_ref'] == args.record_ref]
                if len(match) != 1:
                    raise DecisionBoundaryError(_BINDING_ERROR)
                selection = match[0]['selection_sha256']
                payload = {'query': self._target_row(rows[match[0]['row_index']])}
            else:
                if name not in _MOLECULAR | {'reverse_target_predictor'} or source.tool_name not in _MOLECULAR:
                    raise DecisionBoundaryError(_BINDING_ERROR)
                if type(source.data) is not list or any(type(row) is not dict for row in source.data):
                    raise DecisionBoundaryError(_BINDING_ERROR)
                values = self._molecules([row.get('smiles') for row in source.data])
                self._subject_origin(values, snapshot, admitted)
                payload = self._molecular_payload(name, values, target)
            roles = [dict(role=role, evidence_id=args.input_ref, output_sha256=_digest(source.data))]
        original = json.loads(self._original)
        authority = original
        if name not in ('rag_search', 'target_database_search'):
            context = self._molecular_context(snapshot, admitted)
            # Identity follows what this tool actually consumes. Unrelated
            # clarification text cannot invalidate independent properties;
            # explicit subjects and selected-reference identity remain bound.
            authority = dict(molecules=self._user_molecules(snapshot, admitted),
                selected=(_context_snapshot(context)['resolved_molecule']
                          if effective_molecule(context) is not None else None))
            if name == 'activity_predictor':
                authority['target'] = target
        # Independently bound/hash both inputs before composing; duplicating
        # an escaped 16KiB query must not widen the scientific hash boundary.
        request = _digest(dict(original_sha256=_digest(original), authority_sha256=_digest(authority)))
        identity = dict(profile=B1_PROFILE_REVISION, policy='b1-v1', tool_name=name,
            tool_version=adapter.spec.version, adapter_version=adapter.adapter_version,
            resolved_input=payload, roles=roles, requirements_sha256=self.requirements_sha256,
            selection_sha256=selection, request_input_digest=request)
        return dict(tool_name=name, arguments=arguments, context=snapshot, input_data=payload,
            action_sha256=_digest(identity), request_input_digest=request,
            proof=dict(version='1', profile=B1_PROFILE_REVISION,
                requirements_sha256=self.requirements_sha256, action_sha256=_digest(identity),
                input_sha256=_digest(payload), roles=roles, own_source=None,
                selection_sha256=selection, policy='b1-v1'))

    def _molecular_payload(self, name, values, target):
        from src.agent.planning.bindings import BindingResolver
        if name == 'reverse_target_predictor' and len(values) != 1:
            raise DecisionBoundaryError(_BINDING_ERROR)
        text = BindingResolver().resolve('$.outputs.source', 'smiles_text', {},
            {'source': [{'smiles': value} for value in values]})
        if name == 'activity_predictor':
            if target is None:
                raise DecisionBoundaryError('activity_target_conflict_or_unknown')
            return {'query': {'query': json.loads(self._original)['query'], 'smiles': values, 'target': target}}
        return {'query': text}

    def resolve(self, decision):
        self._check_owner()
        if type(decision) is not ToolDecision:
            raise DecisionBoundaryError(_BINDING_ERROR)
        args = parse_binding_arguments(decision.tool_name, decision.arguments,
                                       profile_revision=B1_PROFILE_REVISION)
        prior = self._observations()
        if args.input_ref != 'user':
            self.verify_binding_closure([args.input_ref])
        admitted = self._prior_subject(prior) if decision.tool_name in _MOLECULAR | {'reverse_target_predictor'} else None
        queries = self._verified_target_queries(prior) if decision.tool_name == 'activity_predictor' else ()
        record = self._resolve(decision.tool_name, dict(decision.arguments),
                              _context_snapshot(self.session.context), prior, admitted, queries)
        action = ResolvedBindingAction(_wire(record, _RECORD_BYTES))
        self._issued.add(action.record_json)
        return action

    def register_action(self, step_id, action):
        self._check_owner()
        if (type(action) is not ResolvedBindingAction or action.record_json not in self._issued
                or type(step_id) is not str or not 1 <= len(step_id) <= 128 or step_id in self._records):
            raise DecisionBoundaryError(_BINDING_ERROR)
        candidate = {**self._records, step_id: action.record_json}
        _native({key: json.loads(value) for key, value in candidate.items()}, 512 * 1024)
        self._records = candidate
        record = json.loads(action.record_json)
        return dict(request_input_digest=record['request_input_digest'],
            input_evidence_ids=[r['evidence_id'] for r in record['proof']['roles']],
            operation_key=_initial_operation_key(record))

    def export_records(self):
        return json.loads(_wire({key: json.loads(value) for key, value in self._records.items()}, 512 * 1024))

    def restore_records(self, records):
        """Explicit caller-owned inputs only, after Session proof/seal restoration.

        Proposed history alone is insufficient: reconstruct every record and
        match its action/input/roles against the actual sealed ledger. Install
        atomically; no tool/source execution or receipt repair occurs here.
        """
        self._check_owner()
        _native(records, 512 * 1024)
        if type(records) is not dict or len(records) > 128 or self._records:
            raise DecisionBoundaryError(_BINDING_ERROR)
        try:
            observations = self._observations()
            if set(records) != {r.quality['step_id'] for r in observations.values()}:
                raise ValueError(_BINDING_ERROR)
            trial = B1BindingResolver(session=self.session, requirements=self.requirements,
                original_context=_snapshot_context(json.loads(self._original)), adapters=self.adapters)
            trial._records = {key: _wire(value, _RECORD_BYTES) for key, value in records.items()}
            for record in records.values():
                if _context_snapshot(_snapshot_context(record['context'])) != record['context']:
                    raise ValueError(_BINDING_ERROR)
            trial.verify_binding_closure()
        except (KeyError, TypeError, ValueError, AttributeError):
            raise DecisionBoundaryError(_BINDING_ERROR) from None
        self._records = trial._records

    def _source(self, result, record, expected=None):
        name = result.tool_name
        if name not in ('rag_search', 'reverse_target_predictor') or not usable(result):
            return None
        tool = self._adapter(name).tool
        hook = getattr(tool, 'validate_current_observation', None)
        if not callable(hook):
            raise DecisionBoundaryError('current_source_unavailable')
        return hook(result.data, result.evidence, input_data=record['input_data']['query'],
                    expected_source=expected)

    def _sanitized_preparation_failure(self, source):
        """Exact Session rollback envelope, never a message-based exception.

        Call only after seal/ledger integrity verification. The initial key
        separately authenticates the original issued input, not scientific proof.
        """
        from src.agent.contracts import AgentErrorCode, ObservationStatus
        message = 'Observation preparation failed'
        quality = source.quality
        return (source.success is False and source.status is ObservationStatus.FAILED
            and source.message == message and source.error is not None
            and source.error.code is AgentErrorCode.INVALID_OUTPUT
            # AgentExecutionError.to_dict serializes None details as {}.
            and source.error.message == message and source.error.details in (None, {})
            and source.data is None and source.formatted == '' and source.elapsed_ms is None
            and source.evidence == [] and source.artifacts == []
            and set(quality) == {'step_id', 'output_key', 'tool_version', 'evidence_id',
                                'input_evidence_ids', 'operation_key', 'request_input_digest'}
            and source.warnings in ([], [f"Optional step {quality['step_id']} ({source.tool_name}) failed: {message}"])
            and source.provenance.model_name is None and source.provenance.model_version is None
            and source.provenance.demo_mode is False and source.provenance.fallback_used is False)

    def _authenticated_record(self, source):
        """Authenticate before indexing, filtering or restoring, without source I/O."""
        verify_observation_integrity(source, self.session)
        raw = self._records.get(source.quality.get('step_id'))
        if type(raw) is not str or len(raw) > _RECORD_BYTES:
            raise DecisionBoundaryError(_BINDING_ERROR)
        try:
            if len(raw.encode('utf-8')) > _RECORD_BYTES:
                raise ValueError(_BINDING_ERROR)
            record = json.loads(raw)
            if type(record) is not dict:
                raise ValueError(_BINDING_ERROR)
            digest = _record_digest(record)
            diagnostic = 'binding_record_sha256' not in source.quality
            if diagnostic:
                if (not self._sanitized_preparation_failure(source)
                        or source.quality['operation_key'] != _initial_operation_key(record)):
                    raise ValueError(_BINDING_ERROR)
            elif source.quality['binding_record_sha256'] != digest:
                raise ValueError(_BINDING_ERROR)
            # The sealed initial commitment authenticates an issued historical
            # record even if preparation could not certify a current source.
            # Validate its input/owner/obligation binding without re-running that
            # failed hook or conferring any subject/target/reference authority.
            proof = parse_binding_proof(record['proof'])
            parse_binding_arguments(record['tool_name'], record['arguments'],
                                    profile_revision=B1_PROFILE_REVISION)
            context = record['context']
            if (_context_snapshot(_snapshot_context(context)) != context
                    or tuple(context[k] for k in ('trace_id', 'session_id', 'user_id')) != self._owner
                    or record['tool_name'] != source.tool_name
                    or proof.requirements_sha256 != self.requirements_sha256
                    or proof.action_sha256 != record['action_sha256']
                    or proof.input_sha256 != _digest(record['input_data']) or proof.own_source is not None
                    or source.quality['request_input_digest'] != record['request_input_digest']
                    or source.quality['input_evidence_ids'] != [r.evidence_id for r in proof.roles]
                    or source.quality['output_key'] != source.quality['step_id']
                    or source.provenance.tool_name != source.tool_name
                    or source.provenance.input_digest != self.session.orchestrator._input_hash(record['input_data'])
                    or source.provenance.tool_version != self._adapter(source.tool_name).spec.version
                    or source.quality.get('tool_version') != source.provenance.tool_version):
                raise ValueError(_BINDING_ERROR)
        except (KeyError, TypeError, ValueError, AttributeError, RecursionError):
            raise DecisionBoundaryError(_BINDING_ERROR) from None
        return record, diagnostic

    def _admitted_subject(self, verified):
        """Only call with observations whose records/seals/proofs were rebuilt.

        This is user-input authority, not a new scientific producer/role. It is
        reconstructed in server observation order, never from chat/input_queries.
        Failed diagnostics cannot introduce a subject for a later action.
        """
        admitted = None
        for source in verified.values():
            if source.tool_name in _MOLECULAR | {'reverse_target_predictor'} and usable(source):
                record = json.loads(self._records[source.quality['step_id']])
                admitted = _context_snapshot(self._molecular_context(record['context'], admitted))
        return admitted

    def _prior_subject(self, observations):
        from .decision_inputs import has_explicit_molecule
        original = _snapshot_context(json.loads(self._original))
        if effective_molecule(original) is not None or has_explicit_molecule(original.query):
            return None
        prefix = {}
        for identity, source in observations.items():
            # Participation is itself an authority decision: a mutated demo/
            # fallback flag must not hide an already admitted subject.
            self._authenticated_record(source)
            prefix[identity] = source
            if source.tool_name in _MOLECULAR | {'reverse_target_predictor'} and usable(source):
                self.verify_binding_closure([identity])
                return self._admitted_subject(prefix)
        return None

    def _target_queries(self, verified):
        return [json.loads(self._records[source.quality['step_id']])['context']['query']
                for source in verified.values() if usable(source)]

    def _verified_target_queries(self, observations):
        # Validate even excluded diagnostics before consulting usability. This
        # checks their original seals, not a success proof/receipt requirement.
        for source in observations.values():
            self._authenticated_record(source)
        self.verify_binding_closure([identity for identity, source in observations.items() if usable(source)])
        return self._target_queries(observations)

    def prepare_observation(self, result, step):
        self._check_owner()
        record = json.loads(self._records[step.name])
        _native(step.input_data)
        if (step.tool_name != record['tool_name'] or step.input_data != record['input_data']
                or step.input_from is not None or step.input_binding is not None
                or step.input_template is not None or step.input_transform != 'identity'
                or step.output_key != step.name):
            raise DecisionBoundaryError(_BINDING_ERROR)
        parents = [role['evidence_id'] for role in record['proof']['roles']]
        if parents:
            self.verify_binding_closure(parents)
        prior = self._observations()
        admitted = self._prior_subject(prior) if record['tool_name'] in _MOLECULAR | {'reverse_target_predictor'} else None
        queries = self._verified_target_queries(prior) if record['tool_name'] == 'activity_predictor' else ()
        reconstructed = self._resolve(record['tool_name'], record['arguments'],
                                      record['context'], prior, admitted, queries)
        if _wire(reconstructed, _RECORD_BYTES) != _wire(record, _RECORD_BYTES):
            raise DecisionBoundaryError(_BINDING_ERROR)
        observation_value(result)
        record_sha256 = _record_digest(record)
        proof = record['proof']
        proof['own_source'] = self._source(result, record)
        proof = parse_binding_proof(proof).model_dump(mode='json')
        final = _digest(dict(action_sha256=record['action_sha256'], own_source=proof['own_source']))
        metadata = dict(request_input_digest=record['request_input_digest'],
            input_evidence_ids=parents, operation_key=final, binding_proof=proof,
            binding_record_sha256=record_sha256)
        for key in ('binding_proof', 'operation_key', 'request_input_digest', 'input_evidence_ids',
                    'own_source', 'action_sha256', 'selection_sha256', 'binding_record_sha256'):
            result.quality.pop(key, None)
        result.quality.update(json.loads(_wire(metadata)))
        step.metadata.update(json.loads(_wire(metadata)))

    def verify_binding_closure(self, evidence_ids=None):
        """Rebuild reachable ancestors in Session order, never trust model history."""
        observations = self._observations()
        wanted = list(observations) if evidence_ids is None else evidence_ids
        _native(wanted)
        if type(wanted) is not list or len(wanted) != len(set(wanted)) or any(e not in observations for e in wanted):
            raise DecisionBoundaryError(_BINDING_ERROR)
        positions = {key: index for index, key in enumerate(observations)}
        needed, pending = set(), list(wanted)
        while pending:
            identity = pending.pop()
            if identity in needed:
                continue
            source = observations[identity]
            record, diagnostic = self._authenticated_record(source)
            proof = parse_binding_proof(record['proof'] if diagnostic else source.quality.get('binding_proof'))
            for role in proof.roles:
                if role.evidence_id not in positions or positions[role.evidence_id] >= positions[identity]:
                    raise DecisionBoundaryError(_BINDING_ERROR)
                if not diagnostic:
                    pending.append(role.evidence_id)
            needed.add(identity)
        verified = {}
        admitted = None
        last = max((positions[e] for e in needed), default=-1)
        for identity, source in observations.items():
            if positions[identity] > last:
                break
            # Earlier successful inputs may establish an initially missing
            # subject. Reconstruct that prefix, including full sealed proof,
            # before allowing it to supply any later user-input authority.
            record, diagnostic = self._authenticated_record(source)
            if diagnostic:
                # Retained diagnostic only: no current-source check, scientific
                # proof construction, input-authority adoption or retry.
                verified[identity] = source
                continue
            record_json = self._records[source.quality['step_id']]
            queries = self._target_queries(verified)
            rebuilt = self._resolve(record['tool_name'], record['arguments'], record['context'], verified, admitted, queries)
            if _wire(rebuilt, _RECORD_BYTES) != record_json or source.tool_name != record['tool_name']:
                raise DecisionBoundaryError(_BINDING_ERROR)
            # Replaying an old per-action input is necessary, but is not current
            # authority. Equal structures from another selected presentation
            # cannot reactivate old actions; unrelated clarification text can.
            current = self._resolve(record['tool_name'], record['arguments'],
                                    _context_snapshot(self.session.context), verified,
                                    _context_snapshot(self._molecular_context(record['context'], admitted)),
                                    [*queries, record['context']['query']])
            if current['action_sha256'] != rebuilt['action_sha256']:
                raise DecisionBoundaryError(_BINDING_ERROR)
            proof = parse_binding_proof(source.quality['binding_proof']).model_dump(mode='json')
            expected = {**record['proof'], 'own_source': proof['own_source']}
            if proof != expected:
                raise DecisionBoundaryError(_BINDING_ERROR)
            own_source = self._source(source, record, proof['own_source'])
            if own_source != proof['own_source']:
                raise DecisionBoundaryError(_BINDING_ERROR)
            final = _digest(dict(action_sha256=record['action_sha256'], own_source=own_source))
            if (source.quality['operation_key'] != final
                    or source.quality['request_input_digest'] != record['request_input_digest']
                    or source.quality['input_evidence_ids'] != [r['evidence_id'] for r in proof['roles']]
                    or source.provenance.input_digest != self.session.orchestrator._input_hash(record['input_data'])
                    or source.provenance.tool_version != self._adapter(source.tool_name).spec.version):
                raise DecisionBoundaryError(_BINDING_ERROR)
            verified[identity] = source
            admitted = self._admitted_subject(verified)
        return [identity for identity in verified if identity in needed]

    def record_descriptors(self, evidence_id):
        self.verify_binding_closure([evidence_id])
        source = self._observations()[evidence_id]
        if source.tool_name != 'reverse_target_predictor' or not usable(source):
            raise DecisionBoundaryError(_BINDING_ERROR)
        return self._raw_descriptors(source)[1]

    def find_reusable(self, action):
        """None means unobserved, never a stale/tampered/failed cached action.

        Unusable observations remain diagnostics in Session; signal a fixed
        denial instead of authorizing another scientific dispatch by cache miss.
        """
        if type(action) is not ResolvedBindingAction or action.record_json not in self._issued:
            raise DecisionBoundaryError(_BINDING_ERROR)
        for identity, source in self._observations().items():
            record, diagnostic = self._authenticated_record(source)
            if record['action_sha256'] == action.action_sha256:
                if diagnostic:
                    raise DecisionBoundaryError('previous_action_not_usable')
                self.verify_binding_closure([identity])
                if not usable(source):
                    raise DecisionBoundaryError('previous_action_not_usable')
                return source
        return None
