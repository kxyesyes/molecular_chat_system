import asyncio
import json

import pytest

from src.web.chat_handler import ChatHandler
from tests.test_rag_index_manifest import retrieval_service
from tests.agent.test_web_decision_runtime import actual_app, ActualSocket, cookie_for, chat_decision


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
def test_retained_prompt_formatter_keeps_complete_question_and_constraints(stream, source):
    # Retained formatting API only. Actual Pi HTTP bounds are exercised in
    # test_pi_web_input_budget; the retired prose-summary pipeline is not run.
    model, socket = Model(), Socket()
    question = "请检索知识库并解释研究方法。" + "完整中文问题" * 180 + "禁止编造结合能；最后约束必须保留。"
    history = [{"user": "旧问题" * 9000, "assistant": '{"SMILES":"' + "C" * 10000 + '"}'}]
    class Rag:
        is_initialized = True
        async def search_similar_molecules(self, *args, **kwargs):
            return [{"SMILES": "CCO", "source": "source-id-1", "notes": "资料" * 20000}]
        async def search_similar_molecules_with_status(self, *args, **kwargs):
            records = await self.search_similar_molecules(*args, **kwargs)
            return {"status": "succeeded", "records": records}
    class Agent:
        def should_use_tools(self, message):
            return True
        def execute(self, *args, **kwargs):
            return dict(success=True, partial=False, status="completed", warnings=["适用范围仅限性质计算"],
                provenance={"source": "tool-source", "method": "synthetic-property-fixture"},
                final_answer='{"status":"completed","source":"tool-source","data":"' + "证据" * 20000 + '"}',
                tools_used=[], tool_results={}, workflow_plan={"steps": []})
    handler = ChatHandler(model, Rag(), Agent() if source == "tools" else None,
        {"inference": {"stream": stream, "input_max_chars": 7000},
         "agent": {"summarize_workflow_results": True}})
    records = asyncio.run(Rag().search_similar_molecules(question)) if source == 'rag' else []
    rag_context = handler._format_rag_context(records) if records else ''
    if source == 'tools':
        result = Agent().execute()
        prompt = handler._build_prompt_with_agent(question, result['final_answer'], rag_context, records,
                                                  agent_result=result)
    else:
        prompt = handler._build_prompt(question, rag_context, records,
                                       conversation_history=history if source == 'history' else [])
    assert not model.prompts
    assert question in prompt
    assert len(prompt) <= 7000
    assert "不得编造" in prompt
    if source == "rag":
        assert handler._rag_info_payload(records, 'fixture records')['type'] == 'rag_info'
    if source == "tools":
        assert '"status":"completed"' in prompt
        assert '"partial":false' in prompt
        assert "适用范围仅限性质计算" in prompt
        assert "tool-source" in prompt and "synthetic-property-fixture" in prompt
        assert "系统已使用专业工具完成分析" not in prompt


@pytest.mark.parametrize("stream", [False, True])
def test_oversized_input_rejected_before_agent_or_model(actual_app, stream):
    async def run():
        async with actual_app(ordinary_policy=None) as bundle:
            bundle.app.chat_handler.config = {'inference': {'stream':stream, 'external_input_max_chars':6000}}
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                frames = await socket.turn({'message':'请解释' + '中文' * 1800,
                                            'enable_rag':True, 'enable_tools':True})
                assert not bundle.calls
                assert frames[-1]['type'] == 'complete'
                assert frames[-1]['error']['details']['reason'] == 'input_budget_exceeded'
                assert bundle.app.agent_state_store.get_tool_executions(frames[-1]['trace_id']) == []
    asyncio.run(run())


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
@pytest.mark.parametrize('field', ['external_input_max_chars', 'history_input_max_chars'])
def test_invalid_budget_rejected_without_model(actual_app, value, field):
    async def run():
        async with actual_app(ordinary_policy=None) as bundle:
            bundle.app.chat_handler.config = {'inference': {field:value}}
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                frames = await socket.turn({'message':'你好', 'enable_rag':False, 'enable_tools':False})
                assert not bundle.calls
                assert frames[-1]['error']['details']['reason'] == 'input_budget_exceeded'
    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("status", ["partial", "completed"])
def test_retained_formatters_preserve_workflow_sources_and_partial_diagnostics(stream, status):
    partial = status == "partial"
    warning = "activity model unavailable" if partial else "property-only scope"
    evidence = {"status": status, "source": "RDKit:1", "molecular_weight": 46.07}
    if partial:
        evidence["error"] = "model unavailable"
    body = json.dumps(evidence)

    class Agent:
        def should_use_tools(self, message):
            return True
        def execute(self, *args, **kwargs):
            return {"success": True, "partial": partial, "status": status,
                "warnings": [warning], "workflow_plan": {"steps": []},
                "provenance": {"source": "RDKit:1", "method": "synthetic-property-fixture"},
                "final_answer": body,
                "tools_used": [], "tool_results": {}}
    model, socket = Model(), Socket()
    handler = ChatHandler(model, None, Agent(), {
        "inference": {"stream": stream}, "agent": {"summarize_workflow_results": True}})
    question = "解释此分析 SMILES: " + "C" * 150 + "，保留所有失败信息。"
    from src.agent.contracts import AgentResult, RunOutcome
    from src.web.decision_chat import _result_frame
    # Synthetic result is only a serializer fixture, never scientific success
    # injected into Pi. Real computed partial input is tested separately.
    result = AgentResult('formatter-fixture', not partial, body, final_answer=body,
        partial=partial,
        outcome=RunOutcome.PARTIAL if partial else RunOutcome.COMPLETED,
        warnings=[warning], metadata={'binding_profile':'medchat-web-v1'})
    socket.messages = [json.loads(frame) for frame in _result_frame(handler, result, False)]
    assert not model.prompts
    assert result.warnings == [warning]
    if partial:
        assert not model.prompts
        completed = [item for item in socket.messages if item["type"] == "complete"]
        assert len(completed) == 1
        assert completed[0]["status"] == "partial"
        assert completed[0]["partial"] is True
        assert completed[0]["warnings"] == [warning]
        assert body in completed[0]["content"]
        assert "RDKit:1" in completed[0]["content"]
        assert "model unavailable" in completed[0]["content"]
        assert handler.conversation_history == []  # Projection never mutates memory.
    else:
        prompt = handler._build_prompt_with_agent(question, body, '', [], agent_result=Agent().execute())
        assert question in prompt
        assert len(prompt) <= handler._input_limit()
        assert "RDKit:1" in prompt and "synthetic-property-fixture" in prompt
        assert warning in prompt
        assert '"status":"completed"' in prompt and '"partial":false' in prompt
        assert "系统已使用专业工具完成分析" not in prompt
        assert socket.messages[-1]['status'] == 'completed' and body in socket.messages[-1]['content']


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
@pytest.mark.parametrize("status", ["partial", "completed"])
def test_required_metadata_too_large_skips_interpretation(stream, status):
    partial = status == "partial"
    warning = "模型不可用" if partial else "适用范围仅限性质计算"
    body = ("仅性质计算完成，活性模型不可用。" if partial else "性质计算完成。")
    body += "来源 RDKit:1；MW=46.07。"

    class Agent:
        def should_use_tools(self, message):
            return True
        def execute(self, *args, **kwargs):
            return {"success": True, "partial": partial, "status": status,
                "warnings": [warning * 5000], "workflow_plan": {"steps": []},
                "provenance": {"source": "RDKit:1", "method": "synthetic-property-fixture"},
                "final_answer": body,
                "tools_used": [], "tool_results": {}}
    model, socket = Model(), Socket()
    handler = ChatHandler(model, None, Agent(), {
        "inference": {"stream": stream}, "agent": {"summarize_workflow_results": True}})
    from src.web.prompt_budget import InputBudgetExceeded
    from src.agent.contracts import AgentResult, RunOutcome
    from src.web.decision_chat import _result_frame
    with pytest.raises(InputBudgetExceeded):
        handler._build_prompt_with_agent('解释结果', body, '', [], agent_result=Agent().execute())
    # No unbounded model summary: the actual serializer preserves the original
    # fixture result and bounds only its public warning projection.
    result = AgentResult('oversize-display-fixture', not partial, body, final_answer=body,
        partial=partial,
        outcome=RunOutcome.PARTIAL if partial else RunOutcome.COMPLETED,
        warnings=[warning * 5000], metadata={'binding_profile':'medchat-web-v1'})
    socket.messages = [json.loads(frame) for frame in _result_frame(handler, result, False)]
    assert result.warnings == [warning * 5000]
    assert not model.prompts
    completed = [item for item in socket.messages if item["type"] == "complete"]
    assert len(completed) == 1
    assert body in completed[0]["content"]
    assert handler.conversation_history == []
    assert not any(frame['type'] in {'stream', 'message'} for frame in socket.messages)
    assert socket.messages[0]['display_redacted_or_truncated'] is True
    if partial:
        assert completed[0]["status"] == "partial"
        assert completed[0]["partial"] is True
        assert completed[0]["error"] is None
        assert "活性模型不可用" in completed[0]["content"]
        assert '摘要已截断' in completed[0]['content']
        assert len(completed[0]["warnings"]) <= 20
        assert all(len(item) <= 256 for item in completed[0]["warnings"])
    else:
        assert completed[0]['status'] == 'completed'
        assert len(socket.messages[0]['warnings']) <= 20
        assert all(len(item) <= 256 for item in socket.messages[0]['warnings'])


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
def test_actual_model_boundary_uses_provider_budget(actual_app, external):
    async def run():
        async with actual_app(ordinary_policy=None) as bundle:
            # Exercise the existing provider classification without pretending
            # this offline HTTP adapter is live Ollama acceptance.
            bundle.model.provider_name = 'openai_compatible' if external else None
            bundle.app.chat_handler.config = {'inference': {
                'input_max_chars':6000, 'external_input_max_chars':16000}}
            question = '你好，请解释' + '研究方法' * 1125
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                frames = await socket.turn({'message':question, 'enable_rag':False, 'enable_tools':False})
                if external:
                    payload, = bundle.calls
                    assert payload['messages'][-1] == {'role':'user', 'content':question}
                    assert sum(len(m.get('content') or '') for m in payload['messages']) <= 16000
                    assert frames[-1]['status'] == 'completed'
                else:
                    assert not bundle.calls
                    assert frames[-1]['error']['details']['reason'] == 'input_budget_exceeded'
    asyncio.run(run())


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
    actual_app, retrieval_service, monkeypatch, entry, rag_cap,
):
    import httpx
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
    # Observe the real transport boundary: strict receipts intentionally bypass
    # the legacy overridable embedding helper. Both paths still embed once.
    requests = []
    sync_transport = httpx.HTTPTransport.handle_request
    async_transport = httpx.AsyncHTTPTransport.handle_async_request

    def send_sync(transport, request):
        requests.append(("sync", str(request.url), json.loads(request.content)))
        return sync_transport(transport, request)

    async def send_async(transport, request):
        requests.append(("async", str(request.url), json.loads(request.content)))
        return await async_transport(transport, request)

    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", send_sync)
    monkeypatch.setattr(httpx.AsyncHTTPTransport, "handle_async_request", send_async)
    tool = RAGSearchTool(rag)
    observations = []

    execute = tool.execute
    def observed(*args, **kwargs):
        result = execute(*args, **kwargs)
        observations.append(result)
        return result
    monkeypatch.setattr(tool, 'execute', observed)
    query = '检索数据库中类似分子' if entry == 'rag_database_search' else '检索知识库中类似分子'
    from src.agent.tooling.factory import build_tool_registry
    from tests.agent.test_pi_web_input_budget import compiled_decision
    from tests.agent.test_web_decision_runtime import result_of
    async def run():
        async with actual_app(ordinary_policy=None, respond=compiled_decision) as bundle:
            extra = build_tool_registry([tool])
            try:
                bundle.app.agent_tool_registry.register(extra.resolve('rag_search'))
                bundle.app.chat_handler.config = {'inference': {
                    'rag_input_max_chars':rag_cap, 'external_input_max_chars':24000}}
                async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                    await socket.ready()
                    frames = await socket.turn({'message':query, 'enable_rag':True,
                                                'enable_tools':entry != 'legacy', 'rag_count':1})
                    result = result_of(frames)
                    assert result['status'] == 'completed', result
                    assert len(bundle.calls) == 2
                    for payload in bundle.calls:
                        assert query in json.dumps(payload['messages'], ensure_ascii=False)
                        assert sum(len(m.get('content') or '') for m in payload['messages']) <= 24000
                    # Tool evidence/receipt is mandatory scientific input, not
                    # the old optional RAG prose block; rag_cap=0 cannot erase it.
                    observation = next(m for m in bundle.calls[-1]['messages'] if m['role'] == 'tool')
                    assert rag.manifest.source_sha256 in observation['content']
                    assert rag.manifest.index_sha256 in observation['content']
                    assert not bundle.rag.retrieve.called
                    return frames
            finally:
                extra.close()
    frames = asyncio.run(run())
    assert calls == []
    assert requests == [(
        "sync", rag.embedding_endpoint,
        {"model": rag.embedding_model_name, "prompt": query},
    )]
    cards = [item for item in frames if item["type"] == "rag_info"]
    assert len(cards) == 1
    record = cards[0]["molecules"][0]
    assert record["smiles"] == "CCC" and record["source_index"] == 2
    assert record["provenance"]["vector_label"] == 1
    assert record["provenance"]["source_sha256"] == rag.manifest.source_sha256
    assert record["provenance"]["index_sha256"] == rag.manifest.index_sha256
    assert "provenance" not in record["properties"]
    assert "source_index" not in record["properties"]
    from src.web.prompt_budget import STATUS_RESERVE, bounded_record
    observation, = observations
    assert observation['success']
    assert observation['evidence'][0]['retrieval_receipt']['diagnostics']['status'] == 'valid_hits'
    assert bounded_record({'tool_results': {entry:observation}}, STATUS_RESERVE - 80) is None
    complete = [item for item in frames if item['type'] == 'complete']
    assert len(complete) == 1 and complete[0]['status'] == 'completed'
    assert rag.manifest.source_sha256 in complete[0]['content']
    assert rag.manifest.index_sha256 in complete[0]['content']


def test_input_rejection_precedes_analysis_and_preserves_history(actual_app, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("input analysis must not run for oversized questions")

    monkeypatch.setattr("src.agent.contracts.generation_request.preflight_generation_request", forbidden)
    history = [{"user": "earlier question", "assistant": "earlier response"}]
    async def run():
        async with actual_app(ordinary_policy=None) as bundle:
            bundle.app.chat_handler.config = {'inference': {'external_input_max_chars':6000}}
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                sender.memory[:] = history
                # The admission module imports this validator lazily. Guard
                # the actual current boundary, not just the retired handler.
                monkeypatch.setattr('src.agent.utils.validators.InputValidator', forbidden)
                frames = await socket.turn({'message':'问' * 5000, 'enable_tools':True, 'enable_rag':True})
                assert sender.memory == history and bundle.app.chat_handler.conversation_history == []
                assert not bundle.calls
                assert frames[-1]['error']['details']['reason'] == 'input_budget_exceeded'
                assert bundle.app.agent_state_store.get_tool_executions(frames[-1]['trace_id']) == []
    asyncio.run(run())


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize("case", ["both", "latest_only", "oversized_old", "total_limited"])
def test_actual_model_history_selection_and_chronology(actual_app, stream, case):
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
        question += "问" * 1200
    async def run():
        async with actual_app(ordinary_policy=None) as bundle:
            bundle.app.chat_handler.config = {'inference': {
                'stream':stream, 'external_input_max_chars':total_cap, 'history_input_max_chars':history_cap}}
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                sender, = bundle.app.decision_runtime.sockets
                sender.memory[:] = history
                frames = await socket.turn({'message':question, 'enable_tools':False, 'enable_rag':False})
                assert frames[-1]['status'] == 'completed', frames[-1]
                payload, = bundle.calls
                messages = payload['messages']
                assert messages[-1] == {'role':'user', 'content':question}
                assert sum(len(m.get('content') or '') for m in messages) <= total_cap
                selected = [m for m in messages if m['role'] != 'system'][:-1]
                latest_pair = [{'role':role, 'content':latest[role]} for role in ('user', 'assistant')]
                assert selected[-2:] == latest_pair
                assert sum(len(m['content']) for m in selected) <= history_cap
                assert sender.memory[:2] == history == [old, latest]
                if case == 'both':
                    assert selected == [{'role':role, 'content':pair[role]}
                                        for pair in (old, latest) for role in ('user', 'assistant')]
                else:
                    assert selected == latest_pair
                    plain = json.dumps(messages, ensure_ascii=False)
                    assert old['user'] not in plain and 'earlier-source-A' not in plain
                    assert 'omitted for the input character budget' in plain
    asyncio.run(run())
