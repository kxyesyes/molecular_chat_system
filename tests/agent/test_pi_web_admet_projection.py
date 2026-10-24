"""Synthetic ADMET-AI envelopes test presentation, not real model predictions."""
from copy import deepcopy
import json
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from src.agent.contracts import ToolProvenance, ToolResult
from src.agent.scientific_session.decision_binding_acceptance import _scientific_data as b1_data
from src.agent.scientific_session.web_acceptance import _scientific_data, web_observation_reasons
from src.agent.tooling.analysis_contract import ADMETOutput, _ADMET_SECTIONS
from test_analysis_contract import analysis_rows


def ai_result():
    admet = dict(
        prediction_method='admet_ai', backend_version='synthetic-test',
        model_name='ADMET-AI', model_version='1.4.0', weights_id='sha256:' + 'a' * 64,
        demo_mode=False, fallback_used=False, physicochemical_source='synthetic-fixture',
        endpoints={
            'synthetic_risk': dict(value=0.8, unit='probability', task_type='classification',
                source='admet_ai_model', name='Synthetic risk', category='synthetic'),
            'synthetic_descriptor': dict(value=46.0, unit='g/mol', task_type='regression',
                source='rdkit_calculation'),
        },
        units={'synthetic_risk': 'probability', 'synthetic_descriptor': 'g/mol'},
        risk_count=1, total_endpoints=2, risk_endpoint_ids=['synthetic_risk'],
        risk_threshold=0.5, risk_summary_method='synthetic_probability_threshold',
    )
    return ToolResult('admet_predictor', True, 'Synthetic test fixture',
        data=[dict(smiles='CCO', admet=admet)])


def test_schema_valid_ai_without_legacy_sections_publishes_exact_observations():
    result = ai_result()
    ADMETOutput.model_validate(vars(result), strict=True)
    original = deepcopy(result)
    assert not set(_ADMET_SECTIONS) & result.data[0]['admet'].keys()
    projected = _scientific_data(result)
    assert projected == result.data
    assert result == original
    assert 'name' not in projected[0]['admet']['endpoints']['synthetic_descriptor']
    # Returned containers cannot mutate the authenticated source observation.
    projected[0]['admet']['endpoints']['synthetic_risk']['value'] = 0.0
    projected[0]['admet']['units']['synthetic_risk'] = 'changed'
    projected[0]['admet']['risk_endpoint_ids'].clear()
    assert result == original


def test_ai_allowlist_excludes_raw_weights_and_opaque_extensions_at_every_level():
    result = ai_result()
    row = result.data[0]
    admet = row['admet']
    hidden = {'weights': [901.125], 'debug': 'PRIVATE_EXTENSION_MARKER'}
    row.update(deepcopy(hidden))
    admet.update(deepcopy(hidden))
    admet['ranking_weights'] = {'admet': 0.95}
    admet['endpoints']['synthetic_risk'].update(deepcopy(hidden))
    admet['units']['debug'] = deepcopy(hidden)
    admet['units']['undeclared_endpoint'] = 'PRIVATE_EXTENSION_MARKER'
    ADMETOutput.model_validate(vars(result), strict=True)
    original = deepcopy(result)
    projected = _scientific_data(result)
    assert projected == ai_result().data
    serialized = json.dumps(projected)
    assert 'PRIVATE_EXTENSION_MARKER' not in serialized
    assert '901.125' not in serialized and 'ranking_weights' not in serialized
    assert '"weights"' not in serialized and '"debug"' not in serialized
    # A provenance digest is public metadata, never the actual model weights.
    assert projected[0]['admet']['weights_id'] == admet['weights_id']
    assert result == original


def test_ai_optional_legacy_sections_keep_only_observed_scalar_claims():
    result = ai_result()
    result.data[0]['admet'].update(
        physicochemical={'molecular_weight': 46.0, 'debug': {'weights': [99]}},
        medicinal={'pains': None, 'leadlikeness': {'debug': 'private'}},
    )
    ADMETOutput.model_validate(vars(result), strict=True)
    projected = _scientific_data(result)[0]['admet']
    assert projected['physicochemical'] == {'molecular_weight': 46.0}
    assert projected['medicinal'] == {'pains': None}
    assert 'solubility' not in projected and 'lipophilicity' not in projected


@pytest.mark.parametrize('method', ['rdkit_rules', 'adme_py'])
def test_legacy_admet_retains_b1_projection_alone_and_in_mixed_batch(method):
    legacy = ToolResult('admet_predictor', True, 'Deterministic rule fixture',
        data=analysis_rows('admet_predictor'))
    if method == 'adme_py':
        legacy.data[0]['admet'] = dict(prediction_method=method,
            backend_version='synthetic-test', **{name: {} for name in _ADMET_SECTIONS})
        legacy.data[0]['admet']['medicinal'] = dict(pains=None, brenk=None, zinc=None)
    ADMETOutput.model_validate(vars(legacy), strict=True)
    expected = b1_data(legacy)
    assert _scientific_data(legacy) == expected
    mixed = ai_result()
    mixed.data = legacy.data + mixed.data
    original = deepcopy(mixed)
    ADMETOutput.model_validate(vars(mixed), strict=True)
    assert _scientific_data(mixed) == expected + ai_result().data
    assert mixed == original


@pytest.mark.parametrize('value', [True, '0.8', float('nan'), float('inf')])
def test_ai_projection_does_not_coerce_or_publish_invalid_endpoint_values(value):
    result = ai_result()
    result.data[0]['admet']['endpoints']['synthetic_risk']['value'] = value
    with pytest.raises(ValidationError):
        ADMETOutput.model_validate(vars(result), strict=True)
    with pytest.raises(ValidationError):
        _scientific_data(result)


@pytest.mark.parametrize('corrupt', [
    lambda a: a['endpoints']['synthetic_risk'].update(source='unknown'),
    lambda a: a['endpoints']['synthetic_risk'].update(unit=1),
    lambda a: a['units'].update(synthetic_risk='mismatched'),
    lambda a: a.update(risk_count=True),
    lambda a: a.update(risk_count=3),
    lambda a: a.update(risk_threshold=2.0),
    lambda a: a.update(risk_endpoint_ids=[{}]),
    lambda a: a.pop('risk_summary_method'),
    lambda a: a.update(demo_mode=True),
    lambda a: a.update(weights_id='not-a-digest'),
])
def test_ai_projection_rejects_invalid_units_source_risk_or_model_provenance(corrupt):
    result = ai_result()
    corrupt(result.data[0]['admet'])
    with pytest.raises(ValidationError):
        ADMETOutput.model_validate(vars(result), strict=True)
    with pytest.raises(ValidationError):
        _scientific_data(result)


def observation_reasons(result):
    result.provenance = ToolProvenance(tool_name='admet_predictor')
    result.quality['evidence_id'] = 'synthetic-admet-evidence'
    return web_observation_reasons(result, {'input_data': {'query': {'smiles': ['CCO']}}},
        SimpleNamespace(), request_query='Assess ADMET for CCO')


def test_schema_valid_ai_endpoints_count_as_observations_without_legacy_sections():
    result = ai_result()
    ADMETOutput.model_validate(vars(result), strict=True)
    assert observation_reasons(result) == []


@pytest.mark.parametrize('endpoints', [{}, {'synthetic': {'value': 0.8}}])
def test_empty_or_unvalidated_ai_endpoints_cannot_satisfy_web_acceptance(endpoints):
    result = ai_result()
    result.data[0]['admet']['endpoints'] = endpoints
    assert 'invalid_scientific_observation' in observation_reasons(result)


def test_empty_legacy_sections_still_do_not_count_as_observations():
    result = ai_result()
    result.data[0]['admet'] = dict(prediction_method='adme_py',
        backend_version='synthetic-test', **{name: {} for name in _ADMET_SECTIONS})
    ADMETOutput.model_validate(vars(result), strict=True)
    assert observation_reasons(result) == ['no_admet_observations']
