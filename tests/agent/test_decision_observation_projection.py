"""The decision model receives a bounded view, while the UI keeps full evidence."""

import json
from copy import deepcopy

from src.system.scientific_contracts import ToolResult, ToolProvenance
from src.agent.scientific_session.decision_policy import model_observation


def test_model_observation_omits_presentation_payload_but_keeps_evidence_identity():
    result = ToolResult.success_result(
        "property_calculator",
        data={"smiles": "CCO", "properties": {"molecular_weight": 46.07}},
        formatted="human-facing table " + "x" * 20000,
        evidence=[{"source": "RDKit", "id": "source-1", "url": "https://example.test"}],
        artifacts=[],
        quality={"evidence_id": "evidence-1", "scientific_usable": True},
        provenance=ToolProvenance(
            tool_name="property_calculator", tool_version="1", input_digest="in", output_digest="out"
        ),
    )

    projected = model_observation(result, {"finish_eligible": False})

    assert projected["tool_name"] == "property_calculator"
    assert projected["data"] == result.data
    assert projected["quality"]["evidence_id"] == "evidence-1"
    assert projected["provenance"]["tool_name"] == "property_calculator"
    assert "formatted" not in projected
    assert "evidence" not in projected
    assert "artifacts" not in projected


def test_web_model_observation_drops_target_structure_payload():
    result = ToolResult.success_result(
        "target_database_search",
        data=[{
            "gene_symbol": "PDE5A",
            "target_name": "phosphodiesterase",
            "source_record_id": "LOCAL_PDE5A",
            "recommended_structures": [{
                "structure_id": f"1ABC-{index}",
                "download_url": "https://files.example.test/" + "x" * 4000,
                "local_file_path": "data/target_db/cache/" + "x" * 4000,
            } for index in range(8)],
        }],
        quality={"evidence_id": "evidence-target", "lookup_status": "resolved"},
    )

    projected = model_observation(result, {
        "profile": "medchat-web-v1", "finish_eligible": False,
    })

    assert projected["data"] == [{
        "gene_symbol": "PDE5A",
        "target_name": "phosphodiesterase",
        "source_record_id": "LOCAL_PDE5A",
    }]
    assert len(json.dumps(projected, ensure_ascii=False).encode()) < 4096


def test_web_admet_projection_preserves_every_endpoint_in_a_lossless_table():
    endpoints = {f'endpoint_{index}': {
        'value': index / 100, 'unit': '-', 'task_type': 'classification',
        'category': 'Toxicity', 'name': f'Endpoint {index}', 'source': 'admet_ai_model',
    } for index in range(49)}
    admet = {'endpoints': endpoints, 'units': {key: row['unit'] for key, row in endpoints.items()},
             'model_version': '1.4.0', 'fallback_used': False,
             'reference_percentiles': {'endpoint_1_drugbank_approved_percentile': 30.5}}
    result = ToolResult.success_result('admet_predictor', data=[{
        'smiles': 'CCO', 'status': 'succeeded', 'admet': admet,
        'warnings': ['Model predictions are not experimental results.'],
    }], quality={'evidence_id': 'evidence-admet', 'scientific_usable': True})
    before = deepcopy(result.to_legacy_dict())

    projected = model_observation(result, {'profile': 'medchat-web-v1'})
    compact = projected['data'][0]['admet']
    table = compact['endpoint_table']
    restored = _restore_table(table)

    assert table['columns'][0] == 'endpoint_id'
    assert restored == endpoints
    assert compact['units_from_endpoint_table'] is True
    assert {key: value['unit'] for key, value in restored.items()} == admet['units']
    assert compact['reference_percentiles'] == admet['reference_percentiles']
    assert projected['data'][0]['warnings'] == before['data'][0]['warnings']
    assert projected['quality'] == before['quality']
    assert result.to_legacy_dict() == before
    assert len(json.dumps(projected['data'])) < len(json.dumps(before['data'])) * .75
    assert model_observation(result, {})['data'] == before['data']


def _restore_table(table):
    restored = {}
    for row in table['rows']:
        fields = dict(zip(table['columns'][1:], row[1:]))
        fields.update({key: values[fields[key]] for key, values in table.get('dictionaries', {}).items()})
        restored[row[0]] = {**table.get('constant_fields', {}), **fields}
    for endpoint, validity in table.get('validity_by_endpoint', {}).items():
        restored[endpoint]['validity'] = validity
    return restored


def test_admet_projection_preserves_sparse_invalidity_without_losing_compaction():
    endpoints = {f'endpoint_{i}': {'value': i / 100, 'unit': '-', 'task_type': 'regression',
                 'category': 'Excretion', 'name': f'Endpoint {i}', 'source': 'admet_ai_model'}
                 for i in range(49)}
    endpoints['endpoint_3']['validity'] = 'out_of_range'
    second = deepcopy(endpoints)
    second['endpoint_7']['validity'] = 'out_of_range'
    result = ToolResult.success_result('admet_predictor', data=[
        {'smiles': name, 'admet': {'endpoints': source}} for name, source in
        (('CCO', endpoints), ('CCN', second))])
    before = deepcopy(result.to_legacy_dict())
    projected = model_observation(result, {'profile': 'medchat-web-v1'})['data']
    assert _restore_table(projected[0]['admet']['endpoint_table']) == endpoints
    assert _restore_table(projected[1]['admet']['endpoint_table']) == second
    assert len(json.dumps(projected)) < len(json.dumps(before['data'])) * .75
    assert result.to_legacy_dict() == before


def test_two_candidate_admet_model_projection_compacts_repeated_metadata_losslessly():
    endpoints = {f'endpoint_{i}': {'value': i / 100, 'unit': '-', 'task_type': 'classification',
                 'category': 'Toxicity' if i % 2 else 'Distribution', 'name': f'Endpoint {i}',
                 'source': 'admet_ai_model' if i % 2 else 'rdkit_calculation'} for i in range(49)}
    percentiles = {f'endpoint_{i}_drugbank_approved_percentile': float(i) for i in range(49)}
    admet = {'endpoints': endpoints, 'units': {key: '-' for key in endpoints},
             'reference_percentiles': percentiles}
    result = ToolResult.success_result('admet_predictor', data=[
        {'smiles': smiles, 'admet': deepcopy(admet)} for smiles in ('CCO', 'CCN')])
    before = deepcopy(result.to_legacy_dict())
    projected = model_observation(result, {'profile': 'medchat-web-v1'})['data']
    for index, row in enumerate(projected):
        compact = row['admet']
        table = compact['endpoint_table']
        if index == 0:
            assert table['dictionaries']['category'] == ['Distribution', 'Toxicity']
            restored = _restore_table(table)
        else:
            assert table['endpoint_metadata_from_row'] == 0
            base = _restore_table(projected[0]['admet']['endpoint_table'])
            restored = {endpoint_id: {**base[endpoint_id], 'value': value}
                        for endpoint_id, value in zip(base, table['values'])}
        assert restored == endpoints
        if index == 0:
            percentile_table = compact['reference_percentile_table']
            assert {key + percentile_table['suffix']: value for key, value in percentile_table['rows']} == percentiles
        else:
            assert compact['reference_percentile_keys_from_row'] == 0
            assert dict(zip(percentiles, compact['reference_percentile_values'])) == percentiles
    assert len(json.dumps(projected)) < len(json.dumps(before['data'])) * 0.55
    assert result.to_legacy_dict() == before


def test_admet_shared_metadata_keeps_distinct_values_and_never_shares_different_schema():
    endpoints = {f'endpoint_{i}': {'value': i / 100, 'unit': '-', 'task_type': 'classification',
                 'category': 'Toxicity', 'name': f'Endpoint {i}', 'source': 'admet_ai_model'} for i in range(49)}
    second = deepcopy(endpoints)
    third = deepcopy(endpoints)
    for item in second.values():
        item['value'] += .01
    third['endpoint_0']['unit'] = 'mg/L'
    result = ToolResult.success_result('admet_predictor', data=[
        {'smiles': name, 'admet': {'endpoints': source}} for name, source in
        (('CCO', endpoints), ('CCN', second), ('CCC', third))])
    original = deepcopy(result.to_legacy_dict())
    rows = model_observation(result, {'profile': 'medchat-web-v1'})['data']
    table = rows[1]['admet']['endpoint_table']
    assert table['endpoint_metadata_from_row'] == 0
    base = _restore_table(rows[0]['admet']['endpoint_table'])
    restored = {key: {**base[key], 'value': value} for key, value in zip(base, table['values'])}
    assert restored == second
    assert 'endpoint_metadata_from_row' not in rows[2]['admet']['endpoint_table']
    assert _restore_table(rows[2]['admet']['endpoint_table']) == third
    assert result.to_legacy_dict() == original


def test_admet_projection_keeps_nonuniform_endpoint_fields_and_distinct_units():
    admet = {'endpoints': {'a': {'value': 1, 'unit': 'mM'}, 'b': {'value': 2}},
             'units': {'a': 'unknown'}}
    result = ToolResult.success_result('admet_predictor', data=[{'admet': admet}])
    assert model_observation(result, {'profile': 'medchat-web-v1'})['data'] == result.data


def test_activity_projection_deduplicates_only_identical_provenance_losslessly():
    common = {'models': {'classification': {'model_id': 'fixture', 'metadata': 'x' * 1000}},
              'scope': {'family': 'fixture'}}
    data = [{'smiles': 'CCO', 'provenance': deepcopy(common), 'predicted_pIC50': 5.1},
            {'smiles': 'CCN', 'provenance': deepcopy(common), 'predicted_pIC50': 5.2}]
    result = ToolResult.success_result('activity_predictor', data=data,
        quality={'model_provenance': [deepcopy(common), deepcopy(common)], 'evidence_id': 'e1'})
    before = deepcopy(result.to_legacy_dict())
    view = model_observation(result, {'profile': 'medchat-web-v1'})
    assert view['data'][0]['provenance'] == common
    assert view['data'][1]['provenance'] == {'data_row_provenance_ref': 0}
    assert view['quality']['model_provenance'] == [{'data_row_provenance_ref': 0}] * 2
    assert result.to_legacy_dict() == before
    assert len(json.dumps(view['data']) + json.dumps(view['quality'])) < len(json.dumps(before['data']) + json.dumps(before['quality'])) / 2
    assert model_observation(result, {})['data'] == before['data']
