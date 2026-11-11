"""Current mounted Pi Web entry for the historical B1 molecular-pair campaign."""
import asyncio
import json
import time

import pytest

from tests.agent.test_web_decision_runtime import ActualSocket, actual_app, cookie_for, result_of
from test_decision_binding_loop import loop_case, run as run_binding
from test_decision_loop import finish, last_observation, tool


CASES = [
    ("plain", "native"), ("plain", "json"), ("profiled", "native"), ("profiled", "json"),
    ("phase-only", "native"), ("functional", "native"), ("functional", "json"),
]
PROPERTY = "property_calculator"
ACTIVITY = "activity_predictor"
ADMET = "admet_predictor"
FABRICATED = "MODEL_INVENTED_pIC50_99_MUST_NOT_RENDER"


def check_execution(execution, trace_id, first, registry):
    from src.agent.orchestrators.workflow import WorkflowOrchestrator

    name, output = execution["tool_name"], execution["output"]
    query = execution["input"]["query"]
    if name == ACTIVITY:
        assert query["smiles"] == [first, "CCN"] and query["target"] == "PDE5A"
        assert [r["smiles"] for r in output["data"]] == [first, "CCN"]
        for row in output["data"]:
            assert row["requested_target"] == "PDE5A" and row["success"] is False
            assert row["predicted_pIC50"] is None and row["activity_probability"] is None
    else:
        assert query.splitlines() == [first, "CCN"]
    quality, provenance = output["quality"], output["provenance"]
    assert execution["trace_id"] == trace_id
    assert execution["status"] == output["status"] and quality["step_id"] == execution["step_id"]
    assert provenance["tool_name"] == name
    assert provenance["input_digest"] == WorkflowOrchestrator._input_hash(execution["input"])
    adapter = registry.resolve(name, require_available=False)
    assert str(provenance["tool_version"]) == str(quality["tool_version"]) == str(adapter.spec.version)


@pytest.fixture
def unavailable_models(tmp_path, monkeypatch):
    from src.activity import prediction_service
    from src.activity.family_predictor import FamilyActivityPredictor
    from src.activity.model_registry import ActivityModelRegistry
    from src.admet import predictor

    empty = FamilyActivityPredictor(ActivityModelRegistry(tmp_path / "empty-models"))
    monkeypatch.setattr(prediction_service, "get_family_predictor", lambda: empty)
    monkeypatch.setattr(predictor, "get_admet_ai_backend", lambda: None)


@pytest.mark.parametrize("wire", ["native", "json"])
def test_four_tool_observation_order_in_b1(loop_case, unavailable_models, wire):
    from src.admet.predictor import ADMETPredictor
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    from src.agent.tools.drug_likeness_assessment import DrugLikenessAssessment
    from src.agent.tools.property_calculator import PropertyCalculator

    for first in ("CCO", "CCCCCCCCO"):
        observed = {}
        order = []

        def choose(messages):
            result = last_observation(messages)
            observed[result["tool_name"]] = result
            if result["tool_name"] == PROPERTY:
                assert [item["smiles"] for item in result["data"]] == [first, "CCN"]
                heavy = result["data"][0]["properties"]["molecular_weight"] > 100
                order.extend(["drug_likeness_assessment", ADMET] if heavy else [ADMET, "drug_likeness_assessment"])
                order.append(ACTIVITY)
            next_name = next((name for name in order if name not in observed), None)
            if next_name:
                return tool(next_name, {"input_ref": observed[PROPERTY]["quality"]["evidence_id"]})
            return finish([item["quality"]["evidence_id"] for item in observed.values() if item["success"]],
                          text=FABRICATED)

        case = loop_case([tool(), choose, choose, choose, choose],
                         [PropertyCalculator(), DrugLikenessAssessment(), ADMETPredictor(), ActivityPredictorTool()],
                         mode=wire)
        query = f"评估分子量和类药性和 ADMET 和 PDE5A 活性；SMILES: {first}; CCN"
        result = run_binding(case, query, required=set(case.calls))
        assert not result.success and FABRICATED not in result.final_answer
        assert [item.tool_name for item in result.tool_results] == [PROPERTY, *order]
        assert [item.tool_name for item in result.tool_results if item.success] == [PROPERTY, "drug_likeness_assessment"]
        assert all(len(calls) == 1 for calls in case.calls.values())
        assert order[:2] == ([ADMET, "drug_likeness_assessment"] if first == "CCO"
                             else ["drug_likeness_assessment", ADMET])
        executions = case.store.get_tool_executions(result.trace_id)
        assert [x["tool_name"] for x in executions] == [PROPERTY, *order]
        prop_id = executions[0]["output"]["quality"]["evidence_id"]
        for execution in executions:
            check_execution(execution, result.trace_id, first, case.loop.registry)
            if execution["tool_name"] != PROPERTY:
                roles = execution["output"]["quality"]["binding_proof"]["roles"]
                assert [r["evidence_id"] for r in roles if r["role"] == "molecules"] == [prop_id]
            if execution["tool_name"] == "drug_likeness_assessment":
                data = execution["output"]["data"]
                assert [r["smiles"] for r in data] == [first, "CCN"] and all(r["assessment"] for r in data)


@pytest.mark.parametrize(("mode", "wire"), CASES, ids=["-".join(case) for case in CASES])
def test_molecular_comparison(actual_app, unavailable_models, monkeypatch, record_property, mode, wire):
    from src.agent.tooling.adapters import LegacyPythonToolAdapter
    from src.web.scientific_obligations import ScientificObligationCompiler

    # An empty activity registry and an unavailable ADMET backend exercise actual
    # validation/error paths without downloading weights or inventing predictions.
    adapter_samples = []
    if mode == "profiled":
        original = LegacyPythonToolAdapter.execute
        def measured(adapter, *args, **kwargs):
            start = time.perf_counter_ns()
            try:
                return original(adapter, *args, **kwargs)
            finally:
                adapter_samples.append((adapter.spec.name, time.perf_counter_ns() - start))
        monkeypatch.setattr(LegacyPythonToolAdapter, "execute", measured)

    async def scenario(first):
        old_query = f"评估分子量和类药性和 ADMET 和 PDE5A 活性；SMILES: {first}; CCN"
        legacy = ScientificObligationCompiler().compile(old_query)
        assert legacy.status == "rejected"
        branches = []

        async def respond(payload):
            marker = ". Immutable result requirements (not a tool sequence): "
            prompt = next(m["content"] for m in payload["messages"] if marker in (m.get("content") or ""))
            requirements, _ = json.JSONDecoder().raw_decode(prompt.split(marker, 1)[1])
            steps = {s["tool_name"]: s["name"] for s in requirements["steps"]}
            observations = {}
            for m in payload["messages"]:
                if m["role"] == ("tool" if wire == "native" else "user"):
                    raw = m.get("content") or ""
                    if raw.startswith("{"):
                        value = json.loads(raw)
                        if isinstance(value, dict) and "tool_name" in value:
                            observations[value["tool_name"]] = value
            order = [PROPERTY]
            if PROPERTY in observations:
                rows = observations[PROPERTY]["data"]
                assert [r["smiles"] for r in rows] == [first, "CCN"]
                mw = rows[0]["properties"]["molecular_weight"]
                order += [ACTIVITY, ADMET] if mw > 100 else [ADMET, ACTIVITY]
            for name in order:
                if name not in observations:
                    branches.append(name)
                    return {"version":"1", "action":"tool", "tool_name":name,
                            "arguments":{"step_ref":steps[name]}, "purpose":"execute compiled step"}
            return {"version":"1", "action":"finish", "response_kind":"scientific", "text":FABRICATED,
                    "evidence_ids":[v["quality"]["evidence_id"] for v in observations.values() if v["success"]]}

        async with actual_app(mode="pi", ordinary_policy="medchat-web-v1", wire=wire,
                              optional_tools=True, respond=respond) as browser:
            query = f"比较 {first} 和 CCN 的分子量、ADMET 和 PDE5A 活性。"
            async with ActualSocket(browser.app, await cookie_for(browser.app)) as ws:
                await ws.ready()
                started = time.perf_counter_ns()
                frames = await ws.turn({"message":query, "enable_tools":True})
                elapsed = time.perf_counter_ns() - started
                result = result_of(frames)
            if browser.protocol_errors:
                raise browser.protocol_errors[0]
            executions = browser.app.agent_state_store.get_tool_executions(result["trace_id"])
            assert [item["tool_name"] for item in executions] == branches
            assert branches == [PROPERTY, *([ACTIVITY, ADMET] if first == "CCCCCCCCO" else [ADMET, ACTIVITY])]
            assert result["status"] == "partial" and FABRICATED not in json.dumps(frames)
            for execution in executions:
                output = execution["output"]
                assert output["success"] is (execution["tool_name"] == PROPERTY)
                if mode == "functional":
                    check_execution(execution, result["trace_id"], first, browser.app.decision_runtime.registry)
            if mode == "phase-only":
                record_property("web_turn_ns", elapsed)
        return branches

    for first in ("CCO", "CCCCCCCCO"):
        asyncio.run(scenario(first))
    if mode == "profiled":
        assert len(adapter_samples) == 6
        for _, elapsed in adapter_samples:
            record_property("adapter_call_ns", elapsed)
