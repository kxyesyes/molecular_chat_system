"""Public adapter scientific boundaries; synthetic observations, no model/assets.

Only tool factories are replaced. Router, Supervisor, Session, registry contracts,
RDKit validation and evidence handling are the production implementations.
"""

from copy import deepcopy
from types import SimpleNamespace
import asyncio
import socket

import pytest

import src.agent.agent_executor as adapter_module


class FixtureTool:
    def __init__(self, name, result):
        self.name = name
        self.description = "Synthetic adapter regression fixture"
        self.result = result
        self.calls = []
        self.should_use_calls = []
        self.closed = 0

    def should_use(self, query):
        # Deliberately over-eager: canonical policy, not this flag, must decide.
        self.should_use_calls.append(query)
        return True

    def execute(self, query, **kwargs):
        self.calls.append((deepcopy(query), deepcopy(kwargs)))
        return deepcopy(self.result)

    def close(self):
        self.closed += 1


def success(data, **fields):
    return {
        "success": True,
        "data": data,
        "message": "Synthetic fixture observation",
        "formatted": "Synthetic fixture observation",
        **fields,
    }


def unavailable(name):
    return {
        "success": False,
        "status": "unavailable",
        "data": None,
        "message": "Synthetic dependency unavailable",
        "error": {
            "code": "external_tool_unavailable",
            "message": "Synthetic dependency unavailable",
            "details": {"dependency": name},
        },
        "warnings": ["synthetic_dependency_unavailable"],
        "quality": {"retryable": False, "fixture": True},
    }


def activity_result(*, demo=False):
    # Complete transport metadata; the demo flag is the ONLY invalid field.
    # The digest is a fixture identifier, not evidence of real model inference.
    provenance = {
        "model_id": "synthetic-adapter-test",
        "weights_sha256": "a" * 64,
        "task_type": "regression",
        "endpoint": "Ki",
        "units": "nM",
        "demo_mode": demo,
        "fallback_used": False,
    }
    return success([
        {
            "smiles": "CCO", "success": True, "task_type": "regression",
            "endpoint": "Ki", "units": "nM", "value": 7.125,
            "activity_score": 7.125,
            "model_provenance": provenance,
        }
    ], formatted="SYNTHETIC_ACTIVITY_CLAIM", quality={
        "model_provenance": {
            "model_path": "synthetic-unloaded-model", "demo_mode": demo,
        }
    })


@pytest.fixture
def rig(monkeypatch):
    def no_network(*args, **kwargs):
        raise AssertionError("Adapter science tests must not connect to services")

    monkeypatch.setattr(socket, "create_connection", no_network)
    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(socket.socket, "connect_ex", no_network)
    monkeypatch.setenv("AGENT_HARNESS_MODE", "legacy")
    core = [
        FixtureTool("property_calculator", success([
            {"smiles": "CCO", "properties": {"molecular_weight": 46.069}}
        ])),
        FixtureTool("drug_likeness_assessment", success([
            {"smiles": "CCO", "drug_likeness": {"lipinski_violations": 0}}
        ])),
        FixtureTool("admet_predictor", success([
            {"smiles": "CCO", "admet": {"prediction_method": "synthetic-fixture"}}
        ])),
        FixtureTool("llm_molecular_generator", success(
            [{"smiles": "CCO"}],
            quality={"model": "gmm-llama:latest", "requested_count": 1},
        )),
        FixtureTool("candidate_ranker", success({"top_candidates": []})),
    ]
    optional = {
        "ActivityPredictorTool": FixtureTool("activity_predictor", activity_result()),
        "TargetDatabaseTool": FixtureTool("target_database_search", success([
            {"gene_symbol": "BCHE", "source_record_id": "synthetic-BCHE",
             "source": "synthetic-fixture"}
        ])),
        "ReverseTargetTool": FixtureTool(
            "reverse_target_predictor", unavailable("reverse-target-fixture")
        ),
        "MolecularDocking": FixtureTool(
            "molecular_docking", unavailable("docking-fixture")
        ),
    }
    tools = {tool.name: tool for tool in [*core, *optional.values()]}
    loads = []
    factory_errors = {}

    def get_optional(name, *args, **kwargs):
        loads.append(name)
        if name in factory_errors:
            raise factory_errors[name]
        # Fail closed: never fall through to a real optional-tool constructor.
        if name not in optional:
            raise AssertionError(f"Unexpected optional factory: {name}")
        return optional[name]

    monkeypatch.setattr(adapter_module, "get_core_tools", lambda: list(core))
    monkeypatch.setattr(adapter_module, "get_optional_tool", get_optional)
    return SimpleNamespace(
        tools=tools, core=core, optional=optional, loads=loads,
        factory_errors=factory_errors,
        make_agent=lambda **kwargs: adapter_module.MolecularAgent(**kwargs),
    )


def observation(result, name):
    rows = [row for row in result["tool_results"] if row["tool_name"] == name]
    assert len(rows) == 1, result
    return rows[0]


def assert_no_execution(rig):
    assert all(not tool.calls for tool in rig.tools.values())


@pytest.mark.parametrize("query", ["你好，今天心情不错。", "什么是药物设计"])
def test_public_policy_ignores_overeager_should_use_for_chat(rig, query):
    agent = rig.make_agent()
    assert agent.should_use_tools(query) is False

    result = agent.execute(query)

    assert result["success"] is False
    assert result["used_tools"] == []
    assert result["tool_results"] == []
    assert rig.loads == []
    assert_no_execution(rig)
    assert all(not tool.should_use_calls for tool in rig.tools.values())


@pytest.mark.parametrize("method", ["execute", "execute_tools"])
def test_invalid_smiles_cannot_reach_a_tool_that_would_claim_success(rig, method):
    result = getattr(rig.make_agent(), method)("计算 SMILES: C1CC 的理化性质")

    assert result["success"] is False, result
    assert result["status"] == "rejected"
    assert result["error"]["code"] == "invalid_input"
    assert_no_execution(rig)
    assert result["tool_results" if method == "execute" else "results"] == []
    assert result["used_tools"] == []
    assert result["agent_events"] == []
    assert rig.loads == []


@pytest.mark.parametrize("method", ["execute", "execute_tools"])
def test_missing_docking_receptor_and_box_never_executes_docking(rig, method):
    result = getattr(rig.make_agent(), method)("请对 CCO 进行分子对接")

    assert result["success"] is False
    assert result["status"] == "rejected"
    assert result["error"]["code"] == "invalid_input"
    assert_no_execution(rig)
    assert result["tool_results" if method == "execute" else "results"] == []
    assert result["used_tools"] == []
    assert result["agent_events"] == []
    assert rig.loads == []


@pytest.mark.parametrize("demo", [False, True], ids=["valid-control", "demo-rejected"])
def test_activity_contract_rejects_only_demo_observation(rig, demo):
    tool = rig.tools["activity_predictor"]
    tool.result = activity_result(demo=demo)

    result = rig.make_agent().execute("预测 CCO 的活性")

    assert rig.loads == ["ActivityPredictorTool"]
    assert len(tool.calls) == 1
    row = observation(result, tool.name)
    assert result["success"] is (not demo)
    assert row["success"] is (not demo)
    if demo:
        assert row["error"]["code"] == "invalid_output"
        assert row["formatted"] == ""
        assert "SYNTHETIC_ACTIVITY_CLAIM" not in result["response"]
    else:
        assert result["status"] == "completed"
        assert row["data"][0]["value"] == 7.125
        assert row["provenance"]["input_digest"]
    assert result["agent_events"]


@pytest.mark.parametrize("target_status", ["not_found", "unavailable"])
def test_absent_target_evidence_blocks_generator(rig, target_status):
    target = rig.tools["target_database_search"]
    if target_status == "not_found":
        # Service-level status belongs in quality, not the observation enum.
        target.result = success([], quality={"status": "not_found"})
    else:
        target.result = unavailable("target-fixture")

    result = rig.make_agent().execute("针对 BuChE 生成 1 个候选分子", mol_count=1)

    assert result["success"] is False
    assert len(target.calls) == 1
    assert target.calls[0][0] == "BCHE"
    assert all(not tool.calls for name, tool in rig.tools.items() if name != target.name)
    assert result["used_tools"] == [target.name]
    row = observation(result, target.name)
    if target_status == "not_found":
        assert row["success"] is True
        assert row["quality"]["status"] == "not_found"
        assert result["partial"] is True
        assert "target_evidence" in str(result["metadata"]["skipped_steps"])
    else:
        assert row["status"] == "unavailable"
        assert row["error"]["code"] == "external_tool_unavailable"


def test_properties_survive_optional_failure_without_complete_success(rig):
    activity = rig.tools["activity_predictor"]
    activity.result = unavailable("activity-fixture")
    result = rig.make_agent().execute("请全面分析这个分子的成药性：CCO")

    assert result["success"] is False
    assert result["partial"] is True
    assert result["status"] == "partial"
    properties = observation(result, "property_calculator")
    failed = observation(result, activity.name)
    assert properties["success"] is True
    assert properties["data"][0]["properties"]["molecular_weight"] == 46.069
    assert properties["quality"]["validated"] is True
    assert properties["provenance"]["input_digest"]
    assert failed["success"] is False
    assert result["used_tools"].index("property_calculator") < result["used_tools"].index(activity.name)
    assert rig.loads.count("ActivityPredictorTool") == 1
    assert len(activity.calls) == 1
    assert failed["status"] == "unavailable"
    assert failed["error"]["code"] == "external_tool_unavailable"
    assert failed["error"]["details"] == {"dependency": "activity-fixture"}
    assert "synthetic_dependency_unavailable" in failed["warnings"]
    assert rig.tools["llm_molecular_generator"].calls == []


def test_missing_optional_factory_keeps_canonical_preflight_failure(rig):
    rig.factory_errors["ActivityPredictorTool"] = RuntimeError("synthetic missing dependency")

    result = rig.make_agent().execute("预测 CCO 的活性")

    assert result["success"] is False
    assert result["partial"] is False
    assert result["error"]["code"] == "tool_unavailable", result
    assert rig.loads == ["ActivityPredictorTool"]
    assert_no_execution(rig)


@pytest.mark.parametrize("exception_type", [KeyboardInterrupt, asyncio.CancelledError])
def test_optional_factory_does_not_swallow_process_or_task_cancellation(rig, exception_type):
    rig.factory_errors["ActivityPredictorTool"] = exception_type()

    with pytest.raises(exception_type):
        rig.make_agent().execute("预测 CCO 的活性")

    assert rig.loads == ["ActivityPredictorTool"]
    assert_no_execution(rig)


def test_invalid_generated_smiles_are_rejected_by_real_candidate_validator(rig):
    generator = rig.tools["llm_molecular_generator"]
    generator.result = success(
        [{"smiles": "C1CC"}], formatted="INVALID_GENERATION_CLAIM",
        quality={"model": "gmm-llama:latest", "requested_count": 1},
    )

    result = rig.make_agent().execute("生成 1 个分子", mol_count=1)

    assert len(generator.calls) == 1
    assert result["success"] is False
    row = observation(result, generator.name)
    assert row["error"]["code"] == "invalid_output"
    assert row["data"]["candidates"] == []
    assert row["quality"]["invalid_count"] == 1
    assert row["quality"]["valid_count"] == 0
    assert row["quality"]["validation_method"] == "RDKit"
    assert row["formatted"] == ""
    assert "INVALID_GENERATION_CLAIM" not in result["response"]
    assert rig.loads == []


def test_duplicate_generated_identity_is_partial_not_two_valid_candidates(rig):
    generator = rig.tools["llm_molecular_generator"]
    generator.result = success(
        [{"smiles": "CCO"}, {"smiles": "OCC"}],
        quality={"model": "gmm-llama:latest", "requested_count": 2},
    )

    result = rig.make_agent().execute("生成 2 个分子", mol_count=2)

    assert result["success"] is False
    assert result["partial"] is True
    assert result["status"] == "partial"
    row = observation(result, generator.name)
    assert row["quality"]["requested_count"] == 2
    assert row["quality"]["unique_count"] == 1
    assert row["quality"]["duplicate_count"] == 1
    assert len(row["data"]["candidates"]) == 1
    assert row["data"]["candidates"][0]["canonical_smiles"] == "CCO"


def test_valid_property_request_with_mol_count_does_not_generate(rig):
    agent = rig.make_agent()
    before = tuple(agent.get_all_tools())

    result = agent.execute("计算 CCO 的理化性质", mol_count=3)

    assert result["success"] is True
    assert result["status"] == "completed"
    assert result["partial"] is False
    assert result["used_tools"] == ["property_calculator", "drug_likeness_assessment"]
    assert observation(result, "property_calculator")["quality"]["validated"] is True
    assert len(rig.tools["property_calculator"].calls) == 1
    assert len(rig.tools["drug_likeness_assessment"].calls) == 1
    assert rig.tools["llm_molecular_generator"].calls == []
    assert rig.loads == []
    assert tuple(agent.get_all_tools()) == before
    assert all(tool.closed == 0 for tool in rig.tools.values())


def test_main_model_does_not_replace_generator_or_rewrite_science(rig):
    class ForbiddenMainModel:
        def generate(self, *args, **kwargs):
            pytest.fail("Deterministic generation must not call the main model")

    local_model = SimpleNamespace(model_name="gmm-llama:latest")
    generator = rig.tools["llm_molecular_generator"]
    generator.llm_model = local_model
    main_model = ForbiddenMainModel()
    agent = rig.make_agent(llm=main_model)

    result = agent.execute("生成 1 个分子", mol_count=1)

    assert result["success"] is True
    assert agent.llm is main_model
    assert generator.llm_model is local_model
    assert len(generator.calls) == 1
    row = observation(result, generator.name)
    assert row["quality"]["validation_method"] == "RDKit"
    assert row["data"]["candidates"][0]["canonical_smiles"] == "CCO"
    assert row["provenance"]["model_name"] == "gmm-llama:latest"
    assert generator.closed == 0


@pytest.mark.parametrize("method", ["execute", "execute_tools", "supervisor"])
def test_nondefault_temperature_reaches_generator(rig, method):
    from src.agent.supervisor import SupervisorAgent
    from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator

    class RecordingModel:
        model_name = "gmm-llama:latest"

        def __init__(self):
            self.temperatures = []

        def generate(self, prompt, *, temperature, max_tokens):
            self.temperatures.append(temperature)
            return "CCO"

    model = RecordingModel()
    generator = LLMMolecularGenerator(llm_model=model)
    rig.core[:] = [generator if tool.name == generator.name else tool for tool in rig.core]
    agent = (SupervisorAgent(tools={tool.name: tool for tool in rig.core})
             if method == "supervisor" else rig.make_agent())
    run = agent.execute if method == "supervisor" else getattr(agent, method)
    for temperature in (0.23, 0.81):
        output = run("生成 1 个分子", temperature=temperature, mol_count=1)
        assert output["success"] is True
    assert model.temperatures == [0.23, 0.81]


@pytest.mark.parametrize("method", ["execute", "execute_tools", "supervisor"])
def test_lead_explicit_count_overrides_malformed_text_count(rig, method):
    from src.agent.supervisor import SupervisorAgent

    generator = rig.tools["llm_molecular_generator"]
    generator.result["quality"]["requested_count"] = 3
    agent = (SupervisorAgent(tools=rig.tools) if method == "supervisor" else rig.make_agent())
    run = agent.execute if method == "supervisor" else getattr(agent, method)
    output = run(
        "Lead optimization of CCO; Generate 7.5 molecules", mol_count=3,
    )
    assert len(generator.calls) == 1
    assert generator.calls[0][0]["metadata"]["requested_count"] == 3
    rows = output[{"execute": "tool_results", "execute_tools": "results",
                   "supervisor": "tool_result_sequence"}[method]]
    generation = next(row for row in rows if row["tool_name"] == "llm_molecular_generator")
    assert generation["quality"]["requested_count"] == 3
    assert generation["quality"]["partial_generation"] is True


def test_binding_failure_observation_is_not_an_attempted_tool(rig):
    output = rig.make_agent().execute("请全面评价 CCO 的成药性")
    assert rig.tools["reverse_target_predictor"].calls
    assert rig.tools["target_database_search"].calls == []
    row = observation(output, "target_database_search")
    assert row["error"]["code"] == "invalid_input"
    assert "target_database_search" not in output["used_tools"]
