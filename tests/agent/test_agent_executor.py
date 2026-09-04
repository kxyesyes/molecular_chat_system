import src.agent.tools.llm_molecular_generator as generator_module
from src.agent.agent_executor import MolecularAgent
from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator


class CapturingLocalModel:
    model_name = "gmm-llama:latest"

    def __init__(self):
        self.prompts = []

    def generate(self, prompt, temperature=0.7, max_tokens=1000):
        self.prompts.append(prompt)
        return "\n".join("C" * length for length in range(1, 11))


def _build_agent(monkeypatch):
    model = CapturingLocalModel()
    generator = LLMMolecularGenerator(llm_model=model)
    monkeypatch.setattr(generator, "validate_smiles", lambda _smiles: True)
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
