import src.agent.tools.llm_molecular_generator as generator_module
import pytest

from src.agent.agent_executor import MolecularAgent
from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator


class CapturingLocalModel:
    model_name = "gmm-llama:latest"

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, temperature=0.7, max_tokens=1000):
        self.prompts.append(prompt)
        return "\n".join("C" * length for length in range(1, 11))


class RecordingMixedTool:
    name = "property_calculator"

    def __init__(self):
        self.should_use_calls = []
        self.calls = []

    def should_use(self, query):
        self.should_use_calls.append(query)
        return True

    def execute(self, query):
        self.calls.append(query)
        return {
            "success": True,
            "formatted": "properties",
            "data": {"query": query},
        }


def _build_agent(monkeypatch):
    model = CapturingLocalModel()
    generator = LLMMolecularGenerator(llm_model=model)
    monkeypatch.setattr(generator, "validate_smiles", lambda _smiles: True)
    monkeypatch.setattr(generator, "_check_rdkit", lambda _result: True)
    monkeypatch.setattr(generator_module, "RDKIT_AVAILABLE", False)
    agent = MolecularAgent.__new__(MolecularAgent)
    agent.llm = None
    agent.core_tools = [generator]
    agent.optional_tools = {}
    return agent, model


def test_agent_executor_omitted_count_rejects_invalid_text_before_model(monkeypatch):
    agent, model = _build_agent(monkeypatch)

    result = agent.execute("Generate 11 molecules")

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert "between 1 and 10" in result["error"]["message"]
    assert result["tool_results"][0]["error"] == result["error"]
    assert result["error"]["details"] == {
        "reason": "requested_count_out_of_range",
        "rejected_value": 11,
    }
    assert model.prompts == []


def test_agent_executor_oversized_numeric_token_is_canonical_invalid_input(
    monkeypatch,
):
    agent, model = _build_agent(monkeypatch)

    result = agent.execute(f"Generate {'9' * 5000} molecules")

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert result["error"]["details"]["reason"] == (
        "malformed_requested_count"
    )
    assert model.prompts == []


def test_execute_tools_rejects_malformed_count_before_any_tool_boundary(monkeypatch):
    agent, model = _build_agent(monkeypatch)
    generator = agent.core_tools[0]
    should_use_calls = []
    original_should_use = generator.should_use

    def recording_should_use(query):
        should_use_calls.append(query)
        return original_should_use(query)

    monkeypatch.setattr(generator, "should_use", recording_should_use)

    result = agent.execute_tools("Generate - 1 molecules")

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert should_use_calls == []
    assert model.prompts == []


@pytest.mark.parametrize(
    "query", ["Generate 11 candidates", "Generate 11 for PDE5A"]
)
def test_agent_executor_canonical_generation_intent_returns_invalid_input(
    monkeypatch, query
):
    agent, model = _build_agent(monkeypatch)

    result = agent.execute(query)

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert result["message"] != "No relevant tools found for this query"
    assert model.prompts == []


def test_agent_executor_omitted_count_preserves_valid_text_count(monkeypatch):
    agent, model = _build_agent(monkeypatch)

    result = agent.execute("Generate 7 molecules")

    assert result["success"] is True
    assert "Task: provide 7 valid" in model.prompts[0]


def test_agent_executor_explicit_count_remains_authoritative(monkeypatch):
    agent, model = _build_agent(monkeypatch)

    result = agent.execute("Generate 7.5 molecules", mol_count=3)

    assert result["success"] is True
    assert "Task: provide 3 valid" in model.prompts[0]


def test_agent_executor_supports_synthesize_through_shared_grammar(monkeypatch):
    agent, model = _build_agent(monkeypatch)

    result = agent.execute("Synthesize 3 molecules")

    assert result["success"] is True
    assert "Task: provide 3 valid" in model.prompts[0]


def test_agent_execute_rejects_invalid_mol_count_before_non_generation_tools(
    monkeypatch,
):
    agent, model = _build_agent(monkeypatch)
    other_tool = RecordingMixedTool()
    agent.core_tools.insert(0, other_tool)

    result = agent.execute("Analyze CCO", mol_count=11)

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert other_tool.should_use_calls == []
    assert other_tool.calls == []
    assert model.prompts == []


def test_execute_tools_rejects_invalid_mol_count_before_any_tool(monkeypatch):
    agent, model = _build_agent(monkeypatch)
    other_tool = RecordingMixedTool()
    agent.core_tools.insert(0, other_tool)

    result = agent.execute_tools("Analyze CCO", mol_count=11)

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert other_tool.should_use_calls == []
    assert other_tool.calls == []
    assert model.prompts == []


def test_valid_mol_count_does_not_force_generation_for_non_generation_query(
    monkeypatch,
):
    agent, model = _build_agent(monkeypatch)
    other_tool = RecordingMixedTool()
    agent.core_tools.insert(0, other_tool)

    result = agent.execute("Analyze CCO", mol_count=3)

    assert result["success"] is True
    assert other_tool.calls == ["Analyze CCO"]
    assert model.prompts == []


def test_agent_executor_prevalidates_invalid_mixed_generation_before_all_tools(
    monkeypatch,
):
    agent, model = _build_agent(monkeypatch)
    other_tool = RecordingMixedTool()
    agent.core_tools.insert(0, other_tool)

    result = agent.execute("Analyze CCO and Generate 11 molecules")

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert other_tool.should_use_calls == []
    assert other_tool.calls == []
    assert model.prompts == []


def test_agent_executor_valid_mixed_generation_behavior_is_unchanged(monkeypatch):
    agent, model = _build_agent(monkeypatch)
    other_tool = RecordingMixedTool()
    agent.core_tools.insert(0, other_tool)

    result = agent.execute("Analyze CCO and Generate 3 molecules")

    assert result["success"] is True
    assert other_tool.calls == ["Analyze CCO and Generate 3 molecules"]
    assert "Task: provide 3 valid" in model.prompts[0]


@pytest.mark.parametrize(
    "query",
    [
        "For example, analyze CCO and generate 3 molecules",
        "As an example, analyze CCO and generate 3 molecules",
        "Explain how to analyze CCO and generate 3 molecules",
    ],
)
def test_agent_executor_does_not_generate_within_explanation_scope(
    monkeypatch, query
):
    agent, model = _build_agent(monkeypatch)
    other_tool = RecordingMixedTool()
    agent.core_tools.insert(0, other_tool)

    result = agent.execute(query)

    assert result["success"] is True
    assert other_tool.calls == [query]
    assert model.prompts == []


@pytest.mark.parametrize(
    "query",
    [
        "Generate 3 molecules and explain how to generate 11 molecules",
        "Explain how to generate 11 molecules and generate 3 molecules",
    ],
)
def test_agent_executor_uses_only_actionable_generation_occurrence(
    monkeypatch, query
):
    agent, model = _build_agent(monkeypatch)

    result = agent.execute(query)

    assert result["success"] is True
    assert "Task: provide 3 valid" in model.prompts[0]


@pytest.mark.parametrize(
    "query",
    [
        "Don't generate 11 molecules and then generate 3 molecules",
        "Generate 3 molecules and as an example generate 11 molecules",
    ],
)
def test_agent_executor_occurrence_scope_is_bounded(monkeypatch, query):
    agent, model = _build_agent(monkeypatch)

    result = agent.execute(query)

    assert result["success"] is True
    assert "Task: provide 3 valid" in model.prompts[0]


@pytest.mark.parametrize(
    "query",
    [
        "Generate 3 molecules and generate 11 molecules",
        "Generate 3 molecules and generate 4 molecules",
        "Generate 3 molecules and generate 7.5 molecules",
        "Generate 3 and generate 11",
        "Generate 3 and generate 4",
        "Generate 3 and generate 7.5",
        "Generate 3 molecules and generate - 1 molecule",
        "Synthesize 3 compounds; Synthesize + 2 compounds",
        "Generate 7.5 potent",
        "Generate 3 potent + synthesize 4 potent",
        "Generate 3 potent plus synthesize 4 potent",
        "Generate 3 potent and also synthesize 4 potent",
    ],
)
def test_agent_executor_validates_all_generation_occurrences_before_tools(
    monkeypatch, query
):
    agent, model = _build_agent(monkeypatch)
    other_tool = RecordingMixedTool()
    agent.core_tools.insert(0, other_tool)

    result = agent.execute(query)

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert other_tool.should_use_calls == []
    assert other_tool.calls == []
    assert model.prompts == []


@pytest.mark.parametrize(
    "separator",
    [" then ", ". ", "; ", ", ", " but ", " instead "],
)
def test_agent_executor_aggregates_nounless_counts_before_model(
    monkeypatch, separator
):
    agent, model = _build_agent(monkeypatch)
    other_tool = RecordingMixedTool()
    agent.core_tools.insert(0, other_tool)

    result = agent.execute(f"Generate 11{separator}Generate 3")

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert other_tool.should_use_calls == []
    assert other_tool.calls == []
    assert model.prompts == []


@pytest.mark.parametrize(
    "query",
    [
        "Generate 1, 000 molecules",
        "生成1 1个分子",
        "生成1\u202f000个分子",
        "生成1， 000个分子",
        "生成一 1个分子",
        "生成1 一个分子",
        "生成一1个分子",
        "生成1一个分子",
    ],
)
def test_agent_executor_rejects_grouped_counts_before_all_tools(monkeypatch, query):
    agent, model = _build_agent(monkeypatch)
    other_tool = RecordingMixedTool()
    agent.core_tools.insert(0, other_tool)

    result = agent.execute(query)

    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert other_tool.should_use_calls == []
    assert other_tool.calls == []
    assert model.prompts == []


@pytest.mark.parametrize(
    "query",
    [
        "Explain `Generate 3 molecules` as an example",
        "Explain `Generate 11 molecules` as an example",
        "Explain ```Generate 3 molecules``` as an example",
        "Explain this example:\n```\nGenerate 3 molecules\n```",
        "Explain this example:\n```\nGenerate 3 molecules",
        "Explain this example:\n~~~text\nGenerate 3 molecules",
        "Do not generate 3 molecules; explain the syntax",
        "Don’t generate 3 molecules; explain the syntax",
        "Can't generate 3 molecules; explain the syntax",
        "Cannot generate 3 molecules; explain the syntax",
        "请勿生成三个分子；解释语法",
        "不生成三个分子；解释语法",
    ],
)
def test_agent_executor_masks_non_actionable_generation_examples(monkeypatch, query):
    agent, model = _build_agent(monkeypatch)

    result = agent.execute(query)

    assert result["success"] is False
    assert result["message"] == "No relevant tools found for this query"
    assert model.prompts == []
