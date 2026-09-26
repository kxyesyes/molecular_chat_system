"""Deterministic B1 acceptance over the actual resolver/Session, never v1 history.

Call through settle_owned_call, checking the root deadline on both sides. Source
freshness checks may hash files; this module neither owns workers nor executes
science. A report is not terminal publication authority: the owning loop must
recheck at its publication barriers. No model text, repair or status coercion.
"""
import json
from types import SimpleNamespace

from pydantic import TypeAdapter

from src.agent.contracts.binding_requirements import required_binding_tools
from src.agent.contracts.decision import EvidenceId
from src.agent.contracts.decision_bindings import B1_PROFILE_REVISION, B1_TOOLS
from .decision_bindings import B1BindingResolver
from .decision_bounds import validate_json
from .decision_policy import DecisionBoundaryError, family_review_observation, usable
from .decision_requirements import _canonical, _check_batch


_ERROR = 'invalid_binding_acceptance'
_EVIDENCE_ID = TypeAdapter(EvidenceId)
_BATCH_TOOLS = B1_TOOLS - {'target_database_search', 'rag_search', 'reverse_target_predictor'}


def _arguments(resolver, required_tools, evidence_ids):
    # Check exact native types and lengths BEFORE iteration, set/hash or copies.
    if (type(resolver) is not B1BindingResolver
            or type(required_tools) not in (list, tuple, set, frozenset)
            or len(required_tools) > 32):
        raise DecisionBoundaryError(_ERROR)
    if any(type(name) is not str or not 1 <= len(name) <= 64 for name in required_tools):
        raise DecisionBoundaryError(_ERROR)
    if len(set(required_tools)) != len(required_tools) or not set(required_tools) <= B1_TOOLS:
        raise DecisionBoundaryError(_ERROR)
    if evidence_ids is not None:
        if type(evidence_ids) is not list or len(evidence_ids) > 128:
            raise DecisionBoundaryError(_ERROR)
        if any(type(value) is not str or not 1 <= len(value) <= 128 for value in evidence_ids):
            raise DecisionBoundaryError(_ERROR)
        try:
            for value in evidence_ids:
                _EVIDENCE_ID.validate_python(value, strict=True)
        except ValueError:
            raise DecisionBoundaryError(_ERROR) from None
        if len(set(evidence_ids)) != len(evidence_ids):
            raise DecisionBoundaryError(_ERROR)


def _groups(requirements):
    groups = {g.tool_name: g for g in (*requirements.molecular_results, *requirements.analysis_results)}
    groups.update({name: getattr(requirements, field) for name, field in (
        ('rag_search', 'retrieval_result'), ('reverse_target_predictor', 'reverse_result'),
        ('target_database_search', 'target_result'))})
    return groups


def _typed(result):
    """Non-projecting validation only; never call the mutating ResultValidator."""
    from src.agent.tooling.analysis_contract import ANALYSIS_OUTPUT_SCHEMAS
    from src.agent.tooling.activity_contract import ActivityPredictOutput
    from src.agent.tooling.rag_contract import RAGSearchOutput
    from src.agent.tooling.target_contract import TargetSearchOutput, ReverseTargetOutput
    schemas = dict(ANALYSIS_OUTPUT_SCHEMAS, activity_predictor=ActivityPredictOutput,
        rag_search=RAGSearchOutput, target_database_search=TargetSearchOutput,
        reverse_target_predictor=ReverseTargetOutput)
    schemas[result.tool_name].model_validate(vars(result))


def _batch_reasons(result, record, group):
    name = result.tool_name
    payload = record['input_data']['query']
    # The authenticated resolver already constructed these complete inputs.
    values = payload['smiles'] if name == 'activity_predictor' else payload.split('\n')
    actual = SimpleNamespace(tool_name=name, exact_molecule_count=len(values),
                             expected_smiles=values, required_metrics=())
    reasons = _check_batch(actual, result)['reason_codes']
    if group is not None:
        required = SimpleNamespace(tool_name=name,
            exact_molecule_count=group.exact_molecule_count,
            expected_smiles=group.expected_smiles,
            required_metrics=getattr(group, 'required_metrics', ()))
        reasons += _check_batch(required, result)['reason_codes']
    return reasons


def _activity_reasons(result, record, group):
    from src.activity.family_contract import resolve_activity_family
    from src.target_identifiers import canonical_target_identifier
    from src.agent.validators.domain_validators import ActivityResultValidator
    if ActivityResultValidator().validate(result):
        return ['invalid_activity_evidence']
    target = record['input_data']['query']['target']
    targets = [target] + ([group.target] if group is not None and group.target is not None else [])
    for row in result.data:
        if 'family_id' in row:
            try:
                family = resolve_activity_family(target)
                if (row['family_id'] != family
                        or resolve_activity_family(row.get('requested_target')) != family
                        or any(resolve_activity_family(t) != family for t in targets)):
                    return ['activity_target_mismatch']
            except ValueError:
                return ['activity_target_mismatch']
        else:
            # target_id is opaque in the legacy activity output contract. It
            # cannot establish a trained target merely by matching the request.
            # Reuse the existing complete endpoint contract, never load a
            # registry/card/weights or invent/backfill missing model evidence.
            metadata = row.get('model_provenance', {})
            if metadata.get('scientific_readiness') != 'endpoint_ready':
                return ['insufficient_target_evidence']
            from src.activity.model_registry import validate_endpoint_metadata
            try:
                observed = validate_endpoint_metadata(metadata)['target_id']
            except (ValueError, TypeError, KeyError):
                return ['insufficient_target_evidence']
            if canonical_target_identifier(observed) is None:
                return ['insufficient_target_evidence']
            if any(canonical_target_identifier(observed) != canonical_target_identifier(t) for t in targets):
                return ['activity_target_mismatch']
    return []


def _eligibility(result, record, group=None, *, review=False):
    if not (family_review_observation(result) if review else usable(result)):
        return ['observation_not_usable']
    try:
        _typed(result)
        name = result.tool_name
        reasons = _batch_reasons(result, record, group) if name in _BATCH_TOOLS else []
        if name == 'activity_predictor':
            reasons += _activity_reasons(result, record, group)
            if not review and not all(row.get('success') is True for row in result.data):
                reasons.append('incomplete_activity_batch')
        elif name == 'admet_predictor':
            from src.agent.tools.admet_predictor import ADMETPredictor
            from src.agent.validators.domain_validators import ADMETResultValidator
            if ADMETResultValidator().validate(result):
                reasons.append('invalid_admet_evidence')
            if not all(ADMETPredictor._has_observations(row['admet']) for row in result.data):
                reasons.append('no_admet_observations')
        elif name == 'rag_search':
            # verify_binding_closure has already required current own_source,
            # exact original query/k3 and strict valid_hits/valid_empty receipt.
            if group is not None:
                if record['input_data']['query'] != group.query:
                    reasons.append('retrieval_query_mismatch')
                if group.require_hits and not result.data:
                    reasons.append('required_retrieval_hits_missing')
        elif name == 'reverse_target_predictor':
            # The strict hook checks exactly one action-input SMILES against
            # the original receipt, not normalized rows or an equal-query guess.
            if group is not None and _canonical(record['input_data']['query']) != _canonical(group.expected_smiles):
                reasons.append('subject_mismatch')
        elif name == 'target_database_search':
            status = result.quality.get('lookup_status')
            if status not in ('resolved', 'not_found'):
                reasons.append('insufficient_target_lookup_evidence')
            if group is not None:
                from_reverse = record['arguments']['input_ref'] != 'user'
                if from_reverse != (group.input == 'reverse'):
                    reasons.append('target_input_mismatch')
                if not from_reverse and record['input_data']['query'] != group.query:
                    reasons.append('target_input_mismatch')
                if group.require_resolved and status != 'resolved':
                    reasons.append('target_not_resolved')
        return sorted(set(reasons))
    except (ValueError, TypeError, KeyError, AttributeError):
        # Typed/domain failures have fixed public codes, never raw payloads.
        return ['invalid_scientific_observation']


def _detached(value):
    validate_json(value, max_bytes=65536, reason=_ERROR)
    return json.loads(json.dumps(value, ensure_ascii=False, allow_nan=False))


def _evaluate(resolver, required_tools, evidence_ids):
    _arguments(resolver, required_tools, evidence_ids)
    # Validate all actual observations, including unselected diagnostics and
    # current admitted head, before consulting their name/status or records.
    all_ids = resolver.verify_binding_closure()
    cited = [] if evidence_ids is None else resolver.verify_binding_closure(evidence_ids)
    records = resolver.export_records()
    results = {r.quality['evidence_id']: r for r in resolver.session.results}
    requirements = resolver.requirements  # immutable original, hash-checked above
    groups = _groups(requirements)
    required = required_binding_tools(requirements, required_tools)
    scope = set(all_ids if evidence_ids is None else cited)
    intrinsic = {identity: _eligibility(result, records[result.quality['step_id']])
                 for identity, result in results.items()}
    eligibility = {identity: _eligibility(result, records[result.quality['step_id']], groups.get(result.tool_name))
                   for identity, result in results.items()}
    checks, selected = [], []
    for name in sorted(required):
        candidates = [identity for identity in all_ids if results[identity].tool_name == name]
        qualifying = [identity for identity in candidates if not eligibility[identity]]
        chosen = next((identity for identity in qualifying if identity in scope), None)
        reasons = []
        if chosen is not None:
            selected.append(chosen)
        elif qualifying:
            reasons = ['missing_required_citation']
        elif candidates:
            reasons = sorted({code for identity in candidates for code in eligibility[identity]})
        else:
            reasons = ['no_usable_observation']
        checks.append(dict(tool_name=name, passed=chosen is not None,
                           reason_codes=reasons, evidence_ids=[chosen] if chosen else []))
    forbidden = sorted({r.tool_name for r in results.values()} & set(requirements.forbidden_tools))
    missing = [c['tool_name'] for c in checks if not c['passed']]
    reasons = {code for c in checks for code in c['reason_codes']}
    if forbidden:
        reasons.add('executed_forbidden_tool')
    if evidence_ids is not None and not evidence_ids:
        reasons.add('scientific_citations_required')
    # Optional failures/review remain partial; citation selection cannot hide
    # them. This does not alter any scientific status or weaken an obligation.
    if any(intrinsic.values()):
        reasons.add('incomplete_scientific_observations')
    satisfied = not missing and not forbidden
    required_ids = resolver.verify_binding_closure(selected)
    report = dict(version='2', profile=B1_PROFILE_REVISION, satisfied=satisfied,
        checks=checks, missing_required_tools=missing, executed_forbidden_tools=forbidden,
        reason_codes=sorted(reasons), cited_closure_ids=cited, required_evidence_ids=required_ids,
        citation_checked=evidence_ids is not None,
        finish_eligible=bool(satisfied and evidence_ids and not any(intrinsic.values())))
    return _detached(report), results, records, intrinsic, groups


def evaluate_binding_acceptance(resolver, *, required_tools=(), evidence_ids=None):
    """Return bounded native progress, or explicitly citation-checked acceptance.

    None is progress ONLY. [] cannot finish science. Invalid native arguments,
    seals, ownership, head or current source raise; no provisional report leaks.
    Satisfied describes required obligations; finish_eligible additionally needs
    nonempty citations and no failed/partial/insufficient optional observations.
    """
    return _evaluate(resolver, required_tools, evidence_ids)[0]


def _pick(value, fields):
    # These allowlists describe leaves only. Optional untyped extensions must
    # never smuggle nested debug payloads through a familiar scalar field name.
    return {key: value[key] for key in fields if key in value
            and (value[key] is None or type(value[key]) in (str, bool, int, float))}


# Existing producer diagnostics, not a heuristic for interpreting arbitrary
# prose as safe science. Unknown uncited text is explicitly withheld, including
# spelled-out numeric predictions. Do not mine numbers or classify with an LLM.
_DIAGNOSTIC_WARNINGS = frozenset({
    'partial_authoritative_results', 'authoritative_normalization_failed',
    'authoritative_identity_mismatch', 'authoritative_lookup_failed',
    'stale_authoritative_cache',
    '分类与回归预测不一致，需复核；已保留两项原始结果。',
    'RAG retrieval contained invalid results; accepted rows are diagnostic partial data only.',
})


def _display_text(value):
    from src.agent.persistence.redaction import sanitize_sensitive_text
    return sanitize_sensitive_text(value, max_chars=8192)


def _warnings(values, *, diagnostic_only=False):
    """Preserve safe diagnostics without copying hidden scientific prose."""
    if type(values) is not list:
        return [], int(values is not None)
    warnings, omitted = [], 0
    for value in values:
        if type(value) is not str:
            omitted += 1
            continue
        if diagnostic_only and value not in _DIAGNOSTIC_WARNINGS:
            omitted += 1
            if value.startswith('Optional step '):
                warning = 'Optional tool step failed; uncited details withheld.'
            else:
                warning = 'Warning details withheld outside verified cited scientific evidence.'
        else:
            warning, changed = _display_text(value)
            omitted += int(changed)
        if warning not in warnings:
            warnings.append(warning)
    return warnings, omitted


def _provenance(result, *, cited):
    from src.agent.contracts import ToolProvenance
    if result.provenance is None:
        return None
    try:
        value = ToolProvenance.from_dict(vars(result.provenance)).to_dict()
    except ValueError:
        raise DecisionBoundaryError(_ERROR) from None
    if not cited:
        # Free-text model labels could themselves contain provisional claims.
        value = _pick(value, ('tool_name', 'tool_version', 'demo_mode',
                              'fallback_used', 'input_digest', 'output_digest'))
    changed = False
    for key, item in value.items():
        if type(item) is str:
            value[key], sanitized = _display_text(item)
            changed |= sanitized
    if changed:
        value['presentation_sanitized'] = True
    return value


def _artifacts(result):
    """Observed descriptors only, not file existence/content certification.

    Called only for verified cited usable/review observations. Opaque metadata
    is not a scientific contract and must not expose debug/provisional payloads.
    This projection performs no path resolution, file access or URL fetching.
    """
    artifacts = []
    for artifact in result.artifacts:
        value = _pick(vars(artifact), ('artifact_type', 'path', 'label', 'mime_type'))
        changed = False
        for key, item in value.items():
            if type(item) is str:
                value[key], sanitized = _display_text(item)
                changed |= sanitized
        if changed:
            value['presentation_sanitized'] = True
        artifacts.append(value)
    return artifacts


def _scientific_data(result):
    """Allowlisted observed values only; no model prose/debug/extensions/defaults."""
    rows = []
    for row in result.data or []:
        name = result.tool_name
        item = _pick(row, ('smiles',))
        if name == 'property_calculator':
            item['properties'] = _pick(row['properties'], ('molecular_formula', 'molecular_weight',
                'logp', 'tpsa', 'hbd', 'hba', 'qed', 'rotatable_bonds'))
        elif name == 'drug_likeness_assessment':
            assessment = row['assessment']
            item['assessment'] = dict(qed_score=assessment['qed_score'],
                molecular_properties=_pick(assessment['molecular_properties'], ('molecular_weight',
                    'logp', 'tpsa', 'hbd', 'hba', 'rotatable_bonds', 'aromatic_rings', 'heteroatoms')),
                lipinski_rule_of_five=_pick(assessment['lipinski_rule_of_five'], ('compliance', 'violation_count')))
        elif name == 'admet_predictor':
            from src.agent.tooling.analysis_contract import _ADMET_SECTIONS
            value = row['admet']
            item['admet'] = _pick(value, ('prediction_method', 'backend_version'))
            for section, fields in _ADMET_SECTIONS.items():
                # Dict-valued rule extras have no closed leaf claim contract.
                item['admet'][section] = {key: value[section][key] for key in fields
                    if key in value[section] and type(value[section][key]) is not dict}
        elif name == 'activity_predictor':
            family = 'family_id' in row
            fields = ('success', 'status', 'execution_status', 'requested_target',
                'family_id', 'bundle_id', 'units',
                'predicted_pIC50', 'activity_probability', 'activity_class', 'label_threshold',
                'probability_threshold', 'classification_regression_consistent') if family else (
                'success', 'task_type', 'endpoint', 'units', 'value', 'probability')
            item.update(_pick(row, fields))
            if family and 'errors' in row:
                item['errors'] = {stage: _display_text(error)[0] for stage, error in row['errors'].items()}
            if family and 'warnings' in row:
                item['warnings'], item['warning_details_omitted'] = _warnings(row['warnings'])
            if family and 'provenance' in row:
                p = row['provenance']
                item['provenance'] = _pick(p, ('bundle_id', 'source_sha256'))
                if 'models' in p:
                    item['provenance']['models'] = {task: _pick(value, ('model_id', 'weights_sha256',
                        'model_card_sha256', 'prepared_dataset_sha256', 'task_type', 'target_id',
                        'demo_mode', 'fallback_used')) for task, value in p['models'].items()
                        if task in ('classification', 'regression')}
            if 'model_provenance' in row:
                item['model_provenance'] = _pick(row['model_provenance'], ('model_id', 'weights_sha256',
                    'task_type', 'endpoint', 'units', 'target_id', 'demo_mode', 'fallback_used',
                    'scientific_readiness', 'endpoint_key', 'label_transform', 'dataset_sha256',
                    'prepared_dataset_sha256', 'model_card_sha256'))
        elif name == 'rag_search':
            item.update(_pick(row, ('SMILES', 'source_index', 'similarity_score')))
            item['provenance'] = _pick(row['provenance'], ('source_sha256', 'index_sha256',
                'embedding_model', 'manifest_schema_version', 'builder_version', 'vector_label'))
        else:
            item.update(_pick(row, ('target_name', 'target_gene', 'gene_symbol', 'protein_name',
                'uniprot_id', 'organism', 'target_identifier', 'source', 'source_record_id',
                'final_similarity', 'morgan_similarity', 'maccs_similarity', 'similar_count',
                'structure_count', 'structure_evidence_status', 'stale')))
        rows.append(item)
    return rows if result.data is not None else None


def render_binding_scientific_answer(resolver, *, required_tools=(), evidence_ids):
    """Deterministic scoped JSON text, including honest partial/review outcomes.

    Never accepts model prose. Rendering a partial result does not authorize a
    successful finish. Uncited successes are omitted; uncited failures retain
    safe diagnostics/binding provenance, never data/artifacts or arbitrary prose.
    """
    if evidence_ids is None:
        raise DecisionBoundaryError(_ERROR)
    report, results, records, eligibility, groups = _evaluate(resolver, required_tools, evidence_ids)
    cited = set(report['cited_closure_ids'])
    blocks = []
    for identity, result in results.items():
        if identity not in cited and usable(result):
            continue
        review = identity in cited and not _eligibility(result, records[result.quality['step_id']],
                                                        groups.get(result.tool_name), review=True)
        allowed = identity in cited and not eligibility[identity]
        block = dict(tool=result.tool_name, evidence_id=identity, status=result.status.value,
                     scientific_usable=allowed, error=result.error.code.value if result.error else None,
                     provenance=_provenance(result, cited=identity in cited),
                     artifacts=_artifacts(result) if allowed or review else [])
        block['warnings'], block['warning_details_omitted'] = _warnings(
            result.warnings, diagnostic_only=not (allowed or review))
        if allowed or review:
            block['data'] = _scientific_data(result)
        if review:
            block['review_message'] = 'Family classification/regression disagreement: partial, requires review.'
        if result.tool_name == 'target_database_search' and identity in cited:
            block['lookup_status'] = result.quality.get('lookup_status')
        blocks.append(block)
    payload = dict(acceptance=report, observations=blocks,
        limitations=['Computed/predicted/retrieved evidence is not experimental validation.',
                     'ADMET available_methods is method-scoped; unknown alerts remain unknown.',
                     'Artifact descriptors are observed references, not file content/existence verification.'])
    validate_json(payload, max_bytes=65536, reason=_ERROR)
    # Publication owners still recheck their own subsequent persistence barriers.
    resolver.verify_binding_closure()
    return json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True)
