"""Pure B profile views; no installed wiring or execution acceptance claims."""
import inspect
import json
from itertools import product
from types import SimpleNamespace

import pytest

from src.web import ordinary_capabilities as caps


ORIGINAL = 'ordinary-semantic-original-four-v1'
B1 = 'ordinary-semantic-b1-v1'
B2 = 'ordinary-semantic-b2-v1'
ERROR = 'ordinary_capabilities_unavailable'
DESCRIPTOR = {'provider': 'openai_compatible', 'model': 'fixture', 'mode': 'json'}
PAIRS = (
    ('property_calculator', 'property_calculator'),
    ('drug_likeness_assessment', 'drug_likeness_assessment'),
    ('activity_predictor', 'activity_predictor'),
    ('target_database_search', 'target_database_search'),
    ('admet_predictor', 'admet_prediction'),
    ('reverse_target_predictor', 'reverse_target'),
    ('rag_search', 'rag_retrieval'),
    ('llm_molecular_generator', 'molecule_generation'),
    ('candidate_ranker', 'molecule_ranking'),
)
FOUR = frozenset(tool for tool, _ in PAIRS[:4])
TOOLS = {B1: frozenset(tool for tool, _ in PAIRS[:7]),
         B2: frozenset(tool for tool, _ in PAIRS)}
# Literal pre-change catalog and enabled/disabled states, not production-derived.
ORIGINAL_ROWS = (
    ('ordinary_chat', 'Main-model qualitative conversation; not computed science.', True, True, 'readiness_unknown'),
    ('property_calculator', 'RDKit molecular properties; numerical results require the actual tool.', True, True, 'readiness_unknown'),
    ('drug_likeness_assessment', 'Rule-based drug-likeness assessment; not clinical efficacy.', True, True, 'readiness_unknown'),
    ('activity_predictor', 'Family activity prediction; registration is not proof of weights or readiness.', True, True, 'readiness_unknown'),
    ('target_database_search', 'Target lookup; registration is not proof of current database availability.', True, True, 'readiness_unknown'),
    ('molecule_generation', 'Separate local gmm molecular generation; B integration is not installed in this entry.', False, False, 'profile_not_supported'),
    ('admet_prediction', 'Product ADMET support does not imply all-method or real-backend availability.', False, False, 'profile_not_supported'),
    ('reverse_target', 'Reverse target search requires B source and ownership integration.', False, False, 'profile_not_supported'),
    ('molecule_ranking', 'Molecule ranking requires real accepted inputs and bindings, never a guessed ranking.', False, False, 'profile_not_supported'),
    ('rag_retrieval', 'RAG requires B1 actual retrieval and a source receipt.', False, False, 'profile_not_supported'),
    ('molecular_docking', 'Docking requires C structured inputs, consent and physical execution.', False, False, 'profile_not_supported'),
)
B_TEXT = {
    'molecule_generation': 'Separate local gmm molecular generation requires approved B2 controls; readiness is unknown.',
    'reverse_target': 'Reverse target hypotheses require same-invocation source receipts; readiness is unknown.',
    'rag_retrieval': 'Local molecular retrieval requires explicit RAG permission and same-invocation source receipts; readiness is unknown.',
}
EXTRAS = frozenset({'unknown_tool', 'alias_property_calculator', 'admet_prediction',
    'reverse_target', 'rag_retrieval', 'molecule_generation', 'molecule_ranking',
    'ordinary_chat', 'molecular_docking'})


def require_api():
    assert {'binding_profile', 'wired_names', 'rag_enabled'} <= set(
        inspect.signature(caps.build_capability_snapshot).parameters)


def legacy(**changes):
    args = dict(registered_names=FOUR, provider_descriptor=dict(DESCRIPTOR),
        semantic_profile=True, intent_capable=True, original_four_profile=True,
        scientific_tools=True, permitted_names=FOUR,
        model_generation='model-fixture', capability_generation='cap-fixture')
    args.update(changes)
    return caps.build_capability_snapshot(**args)


def bview(profile=B1, **changes):
    require_api()
    args = dict(binding_profile=profile, original_four_profile=False,
        registered_names=TOOLS[profile], wired_names=TOOLS[profile],
        permitted_names=TOOLS[profile], rag_enabled=True)
    args.update(changes)
    return legacy(**args)


def states(view):
    return {f.id: (f.wired, f.permitted, f.reason) for f in view.features}


def test_b1_rag_permission_is_independent():
    assert {'binding_profile', 'wired_names', 'rag_enabled'} <= set(
        inspect.signature(caps.build_capability_snapshot).parameters)
    view = caps.build_capability_snapshot(
        registered_names=frozenset({'rag_search'}),
        provider_descriptor={'provider': 'openai_compatible', 'model': 'fixture', 'mode': 'json'},
        semantic_profile=True, intent_capable=True, original_four_profile=False,
        scientific_tools=False, permitted_names=frozenset({'rag_search'}),
        model_generation='model-fixture', capability_generation='cap-fixture',
        binding_profile='ordinary-semantic-b1-v1',
        wired_names=frozenset({'rag_search'}), rag_enabled=True)
    features = {f.id: f for f in view.features}
    assert features['rag_retrieval'].wired and features['rag_retrieval'].permitted
    assert not any(f.permitted for key, f in features.items()
                   if key not in {'ordinary_chat', 'rag_retrieval'})
    assert all(f.readiness == 'unknown' for f in view.features)


def test_exact_api_and_immutable_catalogs():
    require_api()
    parameters = inspect.signature(caps.build_capability_snapshot).parameters
    required = ('registered_names', 'provider_descriptor', 'semantic_profile',
        'intent_capable', 'original_four_profile', 'scientific_tools',
        'permitted_names', 'model_generation', 'capability_generation')
    assert tuple(parameters) == required + ('binding_profile', 'wired_names', 'rag_enabled')
    assert all(p.kind == inspect.Parameter.KEYWORD_ONLY for p in parameters.values())
    assert all(parameters[name].default is inspect.Parameter.empty for name in required)
    assert parameters['binding_profile'].default == ORIGINAL
    assert parameters['wired_names'].default is None
    assert parameters['rag_enabled'].default is False
    assert caps.PROFILE_REVISION == ORIGINAL
    assert caps.CATALOG_REVISION == 'ordinary-product-v1'
    assert caps.B1_PROFILE_REVISION == B1 and caps.B2_PROFILE_REVISION == B2
    assert caps.B_CATALOG_REVISION == 'ordinary-product-b-v1'
    assert caps.ORIGINAL_FOUR == FOUR
    assert caps.B1_TOOLS == TOOLS[B1] and caps.B2_TOOLS == TOOLS[B2]
    assert type(caps.B1_TOOLS) is type(caps.B2_TOOLS) is frozenset
    assert caps.TOOL_FEATURE_PAIRS == PAIRS
    assert type(caps.TOOL_FEATURE_PAIRS) is tuple
    expected = tuple((n, d) for n, d, *_ in ORIGINAL_ROWS)
    assert caps.PRODUCT_CATALOG == expected
    assert caps.B_PRODUCT_CATALOG == tuple((n, B_TEXT.get(n, d)) for n, d in expected)
    for value in (caps.PRODUCT_CATALOG, caps.B_PRODUCT_CATALOG, caps.TOOL_FEATURE_PAIRS):
        assert type(value) is tuple and all(type(row) is tuple for row in value)
        with pytest.raises(TypeError):
            value[0][1] = 'changed'


def test_profile_flag_combinations_are_closed():
    require_api()
    for profile in (B1, B2):
        for semantic, original in product((False, True), repeat=2):
            args = dict(semantic_profile=semantic, original_four_profile=original)
            if semantic and not original:
                assert bview(profile, **args).profile_revision == profile
            else:
                with pytest.raises(ValueError, match='^' + ERROR + '$'):
                    bview(profile, **args)
        for wiring in (None, set(), [], (), {}):
            with pytest.raises(ValueError, match='^' + ERROR + '$'):
                bview(profile, wired_names=wiring)
        # Omission is distinct from an explicit, valid empty frozenset.
        with pytest.raises(ValueError, match='^' + ERROR + '$'):
            legacy(binding_profile=profile, original_four_profile=False)
        assert not any(f.wired for f in bview(profile, wired_names=frozenset()).features
                       if f.id != 'ordinary_chat')
    for selector in (None, '', 'b1', 'semantic_v1', 'ordinary-semantic-b1-v1 ',
                     'ordinary-semantic-b3-v1', 1, True, [], {}):
        with pytest.raises(ValueError, match='^' + ERROR + '$'):
            legacy(binding_profile=selector)
    for selector in ({}, {'binding_profile': ORIGINAL}):
        for extra in ({'wired_names': frozenset()}, {'wired_names': FOUR},
                      {'rag_enabled': True}, {'rag_enabled': 0}, {'rag_enabled': None}):
            with pytest.raises(ValueError, match='^' + ERROR + '$'):
                legacy(**selector, **extra)
    for profile in (ORIGINAL, B1, B2):
        builder = legacy if profile == ORIGINAL else lambda **kw: bview(profile, **kw)
        for flag in ('semantic_profile', 'intent_capable', 'original_four_profile',
                     'scientific_tools', 'rag_enabled'):
            for value in (0, 1, 'true', None, [], {}):
                with pytest.raises(ValueError, match='^' + ERROR + '$'):
                    builder(**{flag: value})


@pytest.mark.parametrize('profile', (B1, B2))
def test_b_canonical_mapping_and_wiring(profile):
    view = bview(profile)
    assert view.catalog_revision == 'ordinary-product-b-v1'
    assert view.profile_revision == profile
    assert [(f.id, f.product_description) for f in view.features] == [
        (n, B_TEXT.get(n, d)) for n, d, *_ in ORIGINAL_ROWS]
    for tool, feature in PAIRS:
        included = tool in TOOLS[profile]
        assert states(view)[feature] == ((True, True, 'readiness_unknown') if included
                                       else (False, False, 'profile_not_supported'))
        if not included:
            continue
        for registered, reviewed, reason in (
            (False, False, 'profile_not_supported'),
            (True, False, 'profile_not_supported'),
            (False, True, 'not_registered'),
            (True, True, 'readiness_unknown'),
        ):
            case = bview(profile, registered_names=frozenset({tool}) if registered else frozenset(),
                wired_names=frozenset({tool}) if reviewed else frozenset())
            expected = registered and reviewed
            assert states(case)[feature] == (expected, expected, reason), (tool, registered, reviewed)
            assert not any(f.wired for f in case.features if f.id not in {'ordinary_chat', feature})
            denied = bview(profile, registered_names=frozenset({tool}) if registered else frozenset(),
                wired_names=frozenset({tool}) if reviewed else frozenset(), permitted_names=frozenset())
            assert states(denied)[feature] == (expected, False,
                'permission_disabled' if expected else reason)
    assert states(view)['ordinary_chat'] == (True, True, 'readiness_unknown')
    assert states(view)['molecular_docking'] == (False, False, 'profile_not_supported')
    assert all(f.readiness == 'unknown' for f in view.features)


@pytest.mark.parametrize('profile', (B1, B2))
def test_b_permission_matrix(profile):
    for scientific, rag, intent in product((False, True), repeat=3):
        for omitted in (None, *sorted(TOOLS[profile])):
            allowed = TOOLS[profile] - {omitted}
            view = bview(profile, scientific_tools=scientific, rag_enabled=rag,
                         intent_capable=intent, permitted_names=allowed)
            for tool, feature in PAIRS:
                if tool not in TOOLS[profile]:
                    assert states(view)[feature] == (False, False, 'profile_not_supported')
                    continue
                permitted = tool in allowed and (rag if tool == 'rag_search' else scientific)
                assert states(view)[feature] == (True, permitted,
                    'readiness_unknown' if permitted else 'permission_disabled'), (tool, scientific, rag)
            assert states(view)['ordinary_chat'] == (intent, intent,
                'readiness_unknown' if intent else 'not_registered')
            assert all(f.readiness == 'unknown' for f in view.features)


@pytest.mark.parametrize('profile', (B1, B2))
def test_registry_extras_do_not_become_wiring_or_permission(profile):
    baseline = bview(profile)
    assert bview(profile, registered_names=TOOLS[B2] | EXTRAS) == baseline
    aliases_only = bview(profile, registered_names=EXTRAS)
    assert not any(f.wired for f in aliases_only.features if f.id != 'ordinary_chat')
    for extra in sorted(EXTRAS | (TOOLS[B2] - TOOLS[profile])):
        for field in ('wired_names', 'permitted_names'):
            with pytest.raises(ValueError, match='^' + ERROR + '$'):
                bview(profile, **{field: TOOLS[profile] | {extra}})
    # A feature name cannot register or grant its nonidentity canonical tool.
    for tool, feature in PAIRS:
        if tool == feature or tool not in TOOLS[profile]:
            continue
        assert states(bview(profile, registered_names=frozenset({feature})))[feature] == (
            False, False, 'not_registered')


@pytest.mark.parametrize('flags', tuple(product((False, True), repeat=4)))
def test_original_serialization_is_unchanged(flags):
    semantic, intent, original, scientific = flags
    changes = dict(semantic_profile=semantic, intent_capable=intent,
                   original_four_profile=original, scientific_tools=scientific)
    expected = dict(version='1', catalog_revision='ordinary-product-v1',
        profile_revision=ORIGINAL, capability_generation='cap-fixture',
        model_generation='model-fixture', provider_descriptor=dict(DESCRIPTOR), features=[])
    for name, description, wired, permitted, reason in ORIGINAL_ROWS:
        if name == 'ordinary_chat':
            if not semantic:
                wired, permitted, reason = False, False, 'profile_not_supported'
            elif not intent:
                wired, permitted, reason = False, False, 'not_registered'
        elif name in FOUR:
            if not original:
                wired, permitted, reason = False, False, 'profile_not_supported'
            elif not scientific:
                permitted, reason = False, 'permission_disabled'
        expected['features'].append(dict(id=name, product_description=description,
            wired=wired, permitted=permitted, readiness='unknown', reason=reason))
    default = legacy(**changes)
    assert default.model_dump(mode='json') == expected
    assert default.model_dump_json() == json.dumps(expected, separators=(',', ':'))
    assert legacy(**changes, registered_names=TOOLS[B2] | EXTRAS,
                  permitted_names=TOOLS[B2] | EXTRAS) == default
    require_api()
    assert legacy(**changes, binding_profile=ORIGINAL,
                  wired_names=None, rag_enabled=False).model_dump_json() == default.model_dump_json()


def test_original_absence_and_permission_reasons_are_pinned():
    for registered, permitted, reason in (
        (frozenset(), FOUR, 'not_registered'),
        (FOUR, frozenset(), 'permission_disabled'),
        (EXTRAS, EXTRAS, 'not_registered'),
    ):
        view = legacy(registered_names=registered, permitted_names=permitted)
        for name in FOUR:
            assert states(view)[name] == (name in registered, False, reason)


@pytest.mark.parametrize('profile', (B1, B2))
def test_strict_names_and_existing_bounds(profile):
    require_api()
    invalid_sets = (set(), [], (), {}, None, frozenset({1}), frozenset({''}),
        frozenset({'a' * 129}), frozenset('n' + str(i) for i in range(257)),
        frozenset({'password=synthetic'}),
        frozenset(('界' * 125) + str(i) for i in range(128)))
    for field in ('registered_names', 'wired_names', 'permitted_names'):
        for value in invalid_sets:
            with pytest.raises(ValueError, match='^' + ERROR + '$'):
                bview(profile, **{field: value})
    # Legal registry extras retain both existing count and per-name boundaries.
    assert bview(profile, registered_names=frozenset('n' + str(i) for i in range(256)))
    assert bview(profile, registered_names=frozenset({'n' * 128}))
    for field in ('model_generation', 'capability_generation'):
        for value in (True, '', 'a' * 129, 'password=synthetic'):
            with pytest.raises(ValueError, match='^' + ERROR + '$'):
                bview(profile, **{field: value})
    for descriptor in (None, [], {**DESCRIPTOR, 'extra': 'x'},
        {**DESCRIPTOR, 'model': 'a' * 257}, {**DESCRIPTOR, 'mode': 'auto'},
        {**DESCRIPTOR, 'provider': 'https://example.invalid'},
        {**DESCRIPTOR, 'model': 'password=synthetic'},
        {**DESCRIPTOR, 'model': '界' * 256, 'provider': '界' * 128}):
        with pytest.raises(ValueError, match='^' + ERROR + '$'):
            bview(profile, provider_descriptor=descriptor)


@pytest.mark.parametrize('profile', (B1, B2))
def test_builder_is_pure_and_detached(profile, monkeypatch):
    import builtins
    import io
    import os
    import socket
    from src.agent.tooling.registry import ToolRegistry
    from src.agent.contracts import ordinary_admission as admission

    descriptor = dict(DESCRIPTOR)
    view = bview(profile, provider_descriptor=descriptor)  # Warm lazy contract imports.
    descriptor['model'] = 'changed'
    detached = view.model_dump(mode='json')
    detached['provider_descriptor']['model'] = 'changed'
    detached['features'][0]['wired'] = False
    assert view.provider_descriptor.model == 'fixture' and view.features[0].wired
    for obj, field, value in ((view, 'profile_revision', ORIGINAL),
        (view.features[0], 'readiness', 'ready'), (view.provider_descriptor, 'model', 'changed')):
        with pytest.raises(ValueError):
            setattr(obj, field, value)
    assert type(view.features) is tuple
    assert admission.parse_capability_snapshot(view.model_dump_json()) == view

    def forbidden(*args, **kwargs):
        pytest.fail('pure projection invoked I/O, registry or health hook')

    with monkeypatch.context() as guard:
        for owner, names in ((builtins, ('open',)), (io, ('open',)),
            (os, ('getenv', 'stat', 'listdir', 'scandir')),
            (socket, ('create_connection',)),
            (ToolRegistry, ('__init__', 'as_mapping', 'resolve', 'by_capability',
                            'resolve_capability', 'health'))):
            for name in names:
                guard.setattr(owner, name, forbidden)
        guard.setattr(os, 'environ', SimpleNamespace(get=forbidden, __getitem__=forbidden))
        assert bview(profile) == view
    assert all(f.readiness == 'unknown' for f in view.features)


@pytest.mark.parametrize('profile', (B1, B2))
def test_b_snapshot_schema_bounds_remain_closed(profile):
    from src.agent.contracts import ordinary_admission as admission
    view = bview(profile)
    for fault in ('extra', 'bool', 'reason', 'duplicate', '33', 'size', 'secret', 'nan'):
        data = view.model_dump(mode='json')
        if fault == 'extra':
            data['execution_authority'] = True
        elif fault == 'bool':
            data['features'][0]['wired'] = 1
        elif fault == 'reason':
            data['features'][0]['reason'] = 'not_wired'
        elif fault == 'duplicate':
            data['features'].append(dict(data['features'][0]))
        elif fault == '33':
            data['features'] *= 3
        elif fault == 'size':
            for feature in data['features']:
                feature['product_description'] = 'a' * 2000
        elif fault == 'secret':
            data['provider_descriptor']['model'] = 'password=synthetic'
        else:
            data['model_generation'] = float('nan')
        with pytest.raises(ValueError, match='^' + ERROR + '$'):
            admission.parse_capability_snapshot(json.dumps(data))
    with pytest.raises(ValueError):
        admission.CapabilitySnapshot(**view.model_dump(mode='json'))


@pytest.mark.parametrize('profile', (B1, B2))
def test_b_view_does_not_open_loop_admission(profile):
    from src.agent.contracts import ordinary_admission as admission

    def carry(view):
        binding = admission.build_admission_binding(view, query='hello', history=[],
            assessment_revision='whole-request-v1', intent_kind='known_chat', intent_requests=0)
        return admission.AdmissionCarryIn(segment=admission.ActiveSegment(10.0, 30.0, 40.0),
            intent_requests=0, intent_record_json=None, binding_json=json.dumps(binding),
            capability_json=view.model_dump_json(), resume_expires_at=None)

    context = SimpleNamespace(query='hello', memory=[], trace_id='fixture-trace')
    kwargs = dict(context=context, request_kind='chat', timeout_seconds=30.0)
    # Positive control uses the same carry and context, isolating the profile gate.
    original_carry = carry(legacy())
    assert admission.loop_admission(original_carry, **kwargs) == original_carry
    view = bview(profile, scientific_tools=False, rag_enabled=True)
    b_carry = carry(view)
    assert b_carry.capability_snapshot() == view
    admission.validate_carry_in(b_carry, capability_snapshot=view, query='hello',
                               history=[], assessment_revision='whole-request-v1')
    with pytest.raises(ValueError, match='^ordinary_admission_invalid$'):
        admission.loop_admission(b_carry, **kwargs)
