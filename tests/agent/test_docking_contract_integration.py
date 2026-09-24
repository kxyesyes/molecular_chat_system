"""Actual docking/registry/delegation; synthetic service, never real Vina."""
from copy import deepcopy
from threading import Event

import pytest

from src.agent.contracts import AgentContext, AgentErrorCode
from src.agent.orchestrators.base import WorkflowStep
from src.agent.runtime.delegated_executor import SpecialistDispatch
from src.agent.specialists.agents import DockingAgent
from src.agent.tooling import build_tool_registry
from src.agent.tools.base_tool import execute_tool_compat
from src.agent.tools.molecular_docking import MolecularDocking


@pytest.fixture
def docking_boundary(tmp_path, monkeypatch):
    from src.docking import molecular_docking_service as service_module

    receptor = tmp_path / "synthetic-receptor.pdb"
    ligand = tmp_path / "synthetic-ligand.sdf"
    pose = tmp_path / "synthetic-pose.pdbqt"
    receptor.write_text("ATOM\n", encoding="utf-8")
    ligand.write_text("$$$$\n", encoding="utf-8")
    pose.write_text("MODEL 1\nREMARK VINA RESULT: -7.2 0 0\nENDMDL\n", encoding="utf-8")
    calls, constructed, payloads = [], [], []
    state = {"available": True, "failed": False, "cancelled": False}

    class SyntheticService:
        def __init__(self, config=None):
            constructed.append(deepcopy(config))

        def verify_environment(self):
            return state["available"]

        def env_diagnostics(self):
            return {"vina_found": False}

        async def perform_docking(self, **kwargs):
            calls.append(kwargs)
            if state["failed"] or state["cancelled"]:
                return {"success": False, "job_id": "synthetic-only",
                        "error_code": "cancelled" if state["cancelled"] else "internal_error",
                        "error": "synthetic service failure", "warnings": ["synthetic warning"]}
            result = {"success": True, "job_id": "synthetic-only", "total_poses": 1,
                      "best_pose": {"binding_energy": -7.2, "pose_file": str(pose)},
                      "warnings": ["Synthetic fixture; not a real docking result"],
                      "extension": {"preserve": [1, "synthetic"]}}
            if "rewrite" in state:
                state["rewrite"](result)
            return result

    monkeypatch.setattr(service_module, "MolecularDockingService", SyntheticService)
    tool = MolecularDocking()
    actual_execute = tool.execute

    def recording_execute(query, **kwargs):
        payloads.append(deepcopy(query))
        return actual_execute(query, **kwargs)

    monkeypatch.setattr(tool, "execute", recording_execute)
    registry = build_tool_registry([tool])
    adapter = registry.resolve("molecular_docking", agent_name="docking")
    dispatch = SpecialistDispatch(AgentContext(query="synthetic", trace_id="docking-test"),
                                  registry, {"docking": DockingAgent()})
    request = {"receptor_path": str(receptor), "ligand_path": str(ligand),
               "center": ["1", 2.0, 3], "size": (20, "21", 22.0),
               "runtime_config": {"fixture": "not a tool executable"},
               "extension": {"retained": ["synthetic"]}}
    try:
        yield {"tool": tool, "adapter": adapter, "registry": registry, "request": request, "state": state,
               "dispatch": lambda query: dispatch(adapter, query, WorkflowStep("dock", "molecular_docking")),
               "calls": calls, "constructed": constructed, "payloads": payloads, "pose": pose}
    finally:
        registry.close()


@pytest.mark.parametrize("delegated", [False, True])
@pytest.mark.parametrize("include_query", [False, True])
@pytest.mark.parametrize("ligand_mode", ["file", "smiles"])
def test_real_docking_receives_complete_structured_request(docking_boundary, delegated, include_query, ligand_mode):
    env = docking_boundary
    request = deepcopy(env["request"])
    if ligand_mode == "smiles":
        request.pop("ligand_path")
        request["smiles"] = "CCO"
    if include_query:
        request["query"] = "dock the provided structure"
    original = deepcopy(request)
    result = env["dispatch"](request) if delegated else env["adapter"].execute(request)
    assert result.success, result.message
    assert len(env["calls"]) == 1
    assert env["payloads"] == [original]
    assert request == original
    assert env["constructed"] == [request["runtime_config"]]
    invocation = env["calls"][0]
    assert invocation["input_type"] == ligand_mode
    assert invocation["ligand_input"] == request.get("smiles", request.get("ligand_path"))
    config = invocation["config"]
    assert (config.center_x, config.center_y, config.center_z) == (1.0, 2.0, 3.0)
    assert (config.size_x, config.size_y, config.size_z) == (20.0, 21.0, 22.0)
    assert config.manual_center is True
    assert result.data["best_pose"] == {"binding_energy": -7.2, "pose_file": str(env["pose"])}
    assert result.data["extension"] == {"preserve": [1, "synthetic"]}
    assert result.quality["docking_inputs"]["ligand_mode"] == ligand_mode
    assert result.provenance.tool_name == "molecular_docking"
    assert result.provenance.demo_mode is False
    assert result.provenance.fallback_used is False
    assert result.warnings == ["Synthetic fixture; not a real docking result"]


@pytest.mark.parametrize("mode", ["success", "unavailable", "failed", "cancelled"])
def test_actual_tool_keeps_legacy_compat_observation(docking_boundary, mode):
    env = docking_boundary
    env["state"].update(available=mode != "unavailable", failed=mode == "failed", cancelled=mode == "cancelled")
    expected = execute_tool_compat(env["tool"], env["request"])
    actual = env["adapter"].execute({"query": env["request"]})
    left, right = expected.to_legacy_dict(), actual.to_legacy_dict()
    left.pop("elapsed_ms")
    right.pop("elapsed_ms")
    assert right == left
    assert len(env["calls"]) == (0 if mode == "unavailable" else 2)


@pytest.mark.parametrize("wrap", [False, True])
def test_text_reaches_domain_missing_parameter_refusal(docking_boundary, wrap):
    env = docking_boundary
    query = "dock aspirin with EGFR, no receptor or box supplied"
    result = env["adapter"].execute({"query": query} if wrap else query)
    assert not result.success
    assert env["payloads"] == [query]
    assert "requires receptor" in result.message
    assert env["constructed"] == env["calls"] == []
    assert result.data is None


@pytest.mark.parametrize("missing", ["receptor_path", "ligand_path", "center", "size"])
@pytest.mark.parametrize("delegated", [False, True])
def test_missing_explicit_input_never_constructs_service(docking_boundary, missing, delegated):
    env = docking_boundary
    payload = deepcopy(env["request"])
    payload.pop(missing)
    result = env["dispatch"](payload) if delegated else env["adapter"].execute(payload)
    assert not result.success
    assert result.error.code == AgentErrorCode.INVALID_INPUT
    assert env["constructed"] == env["calls"] == []
    assert result.data is None


def test_adapter_does_not_promote_untrusted_control_fields(docking_boundary):
    env = docking_boundary
    request = {**env["request"], "job_id": "untrusted-job", "progress_callback": "untrusted-callback",
               "cancel_event": "untrusted-event"}
    result = env["adapter"].execute({"query": request})
    assert result.success
    assert not {"job_id", "progress_callback", "cancel_event"}.intersection(env["calls"][0])


def test_direct_domain_control_keywords_still_reach_service(docking_boundary):
    env = docking_boundary
    callback = lambda payload: None
    cancel = Event()
    raw = env["tool"].execute(env["request"], job_id="server-job", progress_callback=callback, cancel_event=cancel)
    assert raw["success"] is True
    assert len(env["calls"]) == 1
    assert env["calls"][0]["job_id"] == "server-job"
    assert env["calls"][0]["progress_callback"] is callback
    assert env["calls"][0]["cancel_event"] is cancel


@pytest.mark.parametrize("defect", ["no_pose", "nonfinite_energy", "boolean_count", "no_result"])
@pytest.mark.parametrize("delegated", [False, True])
def test_service_success_without_scientific_proof_is_rejected(docking_boundary, defect, delegated):
    env = docking_boundary

    def rewrite(result):
        if defect == "no_pose":
            result["best_pose"].pop("pose_file")
        elif defect == "nonfinite_energy":
            result["best_pose"]["binding_energy"] = float("nan")
        elif defect == "boolean_count":
            result["total_poses"] = True
        else:
            result.pop("best_pose")
            result["total_poses"] = 0

    env["state"]["rewrite"] = rewrite
    # The wrapped form already reaches the domain tool on the old version:
    # these RED failures isolate output proof, not unsupported input shape.
    result = (env["dispatch"](env["request"]) if delegated
              else env["adapter"].execute({"query": env["request"]}))
    assert not result.success
    assert result.error.code == AgentErrorCode.INVALID_OUTPUT
    assert len(env["calls"]) == 1
    assert result.data is None
    assert result.formatted == ""
    assert result.artifacts == result.evidence == []
    assert result.provenance is None


@pytest.mark.parametrize("mode", ["success", "unavailable", "missing_pose"])
def test_shared_session_records_validated_observation_once(docking_boundary, tmp_path, mode):
    from src.agent.persistence import SQLiteAgentStateStore
    from src.agent.planning import WorkflowPlan
    from src.agent.supervisor import SupervisorAgent

    env = docking_boundary
    env["state"]["available"] = mode != "unavailable"
    if mode == "missing_pose":
        env["state"]["rewrite"] = lambda result: result["best_pose"].pop("pose_file")

    class FixedPlanner:
        def plan(self, context):
            return WorkflowPlan(workflow_name="docking_simulation", steps=[
                WorkflowStep("dock", "molecular_docking", input_data=deepcopy(env["request"]))])

    store = SQLiteAgentStateStore(tmp_path / "synthetic-session.sqlite")
    supervisor = SupervisorAgent(
        tools={"molecular_docking": env["tool"]}, planner=FixedPlanner(),
        tool_registry=env["registry"], specialists={"docking": DockingAgent()}, state_store=store)
    result = supervisor.run("synthetic docking fixture", skill_name="docking_simulation",
                            trace_id="synthetic-docking-session")
    success = mode == "success"
    assert result["status"] == ("succeeded" if success else "failed")
    sequence = result["result"]["tool_result_sequence"]
    assert len(sequence) == 1
    observation = sequence[0]
    assert observation["success"] is success
    if success:
        assert observation["data"]["best_pose"]["binding_energy"] == -7.2
        assert observation["provenance"]["tool_name"] == "molecular_docking"
    else:
        assert observation["data"] is None and observation["formatted"] == ""
        assert observation["artifacts"] == observation["evidence"] == []
        # Session adds trace-only provenance after the scientific scrub. It
        # must not restore the provider's model/source or usable science.
        assert observation["provenance"]["model_name"] is None
        assert observation["provenance"]["model_version"] is None
        assert observation["provenance"]["input_digest"]
    records = result["result"]["metadata"]["evidence_ledger"]
    assert len(records) == 1 and records[0]["scientific_usable"] is success
    executions = store.get_tool_executions(result["trace_id"])
    assert len(executions) == 1
    checkpoint = store.latest_checkpoint(result["trace_id"], "dock")
    assert checkpoint["output"]["data"] == observation["data"]
    assert len(env["payloads"]) == 1
    assert len(env["calls"]) == (1 if mode != "unavailable" else 0)
