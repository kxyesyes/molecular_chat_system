"""Two chat turns with real routing/planning/session; tools are offline fixtures."""
import asyncio
import inspect
import json
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from src.agent.contracts import AgentErrorCode, ObservationStatus, CandidateRecord, CandidateSet, ToolResult
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.supervisor import SupervisorAgent
from src.web.chat_handler import ChatHandler
from tests.agent.test_scientific_reference_web import seed, service, pointer


class Socket:
    scope = {"agent_session_id": "owner"}
    def __init__(self): self.messages = []
    async def send_text(self, text): self.messages.append(json.loads(text))


class Tool:
    def __init__(self, name): self.name, self.calls = name, []
    def execute(self, value):
        self.calls.append(value)
        if self.name == "llm_molecular_generator":
            data = CandidateSet(3, tuple(CandidateRecord.from_smiles(i, i, s, s, {})
                                for i, s in enumerate(("CCO", "CCN"), 1)), status=ObservationStatus.PARTIAL).to_dict()
            return ToolResult.success_result(self.name, data=data, warnings=["offline fixture; only two candidates"])
        if self.name == "property_calculator":
            return ToolResult.success_result(self.name, data=[{"smiles": "CCN", "properties": {"molecular_weight": 45.08}}])
        return ToolResult.error_result(self.name, code=AgentErrorCode.TOOL_EXECUTION_FAILED, message="offline fixture unavailable")


def test_two_chat_turns_capture_canonical_tool_input(tmp_path):
    assert "scientific_references" in inspect.signature(ChatHandler).parameters, "ChatHandler reference wiring missing"
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    svc = service(store)
    tools = {name: Tool(name) for name in ("llm_molecular_generator", "property_calculator", "drug_likeness_assessment")}
    agent = SupervisorAgent(tools=tools, state_store=store)
    handler = ChatHandler(None, None, agent, {}, scientific_references=svc)
    socket = Socket()
    async def run():
        await handler._process_message(socket, "生成3个分子", False, True, mol_count=3, session_id="owner")
        assert any(m["type"] == "molecule_candidates" for m in socket.messages), json.dumps(socket.messages, ensure_ascii=False)
        event = next(m for m in socket.messages if m["type"] == "molecule_candidates")
        assert event["source"]["status"] == "partial"
        assert event["warnings"]
        assert "reference" in event, json.dumps({"run": store.get_run(event["trace_id"]),
            "checkpoint": store.latest_checkpoint(event["trace_id"]),
            "sources": store.get_scientific_sources(event["trace_id"], session_id="owner")}, ensure_ascii=False)
        assert svc.confirm(event["reference"], session_id="owner")
        p = pointer(event)
        await handler._process_message(socket, "计算刚才第二个分子的属性", False, True,
                                       session_id="owner", reference=p)
        assert tools["property_calculator"].calls == ["CCN"]
        assert tools["drug_likeness_assessment"].calls == ["CCN"]
        assert handler.conversation_history[-1]["user"] == "计算刚才第二个分子的属性"
        assert svc.restore(p, session_id="owner"), "new turn must not overwrite source run"
    asyncio.run(run())


def confirmed(tmp_path):
    store = SQLiteAgentStateStore(tmp_path / "state.db")
    svc = service(store)
    event = svc.project(seed(store), session_id="owner")[0]
    assert svc.confirm(event["reference"], session_id="owner")
    return store, svc, pointer(event)


@pytest.mark.parametrize("query,selection,expected", [
    ("计算刚才第二个分子的属性", None, "CCN"),
    ("计算属性", {"ordinal": 1}, "CCO"),
    ("计算第33个分子的属性", None, "clarify"),
    ("计算第一个和第二个分子的属性", None, "clarify"),
    ("计算第1到2个分子的属性", None, "clarify"),
    ("计算属性", {"ordinal": True}, "clarify"),
    ("计算属性", {"ordinal": 1, "smiles": "N"}, "clarify"),
    ("计算这个分子的属性", None, "clarify"),
    ("谢谢", {"ordinal": 2}, None),
    ("计算 SMILES: CCC 的属性", {"ordinal": 2}, None),
])
def test_selection_bounds_and_explicit_input_precedence(tmp_path, query, selection, expected):
    _, svc, p = confirmed(tmp_path)
    assert callable(getattr(svc, "resolve", None)), "trusted resolution missing"
    if expected == "clarify":
        with pytest.raises(ValueError, match="选择"):
            svc.resolve(query, p, selection, session_id="owner", enable_tools=True)
    else:
        resolved = svc.resolve(query, p, selection, session_id="owner", enable_tools=True)
        assert (resolved.canonical_smiles if resolved else None) == expected
    assert svc.resolve(query, p, selection, session_id="owner", enable_tools=False) is None


def test_execute_claim_replay_revalidation_and_changed_selection(tmp_path):
    store, svc, p = confirmed(tmp_path)
    assert "resolved_molecule" in inspect.signature(SupervisorAgent.execute).parameters
    tools = {name: Tool(name) for name in ("property_calculator", "drug_likeness_assessment")}
    agent = SupervisorAgent(tools=tools, state_store=store)
    query = "计算属性"
    selected = svc.resolve(query, p, {"ordinal": 2}, session_id="owner", enable_tools=True)
    result = agent.execute(query, session_id="owner", resolved_molecule=selected)
    replay = agent.execute(query, session_id="owner", resolved_molecule=selected)
    assert result["trace_id"] == replay["trace_id"] != "trace"
    assert tools["property_calculator"].calls == ["CCN"]  # successful checkpoint replay, not a new execution
    assert store.get_run(result["trace_id"])["idempotency_key"]
    changed = svc.resolve(query, p, {"ordinal": 1}, session_id="owner", enable_tools=True)
    other = agent.execute(query, session_id="owner", resolved_molecule=changed)
    assert other["trace_id"] != result["trace_id"]
    assert tools["property_calculator"].calls == ["CCN", "CCO"]
    store.update_run_status("trace", "failed")
    rejected = agent.execute(query, session_id="owner", resolved_molecule=selected)
    assert not rejected["success"]
    assert tools["property_calculator"].calls == ["CCN", "CCO"]


def test_docking_missing_parameters_still_rejected(tmp_path):
    store, svc, p = confirmed(tmp_path)
    assert callable(getattr(svc, "resolve", None))
    resolved = svc.resolve("对接刚才第二个分子到 EGFR", p, None, session_id="owner", enable_tools=True)
    tool = Tool("molecular_docking")
    result = SupervisorAgent(tools={tool.name: tool}, state_store=store).execute(
        "对接刚才第二个分子到 EGFR", session_id="owner", resolved_molecule=resolved)
    assert not result["success"]
    assert not tool.calls


@pytest.mark.parametrize("query", ["什么是分子活性", "什么是ADMET", "解释QED是什么意思"])
def test_knowledge_queries_keep_real_router_chat_path(tmp_path, query):
    store, svc, p = confirmed(tmp_path)
    class Model:
        model_name = "offline-main"
        async def generate(self, *args, **kwargs): return "offline explanation"
    tools = {name: Tool(name) for name in ("property_calculator", "drug_likeness_assessment", "activity_predictor", "admet_predictor")}
    handler = ChatHandler(Model(), None, SupervisorAgent(tools=tools, state_store=store),
                          {"inference": {"stream": False}}, scientific_references=svc)
    socket = Socket()
    asyncio.run(handler._process_message(socket, query, False, True, session_id="owner",
                                         reference=p, selection={"ordinal": 1}))
    assert all(not t.calls for t in tools.values())
    assert not any(m["type"] == "agent_event" for m in socket.messages)


def test_card_ordinal_conflict_and_invalid_explicit_smiles(tmp_path):
    _, svc, p = confirmed(tmp_path)
    with pytest.raises(ValueError, match="选择"):
        svc.resolve("计算第二个分子的属性", p, {"ordinal": 1}, session_id="owner", enable_tools=True)
    assert svc.resolve("计算 SMILES: CC(C)X 的属性", p, {"ordinal": 1}, session_id="owner", enable_tools=True) is None


def test_execute_new_explicit_smiles_override_stale_internal_selection(tmp_path):
    store, svc, p = confirmed(tmp_path)
    selected = svc.resolve("计算属性", p, {"ordinal": 2}, session_id="owner", enable_tools=True)
    store.update_run_status("trace", "failed")
    tools = {name: Tool(name) for name in ("property_calculator", "drug_likeness_assessment")}
    SupervisorAgent(tools=tools, state_store=store).execute(
        "计算 SMILES: CCC 的属性", session_id="owner", resolved_molecule=selected)
    assert len(tools["property_calculator"].calls) == 1
    assert "CCC" in tools["property_calculator"].calls[0]


def test_new_target_only_transfers_structure_to_actual_activity_input(tmp_path):
    store, svc, p = confirmed(tmp_path)
    first_view = svc.get(p, session_id="owner")
    old_target = store.publish_scientific_presentation("trace", session_id="owner", target="PDE5A",
        selections=[{"observation_id": r["observation_id"], "candidate_id": r["candidate"]["candidate_id"]}
                    for r in first_view["ordered_candidates"]])
    p = {"trace_id": "trace", "presentation_id": old_target["presentation_id"], "revision": old_target["revision"]}
    assert svc.confirm({**p, "ordered_keys": [[r["observation_id"], r["candidate"]["candidate_id"]]
                                            for r in old_target["ordered_candidates"]]}, session_id="owner")
    query = "预测刚才第二个分子对 BuChE 的活性"
    selected = svc.resolve(query, p, None, session_id="owner", enable_tools=True)
    tool = Tool("activity_predictor")
    agent = SupervisorAgent(tools={tool.name: tool}, state_store=store)
    result = agent.execute(query, session_id="owner", resolved_molecule=selected)
    assert tool.calls == [{"smiles": "CCN", "query": query}]
    assert "PDE5A" not in json.dumps(tool.calls)
    assert not result["success"]  # fixture failed; never promote old scores


def test_concurrent_reference_submission_has_single_claim_winner(tmp_path):
    store, svc, p = confirmed(tmp_path)
    selected = svc.resolve("计算属性", p, {"ordinal": 2}, session_id="owner", enable_tools=True)
    started, release = threading.Event(), threading.Event()
    class BlockingTool(Tool):
        def execute(self, value):
            started.set()
            assert release.wait(10)
            return super().execute(value)
    tool = BlockingTool("property_calculator")
    tools = {tool.name: tool, "drug_likeness_assessment": Tool("drug_likeness_assessment")}
    first = SupervisorAgent(tools=tools, state_store=store)
    second = SupervisorAgent(tools=tools, state_store=SQLiteAgentStateStore(store.db_path))
    with ThreadPoolExecutor(2) as pool:
        running = pool.submit(first.execute, "计算属性", session_id="owner", resolved_molecule=selected)
        try:
            assert started.wait(10)
            conflict = second.execute("计算属性", session_id="owner", resolved_molecule=selected)
            assert not conflict["success"]
        finally:
            release.set()
        running.result(timeout=10)
    assert tool.calls == ["CCN"]
    assert svc.restore(p, session_id="owner")


def test_tools_off_with_valid_selection_does_not_dispatch(tmp_path):
    store, svc, p = confirmed(tmp_path)
    class Model:
        model_name = "offline-main"
        async def generate(self, *args, **kwargs): return "tools disabled"
    tools = {name: Tool(name) for name in ("property_calculator", "drug_likeness_assessment")}
    handler = ChatHandler(Model(), None, SupervisorAgent(tools=tools, state_store=store),
                          {"inference": {"stream": False}}, scientific_references=svc)
    asyncio.run(handler._process_message(Socket(), "计算第二个分子的属性", False, False,
                                         session_id="owner", reference=p, selection={"ordinal": 2}))
    assert all(not t.calls for t in tools.values())


@pytest.mark.parametrize("selection", [None, {"ordinal": 1}])
@pytest.mark.parametrize("query", [
    "针对 EGFR 设计 10 个候选小分子",
    "搜索 EGFR 是否有可用于对接的蛋白结构",
    "请检索 PDE5A 靶点结构",
])
def test_unrelated_new_task_not_hijacked_by_previous_view(tmp_path, query, selection):
    _, svc, p = confirmed(tmp_path)
    assert svc.resolve(query, p, selection, session_id="owner", enable_tools=True) is None


@pytest.mark.parametrize("query", ["calculate logp for candidate 1-2",
                                   "calculate logp for molecule 1 and 2",
                                   "calculate logp for molecule 1, 2",
                                   "calculate logp for candidate #1 and #2",
                                   "calculate logp for first molecule and second",
                                   "calculate logp for candidates 1 and 2",
                                   "calculate logp for molecule 1 or candidate #2",
                                   "calculate logp for first molecule, second",
                                   "calculate logp for candidate 1 and second",
                                   "计算第1个到第2个分子的属性"])
def test_range_and_list_references_never_dispatch_one_molecule(tmp_path, query):
    store, svc, p = confirmed(tmp_path)
    tools = {name: Tool(name) for name in ("property_calculator", "drug_likeness_assessment")}
    handler = ChatHandler(None, None, SupervisorAgent(tools=tools, state_store=store), {},
                          scientific_references=svc)
    socket = Socket()
    asyncio.run(handler._process_message(socket, query, False, True, session_id="owner", reference=p))
    assert all(not t.calls for t in tools.values())
    assert any("选择" in m.get("content", "") for m in socket.messages)


@pytest.mark.parametrize("stage", ["routing", "planning", "tool_started"])
@pytest.mark.parametrize("invalidate", ["revoke", "expire"])
def test_source_invalidated_before_dispatch_cannot_run_tool(tmp_path, monkeypatch, stage, invalidate):
    import src.agent.persistence.scientific_references as boundary
    store, svc, p = confirmed(tmp_path)
    selected = svc.resolve("计算属性", p, {"ordinal": 2}, session_id="owner", enable_tools=True)
    expiry = svc.get(p, session_id="owner")["expires_at"]
    tools = {name: Tool(name) for name in ("property_calculator", "drug_likeness_assessment")}
    agent = SupervisorAgent(tools=tools, state_store=store)
    def revoke():
        if invalidate == "revoke":
            store.update_run_status("trace", "failed")
        else:
            monkeypatch.setattr(boundary.time, "time", lambda: expiry)
    target, method = (agent.skill_router, "decide") if stage == "routing" else (agent.planner, "plan")
    if stage != "tool_started":
        original = getattr(target, method)
        def wrapped(*args, **kwargs):
            result = original(*args, **kwargs)
            revoke()
            return result
        monkeypatch.setattr(target, method, wrapped)
    def event_callback(event):
        payload = event.to_dict() if hasattr(event, "to_dict") else event
        if stage == "tool_started" and payload.get("event") == "tool_started":
            revoke()
    result = agent.execute("计算属性", session_id="owner", resolved_molecule=selected,
                           event_callback=event_callback)
    assert not result["success"]
    assert all(not t.calls for t in tools.values())
