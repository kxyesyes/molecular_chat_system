"""Deterministic full-Web acceptance over authenticated, current observations.

No model text, executor or scientific-tool dispatch. The owner must still
recheck source/publication barriers after this bounded progress/render view.
"""
from __future__ import annotations

from copy import deepcopy
import json
import re
from types import SimpleNamespace

from src.agent.contracts import CandidateSet, ObservationStatus
from src.agent.contracts.web_requirements import WEB_PROFILE_REVISION, parse_web_action_arguments, parse_web_requirements
from .decision_bounds import validate_json
from .decision_policy import DecisionBoundaryError, usable
from .decision_requirements import _check_batch, _canonical
from .web_requirements import required_web_tools, workflow_steps

_ERROR = 'invalid_web_acceptance'
_BATCH = {'property_calculator', 'drug_likeness_assessment', 'admet_predictor', 'activity_predictor'}
_METRICS = {'molecular_weight': r'分子量|\bMW\b|molecular\s+weight',
    'logp': r'\blogp\b', 'tpsa': r'\btpsa\b', 'qed': r'\bqed\b',
    'hba': r'\bhba\b', 'hbd': r'\bhbd\b'}


def _typed(result):
    from src.agent.tooling.analysis_contract import ANALYSIS_OUTPUT_SCHEMAS
    from src.agent.tooling.activity_contract import ActivityPredictOutput
    from src.agent.tooling.rag_contract import RAGSearchOutput
    from src.agent.tooling.target_contract import TargetSearchOutput, ReverseTargetOutput
    from src.agent.tooling.generation_ranking_contract import GenerationOutput, RankingOutput
    from src.agent.tooling.optimization_contract import LeadOptimizationOutput
    if result.tool_name == 'lead_optimization_verifier':
        LeadOptimizationOutput.model_validate(result.data, strict=True)
        return
    schemas = dict(ANALYSIS_OUTPUT_SCHEMAS, activity_predictor=ActivityPredictOutput,
        rag_search=RAGSearchOutput, target_database_search=TargetSearchOutput,
        reverse_target_predictor=ReverseTargetOutput, llm_molecular_generator=GenerationOutput,
        candidate_ranker=RankingOutput)
    schemas[result.tool_name].model_validate(vars(result), strict=True)


def _subjects(payload, name):
    from src.agent.tools.base_tool import BaseMolecularTool
    from src.agent.tools.molecular_input import parse_molecular_smiles
    parser = BaseMolecularTool('web_acceptance', '')
    if type(payload) is dict:
        values = payload.get('smiles')
        if type(values) is list:
            return values
        if type(values) is str:
            return parse_molecular_smiles(values, parser)
        payload = payload.get('query')
    if type(payload) is not str:
        raise ValueError(_ERROR)
    if name == 'activity_predictor':
        from src.agent.tools.activity_input import parse_activity_input
        return parse_activity_input(payload, parser)[1]
    return parse_molecular_smiles(payload, parser)


def web_observation_reasons(result, record, step, *, request_query):
    """Pure domain validation, NOT ownership authorization. Entry points below
    authenticate the resolver, every record and current source before calling.
    """
    reasons = [] if usable(result) else ['observation_not_usable']
    if result.error is not None or result.status not in (ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL):
        return reasons or ['observation_not_usable']
    try:
        _typed(result)
        name = result.tool_name
        payload = record['input_data'] if name == 'lead_optimization_verifier' else record['input_data']['query']
        if name in _BATCH:
            subjects = _subjects(payload, name)
            if not subjects:
                reasons.append('missing_subject_evidence')
            metrics = tuple(key for key, pattern in _METRICS.items() if re.search(pattern, request_query, re.I)) if name in {
                'property_calculator', 'drug_likeness_assessment'} else ()
            requirement = SimpleNamespace(tool_name=name, expected_smiles=subjects,
                exact_molecule_count=len(subjects), required_metrics=metrics)
            reasons += _check_batch(requirement, result)['reason_codes']
            if 'subject_mismatch' in reasons:
                expected = {_canonical(value) for value in subjects}
                observed = [_canonical(row['smiles']) for row in result.data]
                # Missing rows are incomplete work, not foreign evidence. Only
                # a unique, valid strict subset may remain visible; completion
                # still fails and no candidate/prediction is filled in.
                if (observed and None not in expected and None not in observed
                        and len(set(observed)) == len(observed)
                        and set(observed) < expected):
                    reasons.remove('subject_mismatch')
                    reasons.append('incomplete_subject_coverage')
            if name == 'activity_predictor':
                from .decision_binding_acceptance import _activity_reasons
                target = payload.get('target') if type(payload) is dict else None
                if target is None:
                    from .decision_inputs import activity_input_target
                    from src.agent.contracts import AgentContext
                    target = activity_input_target(SimpleNamespace(context=AgentContext(request_query, 'acceptance'),
                        input_queries=[request_query]))
                if target is None:
                    reasons.append('activity_target_missing')
                else:
                    translated = {'input_data': {'query': {'smiles': subjects, 'target': target}}}
                    reasons += _activity_reasons(result, translated, None)
                if not all(row.get('success') is True for row in result.data):
                    reasons.append('incomplete_activity_batch')
            elif name == 'admet_predictor':
                from src.agent.tools.admet_predictor import ADMETPredictor
                from src.agent.validators.domain_validators import ADMETResultValidator
                if ADMETResultValidator().validate(result):
                    reasons.append('invalid_admet_evidence')
                # _typed above validates every AI endpoint; legacy presence
                # checks only understand section-based observations.
                if not all(bool(row['admet']['endpoints'])
                        if row['admet'].get('prediction_method') == 'admet_ai'
                        else ADMETPredictor._has_observations(row['admet']) for row in result.data):
                    reasons.append('no_admet_observations')
        elif name == 'llm_molecular_generator':
            from src.agent.validators.result_validator import AgentResultValidator
            candidates = CandidateSet.from_dict(result.data)
            AgentResultValidator._revalidate_candidate_set(candidates, trusted_checkpoint=False)
            requested = payload['metadata']['requested_count']
            if candidates.requested_count != requested or candidates.valid_count != requested:
                reasons.append('generation_count_mismatch')
        elif name == 'candidate_ranker':
            data = result.data
            requested = payload['metadata']['docking_top_n']
            if data['requested_top_n'] != requested or len(data['top_candidates']) < requested:
                reasons.append('ranking_count_mismatch')
            expected = payload['outputs']['molecules']
            candidates = CandidateSet.from_dict(expected)
            identities = {c.candidate_id: c.canonical_smiles for c in candidates.candidates}
            rows = data['ranked_candidates']
            if ({row['candidate_id'] for row in rows} != set(identities)
                    or any(identities.get(row['candidate_id']) != row['canonical_smiles'] for row in rows)):
                reasons.append('ranking_candidate_mismatch')
            required = set(payload['metadata'].get('required_evidence', ['properties']))
            if (set(data['required_evidence']) != required or data.get('unrankable_candidates')
                    or any(row.get('missing_required_evidence') or
                        required & set(row.get('missing_evidence', [])) for row in rows)):
                reasons.append('required_ranking_evidence_missing')
            from src.agent.contracts.property_ranking_request import property_only_design_request
            if property_only_design_request(request_query):
                # Recompute only this fixed, deterministic heuristic from the
                # already bound inputs; type/identity checks alone do not prove
                # that a producer's scores, weights or ordering are correct.
                from src.agent.tools.candidate_ranker import CandidateRanker
                expected_ranking = CandidateRanker().execute(payload)
                if not expected_ranking.get('success') or data != expected_ranking.get('data'):
                    reasons.append('ranking_evidence_mismatch')
        elif name == 'lead_optimization_verifier':
            data = result.data
            if (data['status'] != 'supported' or data['supported_candidate_count'] < 1
                    or data['unverifiable_constraints']
                    or any(row.get('status') == 'unverified' or row.get('missing_evidence')
                           or row.get('unverified_constraints') for row in data['candidates'])):
                reasons.append('optimization_goal_not_supported')
        elif name == 'rag_search':
            if (type(payload) is not dict or set(payload) != {'query', 'k'}
                    or type(payload['query']) is not str or type(payload['k']) is not int
                    or not 1 <= payload['k'] <= 50):
                reasons.append('retrieval_input_mismatch')
        elif name == 'target_database_search':
            if result.quality.get('lookup_status') not in ('resolved', 'not_found'):
                reasons.append('insufficient_target_lookup_evidence')
        if name in ('rag_search', 'reverse_target_predictor') and usable(result):
            proof = result.quality.get('binding_proof', {})
            source = proof.get('own_source')
            if type(source) is not dict or source.get('kind') != ('rag' if name == 'rag_search' else 'reverse'):
                reasons.append('current_source_proof_missing')
    except (ValueError, TypeError, KeyError, AttributeError, IndexError):
        reasons.append('invalid_scientific_observation')
    return sorted(set(reasons))


def _generation_constraints(requirements, steps, by_step, observations, reasons_by_id, scope):
    """Compare owned, identity-aligned candidate evidence, never generator claims.

    Lead optimization keeps its existing baseline/candidate verifier semantics;
    in particular its baseline is not required to meet the candidate bounds.
    """
    from src.agent.property_constraints import requested_property_bounds, check_property_bounds
    from .decision_requirements import _canonical
    if requirements.workflow_name not in ('molecular_design', 'target_driven_design'):
        return None
    bounds, unknown = requested_property_bounds(requirements.request['query'])
    if not bounds and not unknown:
        return None
    rows, failures = [], set()
    if unknown:
        failures.add('property_bounds_unverified')
    for ref, step in steps.items():
        if step.tool_name != 'llm_molecular_generator':
            continue
        identity = by_step.get(ref)
        generated = observations.get(identity)
        if identity not in scope or generated is None or reasons_by_id[identity]:
            failures.add('property_bounds_unverified')
            continue
        properties, source_id = {}, None
        for property_ref, property_step in steps.items():
            if (property_step.tool_name != 'property_calculator'
                    or property_step.metadata.get('candidate_source') != step.output_key):
                continue
            property_id = by_step.get(property_ref)
            if property_id not in scope or reasons_by_id.get(property_id, ['missing']):
                continue
            properties = {_canonical(row['smiles']): row['properties']
                for row in observations[property_id].data}
            source_id = property_id
        for candidate in generated.data['candidates']:
            smiles = candidate['canonical_smiles']
            checks = check_property_bounds(bounds, properties.get(_canonical(smiles)))
            if any(check['satisfied'] is False for check in checks):
                failures.add('property_bounds_not_satisfied')
            if any(check['satisfied'] is None for check in checks):
                failures.add('property_bounds_unverified')
            rows.append(dict(candidate_id=candidate['candidate_id'], smiles=smiles,
                generation_evidence_id=identity, evidence_id=source_id, checks=checks))
        # Retain the generated output for display, but never call the design
        # complete when its requested bounds failed or could not be checked.
        reasons_by_id[identity] = sorted(set(reasons_by_id[identity]) | failures)
    if not rows:
        failures.add('property_bounds_unverified')
    return dict(satisfied=not failures, requested_bounds=bounds,
        unverified_constraints=unknown, candidates=rows, reason_codes=sorted(failures))


def _evaluate(resolver, required_tools, evidence_ids):
    from .web_bindings import WebBindingResolver
    if type(resolver) is not WebBindingResolver:
        raise DecisionBoundaryError(_ERROR)
    if evidence_ids is not None:
        validate_json(evidence_ids, max_bytes=16384, reason=_ERROR)
        if (type(evidence_ids) is not list or len(evidence_ids) > 128
                or any(type(v) is not str or re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}', v) is None for v in evidence_ids)
                or len(evidence_ids) != len(set(evidence_ids))):
            raise DecisionBoundaryError(_ERROR)
    requirements = parse_web_requirements(resolver.requirements)
    required = required_web_tools(requirements, required_tools)
    # Includes unselected failures/current sources: citations cannot hide them.
    all_ids = resolver.verify_binding_closure()
    cited = [] if evidence_ids is None else resolver.verify_binding_closure(evidence_ids)
    observations = resolver._observations()
    if set(observations) != set(all_ids):
        raise DecisionBoundaryError(_ERROR)
    steps = {s.name: s for s in workflow_steps(requirements)}
    ordinary_chat = requirements.workflow_name == 'ordinary_chat' and not steps
    by_step, reasons_by_id, records = {}, {}, {}
    for identity in all_ids:
        result = observations[identity]
        record, diagnostic = resolver._authenticated_record(result)
        ref = parse_web_action_arguments(record['arguments']).step_ref
        if ref not in steps or steps[ref].tool_name != result.tool_name or ref in by_step:
            raise DecisionBoundaryError(_ERROR)
        by_step[ref] = identity
        records[identity] = record
        reasons_by_id[identity] = (['observation_not_usable'] if diagnostic else
            web_observation_reasons(result, record, steps[ref], request_query=requirements.request['query']))
    scope = set(all_ids if evidence_ids is None else cited)
    constraints = _generation_constraints(requirements, steps, by_step, observations, reasons_by_id, scope)
    checks, selected = [], []
    for ref, step in steps.items():
        identity = by_step.get(ref)
        reasons = ['no_usable_observation'] if identity is None else list(reasons_by_id[identity])
        if identity is not None and not reasons and identity not in scope:
            reasons.append('missing_required_citation')
        passed = not reasons
        if passed:
            selected.append(identity)
        checks.append(dict(step_ref=ref, tool_name=step.tool_name, required=step.required,
            passed=passed, reason_codes=reasons, evidence_ids=[identity] if identity is not None else []))
    missing_steps = [c['step_ref'] for c in checks if not c['passed']]
    missing_tools = sorted({c['tool_name'] for c in checks if not c['passed']} |
        (set(required) - {s.tool_name for s in steps.values()}))
    forbidden = sorted({r.tool_name for r in observations.values()} & set(requirements.forbidden_tools))
    reasons = {r for c in checks for r in c['reason_codes']}
    if forbidden:
        reasons.add('executed_forbidden_tool')
    if not ordinary_chat and evidence_ids is not None and not evidence_ids:
        reasons.add('scientific_citations_required')
    if any(reasons_by_id.values()):
        reasons.add('incomplete_scientific_observations')
    if constraints is not None:
        reasons.update(constraints['reason_codes'])
    satisfied = (bool(steps) or ordinary_chat) and not missing_steps and not missing_tools and not forbidden and (
        constraints is None or constraints['satisfied'])
    report = dict(version='1', profile=WEB_PROFILE_REVISION, plan_sha256=requirements.plan_sha256,
        request_kind='chat' if ordinary_chat else 'scientific',
        satisfied=satisfied, checks=checks, missing_required_steps=missing_steps,
        missing_required_tools=missing_tools, executed_forbidden_tools=forbidden,
        reason_codes=sorted(reasons), cited_closure_ids=cited,
        required_evidence_ids=resolver.verify_binding_closure(selected), citation_checked=evidence_ids is not None,
        finish_eligible=bool(satisfied and evidence_ids and not any(reasons_by_id.values())))
    if constraints is not None:
        report['property_constraints'] = constraints
    validate_json(report, max_bytes=65536, reason=_ERROR)
    return report, observations, reasons_by_id


def evaluate_web_acceptance(resolver, *, required_tools=(), evidence_ids=None):
    return _evaluate(resolver, required_tools, evidence_ids)[0]


def _scientific_data(result):
    from .decision_binding_acceptance import _scientific_data as b1_data, _pick
    if result.tool_name == 'admet_predictor':
        from src.agent.tooling.analysis_contract import _ADMET_SECTIONS
        _typed(result)
        rows = []
        for row in result.data or []:
            value = row['admet']
            if value.get('prediction_method') != 'admet_ai':
                rows.extend(b1_data(SimpleNamespace(tool_name=result.tool_name, data=[row])))
                continue
            # AI endpoints need no legacy section placeholders. Publish only
            # validated observations and provenance, never raw weights/extras.
            clean = _pick(value, ('prediction_method', 'backend_version', 'model_name',
                'model_version', 'weights_id', 'demo_mode', 'fallback_used',
                'physicochemical_source', 'risk_count', 'total_endpoints',
                'risk_threshold', 'risk_summary_method'))
            clean['endpoints'] = {key: _pick(endpoint, ('value', 'unit', 'task_type',
                'source', 'name', 'category')) for key, endpoint in value['endpoints'].items()}
            clean['units'] = _pick(value['units'], value['endpoints'])
            clean['risk_endpoint_ids'] = list(value['risk_endpoint_ids'])
            for section, fields in _ADMET_SECTIONS.items():
                if section in value:
                    clean[section] = _pick(value[section], fields)
            rows.append(dict(smiles=row['smiles'], admet=clean))
        return rows
    if result.tool_name == 'target_database_search':
        # The B1 identity-only view is sufficient for downstream binding, but
        # Web users also requested structure evidence. Keep only typed public
        # fields here, never file paths, producer prose or opaque extensions.
        _typed(result)
        rows = b1_data(result)
        if rows is None:
            return None
        for row, original in zip(rows, result.data):
            row.update(_pick(original, (
                'has_experimental_structure', 'has_alphafold_structure',
                'experimental_structure_count', 'alphafold_structure_count',
                'source_url', 'target_stale', 'structures_stale',
                'target_retrieved_at', 'target_expires_at',
                'structures_retrieved_at', 'structures_expires_at')))
            row['recommended_structures'] = [_pick(structure, (
                'structure_id', 'pdb_id', 'source', 'source_url', 'structure_type',
                'method', 'resolution', 'organism',
                'recommendation_level', 'docking_recommended',
                'retrieved_at', 'expires_at', 'stale'))
                for structure in original.get('recommended_structures', [])]
            for clean, structure in zip(row['recommended_structures'],
                                        original.get('recommended_structures', [])):
                for field in ('chain_ids', 'ligand_ids'):
                    if field in structure:
                        clean[field] = (list(structure[field])
                                        if structure[field] is not None else None)
        return rows
    if result.tool_name == 'drug_likeness_assessment':
        # Web exposes the existing heuristic score, not the producer's prose,
        # grades or category. Keep the independent B1 projection unchanged.
        _typed(result)
        rows = b1_data(result)
        for row, original in zip(rows, result.data):
            row['assessment']['overall_assessment'] = _pick(
                original['assessment']['overall_assessment'], ('score',))
        return rows
    if result.tool_name == 'llm_molecular_generator':
        candidates = CandidateSet.from_dict(result.data)
        raw = candidates.to_dict()
        value = _pick(raw, ('version', 'requested_count', 'valid_count', 'unique_count',
            'invalid_count', 'duplicate_count', 'status'))
        value['candidates'] = [_pick(c, ('candidate_id', 'candidate_index', 'source_index',
            'original_smiles', 'canonical_smiles', 'smiles')) for c in raw['candidates']]
        return value
    if result.tool_name == 'candidate_ranker':
        data = result.data
        value = _pick(data, ('requested_top_n', 'ranked_candidate_count'))
        value['required_evidence'] = list(data['required_evidence'])
        for key in ('top_candidates', 'ranked_candidates', 'unrankable_candidates'):
            value[key] = []
            for row in data.get(key, []):
                clean = _pick(row, ('candidate_id', 'canonical_smiles', 'score', 'rank',
                    'assessment_status', 'docking_ready_for_preparation', 'reason'))
                for field in ('missing_evidence', 'missing_required_evidence'):
                    if field in row:
                        clean[field] = list(row[field])
                if isinstance(row.get('ranking_evidence'), dict):
                    evidence = row['ranking_evidence']
                    clean['ranking_evidence'] = _pick(evidence, ('property_score', 'admet_score', 'activity_score'))
                    clean['ranking_evidence']['weights_used'] = _pick(evidence.get('weights_used', {}),
                        ('properties', 'admet', 'activity'))
                value[key].append(clean)
        return value
    if result.tool_name == 'lead_optimization_verifier':
        data = result.data
        value = _pick(data, ('status', 'baseline_smiles', 'supported_candidate_count'))
        value['unverifiable_constraints'] = list(data['unverifiable_constraints'])
        value['objectives'] = [_pick(r, ('metric', 'direction')) for r in data['objectives']]
        value['candidates'] = []
        for row in data['candidates']:
            clean = _pick(row, ('candidate_id', 'smiles', 'status', 'goal_supported'))
            for field in ('missing_evidence', 'unverified_constraints'):
                clean[field] = list(row.get(field, []))
            for field in ('comparisons', 'constraint_checks'):
                clean[field] = [_pick(v, ('metric', 'direction', 'baseline_value', 'candidate_value',
                    'delta', 'satisfied', 'endpoint', 'units', 'operator', 'threshold', 'value')) for v in row.get(field, [])]
            value['candidates'].append(clean)
        return value
    return b1_data(result)


def render_web_scientific_answer(resolver, *, required_tools=(), evidence_ids, interruption_reason=None):
    # A transport-budget/dependency stop is not a model finish or successful task. The
    # server may still display already authenticated work without inventing a
    # model citation or dropping evidence to squeeze in another model request.
    interrupted = interruption_reason in ('decision_input_budget_exceeded', 'web_dependency_unusable',
                                         'activity_target_conflict_or_unknown')
    if ((interruption_reason is not None and not interrupted)
            or (interrupted and evidence_ids is not None)
            or (evidence_ids is None and not interrupted)):
        raise DecisionBoundaryError(_ERROR)
    from .decision_binding_acceptance import _warnings, _provenance, _artifacts
    from src.agent.persistence.redaction import redact_sensitive
    report, results, reasons = _evaluate(resolver, required_tools, evidence_ids)
    if report['request_kind'] != 'scientific':
        raise DecisionBoundaryError(_ERROR)
    cited = set(report['cited_closure_ids'])
    if interrupted:
        cited = set(resolver.verify_binding_closure())
        report.update(satisfied=False, finish_eligible=False,
            interruption_reason=interruption_reason, displayed_closure_ids=sorted(cited),
            reason_codes=sorted(set(report['reason_codes']) | {interruption_reason}))
    blocks = []
    for identity, result in results.items():
        if identity not in cited and usable(result):
            continue
        display = False
        if identity in cited and result.error is None and result.status in (ObservationStatus.SUCCEEDED, ObservationStatus.PARTIAL):
            try:
                _typed(result)
                display = not any(code in reasons[identity] for code in (
                    'invalid_scientific_observation', 'subject_mismatch', 'ranking_candidate_mismatch',
                    'ranking_evidence_mismatch', 'invalid_smiles', 'duplicate_smiles', 'current_source_proof_missing'))
            except (ValueError, TypeError, KeyError):
                pass
        block = dict(tool=result.tool_name, evidence_id=identity, status=result.status.value,
            scientific_usable=not reasons[identity], reason_codes=reasons[identity],
            error=result.error.code.value if result.error else None,
            provenance=_provenance(result, cited=identity in cited), artifacts=_artifacts(result) if display else [])
        block['warnings'], block['warning_details_omitted'] = _warnings(result.warnings, diagnostic_only=not display)
        if display:
            block['data'] = _scientific_data(result)
        if result.tool_name == 'target_database_search' and identity in cited:
            block['lookup_status'] = result.quality.get('lookup_status')
        blocks.append(block)
    payload = dict(acceptance=report, observations=blocks,
        limitations=['Computed/predicted/retrieved evidence is not experimental validation.',
            'Partial or missing evidence does not establish completion or optimization success.',
            'ADMET available_methods is method-scoped; unknown alerts remain unknown.'])
    validate_json(payload, max_bytes=65536, reason=_ERROR)
    payload = redact_sensitive(deepcopy(payload))
    resolver.verify_binding_closure()
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
