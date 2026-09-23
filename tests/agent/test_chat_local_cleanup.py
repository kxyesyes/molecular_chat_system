"""T06-C contracts against the real ChatHandler; only external I/O is scripted."""
import asyncio
import json
from copy import deepcopy

import pytest
from fastapi import WebSocketDisconnect

from src.web.chat_handler import ChatHandler
from src.web.rag_presentation import rag_info_molecule


class Socket:
    def __init__(self, query=None):
        self.messages = []
        self.scope = {}
        self.query = query

    async def send_text(self, text):
        self.messages.append(json.loads(text))

    async def accept(self):
        pass

    async def receive_text(self):
        if self.query is None:
            raise WebSocketDisconnect()
        query, self.query = self.query, None
        return json.dumps({"message": query, "enable_rag": False, "enable_tools": False})


class Model:
    def __init__(self, stream_error=True):
        self.calls = []
        self.prompts = []
        self.stream_error = stream_error

    async def generate(self, prompt, **kwargs):
        self.calls.append("generate")
        self.prompts.append(prompt)
        return "model answer"

    async def stream_generate(self, prompt, **kwargs):
        self.calls.append("stream")
        self.prompts.append(prompt)
        yield "partial"
        if self.stream_error:
            raise RuntimeError("synthetic stream failure")


class Rag:
    is_initialized = True
    rows = [
        {"SMILES": "CCO", "similarity_score": .98765, "mw": 46.069,
         "source": "test-source", "unused": None, "source_index": 2,
         "provenance": {"vector_label": 1, "source_sha256": "a" * 64}},
        {"SMILES": "C", "similarity_score": .5, "source_index": 0,
         "provenance": {"vector_label": 0, "source_sha256": "a" * 64}},
    ]

    def __init__(self):
        self.calls = []

    async def search_similar_molecules(self, message, count):
        self.calls.append((message, count))
        return deepcopy(self.rows)


class Agent:
    def __init__(self, failed=False, rag=False, interpretation_overflow=False):
        self.failed = failed
        self.rag = rag
        self.interpretation_overflow = interpretation_overflow
        self.route_calls = []
        self.calls = []

    def should_use_tools(self, query):
        self.route_calls.append(query)
        return True

    def execute(self, message, **kwargs):
        self.calls.append(message)
        result = {
            "success": not self.failed,
            "status": "failed" if self.failed else "completed",
            "final_answer": "tool failed" if self.failed else "tool evidence",
            "tools_used": [],
            "workflow_plan": {"workflow_name": "test"},
            "tool_results": {"rag_search": {"success": True, "data": deepcopy(Rag.rows)}}
            if self.rag else {},
        }
        if self.interpretation_overflow:
            result.update(partial=True, warnings=["模型不可用" * 5000],
                          final_answer="仅性质计算完成，活性模型不可用。")
        return result


class History(list):
    """Count actual appends, including duplicates that a length cap could hide."""
    def __init__(self, records=()):
        super().__init__(records)
        self.appended = []

    def append(self, entry):
        self.appended.append(entry)
        super().append(entry)


def seeded_history(count=23):
    return History({"user": str(i), "assistant": str(i), "extra": [i]}
                   for i in range(count))


def terminals(socket):
    return [item for item in socket.messages if item["type"] in {"complete", "message"}]


@pytest.mark.parametrize("mode", [
    "success", "failure", "clarification", "workflow", "stream_retry", "invalid_count",
    "stream_success",
])
def test_each_terminal_path_appends_one_complete_history_entry(mode):
    agent = Agent(failed=mode == "failure") if mode in {"failure", "workflow"} else None
    model = Model(stream_error=mode != "stream_success")
    handler = ChatHandler(model, Rag(), agent, {
        "inference": {"stream": mode in {"stream_retry", "stream_success"}},
    })
    history = seeded_history()
    original_records = list(history)
    socket = Socket()
    query = "请分析这个 SMILES 的成药性：CC(C)((。" if mode == "clarification" else "hello"
    asyncio.run(handler._process_message(
        socket, query, False, agent is not None,
        mol_count=0 if mode == "invalid_count" else None, conversation_history=history,
    ))
    assert len(history) == 20
    assert len(history.appended) == 1
    assert all(after is before for after, before in zip(history[:-1], original_records[4:]))
    record = history[-1]
    assert record is history.appended[0]
    assert record["user"] == query
    assert record["agent_used"] is (agent is not None)
    assert record["molecules_retrieved"] == 0
    assert record["rag_enabled"] is False
    assert set(record) == {
        "user", "assistant", "agent_used", "agent_response", "rag_enabled", "molecules_retrieved",
    } | ({"error"} if mode == "invalid_count" else set())
    terminal = terminals(socket)
    assert len(terminal) == 1
    assert terminal[0]["type"] == ("message" if mode in {"success", "stream_retry"} else "complete")
    assert record["assistant"] == terminal[0].get("content", terminal[0].get("message"))
    assert handler.conversation_history == []
    expected_calls = {
        "success": ["generate"], "stream_retry": ["stream", "generate"],
        "stream_success": ["stream"],
    }
    assert model.calls == expected_calls.get(mode, [])
    assert record["agent_response"] == (record["assistant"] if agent is not None else None)
    if agent is not None:
        assert agent.calls == [query]
    if mode == "failure":
        assert terminal[0]["status"] == "failed"
    if mode == "workflow":
        assert record["assistant"] == "tool evidence"
    if mode == "invalid_count":
        assert record["error"]["code"] == "invalid_input"
        assert record["error"]["message"]
    if mode == "clarification":
        assert "SMILES" in record["assistant"]
    if mode == "stream_retry":
        assert record["assistant"] == "model answer"
        assert [item["content"] for item in socket.messages if item["type"] == "stream"] == ["partial"]


@pytest.mark.parametrize("agent_rag", [True, False])
def test_rag_branches_preserve_payload_provenance_and_existing_limit(agent_rag):
    model, rag = Model(), Rag()
    agent = Agent(rag=True) if agent_rag else None
    handler = ChatHandler(model, rag, agent, {"inference": {"stream": False}})
    socket, history = Socket(), History()
    query = "检索知识库中相关分子"
    asyncio.run(handler._process_message(
        socket, query, True, agent_rag, rag_count=1, conversation_history=history,
    ))
    packets = [item for item in socket.messages if item["type"] == "rag_info"]
    assert len(packets) == 1
    assert set(packets[0]) == {"type", "molecules", "message"}
    assert packets[0]["message"] == (
        "✅ 技能自动触发：从库中找到 2 个相关分子" if agent_rag else "✅ 找到 2 个相关分子"
    )
    assert len(packets[0]["molecules"]) == (1 if agent_rag else 2)
    assert packets[0]["molecules"][0] == {
        "smiles": "CCO", "similarity": .988,
        "properties": {"mw": 46.07, "source": "test-source"},
        "source_index": 2, "provenance": Rag.rows[0]["provenance"],
    }
    for card, row in zip(packets[0]["molecules"], Rag.rows):
        assert card["source_index"] == row["source_index"]
        assert card["provenance"] == row["provenance"]
        assert "source_index" not in card["properties"] and "provenance" not in card["properties"]
    assert len(history) == len(history.appended) == 1
    assert history[0]["molecules_retrieved"] == 2
    assert history[0]["rag_enabled"] is True
    assert model.calls == ([] if agent_rag else ["generate"])
    assert rag.calls == ([] if agent_rag else [(query, 1)])
    assert len(terminals(socket)) == 1
    assert terminals(socket)[0]["type"] == ("complete" if agent_rag else "message")


@pytest.mark.parametrize("size", [0, 19, 20, 23])
def test_history_helper_preserves_record_fields_objects_and_list_identity(size):
    history = seeded_history(size)
    alias = history
    before = list(history)
    record = {"user": "new", "error": {"code": "invalid_input"}, "extra": ["source"]}
    snapshot = deepcopy(record)
    ChatHandler._append_history(history, record)
    assert history is alias
    assert history == (before + [snapshot])[-20:]
    assert history[-1] is record and record == snapshot
    assert history.appended == [record]
    assert all(after is old for after, old in zip(history[:-1], before[-19:]))
    assert history[-1]["error"] is record["error"]
    assert history[-1]["extra"] is record["extra"]


@pytest.mark.parametrize("empty", [False, True])
def test_rag_payload_helper_uses_shared_projection_without_mutating_records(empty):
    rows = [] if empty else deepcopy(Rag.rows) + [{
        "SMILES": "CC", "nested": {"labels": ["source", 2]},
        "missing": float("nan"), "provenance": {"source": "fixture"},
    }]
    before = json.dumps(rows, sort_keys=True)
    result = ChatHandler._rag_info_payload(rows, "caller-owned count and wording")
    assert result == {
        "type": "rag_info", "molecules": [rag_info_molecule(row) for row in rows],
        "message": "caller-owned count and wording",
    }
    assert json.dumps(rows, sort_keys=True) == before
    if rows:
        assert result["molecules"][0]["provenance"] is rows[0]["provenance"]
        assert "missing" not in result["molecules"][-1]["properties"]
        assert json.loads(result["molecules"][-1]["properties"]["nested"]) == {"labels": ["source", 2]}


@pytest.mark.parametrize("stream", [False, True])
def test_interpretation_budget_terminal_keeps_three_field_shape_and_appends_once(stream):
    model, agent, socket = Model(), Agent(interpretation_overflow=True), Socket()
    handler = ChatHandler(model, Rag(), agent, {
        "inference": {"stream": stream}, "agent": {"summarize_workflow_results": True},
    })
    history = seeded_history()
    before = list(history)
    asyncio.run(handler._process_message(socket, "解释结果", False, True, conversation_history=history))
    terminal = terminals(socket)
    assert len(terminal) == 1
    assert terminal[0]["type"] == "complete"
    assert terminal[0]["error"] == {"code": "interpretation_budget_exceeded"}
    assert "活性模型不可用" in terminal[0]["content"]
    assert "未进行模型总结" in terminal[0]["content"]
    assert len(history) == 20 and len(history.appended) == 1
    assert all(after is old for after, old in zip(history[:-1], before[4:]))
    assert history[-1] is history.appended[0]
    assert history[-1] == {"user": "解释结果", "assistant": terminal[0]["content"], "agent_used": True}
    assert handler.conversation_history == []
    assert agent.calls == ["解释结果"] and model.calls == []


@pytest.mark.parametrize("stream", [False, True])
def test_input_budget_terminal_never_appends_or_trims_history_or_calls_model(stream):
    model, agent, rag, socket = Model(), Agent(), Rag(), Socket()
    handler = ChatHandler(model, rag, agent, {"inference": {"stream": stream, "input_max_chars": 6000}})
    history = seeded_history()
    before = list(history)
    asyncio.run(handler._process_message(socket, "问" * 7000, True, True, conversation_history=history))
    assert len(socket.messages) == 1
    assert socket.messages[0]["type"] == "complete"
    assert socket.messages[0]["error"] == {"code": "input_budget_exceeded"}
    assert history == before and len(history) == 23 and history.appended == []
    assert all(after is old for after, old in zip(history, before))
    assert handler.conversation_history == []
    assert agent.route_calls == agent.calls == rag.calls == model.calls == []


def test_explicit_histories_and_handler_default_history_remain_independent():
    model = Model()
    handler = ChatHandler(model, Rag(), None, {"inference": {"stream": False}})
    first, second = History(), History()
    default = handler.conversation_history
    asyncio.run(handler._process_message(Socket(), "first private question", False, False, conversation_history=first))
    asyncio.run(handler._process_message(Socket(), "second private question", False, False, conversation_history=second))
    assert first is not second
    assert len(first) == len(second) == 1 and first[0] is not second[0]
    assert "first private question" not in model.prompts[1]
    assert default is handler.conversation_history and default == []
    asyncio.run(handler._process_message(Socket(), "default question", False, False))
    assert default == [dict(user="default question", assistant="model answer", agent_used=False,
                            agent_response=None, rag_enabled=False, molecules_retrieved=0)]
    assert len(first) == len(second) == 1
    other = ChatHandler(Model(), Rag(), None, {})
    assert other.conversation_history == [] and other.conversation_history is not default


def test_actual_websocket_connections_do_not_share_history():
    model = Model()
    handler = ChatHandler(model, Rag(), None, {"inference": {"stream": False}})
    asyncio.run(handler.handle_websocket(Socket("connection one private question")))
    asyncio.run(handler.handle_websocket(Socket("connection two private question")))
    assert model.calls == ["generate", "generate"]
    assert "connection one private question" in model.prompts[0]
    assert "connection one private question" not in model.prompts[1]
    assert "connection two private question" in model.prompts[1]
    assert handler.conversation_history == []
