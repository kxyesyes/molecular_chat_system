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

    result = agent.execute("Generate 7 molecules", mol_count=3)

    assert result["success"] is True
    assert "Task: provide 3 valid" in model.prompts[0]


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
        "Explain `Generate 3 molecules` as an example",
        "Explain `Generate 11 molecules` as an example",
        "Explain ```Generate 3 molecules``` as an example",
        "Explain this example:\n```\nGenerate 3 molecules\n```",
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
