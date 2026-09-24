"""Offline transport tests: real generator, synthetic local-model responses."""
from copy import deepcopy

import pytest

from src.agent.contracts import AgentContext
from src.agent.orchestrators.base import WorkflowStep
from src.agent.orchestrators.workflow import WorkflowOrchestrator
from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
from src.agent.tooling.factory import build_tool_registry
from src.agent.runtime.delegated_executor import SpecialistDispatch
from src.agent.specialists import build_default_specialists


class RecordingModel:
    model_name = "gmm-llama:latest"

    def __init__(self):
        self.calls = []

    def generate(self, prompt, *, temperature, max_tokens):
        self.calls.append(temperature)
        return "CCO"


@pytest.mark.parametrize("timeout", [None, 5])
@pytest.mark.parametrize("dispatch", [False, True])
def test_temperature_survives_direct_threaded_and_registered_dispatch(timeout, dispatch):
    model = RecordingModel()
    tool = LLMMolecularGenerator(model)
    orchestrator = WorkflowOrchestrator()
    registry = build_tool_registry([tool]) if dispatch else None
    step = WorkflowStep("generate", tool.name, timeout_seconds=timeout)
    original = deepcopy(step)
    digests = []
    for temperature in (0.23, 0.81):
        context = AgentContext(query="生成 1 个分子", trace_id=f"temp-{temperature}",
                               temperature=temperature, mol_count=1)
        if dispatch:
            orchestrator.step_dispatch = SpecialistDispatch(context, registry, build_default_specialists())
        result = orchestrator.run(
            context, [step], registry.as_mapping() if dispatch else {tool.name: tool},
        )
        assert result.success is True, result
        digests.append(result.tool_results[0].provenance.input_digest)
    assert model.calls == [0.23, 0.81]
    assert digests[0] != digests[1], "temperature must participate in checkpoint/provenance identity"
    assert step == original


def test_legacy_string_temperature_and_default_still_work():
    model = RecordingModel()
    tool = LLMMolecularGenerator(model)
    assert tool.execute("生成 1 个分子", temperature=0.23)["success"]
    assert tool.execute("生成 1 个分子")["success"]
    assert model.calls == [0.23, 0.7]


@pytest.mark.parametrize("temperature", [None, True, "hot", float("nan"), float("inf")])
def test_invalid_structured_temperature_fails_before_model(temperature):
    model = RecordingModel()
    result = LLMMolecularGenerator(model).execute({
        "query": "生成 1 个分子",
        "metadata": {"requested_count": 1, "temperature": temperature},
    })
    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"
    assert result["error"]["details"]["reason"] == "invalid_temperature"
    assert model.calls == []
