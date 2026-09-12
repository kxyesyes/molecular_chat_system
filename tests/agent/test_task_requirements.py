"""Strict service-owned requirements, not model-supplied success criteria."""
import pytest

from src.agent.contracts.task_requirements import (
    MolecularRequirement, TaskRequirements, parse_task_requirements,
)


def payload(**kw):
    return {'version': '1', 'molecular_results': [{'tool_name': 'property_calculator', **kw}]}


@pytest.mark.parametrize('value', [True, '2', 0, -1, 101, 1.5])
def test_count_is_strict_and_bounded(value):
    with pytest.raises(ValueError): parse_task_requirements(payload(exact_molecule_count=value))


@pytest.mark.parametrize('value', [
    {'version': '2'}, {'version': '1', 'relax': True},
    payload(required_metrics=['binding_energy']),
    payload(required_metrics=['qed', 'qed']),
    payload(required_metrics=['lipinski_compliant']),
    {'version': '1', 'forbidden_tools': ['run_docking', 'run_docking']},
    {'version': '1', 'molecular_results': [{'tool_name': 'molecular_docking'}]},
    {'version': '1', 'molecular_results': [{'tool_name': 'property_calculator'}] * 2},
    {'version': '1', 'forbidden_tools': ['property_calculator'],
     'molecular_results': [{'tool_name': 'property_calculator'}]},
    payload(expected_smiles=['CCO'], exact_molecule_count=2),
    payload(expected_smiles=['CCO', 'CCO']),
])
def test_invalid_or_ambiguous_requirements_rejected(value):
    with pytest.raises(ValueError): parse_task_requirements(value)


def test_roundtrip_immutable_snapshot():
    raw = payload(exact_molecule_count=2, required_metrics=['qed'])
    parsed = parse_task_requirements(raw)
    raw['molecular_results'][0]['exact_molecule_count'] = 1
    assert parsed.molecular_results[0].exact_molecule_count == 2
    with pytest.raises(ValueError): parsed.molecular_results[0].exact_molecule_count = 1
    assert parse_task_requirements(parsed.model_dump(mode='json')) == parsed
    assert parse_task_requirements(None) == TaskRequirements()


def test_constructed_invalid_instance_is_revalidated():
    with pytest.raises(ValueError):
        parse_task_requirements(TaskRequirements.model_construct(version='wrong'))


@pytest.mark.parametrize('value', [
    {'version': 1}, {'forbidden_tools': ['x' * 65]},
    {'forbidden_tools': [f'tool_{i}' for i in range(33)]},
    {'forbidden_tools': ['../run']}, {'forbidden_tools': [True]},
    payload(expected_smiles=['C' * 8193]), payload(expected_smiles=['']),
    payload(expected_smiles=['CC O']), payload(expected_smiles=[12]),
    payload(expected_smiles=[f'C{i}' for i in range(101)]),
    payload(required_metrics=['qed'] * 8), payload(required_metrics=[True]),
    payload(unknown='private-marker'),
    {'molecular_results': [{'tool_name': 'property_calculator'}] * 3},
])
def test_requirements_fixed_field_bounds_and_unknown_fields(value):
    with pytest.raises(ValueError, match='^invalid_task_requirements$'):
        parse_task_requirements(value)


@pytest.mark.parametrize('count', [1, 100])
def test_count_and_subject_boundaries_are_inclusive(count):
    result = parse_task_requirements(payload(
        exact_molecule_count=count, expected_smiles=[f'C{i}' for i in range(count)],
    ))
    assert result.molecular_results[0].exact_molecule_count == count
    assert len(result.molecular_results[0].expected_smiles) == count


def test_all_supported_metrics_and_both_tools_are_preserved():
    metrics = ['molecular_weight', 'logp', 'tpsa', 'hbd', 'hba', 'qed']
    result = parse_task_requirements({'molecular_results': [
        {'tool_name': 'property_calculator', 'required_metrics': metrics},
        {'tool_name': 'drug_likeness_assessment',
         'required_metrics': metrics + ['lipinski_compliant']},
    ], 'forbidden_tools': [f'tool_{i}' for i in range(31)] + ['x' * 64]})
    assert result.molecular_results[0].required_metrics == tuple(metrics)
    assert result.molecular_results[1].required_metrics == tuple(metrics + ['lipinski_compliant'])
    assert len(result.forbidden_tools) == 32


def test_requirements_are_not_a_chemical_validator_or_tool_authorizer():
    result = parse_task_requirements(payload(expected_smiles=['not-a-molecule']))
    assert result.molecular_results[0].expected_smiles == ('not-a-molecule',)
    assert not hasattr(result, 'authorize')


def test_smiles_length_and_total_utf8_byte_bounds():
    assert parse_task_requirements(payload(expected_smiles=['C' * 8192]))
    # Each item is under the character bound, but the combined UTF-8 exceeds 32 KiB.
    with pytest.raises(ValueError, match='^invalid_task_requirements$'):
        parse_task_requirements(payload(expected_smiles=['分' * 6000, '子' * 6000]))


@pytest.mark.parametrize('value', [
    [], 'private-marker', True, payload(expected_smiles=['\ud800']),
    payload(exact_molecule_count=float('nan')),
    payload(exact_molecule_count=float('inf')),
])
def test_invalid_documents_have_fixed_errors(value):
    with pytest.raises(ValueError, match='^invalid_task_requirements$'):
        parse_task_requirements(value)


def test_constructed_nested_snapshot_is_validated_detached_and_immutable():
    metrics, subjects, forbidden = ['qed'], ['CCO'], ['run_docking']
    nested = MolecularRequirement.model_construct(
        tool_name='property_calculator', exact_molecule_count=1,
        required_metrics=metrics, expected_smiles=subjects,
    )
    rows = [nested]
    original = TaskRequirements.model_construct(molecular_results=rows, forbidden_tools=forbidden)
    parsed = parse_task_requirements(original)
    metrics.append('logp')
    subjects[0] = 'CCN'
    rows.clear()
    forbidden.clear()
    assert parsed is not original
    assert parsed.molecular_results[0] is not nested
    assert parsed.molecular_results[0].required_metrics == ('qed',)
    assert parsed.molecular_results[0].expected_smiles == ('CCO',)
    assert parsed.forbidden_tools == ('run_docking',)
    with pytest.raises(ValueError):
        parsed.forbidden_tools = ()
    exported = parsed.model_dump(mode='json')
    exported['molecular_results'][0]['expected_smiles'].clear()
    assert parsed.molecular_results[0].expected_smiles == ('CCO',)


@pytest.mark.parametrize('nested', [
    {'tool_name': 'property_calculator', 'exact_molecule_count': True},
    {'tool_name': 'property_calculator', 'required_metrics': ['qed', 'qed']},
    {'tool_name': 'property_calculator', 'expected_smiles': ['CCO', 'CCO']},
    {'tool_name': 'property_calculator', 'expected_smiles': ['CCO'], 'exact_molecule_count': 2},
    {'tool_name': 'property_calculator', 'private-key': 'private-value'},
])
def test_constructed_nested_dicts_are_not_trusted(nested):
    original = TaskRequirements.model_construct(molecular_results=[nested])
    with pytest.raises(ValueError, match='^invalid_task_requirements$'):
        parse_task_requirements(original)


@pytest.mark.parametrize('field,value', [
    ('tool_name', b'property_calculator'), ('required_metrics', [b'qed']),
    ('expected_smiles', [b'CCO']),
])
def test_constructed_nested_bytes_cannot_be_coerced_by_serialization(field, value):
    nested = MolecularRequirement.model_construct(**{
        'tool_name': 'property_calculator', field: value,
    })
    with pytest.raises(ValueError, match='^invalid_task_requirements$'):
        parse_task_requirements(TaskRequirements.model_construct(molecular_results=[nested]))


def test_constructed_invalid_data_does_not_echo_in_serialization_warnings(recwarn):
    original = TaskRequirements.model_construct(forbidden_tools=[{'private-key': 'private-value'}])
    with pytest.raises(ValueError, match='^invalid_task_requirements$'):
        parse_task_requirements(original)
    assert not recwarn.list


def test_constructed_circular_data_has_fixed_error():
    rows = []
    rows.append(rows)
    original = TaskRequirements.model_construct(molecular_results=rows)
    with pytest.raises(ValueError, match='^invalid_task_requirements$'):
        parse_task_requirements(original)
