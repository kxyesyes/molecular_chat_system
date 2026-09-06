import pytest

from src.agent.react_agent import ReActMolecularAgent
from src.agent.workflows import WorkflowCatalog


def test_workflow_skill_uses_planner_and_returns_legacy_dict(monkeypatch):
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.tools = {}
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None

    def fake_execute_workflow_skill(query, result, event_callback=None):
        result["success"] = True
        result["active_skill"] = "comprehensive_evaluation"
        result["final_answer"] = "workflow ok"
        result["tools_used"] = ["property_calculator"]
        return result

    monkeypatch.setattr(agent, "_execute_workflow_skill", fake_execute_workflow_skill)

    policy = WorkflowCatalog().require("comprehensive_evaluation")
    response = agent.execute("全面分析 CCO", active_skill=policy)

    assert response["success"] is True
    assert response["active_skill"] == "comprehensive_evaluation"
    assert response["final_answer"] == "workflow ok"


def test_non_generation_workflow_can_quote_malformed_generation_count(monkeypatch):
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.tools = {}
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    calls = []

    def fake_execute_workflow_skill(query, result, event_callback=None):
        calls.append(query)
        result["success"] = True
        result["active_skill"] = "admet_assessment"
        result["final_answer"] = "workflow ok"
        return result

    monkeypatch.setattr(agent, "_execute_workflow_skill", fake_execute_workflow_skill)
    policy = WorkflowCatalog().require("admet_assessment")

    response = agent.execute(
        'Evaluate CCO while explaining "Generate 7.5 molecules"',
        active_skill=policy,
    )

    assert response["success"] is True
    assert calls == ['Evaluate CCO while explaining "Generate 7.5 molecules"']


def test_auto_route_scopes_quoted_generation_phrase_after_routing(monkeypatch):
    policy = WorkflowCatalog().require("admet_assessment")

    class StaticRouter:
        def route(self, query, llm=None):
            return policy

    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.tools = {}
    agent.max_iterations = 5
    agent.skill_router = StaticRouter()
    agent._active_skill = None
    calls = []

    def fake_execute_workflow_skill(query, result, event_callback=None):
        calls.append(query)
        result["success"] = True
        result["active_skill"] = "admet_assessment"
        result["final_answer"] = "workflow ok"
        return result

    monkeypatch.setattr(agent, "_execute_workflow_skill", fake_execute_workflow_skill)

    response = agent.execute(
        'Evaluate CCO while explaining "Generate 7.5 molecules"'
    )

    assert response["success"] is True
    assert response["active_skill"] == "admet_assessment"
    assert calls == ['Evaluate CCO while explaining "Generate 7.5 molecules"']


def test_single_step_policy_blocks_forbidden_tool_execution():
    calls = []

    class RecordingPropertyTool:
        def execute(self, query):
            calls.append(query)
            return {"success": True, "summary": "forbidden tool ran"}

    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.tools = {"property_calculator": RecordingPropertyTool()}
    agent._active_skill = WorkflowCatalog().require("activity_prediction")

    observation, raw_result = agent._execute_tool("property_calculator", "CCO")

    assert calls == []
    assert raw_result == {}
    assert "不允许执行工具 'property_calculator'" in observation


def test_allowed_alias_keyed_tool_executes_through_registered_name():
    calls = []

    class RegisteredRagTool:
        def execute(self, query):
            calls.append(query)
            return {"success": True, "summary": "rag result"}

    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.tools = {"rag_search": RegisteredRagTool()}
    agent._active_skill = WorkflowCatalog().require("rag_search")

    observation, raw_result = agent._execute_tool("rag_search", "本地知识库检索")

    assert calls == ["本地知识库检索"]
    assert raw_result["success"] is True
    assert observation == "rag result"


def test_single_step_policy_prompt_names_only_allowed_tools():
    class PromptTool:
        def __init__(self, description):
            self.description = description

    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.tools = {
        "property_calculator": PromptTool("Calculate molecular properties"),
        "activity_predictor": PromptTool("Predict molecular activity"),
    }
    agent._active_skill = WorkflowCatalog().require("activity_prediction")

    prompt = agent._build_react_prompt("预测 CCO 的活性", [])

    assert "activity_predictor" in prompt
    assert "property_calculator" not in prompt


def test_no_llm_fallback_blocks_forbidden_policy_tool_end_to_end():
    calls = []

    class MatchingPropertyTool:
        def should_use(self, query):
            return True

        def execute(self, query):
            calls.append(query)
            return {"success": True, "formatted": "fabricated property result"}

    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.tools = {"property_calculator": MatchingPropertyTool()}
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None
    policy = WorkflowCatalog().require("activity_prediction")

    response = agent.execute("预测 CCO 的活性", active_skill=policy)

    assert calls == []
    assert response["success"] is False
    assert response["active_skill"] == "activity_prediction"
    assert response["tools_used"] == []
    assert "没有允许且匹配的可用工具" in response["final_answer"]


def test_no_llm_generation_fallback_receives_canonical_public_count():
    class MatchingGenerator:
        name = "llm_molecular_generator"

        def __init__(self):
            self.calls = []

        def should_use(self, query):
            return True

        def execute(self, query):
            self.calls.append(query)
            return {"success": True, "formatted": "generated"}

    tool = MatchingGenerator()
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.tools = {tool.name: tool}
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None

    response = agent.execute("Generate candidates", mol_count=7)

    assert response["success"] is True
    assert tool.calls == [
        {
            "query": "Generate candidates",
            "metadata": {"requested_count": 7},
            "outputs": {},
        }
    ]


def test_no_llm_generation_fallback_rejects_invalid_count_before_tool():
    class MatchingGenerator:
        name = "llm_molecular_generator"

        def __init__(self):
            self.calls = []

        def should_use(self, query):
            return True

        def execute(self, query):
            self.calls.append(query)
            return {"success": True, "formatted": "generated"}

    tool = MatchingGenerator()
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.tools = {tool.name: tool}
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None

    response = agent.execute("Generate candidates", mol_count=11)

    assert response["success"] is False
    assert response["error"]["code"] == "invalid_input"


def test_target_driven_policy_rejects_nounless_invalid_count_before_tools(
    monkeypatch,
):
    class RouterThatMustNotRun:
        def route(self, query, llm=None):
            raise AssertionError("router ran before generation preflight")

    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.tools = {}
    agent.max_iterations = 5
    agent.skill_router = RouterThatMustNotRun()
    agent._active_skill = None
    monkeypatch.setattr(
        agent,
        "_execute_workflow_skill",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("workflow tools ran before generation preflight")
        ),
    )

    response = agent.execute(
        "Generate 11 for PDE5A",
        active_skill=WorkflowCatalog().require("target_driven_design"),
    )

    assert response["success"] is False
    assert response["error"]["code"] == "invalid_input"
    assert response["error"]["details"] == {
        "reason": "requested_count_out_of_range",
        "rejected_value": 11,
    }
    assert response["tools_used"] == []


def test_react_explicit_count_bypasses_malformed_generation_prose():
    class MatchingGenerator:
        name = "llm_molecular_generator"

        def __init__(self):
            self.calls = []

        def should_use(self, query):
            return True

        def execute(self, query):
            self.calls.append(query)
            return {"success": True, "formatted": "generated"}

    tool = MatchingGenerator()
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.tools = {tool.name: tool}
    agent.max_iterations = 5
    agent.skill_router = None
    agent._active_skill = None

    response = agent.execute("Generate 7.5 molecules", mol_count=5)

    assert response["success"] is True
    assert tool.calls[0]["metadata"]["requested_count"] == 5


def test_no_llm_fallback_prioritizes_all_canonical_generation_verbs():
    class MatchingTool:
        def __init__(self, name):
            self.name = name
            self.calls = []

        def should_use(self, _query):
            return True

        def execute(self, query):
            self.calls.append(query)
            return {"success": True, "formatted": self.name}

    for query, count in (
        ("Produce 5 molecules", 5),
        ("Make 3 compounds", 3),
        ("Build 4 structures", 4),
    ):
        property_tool = MatchingTool("property_calculator")
        generator = MatchingTool("llm_molecular_generator")
        agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
        agent.llm = None
        agent.tools = {
            property_tool.name: property_tool,
            generator.name: generator,
        }
        agent.max_iterations = 5
        agent.skill_router = None
        agent._active_skill = None

        response = agent.execute(query)

        assert response["success"] is True
        assert property_tool.calls == []
        assert generator.calls[0]["metadata"]["requested_count"] == count


def test_llm_path_uses_canonical_generation_intent_before_model():
    class HallucinatingLLM:
        def __init__(self):
            self.calls = []

        def generate(self, prompt):
            self.calls.append(prompt)
            return "最终答案: fabricated"

    class Generator:
        name = "llm_molecular_generator"

        def __init__(self):
            self.calls = []

        def execute(self, query):
            self.calls.append(query)
            return {"success": True, "formatted": "generated"}

    for query, count in (
        ("Produce 5 molecules", 5),
        ("Make 3 compounds", 3),
        ("Build 4 structures", 4),
    ):
        llm = HallucinatingLLM()
        generator = Generator()
        agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
        agent.llm = llm
        agent.tools = {generator.name: generator}
        agent.max_iterations = 2
        agent.skill_router = None
        agent._active_skill = None

        response = agent.execute(query)

        assert response["success"] is True
        assert llm.calls == []
        assert generator.calls[0]["metadata"]["requested_count"] == count


def test_react_generation_helpers_share_quoted_canonical_grammar():
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.tools = {}

    for query in (
        "Produce 5 molecules",
        "Make 3 compounds",
        "Build 4 structures",
    ):
        assert agent._is_molecular_generation_query(query) is True
        fallback = agent._intelligent_fallback_response(query)
        assert "行动: llm_molecular_generator" in fallback

    quoted = 'Explain the phrase "Produce 5 molecules"'
    assert agent._is_molecular_generation_query(quoted) is False
    assert "行动: llm_molecular_generator" not in agent._intelligent_fallback_response(
        quoted
    )

    malformed = "Produce five point five molecules"
    assert agent._is_molecular_generation_query(malformed) is True
    fallback = agent._intelligent_fallback_response(malformed)
    assert "Invalid molecular generation request" in fallback
    assert "行动: llm_molecular_generator" not in fallback


@pytest.mark.parametrize(
    "query",
    [
        "Explain `Generate 3 molecules` as an example",
        "Explain ```Generate 3 molecules``` as an example",
        "Do not generate 3 molecules; explain the syntax",
        "Don’t generate 3 molecules; explain the syntax",
        "Cannot generate 3 molecules; explain the syntax",
        "请勿生成三个分子；解释语法",
        "不生成三个分子；解释语法",
    ],
)
def test_react_masks_non_actionable_generation_before_tool_selection(query):
    class CanonicalGenerator:
        name = "llm_molecular_generator"

        def __init__(self):
            self.calls = []

        def should_use(self, query):
            from src.agent.contracts.generation_request import has_generation_intent

            return has_generation_intent(query)

        def execute(self, query):
            self.calls.append(query)
            return {"success": True, "formatted": "generated"}

    generator = CanonicalGenerator()
    agent = ReActMolecularAgent.__new__(ReActMolecularAgent)
    agent.llm = None
    agent.tools = {generator.name: generator}
    agent.max_iterations = 2
    agent.skill_router = None
    agent._active_skill = None

    response = agent.execute(query)

    assert response["success"] is True
    assert response["tools_used"] == []
    assert generator.calls == []
