from types import SimpleNamespace

from src.agent.scientific_session.controller import (
    next_required_web_step,
    web_plan_ready_for_terminal,
    web_result_step_ref,
)


def _step(name, tool_name, *, required=True):
    return SimpleNamespace(name=name, tool_name=tool_name, required=required)


def test_finish_replan_selects_first_required_step_with_ready_dependencies():
    steps = [
        _step('baseline_properties', 'property_calculator'),
        _step('baseline_admet', 'admet_predictor', required=False),
        _step('molecule_generation', 'llm_molecular_generator'),
        _step('candidate_properties', 'property_calculator'),
        _step('optimization_verification', 'lead_optimization_verifier'),
    ]
    dependencies = {
        'baseline_properties': (),
        'baseline_admet': ('baseline_properties',),
        'molecule_generation': ('baseline_properties',),
        'candidate_properties': ('molecule_generation',),
        'optimization_verification': ('baseline_properties', 'molecule_generation', 'candidate_properties', 'baseline_admet'),
    }

    next_step = next_required_web_step(
        steps,
        dependencies,
        completed={'baseline_properties', 'molecule_generation', 'candidate_properties'},
    )

    assert next_step.name == 'optimization_verification'
    assert next_step.tool_name == 'lead_optimization_verifier'


def test_finish_replan_does_not_skip_required_dependency():
    steps = [_step('baseline_properties', 'property_calculator'),
             _step('molecule_generation', 'llm_molecular_generator')]
    dependencies = {'baseline_properties': ('missing_input',),
                    'molecule_generation': ('baseline_properties',)}

    assert next_required_web_step(steps, dependencies, completed=set()) is None


def test_web_plan_can_publish_partial_terminal_when_no_required_tool_is_missing():
    steps = [_step("baseline_properties", "property_calculator"),
             _step("baseline_admet", "admet_predictor", required=False),
             _step("optimization_verification", "lead_optimization_verifier")]
    assert web_plan_ready_for_terminal(steps, completed={"baseline_properties", "optimization_verification"})
    assert not web_plan_ready_for_terminal(steps, completed={"baseline_properties"})


def test_web_result_step_ref_falls_back_to_binding_action_hash():
    result = SimpleNamespace(quality={
        "step_id": "unmatched-result-id",
        "binding_proof": {"action_sha256": "action-hash"},
    })
    records = {"web-action": {
        "action_hash": "action-hash",
        "arguments": {"step_ref": "optimization_verification"},
    }}

    assert web_result_step_ref(result, records) == "optimization_verification"
