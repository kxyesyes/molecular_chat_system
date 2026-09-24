"""Public ReAct assembly tests using synthetic tools, never live services."""
import pytest

from src.agent.react_agent import ReActMolecularAgent
from src.agent.workflows import WorkflowCatalog
from tests.agent.test_target_driven_design_workflow import FakeTool
from tests.agent.test_workflow_skills import FakeTool as WorkflowTool


class RecordingTool(WorkflowTool):
    def __init__(self, name):
        super().__init__(name)
        self.calls = []

    def should_use(self, query):
        # Deliberately permissive: it must not bypass workflow permissions.
        return True

    def execute(self, query):
        self.calls.append(query)
        return super().execute(query)


class HallucinatingLLM:
    def __init__(self):
        self.calls = []

    def generate(self, prompt, **kwargs):
        self.calls.append(prompt)
        return "行动: property_calculator\n行动输入: CCO\n最终答案: fabricated"


def build_agent(monkeypatch, tools, llm=None):
    monkeypatch.setattr("src.agent.tools.get_all_tools", lambda _llm: tools)
    return ReActMolecularAgent(llm=llm)


def assert_generated(response, generator, query, count, temperature=0.7):
    assert response["success"] is True
    assert response["status"] == "completed"
    assert response["partial"] is False
    assert len(generator.inputs) == 1
    assert generator.inputs[0]["query"] == query
    assert generator.inputs[0]["metadata"]["requested_count"] == count
    assert generator.inputs[0]["metadata"]["temperature"] == temperature
    observation = response["tool_results"]["llm_molecular_generator"]
    assert observation["quality"]["requested_count"] == count
    assert observation["quality"]["actual_count"] == count
    assert observation["quality"]["invalid_count"] == 0
    assert observation["quality"]["duplicate_count"] == 0
    candidates = observation["data"]["candidates"]
    assert len({row["canonical_smiles"] for row in candidates}) == count
    assert all(row["generation_provenance"]["model_name"] == "synthetic-test-generator"
               for row in candidates)
    assert response["tools_used"] == ["llm_molecular_generator"]


def test_workflow_skill_uses_planner_and_returns_legacy_dict(monkeypatch):
    names = list(WorkflowCatalog().require("comprehensive_evaluation").allowed_tools)
    tools = [RecordingTool(name) for name in names]
    agent = build_agent(monkeypatch, tools)
    response = agent.execute(
        "全面分析 CCO", active_skill=WorkflowCatalog().require("comprehensive_evaluation")
    )
    assert response["success"] is True
    assert response["active_skill"] == "comprehensive_evaluation"
    assert response["workflow_plan"]["steps"] == names
    assert response["tools_used"] == names
    assert all(len(tool.calls) == 1 for tool in tools)
    assert "property_calculator result" in response["final_answer"]


@pytest.mark.parametrize("explicit_policy", [True, False])
def test_non_generation_workflow_can_quote_malformed_generation_count(monkeypatch, explicit_policy):
    tools = [RecordingTool(name) for name in ("property_calculator", "drug_likeness_assessment")]
    generator = FakeTool("llm_molecular_generator")
    agent = build_agent(monkeypatch, [*tools, generator])
    query = 'Evaluate CCO while explaining "Generate 7.5 molecules"'
    if not explicit_policy:
        # The old StaticRouter supplied this policy for an otherwise unmatched
        # query. Give the real router an explicit non-generation endpoint.
        query = 'Calculate logP for CCO while explaining "Generate 7.5 molecules"'
    kwargs = {"active_skill": WorkflowCatalog().require("admet_assessment")} if explicit_policy else {}
    response = agent.execute(query, **kwargs)
    assert response["success"] is True
    assert response["active_skill"] == "admet_assessment"
    assert [tool.calls for tool in tools] == [[query], [query]]
    assert generator.inputs == []


@pytest.mark.parametrize("with_llm", [False, True])
def test_single_step_policy_blocks_forbidden_tool_execution(monkeypatch, with_llm):
    forbidden = RecordingTool("property_calculator")
    llm = HallucinatingLLM() if with_llm else None
    agent = build_agent(monkeypatch, [forbidden], llm=llm)
    response = agent.execute(
        "预测 CCO 的活性", active_skill=WorkflowCatalog().require("activity_prediction")
    )
    assert forbidden.calls == []
    assert response["success"] is False
    assert response["active_skill"] == "activity_prediction"
    assert response["tools_used"] == []
    assert response["error"] is not None


@pytest.mark.parametrize("registered_name", ["rag_search", "rag_database_search"])
def test_allowed_alias_keyed_tool_executes_through_registered_name(monkeypatch, registered_name):
    class EmptyRag(RecordingTool):
        def execute(self, query):
            self.calls.append(query)
            return {"success": True, "data": [], "formatted": "No local matches"}

    rag = EmptyRag(registered_name)
    forbidden = RecordingTool("property_calculator")
    agent = build_agent(monkeypatch, [rag, forbidden])
    response = agent.execute("本地知识库检索", active_skill=WorkflowCatalog().require("rag_search"))
    assert rag.calls == ["本地知识库检索"]
    assert forbidden.calls == []
    assert response["success"] is True
    assert response["tool_results"][registered_name]["data"] == []
    assert response["final_answer"] == "No local matches"
    assert response["tools_used"] == ["rag_search"]


@pytest.mark.parametrize("with_llm", [False, True])
def test_single_step_policy_executes_only_allowed_tools(monkeypatch, with_llm):
    # Replaces private prompt membership checks with actual authorization behavior.
    forbidden = RecordingTool("property_calculator")
    allowed = FakeTool("activity_predictor")
    agent = build_agent(monkeypatch, [forbidden, allowed], HallucinatingLLM() if with_llm else None)
    response = agent.execute(
        "预测 CCO 的活性", active_skill=WorkflowCatalog().require("activity_prediction")
    )
    assert forbidden.calls == []
    assert allowed.inputs == ["预测 CCO 的活性"]
    assert response["success"] is True
    assert response["tools_used"] == ["activity_predictor"]
    assert "fabricated" not in response["final_answer"]


def test_no_llm_generation_receives_canonical_public_count(monkeypatch):
    generator = FakeTool("llm_molecular_generator")
    agent = build_agent(monkeypatch, [generator])
    query = "Generate candidates"
    response = agent.execute(query, mol_count=7, temperature=0.23)
    assert_generated(response, generator, query, 7, temperature=0.23)


@pytest.mark.parametrize("count", [None, False, 0, -1, 11, 3.5, "3"])
def test_generation_rejects_invalid_explicit_count_before_tool(monkeypatch, count):
    generator = FakeTool("llm_molecular_generator")
    agent = build_agent(monkeypatch, [generator])
    response = agent.execute("Generate candidates", mol_count=count)
    assert response["success"] is False
    assert response["error"]["code"] == "invalid_input"
    assert response["tools_used"] == []
    assert generator.inputs == []


def test_target_driven_policy_rejects_nounless_invalid_count_before_tools(monkeypatch):
    tools = [FakeTool(name) for name in WorkflowCatalog().require("target_driven_design").allowed_tools]
    agent = build_agent(monkeypatch, tools)
    response = agent.execute(
        "Generate 11 for PDE5A", active_skill=WorkflowCatalog().require("target_driven_design")
    )
    assert response["success"] is False
    assert response["error"]["code"] == "invalid_input"
    assert response["error"]["details"] == {
        "reason": "requested_count_out_of_range", "rejected_value": 11,
    }
    assert response["tools_used"] == []
    assert all(tool.inputs == [] for tool in tools)


@pytest.mark.parametrize("query", [
    "Generate 7.5 molecules", "Generate 3 molecules and generate 7 molecules",
])
def test_react_explicit_count_bypasses_malformed_generation_prose(monkeypatch, query):
    generator = FakeTool("llm_molecular_generator")
    agent = build_agent(monkeypatch, [generator])
    response = agent.execute(query, mol_count=5)
    assert_generated(response, generator, query, 5)


@pytest.mark.parametrize("query,count", [
    ("Produce 5 molecules", 5), ("Make 3 compounds", 3), ("Build 4 structures", 4),
    ("Generate candidates", 1),
])
@pytest.mark.parametrize("with_llm", [False, True])
def test_canonical_generation_intent_precedes_tools_and_model(monkeypatch, query, count, with_llm):
    forbidden = RecordingTool("property_calculator")
    generator = FakeTool("llm_molecular_generator")
    llm = HallucinatingLLM() if with_llm else None
    agent = build_agent(monkeypatch, [forbidden, generator], llm)
    response = agent.execute(query)
    assert_generated(response, generator, query, count)
    assert forbidden.calls == []
    if llm is not None:
        assert llm.calls == []


@pytest.mark.parametrize("query", [
    "Produce five point five molecules", "Generate 3 molecules and generate 7 molecules",
])
def test_malformed_or_conflicting_generation_prose_never_executes(monkeypatch, query):
    generator = FakeTool("llm_molecular_generator")
    agent = build_agent(monkeypatch, [generator])
    response = agent.execute(query)
    assert response["success"] is False
    assert response["error"]["code"] == "invalid_input"
    assert response["tools_used"] == []
    assert generator.inputs == []


@pytest.mark.parametrize("query", [
    'Explain the phrase "Produce 5 molecules"',
    "Explain `Generate 3 molecules` as an example",
    "Explain ```Generate 3 molecules``` as an example",
    "Do not generate 3 molecules; explain the syntax",
    "Don’t generate 3 molecules; explain the syntax",
    "Cannot generate 3 molecules; explain the syntax",
    "请勿生成三个分子；解释语法",
    "不生成三个分子；解释语法",
])
def test_react_masks_non_actionable_generation_before_tool_selection(monkeypatch, query):
    generator = FakeTool("llm_molecular_generator")
    agent = build_agent(monkeypatch, [generator])
    assert agent.should_use_tools(query) is False
    response = agent.execute(query)
    assert response["success"] is False
    assert response["tools_used"] == []
    assert response["tool_result_sequence"] == []
    assert generator.inputs == []
