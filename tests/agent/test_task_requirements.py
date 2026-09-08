"""Strict service-owned requirements, not model-supplied success criteria."""
import pytest

from src.agent.contracts.task_requirements import TaskRequirements, parse_task_requirements


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
