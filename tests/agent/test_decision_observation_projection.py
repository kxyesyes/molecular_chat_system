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
    restored = {row[0]: dict(zip(table['columns'][1:], row[1:])) for row in table['rows']}

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


def test_admet_projection_keeps_nonuniform_endpoint_fields_and_distinct_units():
    admet = {'endpoints': {'a': {'value': 1, 'unit': 'mM'}, 'b': {'value': 2}},
             'units': {'a': 'unknown'}}
    result = ToolResult.success_result('admet_predictor', data=[{'admet': admet}])
    assert model_observation(result, {'profile': 'medchat-web-v1'})['data'] == result.data
