"""PR209 planning/route gates; offline synthetic inputs, not scientific acceptance."""
from copy import deepcopy
import socket

import pytest

from src.agent.contracts import AgentContext
from src.agent.contracts.target_request import analyze_target_request
from src.agent.planning.request_parsing import positive_intent_text, wants_activity, wants_admet
from src.agent.planning.task_planner import TaskPlanner
from src.agent.routing.hybrid import HybridSkillRouter
from src.agent.runtime.workflow_executor import PreparedWorkflow, WorkflowExecution, WorkflowExecutor
from src.agent.workflows import WorkflowCatalog


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    connect, socketpair = socket.socket.connect, socket.socketpair
    def blocked(*args, **kwargs):
        raise AssertionError("PR209 tests must not contact external services")
    def local_pair(*args, **kwargs):
        # Windows asyncio builds its private wakeup pipe using socketpair's
        # loopback fallback. Only that stdlib construction may connect.
        with monkeypatch.context() as local:
            local.setattr(socket.socket, "connect", connect)
            return socketpair(*args, **kwargs)
    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setattr(socket, "create_connection", blocked)
    monkeypatch.setattr(socket, "socketpair", local_pair)


class ForbiddenTool:
    def __init__(self, name):
        self.name, self.calls = name, []

    def execute(self, value):
        self.calls.append(deepcopy(value))
        raise AssertionError("No scientific execution authorized by this fixture")


def context(query, skill):
    return AgentContext(query=query, active_skill=skill, trace_id="synthetic-pr209")


@pytest.mark.parametrize("separator", ["", "，", ", "])
@pytest.mark.parametrize("comparison,operator", [("不大于", "<="), ("不小于", ">=")])
def test_p2_1_target_role_bounds_reach_compiled_generation(separator, comparison, operator):
    query = f"以PDE为靶点设计2个分子{separator}要求MW{comparison}450"
    assert not analyze_target_request(query).needs_clarification
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill == "target_driven_design" and not route.requires_confirmation
    policy = WorkflowCatalog().require(route.selected_skill)
    tools = {name: ForbiddenTool(name) for name in policy.allowed_tools}
    prepared = WorkflowExecutor().prepare(context=context(query, policy.name), policy=policy, all_tools=tools)
    assert isinstance(prepared, PreparedWorkflow)
    plan = prepared.compiled.plan
    assert plan.metadata["target_hint"] == "PDE" and plan.metadata["requested_count"] == 2
    generation = next(step for step in plan.steps if step.tool_name == "llm_molecular_generator")
    assert generation.input_data["query"] == query
    assert generation.preconditions == ("target_evidence",)
    assert all(not tool.calls for tool in tools.values())


@pytest.mark.parametrize("comparison,operator", [("不大于", "<="), ("不小于", ">=")])
@pytest.mark.parametrize("state,label", [("equal", "满足"), ("missing", "未验证"), ("violating", "不满足")])
def test_p2_1_report_keeps_inclusive_bounds_and_missing_evidence(comparison, operator, state, label):
    from src.web.property_constraints import generation_constraint_report
    from tests.agent.test_browser_property_constraints import _generation_result
    mass = {"equal": 450.0, "missing": None, "violating": 451.0 if operator == "<=" else 449.0}[state]
    result = _generation_result()
    for row in result["tool_result_sequence"][1]["data"]:
        row["properties"]["molecular_weight"] = mass
    report = generation_constraint_report(result, f"以PDE为靶点设计2个分子要求MW{comparison}450")
    assert f"molecular_weight {operator} 450.0 Da | {mass if mass is not None else '不可用'} | {label}" in report


@pytest.mark.parametrize("query", [
    "以PDE为靶点设计2个分子，要求MW不大于450kDa",
    "以PDE为靶点设计2个分子，要求MW不小于450 nm²",
    "以XYZ999为靶点设计2个分子，要求MW不大于450",
    "不要以PDE为靶点设计2个分子，要求MW不大于450",
    "以PDE为靶点设计2个分子，要求MW不大于450，靶点: XYZ999",
])
def test_p2_1_bad_units_unknown_and_negation_still_block(query):
    assert analyze_target_request(query).needs_clarification
    policy = WorkflowCatalog().require("target_driven_design")
    tools = {name: ForbiddenTool(name) for name in policy.allowed_tools}
    execution = WorkflowExecutor().execute(context=context(query, policy.name), policy=policy, all_tools=tools)
    assert isinstance(execution, WorkflowExecution) and not execution.result.success
    assert execution.plan.metadata["reason"] == "target_clarification_required"
    assert execution.tool_attempt_count == 0 and all(not tool.calls for tool in tools.values())


@pytest.mark.parametrize("query", [
    "Skip activity and calculate ADMET for CCO",
    "不要预测活性并计算 CCO 的 ADMET",
])
def test_p2_2_new_positive_operation_survives_negation(query):
    assert wants_admet(query) and not wants_activity(query), positive_intent_text(query)
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill == "admet_assessment" and not route.requires_confirmation
    plan = TaskPlanner().plan(context(query, route.selected_skill))
    assert "admet_predictor" in [step.tool_name for step in plan.steps]
    assert "activity_predictor" not in [step.tool_name for step in plan.steps]


@pytest.mark.parametrize("query", [
    "不要活性和ADMET",
    "Skip activity and ADMET for CCO", "Do not predict activity and ADMET for CCO",
    "不要预测活性并且ADMET", "不要活性并不要计算ADMET",
    "不要预测活性并评估 CCO 的 ADMET",
    "Do not predict activity and please calculate ADMET for CCO",
])
def test_p2_2_shared_or_repeated_negation_never_revives_admet(query):
    assert not wants_admet(query) and not wants_activity(query)
    assert "admet_predictor" not in [step.tool_name for step in TaskPlanner().plan(context(query, "admet_assessment")).steps]


@pytest.mark.parametrize("connector", ["和", "及", "与", "并"])
def test_p2_2_shared_chinese_negation_cannot_authorize_another_verb(connector):
    query = f"不要计算性质{connector}预测活性"
    assert not wants_activity(query), positive_intent_text(query)
    assert "activity_prediction" not in [item.skill_name for item in HybridSkillRouter().decide(query).candidates]


@pytest.mark.parametrize("query", [
    "Do not calculate properties and predict activity for CCO",
    "Do not calculate properties and calculate ADMET for CCO",
    "不要计算性质并计算 CCO 的 ADMET",
    "不要预测活性和计算 CCO 的 ADMET",
    "不要预测活性及计算 CCO 的 ADMET",
    "不要预测活性与计算 CCO 的 ADMET",
    "Skip activity and predict activity for CCO",
    "Skip activity and calculate ADMET for CCO and predict activity",
])
def test_p2_2_ambiguous_shared_negation_stays_closed_even_with_new_verb(query):
    assert not wants_activity(query) and not wants_admet(query), positive_intent_text(query)
    route = HybridSkillRouter().decide(query)
    assert not any(item.skill_name == "activity_prediction" for item in route.candidates)
    assert "activity_predictor" not in [step.tool_name for step in TaskPlanner().plan(context(query, route.selected_skill)).steps]


@pytest.mark.parametrize("query", ["Skip activity and calculate ADMET for CCO", "不要预测活性并计算 CCO 的 ADMET"])
def test_p2_2_runtime_dispatch_keeps_positive_admet_and_unavailable_state(query):
    from src.agent.tools.property_calculator import PropertyCalculator
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    class UnavailableAdmet:
        name = "admet_predictor"
        def __init__(self):
            self.calls = []
        def execute(self, value):
            self.calls.append(value)
            return {"success": False, "status": "unavailable", "message": "synthetic unavailable", "data": None}
    admet = UnavailableAdmet()
    policy = WorkflowCatalog().require("admet_assessment")
    result = WorkflowExecutor().execute(context=context(query, policy.name), policy=policy,
        all_tools={"property_calculator": PropertyCalculator(), "drug_likeness_assessment": DrugLikenessAssessment(),
                   "admet_predictor": admet})
    assert admet.calls == [query]
    observations = [item for item in result.result.tool_results if item.tool_name == admet.name]
    assert len(observations) == 1 and observations[0].status.value == "unavailable"
    assert not any(item.tool_name == "activity_predictor" for item in result.result.tool_results)


@pytest.mark.parametrize("admet", [False, True])
def test_p2_3_comparison_dispatch_canonical_identity_for_every_tool(admet):
    query = "Compare and rank properties " + ("ADMET " if admet else "") + "and BuChE activity; SMILES: CCO; SMILES: OCC; SMILES: CCN"
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill == "comprehensive_evaluation" and not route.requires_confirmation
    plan = TaskPlanner().plan(context(query, route.selected_skill))
    assert {step.tool_name for step in plan.steps} == set(route.allowed_tools)
    for step in plan.steps:
        batch = step.input_data["smiles"] if isinstance(step.input_data, dict) else step.input_data.splitlines()
        assert batch == ["CCO", "CCN"]
        if isinstance(step.input_data, dict):
            assert step.input_data["query"] == query
            from src.agent.tools.activity_input import parse_activity_input
            from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
            _, parsed, target = parse_activity_input(step.input_data, ActivityPredictorTool())
            assert parsed == batch and target == "BuChE"


@pytest.mark.parametrize("tail", ["CCO and OCC", "SMILES: OCC; SMILES: CCO; SMILES: CCN"])
@pytest.mark.parametrize("include_admet", [False, True])
def test_p2_3_runtime_report_accepts_deduplicated_evidence(tmp_path, tail, include_admet):
    from tests.agent.test_browser_evidence_followup import handler_for, prediction, turn
    from src.agent.evidence_followup import accepted_activity_evidence
    from src.agent.tools.property_calculator import PropertyCalculator
    query = "Compare and rank properties " + ("ADMET " if include_admet else "") + "and BuChE activity"
    query += ("; " if "SMILES:" in tail else " for ") + tail
    smiles = ["CCO"] + (["CCN"] if "CCN" in tail else [])
    handler, activity, admet = handler_for(tmp_path, [prediction(smi, 4.8) for smi in smiles])
    properties = PropertyCalculator()
    calls = []
    execute = properties.execute
    def recording(value):
        calls.append(value)
        return execute(value)
    # Registry retains the tool instance used by the real native runtime.
    from src.agent.harness.factory import HarnessFactory
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.supervisor import SupervisorAgent
    properties.execute = recording
    handler.agent_system = SupervisorAgent(tools={"activity_predictor": activity, "property_calculator": properties,
                                               "admet_predictor": admet},
        harness_factory=HarnessFactory(mode="legacy"), state_store=SQLiteAgentStateStore(tmp_path / "dedup.sqlite"))
    history = []
    complete, _ = turn(handler, query, history)
    assert activity.calls == [{"smiles": smiles, "query": query}]
    assert calls == ["\n".join(smiles)]
    assert admet.calls == (["\n".join(smiles)] if include_admet else [])
    assert complete["status"] == ("partial" if include_admet else "succeeded"), complete["content"][:2000]
    assert "| 1 | CCO |" in complete["content"]
    assert "property_calculator：不可用" not in complete["content"]
    assert "activity_predictor：不可用" not in complete["content"]
    assert "46.07" in complete["content"] and "evidence-" in complete["content"]
    assert history[-1]["activity_evidence"] is not None
    result = {"trace_id": "synthetic-pr209", "tool_result_sequence": history[-1]["activity_evidence"]["observations"]}
    assert accepted_activity_evidence(result, query, "synthetic-socket") is not None
    result["tool_result_sequence"][0]["data"].append(deepcopy(result["tool_result_sequence"][0]["data"][0]))
    assert accepted_activity_evidence(result, query, "synthetic-socket") is None


@pytest.mark.parametrize("defect", ["unavailable_validation", "oversized_batch"])
def test_p2_3_canonical_dispatch_preserves_validation_and_raw_count_caps(monkeypatch, defect):
    query = "Compare and rank properties and BuChE activity; SMILES: CCO; SMILES: OCC"
    if defect == "unavailable_validation":
        monkeypatch.setattr("src.agent.utils.validators._load_rdkit_chem", lambda: None)
    else:
        # The canonical path must retain the existing 16-candidate cap.
        query = "Compare and rank properties and BuChE activity; " + "; ".join(
            "SMILES: " + "C" * (index + 2) for index in range(17))
    plan = TaskPlanner().plan(context(query, "comprehensive_evaluation"))
    assert plan.steps == [] and plan.metadata["reason"] == "comparison_input_required"


@pytest.mark.parametrize("tail", ["CCO and OCC and C1(CC", "CCO; SMILES: not-a-molecule"])
def test_p2_3_invalid_comparison_cannot_be_rescued_by_deduplication(tail):
    query = "Compare and rank properties and BuChE activity for " + tail
    policy = WorkflowCatalog().require("comprehensive_evaluation")
    tools = {name: ForbiddenTool(name) for name in policy.allowed_tools}
    result = WorkflowExecutor().execute(context=context(query, policy.name), policy=policy, all_tools=tools)
    assert result.plan.metadata["reason"] == "comparison_input_required"
    assert result.tool_attempt_count == 0 and all(not tool.calls for tool in tools.values())


@pytest.mark.parametrize("query", [
    "List protein sources in a vegan diet", "列出蛋白质含量的计算方法",
    "搜索蛋白质含量的计算方法", "Search protein sources in a vegan diet",
])
def test_p2_4_non_target_protein_list_cannot_authorize_lookup(query):
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill != "target_database_search"
    assert not any(candidate.skill_name == "target_database_search" for candidate in route.candidates)
    plan = TaskPlanner().plan(context(query, route.selected_skill))
    assert not any(step.tool_name == "target_database_search" for step in plan.steps)


@pytest.mark.parametrize("query", [
    "List protein structures for EGFR", "列出PDE5A的蛋白结构",
    "List structures in PDB", "搜索 UniProt 数据库中的蛋白记录",
])
def test_p2_4_real_target_database_and_structure_objects_keep_lookup(query):
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill == "target_database_search" and not route.requires_confirmation
    assert TaskPlanner().plan(context(query, route.selected_skill)).steps[0].tool_name == "target_database_search"


@pytest.mark.parametrize("query", ["查找排名第一的靶点结构，并说明能否继续对接。", "Find structures for the previous target"])
def test_p2_4_unresolved_target_reference_and_invalid_smiles_keep_gates(query):
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill is None and "target_reference_required" in route.reasons
    route = HybridSkillRouter().decide("List EGFR protein structures; SMILES: C1(CC")
    assert route.requires_confirmation and any("SMILES" in reason for reason in route.reasons)
    route = HybridSkillRouter().decide("不要查找 EGFR 蛋白结构")
    assert route.selected_skill != "target_database_search"


@pytest.mark.parametrize("query", [
    "Search protein structure prediction methods",
    "列出蛋白结构预测的方法",
    "解释蛋白结构预测的方法",
    "Search EGFR protein structure prediction methods",
    "解释EGFR的蛋白结构预测的方法",
    "Search protein structure prediction methods for EGFR",
    "Compare EGFR protein structure prediction methods",
    "比较EGFR的蛋白结构预测的方法",
])
def test_p2_4_prediction_method_object_is_knowledge_not_structure_lookup(query):
    request = analyze_target_request(query)
    assert not request.unknown
    if "EGFR" in query:
        assert request.targets == ("EGFR",)  # An entity is not lookup authorization.
    else:
        assert not request.targets and not request.explicit
    assert positive_intent_text(query) == query  # Knowledge is not a prohibition.
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill is None and route.allowed_tools == []
    plan = TaskPlanner().plan(context(query, route.selected_skill))
    assert plan.steps == []  # No target search or other scientific/RAG tool.


@pytest.mark.parametrize("query", [
    "Find EGFR protein structures and describe prediction methods",
    "查找 EGFR 的蛋白结构，并说明结构预测的方法",
    "Search PDB for EGFR structures and explain experimental methods",
    "搜索 UniProt 数据库中的蛋白记录，并说明检索方法",
    "List experimental protein structures",
    "列出实验结构",
])
def test_p2_4_method_words_do_not_veto_explicit_structure_lookup(query):
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill == "target_database_search" and not route.requires_confirmation
    plan = TaskPlanner().plan(context(query, route.selected_skill))
    assert [step.tool_name for step in plan.steps] == ["target_database_search"]


def _recording_lookup_runtime(tmp_path):
    from src.agent.harness.factory import HarnessFactory
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.supervisor import SupervisorAgent
    class RecordingLookup:
        name = "target_database_search"
        def __init__(self):
            self.calls = []
        def execute(self, value):
            self.calls.append(deepcopy(value))
            return {"success": False, "status": "unavailable", "data": None,
                    "message": "offline recording stub; no target database queried"}
    lookup = RecordingLookup()
    agent = SupervisorAgent(tools={lookup.name: lookup}, harness_factory=HarnessFactory(mode="legacy"),
        state_store=SQLiteAgentStateStore(tmp_path / "lookup.sqlite"))
    return agent, lookup


@pytest.mark.parametrize("query", [
    "Compare EGFR protein structure prediction methods",
    "比较EGFR的蛋白结构预测的方法",
])
def test_p2_4_comparison_methods_runtime_never_dispatches_lookup(tmp_path, query):
    agent, lookup = _recording_lookup_runtime(tmp_path)
    result = agent.execute(query)
    assert lookup.calls == []
    assert result["tools_used"] == [] and result["tool_result_sequence"] == []
    assert result.get("workflow_plan", {}).get("steps", []) == []


@pytest.mark.parametrize("query", [
    "Compare EGFR protein structure prediction methods; find EGFR protein structures",
    "比较EGFR的蛋白结构预测的方法；查找EGFR的蛋白结构",
    "Compare EGFR and PDE5A protein structures",
    "List structures in PDB",
    "List experimental protein structures",
])
def test_p2_4_explicit_lookup_runtime_survives_knowledge_mask(tmp_path, query):
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill == "target_database_search" and not route.requires_confirmation
    agent, lookup = _recording_lookup_runtime(tmp_path)
    result = agent.execute(query)
    assert len(lookup.calls) == 1
    assert [item["tool_name"] for item in result["tool_result_sequence"]] == [lookup.name]
    assert result["tool_result_sequence"][0]["status"] == "unavailable"


def test_p2_4_knowledge_target_cannot_resolve_old_rank_reference(tmp_path):
    query = "Compare EGFR protein structure prediction methods; Find structures for the previous target"
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill is None and "target_reference_required" in route.reasons
    agent, lookup = _recording_lookup_runtime(tmp_path)
    result = agent.execute(query)
    assert lookup.calls == [] and result["tools_used"] == []


MIXED_LOOKUP_QUERIES = [
    "Compare EGFR protein structure prediction methods; find PDE5A protein structures",
    "比较EGFR的蛋白结构预测的方法；查找PDE5A的蛋白结构",
]


@pytest.mark.parametrize("query", MIXED_LOOKUP_QUERIES)
def test_p2_4_mixed_lookup_runtime_binds_only_explicit_target(tmp_path, query):
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill == "target_database_search" and not route.requires_confirmation
    agent, lookup = _recording_lookup_runtime(tmp_path)
    result = agent.execute(query, session_id="synthetic-mixed-lookup")
    audit = agent.state_store.get_run(result["trace_id"])
    assert audit["query"] == query
    assert lookup.calls == ["PDE5A"]
    plan = agent.planner.plan(context(query, route.selected_skill))
    assert plan.steps[0].input_data == "PDE5A"
    assert result["tool_result_sequence"][0]["status"] == "unavailable"


@pytest.mark.parametrize("query", MIXED_LOOKUP_QUERIES)
def test_p2_4_mixed_lookup_chat_preserves_query_and_history(tmp_path, monkeypatch, query):
    from src.web.chat_handler import ChatHandler
    from tests.agent.test_browser_evidence_followup import NoScienceModel, turn
    agent, lookup = _recording_lookup_runtime(tmp_path)
    received = []
    execute = agent.execute
    def recording(value, *args, **kwargs):
        received.append(value)
        return execute(value, *args, **kwargs)
    monkeypatch.setattr(agent, "execute", recording)
    handler = ChatHandler(NoScienceModel(), None, agent, {})
    history = [{"user": "Prior synthetic question", "assistant": "Prior synthetic answer"}]
    before = deepcopy(history)
    complete, _ = turn(handler, query, history, session="synthetic-mixed-chat")
    assert received == [query]
    assert history[:-1] == before and history[-1]["user"] == query
    assert complete["content"]
    assert lookup.calls == ["PDE5A"]


@pytest.mark.parametrize("query", [
    "Compare EGFR and PDE5A protein structures",
    "Compare EGFR protein structure prediction methods; find EGFR and PDE5A protein structures",
    "比较EGFR的蛋白结构预测的方法；查找EGFR和PDE5A的蛋白结构",
])
def test_p2_4_mixed_lookup_keeps_both_explicit_targets(tmp_path, query):
    agent, lookup = _recording_lookup_runtime(tmp_path)
    result = agent.execute(query)
    assert lookup.calls == [["EGFR", "PDE5A"]]
    assert agent.state_store.get_run(result["trace_id"])["query"] == query


@pytest.mark.parametrize("query", MIXED_LOOKUP_QUERIES)
def test_p2_4_mixed_lookup_pdb_input_keeps_constraint_not_knowledge_target(query):
    from src.target_search.exact_pdb import requested_pdb_id
    query += "; PDB: 1ABC"
    original = context(query, "target_database_search")
    plan = TaskPlanner().plan(original)
    lookup_input = plan.steps[0].input_data
    assert original.query == query
    assert requested_pdb_id(lookup_input) == "1ABC"
    assert analyze_target_request(lookup_input).targets == ("PDE5A",)


def test_p2_4_lookup_mask_does_not_change_global_design_target_collection():
    query = "Compare EGFR protein structure prediction methods; design 2 molecules for PDE5A"
    assert analyze_target_request(query).targets == ("EGFR", "PDE5A")
    plan = TaskPlanner().plan(context(query, "target_driven_design"))
    assert plan.steps == [] and plan.metadata["reason"] == "target_clarification_required"


KNOWLEDGE_ALIAS_PAIRS = [
    ("algorithms", "算法"), ("principles", "原理"), ("techniques", "技术"),
    ("approaches", "思路"), ("theory", "理论"), ("theories", "理论"),
]
ALIAS_KNOWLEDGE_QUERIES = [
    "Search protein structure prediction algorithms",
    "解释蛋白结构预测的原理",
    *(query for english, chinese in KNOWLEDGE_ALIAS_PAIRS for query in (
        f"Compare EGFR protein structure prediction {english}",
        f"比较EGFR的蛋白结构预测的{chinese}",
    )),
]


@pytest.mark.parametrize("query", ALIAS_KNOWLEDGE_QUERIES)
def test_p2_4_alias_knowledge_router_and_planner_do_not_authorize_lookup(query):
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill is None and route.allowed_tools == []
    assert not any(item.skill_name == "target_database_search" for item in route.candidates)
    assert TaskPlanner().plan(context(query, route.selected_skill)).steps == []


@pytest.mark.parametrize("query", ALIAS_KNOWLEDGE_QUERIES)
def test_p2_4_alias_knowledge_runtime_never_dispatches_lookup(tmp_path, query):
    agent, lookup = _recording_lookup_runtime(tmp_path)
    result = agent.execute(query)
    assert lookup.calls == []
    assert result["tools_used"] == [] and result["tool_result_sequence"] == []


@pytest.mark.parametrize("query", [
    query for english, chinese in KNOWLEDGE_ALIAS_PAIRS for query in (
        f"Compare EGFR protein structure prediction {english}; find PDE5A protein structures",
        f"比较EGFR的蛋白结构预测的{chinese}；查找PDE5A的蛋白结构",
    )
])
def test_p2_4_alias_mixed_runtime_only_looks_up_explicit_different_id(tmp_path, query):
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill == "target_database_search" and not route.requires_confirmation
    original = context(query, route.selected_skill)
    agent, lookup = _recording_lookup_runtime(tmp_path)
    result = agent.execute(query)
    assert agent.state_store.get_run(result["trace_id"])["query"] == query
    assert original.query == query
    assert lookup.calls == ["PDE5A"]
    assert TaskPlanner().plan(original).steps[0].input_data == "PDE5A"


@pytest.mark.parametrize("query", [
    query for english, chinese in KNOWLEDGE_ALIAS_PAIRS for query in (
        f"Compare EGFR protein structure prediction {english}; find EGFR and PDE5A protein structures",
        f"比较EGFR的蛋白结构预测的{chinese}；查找EGFR和PDE5A的蛋白结构",
    )
])
def test_p2_4_alias_knowledge_does_not_veto_independent_explicit_lookup(tmp_path, query):
    agent, lookup = _recording_lookup_runtime(tmp_path)
    result = agent.execute(query)
    assert lookup.calls == [["EGFR", "PDE5A"]]
    assert result["tool_result_sequence"][0]["status"] == "unavailable"


@pytest.mark.parametrize("spelling", ["approach", "approaches"])
def test_p2_4_approach_spelling_router_and_planner(spelling):
    query = f"Search protein structure prediction {spelling}"
    route = HybridSkillRouter().decide(query)
    assert route.selected_skill is None and route.allowed_tools == []
    assert TaskPlanner().plan(context(query, route.selected_skill)).steps == []


@pytest.mark.parametrize("spelling", ["approach", "approaches"])
def test_p2_4_approach_spelling_runtime_does_not_lookup(tmp_path, spelling):
    query = f"Search protein structure prediction {spelling}"
    agent, lookup = _recording_lookup_runtime(tmp_path)
    result = agent.execute(query)
    assert lookup.calls == [] and result["tools_used"] == []


@pytest.mark.parametrize("spelling", ["approach", "approaches"])
def test_p2_4_approach_spelling_mixed_runtime_only_pde5a(tmp_path, spelling):
    query = f"Explain protein structure prediction {spelling} for EGFR; find PDE5A protein structures"
    agent, lookup = _recording_lookup_runtime(tmp_path)
    result = agent.execute(query)
    assert agent.state_store.get_run(result["trace_id"])["query"] == query
    assert lookup.calls == ["PDE5A"]


@pytest.mark.parametrize("spelling", ["approache", "approachable", "approaches_extra"])
def test_p2_4_approach_spelling_keeps_finite_alias_boundary(spelling):
    from src.agent.planning.request_parsing import target_lookup_text
    query = f"Search protein structure prediction {spelling}"
    assert target_lookup_text(query) == query
