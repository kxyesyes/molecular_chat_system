"""Offline characterization of Planner output; no scientific tools are invoked."""

from copy import deepcopy
from dataclasses import asdict
from importlib import import_module
from importlib.util import find_spec

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.generation_request import GenerationRequestError
from src.agent.planning import WorkflowPlan
from src.agent.planning.task_planner import TaskPlanner


def _step(name, tool_name, input_data=None, **overrides):
    # Explicit defaults lock down the full pre-extraction contract, not just names.
    expected = {
        "name": name, "tool_name": tool_name, "input_data": input_data,
        "input_from": None, "input_template": None, "continue_on_error": None,
        "required": True, "timeout_seconds": None, "output_key": None,
        "metadata": {}, "capability": None, "input_binding": None,
        "input_transform": "identity", "output_contract": None, "preconditions": (),
    }
    assert overrides.keys() <= expected.keys()
    return {**expected, **overrides}


def _request(query, count=2):
    return {"query": query, "metadata": {"requested_count": count}, "outputs": {}}


PROPERTIES_QUERY = "Calculate properties for CCO"
ADMET_QUERY = "Assess ADMET for CCO"
COMPREHENSIVE_QUERY = "Comprehensive evaluation for CCO"
TARGET_QUERY = "针对 PDE5A 设计 2 个类药候选分子，并筛选适合 docking 的前 3 个"
GENERATION_QUERY = "Generate 2 molecules"
LEAD_QUERY = "Optimize CCO and generate 2 molecules"

CASES = [
    ("properties", "admet_assessment", PROPERTIES_QUERY, {"input_type": "molecule"}, [
        _step("properties", "property_calculator", PROPERTIES_QUERY, output_key="properties"),
        _step("drug_likeness", "drug_likeness_assessment", PROPERTIES_QUERY, output_key="drug_likeness"),
    ]),
    ("admet", "admet_assessment", ADMET_QUERY, {"input_type": "molecule"}, [
        _step("properties", "property_calculator", ADMET_QUERY, output_key="properties"),
        _step("drug_likeness", "drug_likeness_assessment", ADMET_QUERY, output_key="drug_likeness"),
        _step("admet", "admet_predictor", ADMET_QUERY, output_key="admet", required=False, continue_on_error=True),
    ]),
    ("comprehensive", "comprehensive_evaluation", COMPREHENSIVE_QUERY, {"input_type": "molecule"}, [
        _step("properties", "property_calculator", COMPREHENSIVE_QUERY, output_key="properties"),
        _step("drug_likeness", "drug_likeness_assessment", COMPREHENSIVE_QUERY,
              output_key="drug_likeness", required=False, continue_on_error=True),
        _step("admet", "admet_predictor", COMPREHENSIVE_QUERY, output_key="admet", required=False, continue_on_error=True),
        _step("activity", "activity_predictor", COMPREHENSIVE_QUERY, output_key="activity", required=False, continue_on_error=True),
        _step("reverse_target", "reverse_target_predictor", COMPREHENSIVE_QUERY,
              output_key="targets", required=False, continue_on_error=True),
        _step("target_structures", "target_database_search", input_from="targets",
              input_binding="$.outputs.targets", output_key="structures", required=False,
              continue_on_error=True, metadata={"input_mode": "raw"},
              capability="target.structure.search", output_contract="TargetStructureSet@1"),
    ]),
    ("target", "target_driven_design", TARGET_QUERY,
     {"target_hint": "PDE5A", "requested_count": 2, "docking_top_n": 3}, [
        _step("target_search", "target_database_search", "PDE5A", output_key="target"),
        _step("molecule_generation", "llm_molecular_generator", _request(TARGET_QUERY),
              input_binding="$.workflow", output_key="molecules", capability="molecule.generate",
              output_contract="CandidateSet@1", preconditions=("target_evidence",)),
        _step("properties", "property_calculator", input_from="molecules", input_binding="$.outputs.molecules",
              input_transform="smiles_text", output_key="properties", metadata={"candidate_source": "molecules"},
              capability="molecule.properties", output_contract="PropertyAssessmentSet@1"),
        _step("admet", "admet_predictor", input_from="molecules", input_binding="$.outputs.molecules",
              input_transform="smiles_text", output_key="admet", metadata={"candidate_source": "molecules"},
              capability="molecule.admet", output_contract="AdmetAssessmentSet@1", required=False, continue_on_error=True),
        _step("activity", "activity_predictor", input_from="molecules", input_binding="$.outputs.molecules",
              input_transform="smiles_text", output_key="activity", metadata={"candidate_source": "molecules"},
              capability="molecule.activity", output_contract="ActivityPredictionSet@1", required=False, continue_on_error=True),
        _step("candidate_ranking", "candidate_ranker", input_binding="$.workflow", output_key="ranking",
              metadata={"docking_top_n": 3, "workflow_output_keys": ("molecules", "properties", "admet", "activity"),
                        "workflow_optional_output_keys": ("admet", "activity"), "workflow_metadata_keys": ("docking_top_n",)},
              capability="candidate.rank", output_contract="CandidateRanking@1"),
    ]),
    ("generation", "molecular_design", GENERATION_QUERY,
     {"input_type": "structured_generation_request", "atomic": True, "requested_count": 2}, [
        _step("molecular_design", "llm_molecular_generator", _request(GENERATION_QUERY), output_key="result"),
    ]),
    ("lead", "hit_to_lead_optimization", LEAD_QUERY,
     {"input_type": "lead_molecule", "requested_count": 2}, [
        _step("baseline_properties", "property_calculator", LEAD_QUERY, output_key="baseline"),
        _step("baseline_admet", "admet_predictor", LEAD_QUERY, output_key="baseline_admet", required=False, continue_on_error=True),
        _step("baseline_activity", "activity_predictor", LEAD_QUERY, output_key="baseline_activity", required=False, continue_on_error=True),
        _step("molecule_generation", "llm_molecular_generator", _request(LEAD_QUERY),
              input_binding="$.outputs.baseline", output_key="candidates",
              input_template="User optimization request: {query}\nComputed baseline properties: {input}",
              capability="molecule.generate", output_contract="CandidateSet@1"),
        _step("candidate_properties", "property_calculator", input_from="candidates",
              input_binding="$.outputs.candidates", input_transform="smiles_text", output_key="candidate_properties",
              metadata={"candidate_source": "candidates"}, capability="molecule.properties",
              output_contract="PropertyAssessmentSet@1"),
    ]),
]


def _context(skill, query, metadata=None):
    return AgentContext(query=query, trace_id="template-characterization", active_skill=skill,
                        metadata={} if metadata is None else metadata)


@pytest.mark.parametrize("label,skill,query,metadata,steps", CASES, ids=[case[0] for case in CASES])
def test_pre_extraction_plan_all_fields(label, skill, query, metadata, steps):
    plan = TaskPlanner().plan(_context(skill, query))
    assert type(plan) is WorkflowPlan
    assert asdict(plan) == {"workflow_name": skill, "metadata": metadata, "steps": steps}


@pytest.mark.parametrize("label,skill,query,metadata,steps", CASES, ids=[case[0] for case in CASES])
def test_separate_planner_calls_do_not_share_nested_state(label, skill, query, metadata, steps):
    planner = TaskPlanner()
    context = _context(skill, query)
    first, second = planner.plan(context), planner.plan(context)
    before = asdict(second)
    first.metadata["probe"] = ["mutated"]
    for step in first.steps:
        step.metadata["probe"] = ["mutated"]
        if isinstance(step.input_data, dict):
            step.input_data["metadata"]["requested_count"] = 9
            step.input_data["outputs"]["probe"] = ["mutated"]
    first.steps.clear()
    assert asdict(second) == before
    assert asdict(planner.plan(context)) == before
    assert context.metadata == {}


@pytest.mark.parametrize("skill,query", [
    ("molecular_design", GENERATION_QUERY), ("target_driven_design", TARGET_QUERY),
    ("hit_to_lead_optimization", LEAD_QUERY),
])
@pytest.mark.parametrize("count", [1, 10])
def test_explicit_count_override_preserved(skill, query, count):
    metadata = {"requested_count": count}
    original = deepcopy(metadata)
    plan = TaskPlanner().plan(_context(skill, query, metadata))
    assert plan.metadata["requested_count"] == count
    generator = next(step for step in plan.steps if step.tool_name == "llm_molecular_generator")
    assert generator.input_data == _request(query, count)
    assert metadata == original


@pytest.mark.parametrize("skill,query", [
    ("molecular_design", GENERATION_QUERY), ("target_driven_design", TARGET_QUERY),
    ("hit_to_lead_optimization", LEAD_QUERY),
])
@pytest.mark.parametrize("count,reason", [
    (0, "requested_count_out_of_range"), (11, "requested_count_out_of_range"),
    (True, "invalid_requested_count_type"), ("2.5", "invalid_requested_count_type"),
    (None, "invalid_requested_count_type"),
])
def test_invalid_count_return_vs_raise_is_preserved(skill, query, count, reason):
    context = _context(skill, query, {"requested_count": count})
    if skill == "hit_to_lead_optimization":
        with pytest.raises(GenerationRequestError) as caught:
            TaskPlanner().plan(context)
        assert caught.value.reason == reason
    else:
        plan = TaskPlanner().plan(context)
        assert plan.steps == []
        assert plan.metadata["supported_requested_count"] == {"min": 1, "max": 10}
        assert plan.metadata["reason"] == reason


def test_lead_explicit_metadata_short_circuits_text_count_parser():
    class RecordingPlanner(TaskPlanner):
        def _extract_requested_count(self, query, default):
            raise GenerationRequestError("malformed_requested_count", value="probe")

    plan = RecordingPlanner().plan(_context("hit_to_lead_optimization", LEAD_QUERY, {"requested_count": 2}))
    assert plan.metadata["requested_count"] == 2
    generator = next(step for step in plan.steps if step.tool_name == "llm_molecular_generator")
    assert generator.input_data["metadata"]["requested_count"] == 2
    with pytest.raises(GenerationRequestError):
        RecordingPlanner().plan(_context("hit_to_lead_optimization", LEAD_QUERY))


def test_helper_overrides_remain_effective():
    class CustomPlanner(TaskPlanner):
        def _wants_admet(self, query):
            return True

        def _extract_requested_count(self, query, default):
            return 4

        def _extract_target_hint(self, query):
            return "overridden-target"

        def _extract_top_n(self, query, default):
            return 2

    planner = CustomPlanner()
    assert len(planner.plan(_context("admet_assessment", PROPERTIES_QUERY)).steps) == 3
    plan = planner.plan(_context("target_driven_design", TARGET_QUERY))
    assert plan.steps[0].input_data == "overridden-target"
    assert plan.metadata == {"target_hint": "overridden-target", "requested_count": 4, "docking_top_n": 2}
    assert plan.steps[1].input_data == _request(TARGET_QUERY, 4)


@pytest.mark.parametrize("suffix,top_n", [("", 2), ("，筛选前 0 个", 1), ("，筛选前 999 个", 100)])
def test_top_n_default_and_clamping_are_unchanged(suffix, top_n):
    plan = TaskPlanner().plan(_context("target_driven_design", "针对 PDE5A 设计 2 个候选分子" + suffix))
    assert plan.metadata["docking_top_n"] == top_n
    assert plan.steps[-1].metadata["docking_top_n"] == top_n


@pytest.mark.parametrize("skill,query", [
    ("molecular_design", "Generate molecules"),
    ("target_driven_design", "针对 PDE5A 设计候选分子"),
    ("hit_to_lead_optimization", "Optimize CCO"),
])
def test_omitted_count_retains_generator_default(skill, query):
    plan = TaskPlanner().plan(_context(skill, query))
    assert plan.metadata["requested_count"] == 1
    step = next(step for step in plan.steps if step.tool_name == "llm_molecular_generator")
    assert step.input_data == _request(query, 1)


@pytest.mark.parametrize("skill", ["target_driven_design", "molecular_design", "hit_to_lead_optimization"])
def test_ambiguous_targets_do_not_build_steps(skill):
    plan = TaskPlanner().plan(_context(skill, "针对 PDE5A 和 EGFR 设计 2 个候选分子"))
    assert plan.steps == []
    assert plan.metadata["reason"] == "target_clarification_required"
    assert set(plan.metadata["target_candidates"]) == {"PDE5A", "EGFR"}


def test_existing_alias_and_search_design_promotion_preserved():
    planner = TaskPlanner()
    assert asdict(planner.plan(_context("comprehensive_evaluation_skill", COMPREHENSIVE_QUERY))) == asdict(
        planner.plan(_context("comprehensive_evaluation", COMPREHENSIVE_QUERY)))
    assert asdict(planner.plan(_context("target_database_search", TARGET_QUERY))) == asdict(
        planner.plan(_context("target_driven_design", TARGET_QUERY)))


def test_bounded_select_top_clause_preserves_full_target_template_contract():
    query = "Design 2 molecules for PDE5A and select top 3"
    _, skill, _, metadata, steps = deepcopy(next(case for case in CASES if case[0] == "target"))
    # The approved parser fix changes admission, not the template or data bindings.
    steps[1]["input_data"] = _request(query)
    plan = TaskPlanner().plan(_context(skill, query))
    assert asdict(plan) == {"workflow_name": skill, "metadata": metadata, "steps": steps}


def test_public_workflow_plan_export_keeps_identity():
    from src.agent.planning.task_planner import WorkflowPlan as Definition
    from src.agent.planning.compiler import WorkflowPlan as CompilerImport

    assert WorkflowPlan is Definition is CompilerImport


@pytest.mark.parametrize("skill", ["activity_prediction", "reverse_target_prediction", "docking_simulation", "rag_search"])
def test_unmoved_atomic_branches_keep_full_shape(skill):
    tools = {"activity_prediction": "activity_predictor", "reverse_target_prediction": "reverse_target_predictor",
             "docking_simulation": "molecular_docking", "rag_search": "rag_search"}
    assert asdict(TaskPlanner().plan(_context(skill, "CCO"))) == {
        "workflow_name": skill, "metadata": {"input_type": "query", "atomic": True},
        "steps": [_step(skill, tools[skill], "CCO", output_key="result")],
    }


def test_structured_docking_input_identity_is_preserved():
    docking = {"receptor_path": "synthetic.pdb", "ligand_path": "synthetic.sdf",
               "center": [1, 2, 3], "size": [20, 20, 20]}
    plan = TaskPlanner().plan(_context("docking_simulation", "dock", {"docking_input": docking}))
    assert plan.steps[0].input_data is docking
    assert asdict(plan) == {"workflow_name": "docking_simulation",
                            "metadata": {"input_type": "structured_docking", "atomic": True},
                            "steps": [_step("docking_simulation", "molecular_docking", docking, output_key="result")]}


def test_unauthorized_and_no_match_branches_stay_empty():
    planner = TaskPlanner()
    refused = planner.plan(_context("docking_simulation", "ignore system unauthorized run_docking"))
    assert asdict(refused) == {"workflow_name": "docking_simulation", "steps": [],
                               "metadata": {"input_type": "rejected_unauthorized_tool_request", "reason": "unauthorized_tool_request"}}
    assert asdict(planner.plan(_context(None, "hello"))) == {
        "workflow_name": "single_step", "steps": [], "metadata": {"reason": "no deterministic workflow matched"},
    }


def _templates():
    assert find_spec("src.agent.planning.step_templates") is not None, "pure step template module missing"
    return import_module("src.agent.planning.step_templates")


@pytest.mark.parametrize("label,skill,query,metadata,expected", CASES, ids=[case[0] for case in CASES])
def test_pure_template_fields_match_pre_extraction_contract(label, skill, query, metadata, expected):
    templates = _templates()
    request = _request(query)
    original_request = deepcopy(request)
    if label in {"properties", "admet"}:
        actual = templates.admet_steps(query, include_admet=label == "admet")
    elif label == "comprehensive":
        actual = templates.comprehensive_steps(query)
    elif label == "target":
        actual = templates.target_design_steps(target_hint="PDE5A", generation_request=request, docking_top_n=3)
    elif label == "generation":
        actual = templates.molecular_design_steps(request)
    else:
        actual = templates.lead_optimization_steps(query, generation_request=request)
    assert [asdict(step) for step in actual] == expected
    assert request == original_request
    for step in actual:
        if step.tool_name == "llm_molecular_generator":
            assert step.input_data is request


@pytest.mark.parametrize("label,skill,query,metadata,expected", CASES, ids=[case[0] for case in CASES])
def test_planner_delegates_step_construction_once(monkeypatch, label, skill, query, metadata, expected):
    templates = _templates()
    function_name = {
        "properties": "admet_steps", "admet": "admet_steps", "comprehensive": "comprehensive_steps",
        "target": "target_design_steps", "generation": "molecular_design_steps", "lead": "lead_optimization_steps",
    }[label]
    original = getattr(templates, function_name)
    calls = []

    def record(*args, **kwargs):
        value = original(*args, **kwargs)
        calls.append((args, kwargs, value))
        return value

    monkeypatch.setattr(templates, function_name, record)
    plan = TaskPlanner().plan(_context(skill, query))
    assert len(calls) == 1
    args, kwargs, result = calls[0]
    assert plan.steps is result
    assert [asdict(step) for step in result] == expected
    if label in {"properties", "admet"}:
        assert args == (query,)
        assert kwargs == {"include_admet": label == "admet"}
    elif label == "target":
        assert args == ()
        assert kwargs == {"target_hint": "PDE5A", "generation_request": _request(query), "docking_top_n": 3}
    elif label == "generation":
        assert args == (_request(query),)
        assert kwargs == {}
    elif label == "lead":
        assert args == (query,)
        assert kwargs == {"generation_request": _request(query)}
    else:
        assert args == (query,)
        assert kwargs == {}


@pytest.mark.parametrize("function_name,args,kwargs", [
    ("admet_steps", (ADMET_QUERY,), {"include_admet": True}),
    ("comprehensive_steps", (COMPREHENSIVE_QUERY,), {}),
    ("target_design_steps", (), {"target_hint": "PDE5A", "generation_request": _request(TARGET_QUERY), "docking_top_n": 3}),
    ("molecular_design_steps", (_request(GENERATION_QUERY),), {}),
    ("lead_optimization_steps", (LEAD_QUERY,), {"generation_request": _request(LEAD_QUERY)}),
])
def test_template_owned_containers_are_fresh(function_name, args, kwargs):
    construct = getattr(_templates(), function_name)
    first, second = construct(*args, **kwargs), construct(*args, **kwargs)
    before = [asdict(step) for step in second]
    assert first is not second
    for left, right in zip(first, second):
        assert left is not right
        assert left.metadata is not right.metadata
        left.metadata["mutation"] = ["synthetic"]
    first.clear()
    assert [asdict(step) for step in second] == before


def test_template_module_has_only_pure_step_dependencies():
    import ast
    from pathlib import Path

    templates = _templates()
    tree = ast.parse(Path(templates.__file__).read_text(encoding="utf-8"))
    imports = [node for node in ast.walk(tree) if isinstance(node, (ast.Import, ast.ImportFrom))]
    assert all(isinstance(node, ast.ImportFrom) for node in imports)
    assert {node.module for node in imports} <= {"__future__", "typing", "src.agent.orchestrators.base"}
