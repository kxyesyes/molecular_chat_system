import asyncio
import json

import pytest

from src.web.chat_handler import ChatHandler
from tests.test_rag_index_manifest import retrieval_service


class Socket:
    def __init__(self):
        self.messages = []
    async def send_text(self, text):
        self.messages.append(json.loads(text))


class Model:
    def __init__(self):
        self.prompts = []
    async def generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return "offline response"
    async def stream_generate(self, prompt, **kwargs):
        self.prompts.append(prompt)
        yield "offline response"


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("source", ["history", "rag", "tools", "none"])
def test_actual_model_input_keeps_complete_question_and_constraints(stream, source):
    model, socket = Model(), Socket()
    question = "请检索知识库并解释研究方法。" + "完整中文问题" * 180 + "禁止编造结合能；最后约束必须保留。"
    history = [{"user": "旧问题" * 9000, "assistant": '{"SMILES":"' + "C" * 10000 + '"}'}]
    class Rag:
        is_initialized = True
        async def search_similar_molecules(self, *args, **kwargs):
            return [{"SMILES": "CCO", "source": "source-id-1", "notes": "资料" * 20000}]
    class Agent:
        def should_use_tools(self, message):
            return True
        def execute(self, *args, **kwargs):
            return dict(success=True, partial=True, status="partial", warnings=["模型不可用"],
                final_answer='{"status":"partial","source":"tool-source","data":"' + "证据" * 20000 + '"}',
                tools_used=[], tool_results={})
    handler = ChatHandler(model, Rag(), Agent() if source == "tools" else None,
        {"inference": {"stream": stream, "input_max_chars": 7000}})
    asyncio.run(handler._process_message(socket, question, source == "rag", source == "tools",
        conversation_history=history if source == "history" else []))
    assert len(model.prompts) == 1
    prompt = model.prompts[0]
    assert question in prompt
    assert len(prompt) <= 7000
    assert "不得编造" in prompt
    if source == "rag":
        assert any(item["type"] == "rag_info" for item in socket.messages)
    if source == "tools":
        assert "partial" in prompt and "不可" in prompt
        assert "系统已使用专业工具完成分析" not in prompt


@pytest.mark.parametrize("stream", [False, True])
def test_oversized_input_rejected_before_agent_or_model(stream):
    model, socket = Model(), Socket()
    class Agent:
        def should_use_tools(self, *args):
            pytest.fail("oversized input must not trigger routing")
    handler = ChatHandler(model, None, Agent(), {"inference": {"stream": stream, "input_max_chars": 6000}})
    asyncio.run(handler._process_message(socket, "请解释" + "中文" * 6000, True, True))
    assert not model.prompts
    assert socket.messages[-1]["type"] == "complete"
    assert socket.messages[-1]["error"]["code"] == "input_budget_exceeded"


def test_rag_context_is_bounded_before_prompt_assembly():
    handler = ChatHandler(Model(), None, None, {"inference": {"rag_input_max_chars": 500}})
    context = handler._format_rag_context([
        {"SMILES": "C" * 10000, "source": "large-record"},
        {"SMILES": "CCO", "source": "source-id-2"},
    ])
    assert len(context) <= 500
    assert "source-id-2" in context
    assert "large-record" not in context
    assert "省略" in context


def test_partial_status_survives_when_question_uses_available_budget():
    from src.web.prompt_budget import OMITTED, STATUS_RESERVE, required_prompt
    handler = ChatHandler(Model(), None, None, {"inference": {"input_max_chars": 7000}})
    # The remaining budget is reserved for mandatory tool status, not optional evidence.
    question = "问" * (7000 - len(required_prompt("")) - len(OMITTED) - STATUS_RESERVE)
    prompt = handler._build_prompt_with_agent(question, "证据" * 10000, "", [],
        agent_result={"partial": True, "warnings": ["模型不可用"]})
    assert question in prompt
    assert '"partial":true' in prompt
    assert "模型不可用" in prompt
    assert len(prompt) <= 7000


@pytest.mark.parametrize("external", [False, True])
def test_provider_specific_budget_and_atomic_history(external):
    from src.agent.prompts import DRUG_DESIGN_SYSTEM_PROMPT
    model = Model()
    if external:
        model.provider_name = "openai_compatible"
    handler = ChatHandler(model, None, None, {"inference": {
        "input_max_chars": 6000, "external_input_max_chars": 16000,
        "history_input_max_chars": 2000,
    }})
    assert handler._input_limit() == (16000 if external else 6000)
    evidence = json.dumps({"SMILES": "C" * 600, "source": "source-id"})
    prompt = handler._build_prompt("解释", "", [], [{"user": "先前问题", "assistant": evidence}])
    assert DRUG_DESIGN_SYSTEM_PROMPT in prompt
    assert evidence in prompt
    assert len(prompt) <= handler._input_limit()


@pytest.mark.parametrize("value", [-1, True, "7000", None])
def test_invalid_budget_rejected_without_model(value):
    model, socket = Model(), Socket()
    handler = ChatHandler(model, None, None, {"inference": {"input_max_chars": value}})
    asyncio.run(handler._process_message(socket, "你好", False, False))
    assert not model.prompts
    assert socket.messages[-1]["error"]["code"] == "input_budget_exceeded"


@pytest.mark.parametrize("stream", [False, True])
def test_workflow_interpretation_keeps_partial_and_source(stream):
    class Agent:
        def should_use_tools(self, message):
            return True
        def execute(self, *args, **kwargs):
            return {"success": True, "partial": True, "status": "partial",
                "warnings": ["activity model unavailable"], "workflow_plan": {"steps": []},
                "final_answer": '{"status":"partial","error":"model unavailable","source":"RDKit:1"}',
                "tools_used": [], "tool_results": {}}
    model, socket = Model(), Socket()
    handler = ChatHandler(model, None, Agent(), {
        "inference": {"stream": stream}, "agent": {"summarize_workflow_results": True}})
    question = "解释此分析 SMILES: " + "C" * 150 + "，保留所有失败信息。"
    asyncio.run(handler._process_message(socket, question, False, True))
    assert len(model.prompts) == 1
    assert question in model.prompts[0]
    assert "RDKit:1" in model.prompts[0]
    assert "model unavailable" in model.prompts[0]
    assert "partial" in model.prompts[0]
    assert "系统已使用专业工具完成分析" not in model.prompts[0]


def test_nested_rag_evidence_remains_atomic_and_bounded():
    handler = ChatHandler(Model(), None, None, {"inference": {"rag_input_max_chars": 500}})
    class UnboundedRepresentation:
        def __str__(self):
            pytest.fail("unsupported objects must not be formatted into prompts")
    context = handler._format_rag_context([
        {"SMILES": "CCO", "source": {"id": "ref-2", "database": "local"}},
        {"SMILES": "CCC", "source": {"blob": "X" * 100000}},
        {"SMILES": "CC", "source": UnboundedRepresentation()},
    ])
    assert "ref-2" in context and "local" in context
    assert "X" * 10 not in context
    assert len(context) <= 500


def test_question_containing_section_delimiter_is_not_split():
    handler = ChatHandler(Model(), None, None, {})
    question = "开头\n\n## 当前用户问题\n原始问题尾部"
    prompt = handler._build_prompt(question, "来源：ref-1", [])
    assert question in prompt


def test_history_notices_share_one_budget():
    handler = ChatHandler(Model(), None, None, {"inference": {"history_input_max_chars": 30}})
    prompt = handler._build_prompt("解释", "", [], [
        {"user": "旧" * 100, "assistant": "旧" * 100},
        {"user": "旧" * 100, "assistant": "旧" * 100},
    ])
    assert prompt.count("历史记录因预算整轮省略") <= 1


def test_oversize_narrative_preserves_error_and_provenance_details():
    handler = ChatHandler(Model(), None, None, {})
    prompt = handler._build_prompt_with_agent("解释", "X" * 20000, "", [], agent_result={
        "partial": True, "warnings": ["RG-MPNN weights missing"],
        "provenance": {"source": "RDKit:real"},
        "tool_results": {"activity": {"success": False, "error": "missing_model"}},
    })
    assert "RG-MPNN weights missing" in prompt
    assert "RDKit:real" in prompt and "missing_model" in prompt


@pytest.mark.parametrize("stream", [False, True])
def test_required_metadata_too_large_skips_interpretation(stream):
    class Agent:
        def should_use_tools(self, message):
            return True
        def execute(self, *args, **kwargs):
            return {"success": True, "partial": True,
                "warnings": ["模型不可用" * 5000], "workflow_plan": {"steps": []},
                "final_answer": "仅性质计算完成，活性模型不可用。",
                "tools_used": [], "tool_results": {}}
    model, socket = Model(), Socket()
    handler = ChatHandler(model, None, Agent(), {
        "inference": {"stream": stream}, "agent": {"summarize_workflow_results": True}})
    asyncio.run(handler._process_message(socket, "解释结果", False, True))
    assert not model.prompts
    assert socket.messages[-1]["type"] == "complete"
    assert socket.messages[-1]["error"]["code"] == "interpretation_budget_exceeded"
    assert "活性模型不可用" in socket.messages[-1]["content"]
    assert "未进行模型总结" in socket.messages[-1]["content"]


def test_empty_rag_records_cannot_exceed_budget():
    handler = ChatHandler(Model(), None, None, {"inference": {"rag_input_max_chars": 50}})
    assert len(handler._format_rag_context([{}] * 1000)) <= 50


def test_exact_question_boundary_and_one_character_over():
    from src.web.prompt_budget import InputBudgetExceeded, OMITTED, STATUS_RESERVE, required_prompt
    handler = ChatHandler(Model(), None, None, {"inference": {"input_max_chars": 7000}})
    question = "问" * (7000 - len(required_prompt("")) - len(OMITTED) - STATUS_RESERVE)
    assert question in handler._build_prompt(question, "", [])
    with pytest.raises(InputBudgetExceeded):
        handler._build_prompt(question + "问", "", [])


@pytest.mark.parametrize("external", [False, True])
def test_actual_model_boundary_uses_provider_budget(external):
    model, socket = Model(), Socket()
    if external:
        model.provider_name = "openai_compatible"
    handler = ChatHandler(model, None, None, {"inference": {
        "stream": False, "input_max_chars": 6000, "external_input_max_chars": 16000}})
    question = "请解释" + "研究方法" * 1800
    asyncio.run(handler._process_message(socket, question, False, False))
    if external:
        assert question in model.prompts[0]
        assert len(model.prompts[0]) <= 16000
    else:
        assert not model.prompts
        assert socket.messages[-1]["error"]["code"] == "input_budget_exceeded"


@pytest.mark.parametrize("field", ["tool_result_sequence", "tool_results_by_step"])
def test_repeated_tool_step_evidence_is_not_lost(field):
    handler = ChatHandler(Model(), None, None, {})
    earlier = {"step_id": "first", "success": False, "error": "first-error", "source": "first-source"}
    later = {"step_id": "last", "success": True, "source": "last-source"}
    metadata = {"tool_results": {"property_calculator": later},
        field: [earlier, later] if field == "tool_result_sequence" else {"first": earlier, "last": later}}
    prompt = handler._build_prompt_with_agent("解释", "X" * 20000, "", [], agent_result=metadata)
    assert "first-error" in prompt and "first-source" in prompt
    assert "last-source" in prompt


def test_budgeted_rag_retains_shared_presentation_for_complete_records():
    from src.web.rag_presentation import format_rag_context

    records = [
        {"SMILES": "CCO", "similarity_score": 0.91, "source_index": 2,
         "provenance": {"vector_label": 1, "source_sha256": "a" * 64}},
        {"SMILES": "CCC", "similarity_score": 0.8, "source_index": 0,
         "provenance": {"vector_label": 0, "source_sha256": "a" * 64}},
    ]
    handler = ChatHandler(Model(), None, None, {})
    assert handler._format_rag_context(records) == format_rag_context(records)


def test_budgeted_rag_preflights_records_before_shared_formatter(monkeypatch):
    import src.web.chat_handler as chat_module
    from src.web.rag_presentation import format_rag_context

    small = {"SMILES": "CCO", "source_index": 2,
             "provenance": {"source_sha256": "a" * 64, "vector_label": 1}}
    large = {"SMILES": "C" * 10000, "source_index": 1,
             "provenance": {"source_sha256": "b" * 64}}
    seen = []

    def checked_format(records):
        assert all(len(record["SMILES"]) < 10000 for record in records)
        seen.append(records)
        return format_rag_context(records)

    monkeypatch.setattr(chat_module, "format_rag_context", checked_format)
    handler = ChatHandler(Model(), None, None, {"inference": {"rag_input_max_chars": 500}})
    context = handler._format_rag_context([large, small])
    assert seen and small in seen[-1]
    assert "a" * 64 in context and "b" * 64 not in context
    assert '"vector_label": 1' in context
    assert "省略" in context and len(context) <= 500


@pytest.mark.parametrize("entry", ["legacy", "rag_search", "rag_database_search"])
@pytest.mark.parametrize("rag_cap", [0, 1000])
def test_shared_retrieval_and_ui_provenance_survive_prompt_budget(
    retrieval_service, monkeypatch, entry, rag_cap,
):
    from src.agent.tools.rag_search_tool import RAGSearchTool

    rag = retrieval_service
    async_embedding, sync_embedding = rag.get_embedding, rag.get_embedding_sync
    calls = []

    async def embed_async(text):
        calls.append("async")
        return await async_embedding(text)

    def embed_sync(text):
        calls.append("sync")
        return sync_embedding(text)

    monkeypatch.setattr(rag, "get_embedding", embed_async)
    monkeypatch.setattr(rag, "get_embedding_sync", embed_sync)
    tool = RAGSearchTool(rag)

    class Agent:
        def should_use_tools(self, message):
            return True

        def execute(self, message, **kwargs):
            result = tool.execute(message, k=1)
            return {"success": result["success"], "final_answer": result["summary"],
                    "tools_used": [entry], "tool_results": {entry: result},
                    "workflow_plan": {"name": "rag_search"}}

    model, socket = Model(), Socket()
    handler = ChatHandler(model, rag, None if entry == "legacy" else Agent(), {
        "inference": {"stream": False, "rag_input_max_chars": rag_cap},
        "agent": {"summarize_workflow_results": True},
    })
    asyncio.run(handler._process_message(
        socket, "检索知识库中类似分子", True, entry != "legacy", rag_count=1,
    ))
    assert calls == (["async"] if entry == "legacy" else ["sync"])
    cards = [item for item in socket.messages if item["type"] == "rag_info"]
    assert len(cards) == 1
    record = cards[0]["molecules"][0]
    assert record["smiles"] == "CCC" and record["source_index"] == 2
    assert record["provenance"]["vector_label"] == 1
    assert record["provenance"]["source_sha256"] == rag.manifest.source_sha256
    assert record["provenance"]["index_sha256"] == rag.manifest.index_sha256
    assert "provenance" not in record["properties"]
    assert "source_index" not in record["properties"]
    assert len(model.prompts) == 1
    assert "检索知识库中类似分子" in model.prompts[0]
    if rag_cap:
        assert rag.manifest.source_sha256 in model.prompts[0]
    else:
        assert "RAG 检索证据" not in model.prompts[0]


def test_input_rejection_precedes_analysis_and_preserves_history(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("input analysis must not run for oversized questions")

    monkeypatch.setattr("src.web.chat_handler.preflight_generation_request", forbidden)
    monkeypatch.setattr("src.web.chat_handler.InputValidator", forbidden)
    model, socket = Model(), Socket()
    handler = ChatHandler(model, None, None, {"inference": {"input_max_chars": 6000}})
    history = [{"user": "earlier question", "assistant": "earlier response"}]
    before = list(history)
    asyncio.run(handler._process_message(
        socket, "问" * 7000, True, True, conversation_history=history,
    ))
    assert history == before and handler.conversation_history == []
    assert not model.prompts
    assert socket.messages == [{"type": "complete", "content": socket.messages[0]["content"],
                                "error": {"code": "input_budget_exceeded"}}]


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("case", ["both", "latest_only", "oversized_old", "total_limited"])
def test_actual_model_history_selection_and_chronology(stream, case):
    from src.web.prompt_budget import OMITTED, STATUS_RESERVE, required_prompt

    size = 1300 if case == "total_limited" else 100
    old = {"user": "earlier-turn-A", "assistant": json.dumps({
        "SMILES": "C" * (10000 if case == "oversized_old" else size),
        "provenance": {"source": "earlier-source-A"},
    })}
    latest = {"user": "latest-turn-B", "assistant": json.dumps({
        "SMILES": "N" + "C" * size,
        "provenance": {"source": "latest-source-B"},
    })}
    history = [old.copy(), latest.copy()]
    history_cap = (len(latest["user"]) + len(latest["assistant"]) + 40
                   if case in {"latest_only", "oversized_old"} else 4000)
    total_cap = 7000
    question = "Explain the latest answer."
    if case == "total_limited":
        question += "问" * (total_cap - len(required_prompt(question)) - len(OMITTED) - STATUS_RESERVE)
    model, socket = Model(), Socket()
    handler = ChatHandler(model, None, None, {"inference": {
        "stream": stream, "input_max_chars": total_cap,
        "history_input_max_chars": history_cap,
    }})
    asyncio.run(handler._process_message(
        socket, question, False, False, conversation_history=history,
    ))

    assert len(model.prompts) == 1
    prompt = model.prompts[0]
    latest_text = "用户: " + latest["user"] + "\n助手: " + latest["assistant"]
    assert latest_text in prompt
    assert prompt.endswith("\n\n## 当前用户问题\n" + question)
    assert len(prompt) <= total_cap
    assert len(prompt) - len(required_prompt(question)) <= history_cap + len(OMITTED)
    assert history[:2] == [old, latest]
    if case == "both":
        old_text = "用户: " + old["user"] + "\n助手: " + old["assistant"]
        assert old_text in prompt
        assert prompt.index(old_text) < prompt.index(latest_text) < prompt.index(question)
    else:
        assert old["user"] not in prompt and "earlier-source-A" not in prompt
        assert "省略" in prompt
