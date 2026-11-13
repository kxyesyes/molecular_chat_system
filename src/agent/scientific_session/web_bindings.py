"""Server-owned single-action bindings for the versioned full Web profile.

No model loop and no workflow executor: compiled actions define authority, Pi
chooses one action, and the existing Session executes one registered adapter.
"""
from copy import deepcopy
from dataclasses import replace
import json
import math
import re
from types import SimpleNamespace

from src.agent.contracts import AgentContext, ToolProvenance
from src.agent.contracts.decision import ToolDecision
from src.agent.contracts.web_requirements import (
    WEB_PROFILE_REVISION, WEB_TOOLS, parse_web_requirements, parse_web_binding_proof,
    WebActionArguments,
)
from src.agent.evidence import EvidenceLedger
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.runtime.run_session import WorkflowRunSession
from src.agent.tooling.adapters import ToolAdapter
from src.agent.validators.partial_observations import has_usable_observation
from .decision_binding_inputs import BindingInputJournal
from .decision_bindings import ResolvedBindingAction, B1BindingResolver
from .decision_bounds import context_value, observation_value, validate_json
from .decision_inputs import verify_observation_integrity, effective_molecule, has_explicit_molecule
from .decision_policy import DecisionBoundaryError, usable
from .web_requirements import prepare_web_requirements, workflow_steps


_ERROR = 'invalid_dynamic_binding'
_RECORD_LIMIT = 512 * 1024


def _wire(value, limit=65536):
    validate_json(value, max_bytes=limit, reason=_ERROR)
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)


def _digest(value, limit=65536):
    _wire(value, limit)
    return EvidenceLedger.output_digest(value)


def _initial_key(record):
    return _digest({'kind': 'web-preparation-input', 'record_sha256': _digest(record, _RECORD_LIMIT)})


def _clarification_molecules(query, original, tools, *, molecular_input):
    """Closed additive reply grammar; changed requirements require a new run.

    A checksum authenticates text, not its meaning. Do not strip arbitrary
    prose and then reuse the old plan: that could discard a cancellation or a
    new constraint. Only exact acknowledgements, complete structures and an
    activity target field are additive inputs to this immutable plan.
    """
    if query == original or not tools:
        return query
    from src.agent.tools.base_tool import BaseMolecularTool
    from src.agent.tools.molecular_input import _MARKER, _unquote, parse_molecular_smiles
    from src.activity.family_contract import resolve_activity_family
    acknowledgements = {'继续', '请继续', '确认', '好的', 'continue', 'proceed', 'ok', 'yes'}
    operations = {
        'property_calculator': ('计算理化性质', '计算性质'),
        'drug_likeness_assessment': ('评估类药性',),
        'activity_predictor': ('预测活性',),
        'admet_predictor': ('预测admet',),
        'llm_molecular_generator': ('生成',),
        'candidate_ranker': ('排序',),
        'rag_search': ('检索',),
        'target_database_search': ('查找结构',),
        'reverse_target_predictor': ('反向寻靶',),
    }
    for name in tools:
        acknowledgements.update('继续' + op for op in operations.get(name, ()))
    parser = BaseMolecularTool('web-clarification', '')
    molecules = []
    clauses = [part.strip() for part in re.split(r'[;；\r\n]', query) if part.strip()]
    if not clauses:
        raise DecisionBoundaryError('web_clarification_changes_request')
    for clause in clauses:
        if clause.casefold().rstrip('。!！') in acknowledgements:
            continue
        if 'activity_predictor' in tools:
            from src.agent.tools.activity_input import _SPECIES_DECLARATION, requested_activity_species
            if _SPECIES_DECLARATION.fullmatch(clause.rstrip('。!！')):
                try:
                    requested_activity_species(clause)
                except ValueError:
                    raise DecisionBoundaryError('web_clarification_changes_request') from None
                continue
        target = re.fullmatch(r'(?:(?:target|靶点)\s*[:：=]\s*)?([\w-]+)', clause, re.I)
        if target and 'activity_predictor' in tools:
            try:
                resolve_activity_family(target[1])
                continue
            except ValueError:
                pass
        if not molecular_input:
            raise DecisionBoundaryError('web_clarification_changes_request')
        # The normal entry accepts prose surrounding a field. A reply may
        # fill only whole fields, including each member of an explicit batch.
        fields = re.split(r'，|\s+(?:(?:和|与|and)\s+)?(?=SMILES\b)',
                          clause.removesuffix('。'), flags=re.I)
        try:
            candidates = []
            for field in fields:
                field = field.strip()
                marker = _MARKER.match(field)
                candidates.append(_unquote(field[marker.end():] if marker else field))
            values = parse_molecular_smiles(';'.join('SMILES: ' + value for value in candidates), parser)
        except ValueError:
            raise DecisionBoundaryError('web_clarification_changes_request') from None
        # Regular requests may contain prose after the structure; a continuation
        # may only fill the missing field. Require the entire value to survive.
        if values != candidates:
            raise DecisionBoundaryError('web_clarification_changes_request')
        molecules.extend(values)
    return ';'.join('SMILES: ' + value for value in molecules) or None


class WebBindingResolver:
    def __init__(self, *, session, requirements, original_context, adapters, input_journal=None):
        if type(session) is not WorkflowRunSession or not session.dynamic:
            raise DecisionBoundaryError(_ERROR)
        if type(original_context) is not AgentContext:
            raise DecisionBoundaryError(_ERROR)
        self.session = session
        self._input_journal = input_journal or BindingInputJournal(original_context)
        if (type(self._input_journal) is not BindingInputJournal
                or not self._input_journal.matches_context(original_context, input_turn=0)):
            raise DecisionBoundaryError(_ERROR)
        self._original = _wire(context_value(original_context, query_content_bytes=True))
        self._owner = tuple(getattr(original_context, key) for key in ('trace_id', 'session_id', 'user_id'))
        self.adapters = dict(adapters)
        if any(name not in WEB_TOOLS or not isinstance(adapter, ToolAdapter)
               or adapter.spec.name != name for name, adapter in self.adapters.items()):
            raise DecisionBoundaryError(_ERROR)
        parsed = parse_web_requirements(requirements)
        self.requirements = prepare_web_requirements(parsed, context=original_context,
            request_kind='scientific' if parsed.steps else 'chat', allowed_tools=set(adapters), required_tools=())
        self.requirements_sha256 = _digest(self.requirements.model_dump(mode='json'))
        self._records, self._issued = {}, set()
        self._check_owner()

    def _check_owner(self):
        if (tuple(getattr(self.session.context, key) for key in ('trace_id', 'session_id', 'user_id')) != self._owner
                or not self._input_journal.matches_context(self.session.context)):
            raise DecisionBoundaryError(_ERROR)
        checked = parse_web_requirements(self.requirements)
        if _digest(checked.model_dump(mode='json')) != self.requirements_sha256:
            raise DecisionBoundaryError(_ERROR)
        for turn in range(1, self._input_journal.head_turn + 1):
            self._reply_molecules(self._input_journal.context(turn).query)

    def _reply_molecules(self, query):
        steps = self.requirements.steps
        # Only a direct molecular-input action can consume an added structure.
        # Generated-candidate dependants must not silently ignore a new seed.
        molecular_input = any(step.tool_name in {'property_calculator', 'drug_likeness_assessment',
            'admet_predictor', 'activity_predictor', 'reverse_target_predictor'}
            and step.input_binding is None and step.input_from is None for step in steps)
        return _clarification_molecules(query, self._input_journal.context(0).query,
            {step.tool_name for step in steps}, molecular_input=molecular_input)

    def _steps(self):
        return {step.name: step for step in workflow_steps(self.requirements)}

    def _adapter(self, name):
        from src.agent.tooling.factory import TOOL_AGENT_OWNERS
        adapter = self.adapters.get(name)
        if (adapter is None or name in self.requirements.forbidden_tools
                or not adapter.spec.idempotent or adapter.spec.side_effects != 'none'
                or TOOL_AGENT_OWNERS[name] not in adapter.spec.owner_agents):
            raise DecisionBoundaryError(_ERROR)
        capabilities = json.loads(self._original)['metadata'].get('capabilities', {})
        if not capabilities.get('rag' if name == 'rag_search' else 'scientific_tools', True):
            raise DecisionBoundaryError(_ERROR)
        return adapter

    def _observations(self):
        self._check_owner()
        if type(self.session.results) is not list or len(self.session.results) > 12:
            raise DecisionBoundaryError(_ERROR)
        found = {}
        for result in self.session.results:
            observation_value(result)
            identity = result.quality.get('evidence_id')
            if type(identity) is not str or identity in found:
                raise DecisionBoundaryError(_ERROR)
            found[identity] = result
        return found

    def _context(self, turn):
        """Clarifications may fill an absent molecular input, never replace one."""
        from src.agent.tools.base_tool import BaseMolecularTool
        from src.agent.tools.molecular_input import parse_molecular_smiles
        from .decision_requirements import _canonical
        original = self._input_journal.context(0)
        tools = {step.tool_name for step in self._steps().values()}
        if tools <= {'rag_search', 'target_database_search'}:
            return original  # Retrieval text is not an asserted molecular batch.

        def subject_context(context):
            if tools == {'reverse_target_predictor'}:
                from src.agent.routing.reverse_request import reverse_molecular_input_query
                return replace(context, query=reverse_molecular_input_query(context.query))
            if 'llm_molecular_generator' not in tools:
                return context
            # "Is" can resemble a structure lexeme to the analysis parser.
            # Strip only this complete generation prelude for subject detection;
            # the original prompt, explicit fields and all suffixes stay intact.
            query = re.sub(r'^\s*(?:is\s+it\s+possible\s+to|would\s+it\s+be\s+possible\s+to|'
                           r'it\s+is\s+possible\s+to)\s+(?=generate\b)', '', context.query, flags=re.I)
            return replace(context, query=query)

        original_subject = subject_context(original)
        current = original
        subject, selection = None, None
        for index in range(turn + 1):
            context = self._input_journal.context(index)
            if index:
                query = self._reply_molecules(context.query)
                if query is None and context.resolved_molecule is None:
                    continue
                context = replace(context, query=query or '')
            molecular_context = subject_context(context)
            selected = effective_molecule(molecular_context)
            explicit = has_explicit_molecule(molecular_context.query)
            if selected is not None:
                if not selected.revalidate(self.session.orchestrator.state_store, context.session_id):
                    raise DecisionBoundaryError('scientific_reference_unavailable')
                identity = context_value(context, query_content_bytes=True)['resolved_molecule']
                if selection is not None and identity != selection:
                    raise DecisionBoundaryError(_ERROR)
                selection = identity
                values = [selected.canonical_smiles]
            elif explicit:
                parser = BaseMolecularTool('web-input', '')
                try:
                    values = parse_molecular_smiles(molecular_context.query, parser)
                except ValueError:
                    from src.agent.tools.activity_input import parse_activity_input
                    values = parse_activity_input(molecular_context.query, parser)[1]
            else:
                continue
            canonical = [_canonical(value) for value in values]
            if (self.requirements.workflow_name == 'comprehensive_evaluation'
                    and self.requirements.metadata.get('input_type') == 'explicit_comparison'):
                # Validate complete fields before the comparison's canonical
                # deduplication. Candidate extraction can truncate bad suffixes.
                from src.agent.evidence_followup import MAX_CANDIDATES
                if len(values) > MAX_CANDIDATES:
                    raise DecisionBoundaryError(_ERROR)
                canonical = list(dict.fromkeys(canonical))
            if not canonical or any(value is None for value in canonical) or len(set(canonical)) != len(canonical):
                raise DecisionBoundaryError(_ERROR)
            canonical = tuple(sorted(canonical))
            if subject is not None and canonical != subject:
                raise DecisionBoundaryError(_ERROR)
            subject = canonical
            if index == 0 or current is original and not has_explicit_molecule(original_subject.query) and effective_molecule(original_subject) is None:
                current = context
        # Molecular clarification changes only query/selection. Frozen routing,
        # generation count, temperature, permissions and original metadata stay.
        return replace(original, query=current.query, resolved_molecule=current.resolved_molecule)

    def _outputs(self, prior):
        from src.agent.contracts.generation_request import preserve_target_quality
        steps = self._steps()
        completed, outputs = {}, {}
        for identity, result in prior.items():
            record = json.loads(self._records[result.quality['step_id']])
            ref = record['arguments']['step_ref']
            if ref in completed:
                raise DecisionBoundaryError(_ERROR)
            completed[ref] = (identity, result)
            step = steps[ref]
            eligible = (usable(result) if result.tool_name in ('rag_search', 'reverse_target_predictor')
                        else has_usable_observation(result))
            if eligible and result.provenance and not (
                    result.provenance.demo_mode or result.provenance.fallback_used):
                if step.output_key:
                    data = deepcopy(result.data)
                    outputs[step.output_key] = (preserve_target_quality(data, result.quality)
                        if step.output_key == 'target' else data)
        return completed, outputs

    def _resolve(self, name, arguments, prior, *, turn):
        validate_json(arguments, max_bytes=4096, reason=_ERROR)
        args = WebActionArguments.model_validate(arguments, strict=True)
        steps = self._steps()
        step = steps.get(args.step_ref)
        if step is None or step.tool_name != name:
            raise DecisionBoundaryError(_ERROR)
        adapter = self._adapter(name)
        context = self._context(turn)
        completed, outputs = self._outputs(prior)
        roles = []
        optional = set(step.metadata.get('workflow_optional_output_keys', ()))
        for dependency in self.requirements.dependencies[step.name]:
            if dependency not in completed:
                dependency_step = steps.get(dependency)
                if dependency_step is not None and not dependency_step.required:
                    # Optional assessments are allowed to be absent. The
                    # downstream verifier receives the remaining authenticated
                    # workflow outputs and must report missing evidence rather
                    # than being blocked before it can evaluate the result.
                    continue
                raise DecisionBoundaryError('web_dependency_not_ready')
            identity, source = completed[dependency]
            output_key = steps[dependency].output_key
            if output_key not in outputs and output_key not in optional:
                raise DecisionBoundaryError('web_dependency_unusable')
            roles.append({'role': 'dependency', 'step_ref': dependency,
                          'evidence_id': identity, 'output_sha256': _digest(source.data)})
        # No additional output outside the compiled dependency allowlist can
        # enter $.workflow, even when Pi chose independent actions earlier.
        keys = {steps[ref].output_key for ref in self.requirements.dependencies[step.name]}
        outputs = {key: value for key, value in outputs.items() if key in keys}
        original = self._input_journal.context(0)
        if name not in ('llm_molecular_generator', 'rag_search', 'target_database_search'):
            if step.input_data == original.query and context.query != original.query:
                step = replace(step, input_data=context.query)
        semantic_input = WorkflowOrchestrator._resolve_semantic_input(context, step, outputs)
        semantic = self.session.orchestrator.semantic_validator.validate(step, semantic_input)
        if not semantic.allowed:
            raise DecisionBoundaryError('web_semantic_precondition_failed')
        resolved = WorkflowOrchestrator._resolve_input(context, step, outputs)
        if (self.requirements.workflow_name == 'hit_to_lead_optimization'
                and step.name in {'baseline_properties', 'baseline_admet'}
                and isinstance(resolved, str)
                and any(item.tool_name == 'activity_predictor' for item in steps.values())):
            from src.agent.tools.activity_input import activity_intent_requested, parse_activity_input
            if activity_intent_requested(resolved):
                from src.agent.tools.base_tool import BaseMolecularTool
                _, molecules, _ = parse_activity_input(resolved, BaseMolecularTool('web-input', ''))
                if len(molecules) != 1:
                    raise DecisionBoundaryError(_ERROR)
                resolved = molecules[0]
        if name == 'reverse_target_predictor':
            from src.agent.tools.molecular_input import parse_molecular_smiles
            from src.agent.routing.reverse_request import reverse_molecular_input_query
            # Bind the same complete structure consumed by the tool before
            # hashing or dispatch. Receipts identify exact input SMILES, not
            # the surrounding request prose. Never choose a batch's first item.
            values = parse_molecular_smiles(reverse_molecular_input_query(resolved), adapter.tool)
            if len(values) != 1:
                raise DecisionBoundaryError(_ERROR)
            resolved = values[0]
        if name == 'rag_search':
            count = original.metadata.get('rag_count', 3)
            if type(count) is not int or not 1 <= count <= 50:
                raise DecisionBoundaryError(_ERROR)
            resolved = {'query': resolved, 'k': count}
        if name == 'activity_predictor':
            from .decision_inputs import activity_input_target
            from src.agent.tools.activity_input import requested_activity_species
            queries = [self._input_journal.context(index).query for index in range(turn + 1)]
            target = activity_input_target(SimpleNamespace(context=original,
                input_queries=queries))
            if target is not None:
                resolved = ({**resolved, 'target': target} if type(resolved) is dict
                            else {'query': resolved, 'target': target})
            try:
                species = {value for query in queries if (value := requested_activity_species(query)) is not None}
            except ValueError:
                raise DecisionBoundaryError('web_clarification_changes_request') from None
            if len(species) > 1:
                raise DecisionBoundaryError('web_clarification_changes_request')
            if species:
                model_request = dict(resolved.get('model_request') or {})
                value = next(iter(species))
                if 'species' in model_request and model_request['species'] != value:
                    raise DecisionBoundaryError('web_clarification_changes_request')
                model_request['species'] = value
                resolved = {**resolved, 'model_request': model_request}
        # The optimization verifier's existing typed adapter consumes the
        # workflow envelope directly; other domain adapters accept query views.
        payload = resolved if name == 'lead_optimization_verifier' else {'query': resolved}
        if name == 'target_database_search':
            from src.agent.contracts.target_request import UNSUPPORTED_TARGET_SPECIES, unrecognized_lookup_qualifier
            from src.agent.planning.request_parsing import positive_intent_text
            from src.agent.tools.activity_input import requested_activity_species
            qualifiers = re.findall(r'([\u4e00-\u9fff]{1,12}源)\s*(?=[A-Za-z][A-Za-z0-9_-]*)', original.query)
            if (unrecognized_lookup_qualifier(original.query)
                    or any(not qualifier.endswith(('人源', '小鼠源', '大鼠源', '犬源', '狗源', '猪源'))
                    for qualifier in qualifiers)
                    or re.search(rf'(?<![A-Za-z])(?:{UNSUPPORTED_TARGET_SPECIES})\s*'
                                 r'(?=[A-Za-z][A-Za-z0-9_-]*)', original.query, re.I)):
                raise DecisionBoundaryError('web_clarification_changes_request')
            try:
                species = requested_activity_species(original.query)
            except ValueError:
                raise DecisionBoundaryError('web_clarification_changes_request') from None
            if species not in (None, 'human', 'mouse'):
                raise DecisionBoundaryError('web_clarification_changes_request')
            if species == 'mouse':
                payload['organism'] = 'Mus musculus'
            if re.search(r'功能|\bfunction\b', positive_intent_text(original.query), re.I):
                payload['include_function'] = True
        snapshot = context_value(self._input_journal.context(turn), query_content_bytes=True)
        selected = context_value(context, query_content_bytes=True)['resolved_molecule']
        selection = _digest(selected) if selected is not None else None
        request = _digest({'original_sha256': _digest(json.loads(self._original)),
            'input_sha256': _digest(payload), 'selection_sha256': selection})
        identity = dict(profile=WEB_PROFILE_REVISION, policy='web-v1', step_ref=step.name,
            plan_sha256=self.requirements.plan_sha256, requirements_sha256=self.requirements_sha256,
            tool_name=name, tool_version=adapter.spec.version, adapter_version=adapter.adapter_version,
            resolved_input=payload, roles=roles, selection_sha256=selection, request_input_digest=request)
        action_hash = _digest(identity)
        return dict(tool_name=name, arguments={'step_ref': step.name}, context=snapshot,
            input_turn=turn, input_prefix_sha256=self._input_journal.prefix_digest(turn),
            input_data=payload, action_sha256=action_hash, request_input_digest=request,
            proof=dict(version='1', profile=WEB_PROFILE_REVISION, policy='web-v1',
                step_ref=step.name, plan_sha256=self.requirements.plan_sha256,
                requirements_sha256=self.requirements_sha256, action_sha256=action_hash,
                input_sha256=_digest(payload), roles=roles, own_source=None, selection_sha256=selection))

    def resolve(self, decision):
        self._check_owner()
        if type(decision) is not ToolDecision:
            raise DecisionBoundaryError(_ERROR)
        self.verify_binding_closure()
        record = self._resolve(decision.tool_name, decision.arguments, self._observations(),
                               turn=self._input_journal.head_turn)
        action = ResolvedBindingAction(_wire(record, _RECORD_LIMIT))
        self._issued.add(action.record_json)
        return action

    def step_metadata(self, action):
        record = json.loads(action.record_json)
        return {'web_step_ref': record['arguments']['step_ref'], 'web_plan_sha256': self.requirements.plan_sha256}

    def step_timeout(self, action, remaining):
        self._check_owner()
        if (type(action) is not ResolvedBindingAction or action.record_json not in self._issued
                or type(remaining) not in (int, float) or not math.isfinite(remaining) or remaining <= 0):
            raise DecisionBoundaryError(_ERROR)
        record = json.loads(action.record_json)
        cap = self._steps()[record['arguments']['step_ref']].timeout_seconds
        return min(remaining, cap) if cap is not None else remaining

    def register_action(self, step_id, action):
        self._check_owner()
        if (type(action) is not ResolvedBindingAction or action.record_json not in self._issued
                or type(step_id) is not str or not 1 <= len(step_id) <= 128 or step_id in self._records):
            raise DecisionBoundaryError(_ERROR)
        record = json.loads(action.record_json)
        if any(json.loads(raw)['arguments'] == record['arguments'] for raw in self._records.values()):
            raise DecisionBoundaryError('previous_action_not_usable')
        candidate = {**self._records, step_id: action.record_json}
        _wire({key: json.loads(raw) for key, raw in candidate.items()}, _RECORD_LIMIT)
        self._records = candidate
        return self._initial_metadata(record)

    def _initial_metadata(self, record):
        return dict(request_input_digest=record['request_input_digest'],
            input_evidence_ids=[role['evidence_id'] for role in record['proof']['roles']],
            operation_key=_initial_key(record))

    def export_records(self):
        return json.loads(_wire({key: json.loads(raw) for key, raw in self._records.items()}, _RECORD_LIMIT))

    def _check_step(self, step, record, input_data):
        if (step.tool_name != record['tool_name'] or step.output_key != step.name
                or step.input_from is not None or step.input_binding is not None
                or step.input_template is not None or step.input_transform != 'identity'
                or _wire(step.input_data) != _wire(record['input_data'])
                or _wire(input_data) != _wire(record['input_data'])):
            raise DecisionBoundaryError(_ERROR)

    def guard_dispatch(self, step, input_data):
        self._check_owner()
        try:
            raw = self._records[step.name]
            if raw not in self._issued:
                raise DecisionBoundaryError(_ERROR)
            record = json.loads(raw)
            self._check_step(step, record, input_data)
            expected = self._initial_metadata(record)
            if any(step.metadata.get(key) != value for key, value in expected.items()) or 'binding_proof' in step.metadata:
                raise DecisionBoundaryError(_ERROR)
            self.verify_binding_closure()
            rebuilt = self._resolve(step.tool_name, record['arguments'], self._observations(),
                                    turn=self._input_journal.head_turn)
            if _wire(rebuilt, _RECORD_LIMIT) != raw:
                raise DecisionBoundaryError(_ERROR)
        except (KeyError, TypeError, AttributeError):
            raise DecisionBoundaryError(_ERROR) from None

    def _source(self, result, record, expected=None):
        if result.tool_name not in ('rag_search', 'reverse_target_predictor') or not usable(result):
            return None
        hook = getattr(self._adapter(result.tool_name).tool, 'validate_current_observation', None)
        if not callable(hook):
            raise DecisionBoundaryError('current_source_unavailable')
        return hook(result.data, result.evidence, input_data=record['input_data']['query'], expected_source=expected)

    def _sanitized_preparation_failure(self, source):
        return B1BindingResolver._sanitized_preparation_failure(self, source)

    def _authenticated_record(self, source):
        verify_observation_integrity(source, self.session)
        try:
            raw = self._records[source.quality['step_id']]
            record = json.loads(raw)
            proof = parse_web_binding_proof(record['proof'])
            turn = record['input_turn']
            if (record['input_prefix_sha256'] != self._input_journal.prefix_digest(turn)
                    or _wire(record['context']) != _wire(context_value(self._input_journal.context(turn), query_content_bytes=True))
                    or proof.requirements_sha256 != self.requirements_sha256
                    or proof.plan_sha256 != self.requirements.plan_sha256 or proof.own_source is not None
                    or proof.action_sha256 != record['action_sha256'] or proof.input_sha256 != _digest(record['input_data'])
                    or source.tool_name != record['tool_name']
                    or source.quality['request_input_digest'] != record['request_input_digest']
                    or source.quality['input_evidence_ids'] != [role.evidence_id for role in proof.roles]
                    or source.quality['output_key'] != source.quality['step_id']
                    or source.provenance.tool_version != self._adapter(source.tool_name).spec.version
                    or source.provenance.input_digest != self.session.orchestrator._input_hash(record['input_data'])):
                raise DecisionBoundaryError(_ERROR)
            diagnostic = 'binding_record_sha256' not in source.quality
            if diagnostic:
                if not self._sanitized_preparation_failure(source) or source.quality['operation_key'] != _initial_key(record):
                    raise DecisionBoundaryError(_ERROR)
            elif source.quality['binding_record_sha256'] != _digest(record, _RECORD_LIMIT):
                raise DecisionBoundaryError(_ERROR)
            return record, diagnostic
        except (ValueError, KeyError, TypeError, AttributeError):
            raise DecisionBoundaryError(_ERROR) from None

    def prepare_observation(self, result, step):
        self._prepare_observation(result, step, restored=False)

    def prepare_restored_observation(self, result, step):
        self._prepare_observation(result, step, restored=True)

    def validate_checkpoint(self, result, step, checkpoint):
        """Validate a claimed checkpoint without installing or repairing it.

        The caller owns durable claim/model-compatibility checks. This boundary
        requires the old observation and commitments before recomputing anything;
        a fresh proof must never turn an unproved checkpoint into trusted work.
        """
        try:
            self._check_owner()
            validate_json(checkpoint, max_bytes=_RECORD_LIMIT, reason=_ERROR)
            before = _wire(observation_value(result))
            if (type(checkpoint) is not dict or type(checkpoint['metadata']) is not dict
                    or result.status.value not in ('succeeded', 'partial')
                    or result.error is not None or checkpoint['error'] is not None
                    or (result.status.value == 'succeeded' and result.success is not True)
                    or result.provenance is None):
                raise DecisionBoundaryError(_ERROR)
            record = json.loads(self._records[step.name])
            self._check_step(step, record, step.input_data)
            adapter = self._adapter(step.tool_name)
            provenance = ToolProvenance.from_dict(result.provenance.to_dict())
            input_digest = self.session.orchestrator._input_hash(record['input_data'])
            expected = dict(trace_id=self._owner[0], step_id=step.name, tool_name=step.tool_name,
                workflow_version=self.session.orchestrator.workflow_version,
                tool_version=adapter.spec.version, adapter_version=adapter.adapter_version,
                model_version=provenance.model_version or '', input_hash=input_digest,
                status=result.status.value)
            if (any(checkpoint[key] != value for key, value in expected.items())
                    or result.tool_name != step.tool_name or provenance.tool_name != step.tool_name
                    or provenance.tool_version != adapter.spec.version
                    or provenance.demo_mode is not False or provenance.fallback_used is not False
                    or provenance.input_digest != input_digest
                    or provenance.output_digest != _digest(result.data)
                    or _wire(checkpoint['output']) != _wire(result.to_legacy_dict())
                    or result.quality['step_id'] != step.name or result.quality['output_key'] != step.name
                    or result.quality['binding_record_sha256'] != _digest(record, _RECORD_LIMIT)):
                raise DecisionBoundaryError(_ERROR)
            proof = parse_web_binding_proof(result.quality['binding_proof']).model_dump(mode='json')
            if _wire(proof) != _wire({**record['proof'], 'own_source': proof['own_source']}):
                raise DecisionBoundaryError(_ERROR)
            for key in ('binding_proof', 'binding_record_sha256', 'request_input_digest',
                        'input_evidence_ids', 'operation_key'):
                if _wire(checkpoint['metadata'][key]) != _wire(result.quality[key]):
                    raise DecisionBoundaryError(_ERROR)
            for key, value in dict(web_step_ref=record['arguments']['step_ref'],
                                  web_plan_sha256=self.requirements.plan_sha256).items():
                if checkpoint['metadata'][key] != value or step.metadata[key] != value:
                    raise DecisionBoundaryError(_ERROR)
            candidate, candidate_step = deepcopy(result), deepcopy(step)
            # Check the OLD source identity explicitly, before prepare_restored
            # builds its candidate proof against the current source.
            if self._source(candidate, record, proof['own_source']) != proof['own_source']:
                raise DecisionBoundaryError(_ERROR)
            self.prepare_restored_observation(candidate, candidate_step)
            # An isolated ledger recomputes the original evidence ID, without
            # adding authority to this request's ledger or replacing its seals.
            identity = EvidenceLedger(self._owner[0]).register_tool_result(
                step.name, input_digest, candidate, binding_proof=proof)
            if (identity != result.quality['evidence_id'] or candidate.success is not result.success
                    or _wire(observation_value(candidate)) != before):
                raise DecisionBoundaryError(_ERROR)
            return True
        except DecisionBoundaryError:
            raise
        except Exception:
            raise DecisionBoundaryError(_ERROR) from None

    def _prepare_observation(self, result, step, *, restored):
        self._check_owner()
        record = json.loads(self._records[step.name])
        self._check_step(step, record, step.input_data)
        self.verify_binding_closure()
        prior = self._observations()
        rebuilt = self._resolve(step.tool_name, record['arguments'], prior, turn=record['input_turn'])
        if _wire(rebuilt, _RECORD_LIMIT) != _wire(record, _RECORD_LIMIT):
            raise DecisionBoundaryError(_ERROR)
        current = self._resolve(step.tool_name, record['arguments'], prior, turn=self._input_journal.head_turn)
        if current['action_sha256'] != record['action_sha256']:
            raise DecisionBoundaryError(_ERROR)
        compiled = self._steps()[record['arguments']['step_ref']]
        if compiled.metadata.get('candidate_source') and has_usable_observation(result) and not restored:
            from src.agent.validators import align_candidate_results
            if result.provenance is None or (result.provenance.output_digest is not None
                    and result.provenance.output_digest != _digest(result.data)):
                raise DecisionBoundaryError('candidate_output_digest_mismatch')
            _, outputs = self._outputs(prior)
            aligned = align_candidate_results(outputs.get(compiled.metadata['candidate_source']), result,
                                               required=compiled.required)
            if aligned is not result:
                raise DecisionBoundaryError(_ERROR)
            result.provenance = replace(result.provenance, output_digest=_digest(result.data))
            # Candidate alignment renders the entire structured data a second
            # time as indented JSON. Keep the authoritative data untouched, but
            # avoid letting that redundant presentation copy exhaust the Web
            # observation budget. The normal bound below still validates all
            # data, warnings, provenance and binding fields without truncation.
            if len(result.formatted.encode('utf-8')) > 16 * 1024:
                result.formatted = '候选结果已记录；完整字段需要支持结果明细的客户端。'
        observation_value(result)
        proof = deepcopy(record['proof'])
        proof['own_source'] = self._source(result, record)
        proof = parse_web_binding_proof(proof).model_dump(mode='json')
        metadata = dict(request_input_digest=record['request_input_digest'],
            input_evidence_ids=[role['evidence_id'] for role in proof['roles']],
            operation_key=_digest(dict(action_sha256=record['action_sha256'], own_source=proof['own_source'])),
            binding_proof=proof, binding_record_sha256=_digest(record, _RECORD_LIMIT))
        for key in ('binding_proof', 'operation_key', 'request_input_digest', 'input_evidence_ids',
                    'own_source', 'action_sha256', 'selection_sha256', 'binding_record_sha256'):
            result.quality.pop(key, None)
        result.quality.update(deepcopy(metadata))
        step.metadata.update(deepcopy(metadata))

    def verify_binding_closure(self, evidence_ids=None):
        observations = self._observations()
        wanted = list(observations) if evidence_ids is None else evidence_ids
        validate_json(wanted, max_bytes=65536, reason=_ERROR)
        if (type(wanted) is not list or any(type(value) is not str for value in wanted)
                or len(wanted) != len(set(wanted)) or any(value not in observations for value in wanted)):
            raise DecisionBoundaryError(_ERROR)
        verified, ancestry = {}, {}
        for identity, source in observations.items():
            record, diagnostic = self._authenticated_record(source)
            roles = record['proof']['roles']
            if any(role['evidence_id'] not in verified for role in roles):
                raise DecisionBoundaryError(_ERROR)
            rebuilt = self._resolve(record['tool_name'], record['arguments'], verified, turn=record['input_turn'])
            if _wire(rebuilt, _RECORD_LIMIT) != self._records[source.quality['step_id']]:
                raise DecisionBoundaryError(_ERROR)
            current = self._resolve(record['tool_name'], record['arguments'], verified, turn=self._input_journal.head_turn)
            if current['action_sha256'] != record['action_sha256']:
                raise DecisionBoundaryError(_ERROR)
            if not diagnostic:
                proof = parse_web_binding_proof(source.quality['binding_proof']).model_dump(mode='json')
                if proof != {**record['proof'], 'own_source': proof['own_source']}:
                    raise DecisionBoundaryError(_ERROR)
                own = self._source(source, record, proof['own_source'])
                if own != proof['own_source'] or source.quality['operation_key'] != _digest(
                        dict(action_sha256=record['action_sha256'], own_source=own)):
                    raise DecisionBoundaryError(_ERROR)
            ancestry[identity] = {identity}
            for role in roles:
                ancestry[identity].update(ancestry[role['evidence_id']])
            verified[identity] = source
        needed = set().union(*(ancestry[value] for value in wanted)) if wanted else set()
        return [identity for identity in verified if identity in needed]

    def restore_records(self, records):
        self._check_owner()
        _wire(records, _RECORD_LIMIT)
        if type(records) is not dict or len(records) > 12 or self._records:
            raise DecisionBoundaryError(_ERROR)
        if set(records) != {result.quality['step_id'] for result in self._observations().values()}:
            raise DecisionBoundaryError(_ERROR)
        candidate = {key: _wire(value, _RECORD_LIMIT) for key, value in records.items()}
        self._records = candidate
        try:
            self.verify_binding_closure()
        except Exception:
            self._records = {}
            raise

    def find_reusable(self, action):
        self._check_owner()
        if type(action) is not ResolvedBindingAction or action.record_json not in self._issued:
            raise DecisionBoundaryError(_ERROR)
        self.verify_binding_closure()
        candidate = json.loads(action.record_json)
        for source in self._observations().values():
            record, diagnostic = self._authenticated_record(source)
            if record['arguments'] == candidate['arguments']:
                if record['action_sha256'] != action.action_sha256 or diagnostic or not has_usable_observation(source):
                    raise DecisionBoundaryError('previous_action_not_usable')
                return source
        return None

    def record_descriptors(self, evidence_id):
        self.verify_binding_closure([evidence_id])
        source = self._observations()[evidence_id]
        if source.tool_name != 'reverse_target_predictor' or not usable(source):
            raise DecisionBoundaryError(_ERROR)
        # Same receipt-row descriptor codec; Web model arguments remain step_ref
        # only, so a descriptor cannot manufacture a new target-search action.
        return B1BindingResolver._raw_descriptors(self, source)[1]
