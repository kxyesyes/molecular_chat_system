"""Offline extraction contracts. All scientific values are synthetic fixtures."""

import copy
import importlib
import os
from pathlib import Path
import subprocess
import sys
from collections.abc import Mapping
from types import SimpleNamespace

import pytest

from src.agent.contracts import AgentResult
from src.web.chat_handler import ChatHandler
from src.web.prompt_budget import InputBudgetExceeded, OMITTED, STATUS_RESERVE, required_prompt


BODY = "\n  合成测试 MW=46.07000；IC50=0.2500 µg/mL；F/C=C/F\n\n"
FALLBACK = "科学计算未成功完成，请检查输入或工具状态后重试。"
NOTICE = "\n[RAG 记录因预算整条省略，不能推断其内容。]"


def handler(config=None, cls=ChatHandler):
    return cls(None, None, None, {} if config is None else config)


@pytest.fixture(params=["legacy", "direct"])
def result_api(request):
    if request.param == "direct":
        def call(name):
            def invoke(*args, **kwargs):
                module = importlib.import_module("src.web.agent_result_presentation")
                return getattr(module, name)(*args, **kwargs)
            return invoke
        return SimpleNamespace(**{name: call(name) for name in (
            "failure_envelope", "sanitize_failure_text", "sanitize_warnings",
            "presentation_status", "partial_projection", "failure_content")})
    return SimpleNamespace(
        failure_envelope=ChatHandler._agent_failure_envelope,
        sanitize_failure_text=ChatHandler._sanitize_agent_failure_text,
        sanitize_warnings=ChatHandler._sanitize_agent_warnings,
        presentation_status=ChatHandler._agent_presentation_status,
        partial_projection=ChatHandler._partial_agent_projection,
        failure_content=ChatHandler._agent_failure_content,
    )


@pytest.fixture(params=["legacy", "direct"])
def prompt_api(request):
    if request.param == "direct":
        def module():
            return importlib.import_module("src.web.chat_prompt_builder")
        return SimpleNamespace(
            rag=lambda h, rows: module().format_budgeted_rag_context(rows, config=h.config),
            chat=lambda h, question, rag, history=None: module().build_chat_prompt(
                question, rag, config=h.config, input_limit=h._input_limit,
                history=h.conversation_history if history is None else history),
            agent=lambda h, question, body, rag, result: module().build_agent_prompt(
                question, body, rag, config=h.config, input_limit=h._input_limit,
                agent_result=result),
        )
    return SimpleNamespace(
        rag=lambda h, rows: h._format_rag_context(rows),
        chat=lambda h, question, rag, history=None: h._build_prompt(question, rag, [], history),
        agent=lambda h, question, body, rag, result: h._build_prompt_with_agent(
            question, body, rag, [], result),
    )


def test_envelope_is_exact_and_fresh(result_api):
    expected = {"success": False, "status": "failed", "final_answer": "",
                "active_skill": "", "trace_id": "", "error": None,
                "warnings": [], "tool_result_sequence": []}
    assert result_api.failure_envelope() == expected
    first = result_api.failure_envelope()
    first["warnings"].append("changed")
    first["tool_result_sequence"].append({})
    assert result_api.failure_envelope() == expected


@pytest.mark.parametrize("value,expected", [
    ({"success": True}, "completed"), ({"success": 1}, "failed"),
    ({"success": False, "partial": True}, "partial"),
    ({"success": True, "partial": 1}, "completed"),
    ({"success": False, "status": "completed"}, "failed"),
    ({"success": True, "status": "completed", "partial": True}, "partial"),
    ({"success": False, "status": "partial"}, "partial"),
    *[({"success": True, "partial": True, "status": status}, status)
      for status in ("failed", "cancelled", "rejected")],
    *[({"success": True, "partial": True, "status": status}, "failed")
      for status in (None, [], {}, "invalid", "succeeded")],
])
def test_status_precedence(result_api, value, expected):
    before = copy.deepcopy(value)
    assert result_api.presentation_status(value) == expected
    assert value == before


@pytest.mark.parametrize("value,expected", [
    (None, ("", False)), (42, ("", False)), (" \n", ("", False)),
    ("  MW=46.07000  ", ("MW=46.07000", False)),
])
def test_safe_failure_text(result_api, value, expected):
    assert result_api.sanitize_failure_text(value, max_chars=1024) == expected


@pytest.mark.parametrize("value,expected", [
    ({"final_answer": "  authoritative  ", "error": {"message": "ignored"}}, "authoritative"),
    ({"final_answer": "workflow failed", "error": {"message": "specific error"}}, "specific error"),
    ({"final_answer": "NO WORKFLOW STEPS WERE EXECUTED", "tool_result_sequence": [
        None, {"success": True, "message": "ignored"},
        {"status": "rejected", "message": "first failure"},
        {"success": False, "message": "second failure"}]}, "first failure"),
    ({}, FALLBACK),
    ({"final_answer": "api_key=" + "synthetic-secret", "error": {"message": "safe"}}, FALLBACK),
])
def test_failure_content_selection(result_api, value, expected):
    before = copy.deepcopy(value)
    assert result_api.failure_content(value) == expected
    assert value == before


def test_partial_exact_content_metadata_and_typed_skips(result_api):
    typed = AgentResult("typed", True, "synthetic", metadata={"skipped_steps": [
        {"step_id": "dock", "status": "skipped_precondition", "reason": "blocked_by:rank"}]})
    value = {"final_answer": BODY, "trace_id": " trace ", "active_skill": " design ",
             "warnings": [" safe warning ", "", None, "[redacted]"],
             "error": {"code": "unavailable", "message": "missing model", "details": {"not": "projected"}},
             "tool_result_sequence": [None, {"status": "succeeded"},
                 {"step_id": "rank", "tool_name": "ranker", "success": False, "message": "no score"}],
             "tool_results": {"ignored": {"success": False}}, "agent_result": typed}
    before = copy.deepcopy(value)
    expected = {
        "content": "部分完成，并非全部步骤成功。\n\n" + BODY
            + "\n\n未完成步骤（失败、部分完成或跳过）："
            + "\n\n- rank (ranker) [failed]：no score"
            + "\n\n- dock [skipped_precondition]：blocked_by:rank"
            + "\n\n错误说明：unavailable：missing model\n\n警告：\n- safe warning",
        "status": "partial", "partial": True, "trace_id": "trace", "active_skill": "design",
        "warnings": ["safe warning"], "error": {"code": "unavailable", "message": "missing model"},
        "failed_steps": [
            {"step_id": "rank", "tool_name": "ranker", "status": "failed", "message": "no score", "error_code": ""},
            {"step_id": "dock", "tool_name": "", "status": "skipped_precondition", "message": "blocked_by:rank", "error_code": ""}],
    }
    assert result_api.partial_projection(value) == expected
    assert value == before


def test_partial_fallback_observations_and_warning_bounds(result_api):
    value = {"tool_results": {"tool": {"success": False, "error": "missing"}},
             "warnings": [" w "] * 21, "final_answer": BODY * 100}
    before = copy.deepcopy(value)
    projection = result_api.partial_projection(value)
    assert projection["failed_steps"] == [{"step_id": "", "tool_name": "tool", "status": "failed",
                                           "message": "missing", "error_code": ""}]
    assert projection["warnings"] == ["w"] * 20
    assert projection["content"] == ("部分完成，并非全部步骤成功。\n\n" + BODY * 100
        + "\n\n未完成步骤（失败、部分完成或跳过）：\n\n- 未命名步骤 (tool) [failed]：missing"
        + "\n\n警告：\n" + "\n".join(["- w"] * 20)
        + "\n\n摘要已截断，以上未列出全部元信息或未完成步骤。")
    assert value == before


def test_partial_secret_guard_precedes_truncation(result_api):
    unsafe = "x" * 20000 + " Cookie: session=synthetic-only"
    result = result_api.partial_projection({"final_answer": BODY + unsafe, "trace_id": unsafe,
        "active_skill": unsafe, "warnings": [unsafe], "error": {"code": unsafe, "message": unsafe}})
    assert result == {
        "content": "部分完成，并非全部步骤成功。\n\n工具正文含敏感信息，出于安全原因未展示；请检查工具输出。"
                   "\n\n部分结果原因未提供；不能据此认为其余步骤已完成。"
                   "\n\n摘要已截断，以上未列出全部元信息或未完成步骤。",
        "status": "partial", "partial": True, "trace_id": "", "active_skill": "", "warnings": [],
        "error": {"code": "", "message": ""}, "failed_steps": [],
    }


def test_warning_sanitizer_filters_and_bounds(result_api):
    assert result_api.sanitize_warnings("not a list") == []
    assert result_api.sanitize_warnings([None, "", "[REDACTED]", " a ",
        "api_key=" + "synthetic-only", " b "]) == ["a", "b"]
    assert result_api.sanitize_warnings(["w"] * 21) == ["w"] * 20


class BrokenStep(Mapping):
    def __iter__(self):
        raise ValueError("synthetic malformed observation")

    def __len__(self):
        return 1

    def __getitem__(self, key):
        raise ValueError("synthetic malformed observation")


def test_malformed_step_logs_before_later_exception(monkeypatch):
    import src.web.chat_handler as chat_module
    calls = []

    class LateError(dict):
        def get(self, key, default=None):
            if key == "metadata":
                calls.append("metadata")
                raise RuntimeError("later failure")
            return super().get(key, default)

    monkeypatch.setattr(chat_module.logger, "warning", lambda message: calls.append(message))
    with pytest.raises(RuntimeError, match="later failure"):
        ChatHandler._partial_agent_projection(LateError(tool_result_sequence=[BrokenStep(), BrokenStep()]))
    assert calls == ["Skipped malformed partial step metadata"] * 2 + ["metadata"]


def test_text_sanitizer_subclass_dispatch():
    calls = []

    class Override(ChatHandler):
        @staticmethod
        def _sanitize_agent_failure_text(value, *, max_chars):
            calls.append((value, max_chars))
            return ("hook:" + value, False) if isinstance(value, str) else ("", False)

    assert Override._sanitize_agent_warnings(["w"]) == ["hook:w"]
    assert calls == [("w", 256)]
    calls.clear()
    assert Override._agent_failure_content({"final_answer": "body"}) == "hook:body"
    assert calls == [("body", 1024)]
    calls.clear()
    result = Override._partial_agent_projection({"trace_id": "t", "active_skill": "s", "warnings": ["w"]})
    assert result["trace_id"] == "hook:t" and result["warnings"] == ["hook:w"]
    assert calls == [("t", 128), ("s", 128), ("w", 256)]


def test_warning_sanitizer_subclass_dispatch():
    calls = []

    class Override(ChatHandler):
        @classmethod
        def _sanitize_agent_warnings(cls, warnings):
            calls.append((cls, warnings))
            return ["hook warning"]

    result = Override._partial_agent_projection({"warnings": ["w"]})
    assert result["warnings"] == ["hook warning"]
    assert calls == [(Override, ["w"])]


def test_prompt_exact_history_selection_and_nonmutation(prompt_api):
    h = handler()
    h.conversation_history = [
        {"user": "ignored", "assistant": "oldest"},
        {"user": "first", "assistant": BODY}, {"user": "second", "assistant": "CCO"}]
    before = copy.deepcopy((h.config, h.conversation_history))
    question = "解释\n\n## 当前用户问题\n仍是问题"
    prefix = required_prompt("").removesuffix("\n\n## 当前用户问题\n")
    assert prompt_api.chat(h, question, "RAG:46.07000") == (
        prefix + "\nRAG 检索证据\nRAG:46.07000\n\n"
        + "\n历史\n用户: first\n助手: " + BODY + "\n\n"
        + "\n历史\n用户: second\n助手: CCO\n\n\n\n## 当前用户问题\n" + question)
    assert prompt_api.chat(h, question, "", []) == required_prompt(question)
    assert (h.config, h.conversation_history) == before


def test_agent_prompt_metadata_order_and_nonmutation(prompt_api):
    h = handler()
    value = {"trace_id": "trace", "error": {"code": "unavailable"}, "status": "partial",
             "partial": True, "success": True, "ignored": "not evidence"}
    before = copy.deepcopy(value)
    prefix = required_prompt("").removesuffix("\n\n## 当前用户问题\n")
    assert prompt_api.agent(h, "解释", BODY, "CCO", value) == (
        prefix + '\n工具状态与来源（逐项核对，不可推断全部成功）：\n'
        + '{"success":true,"partial":true,"status":"partial","error":{"code":"unavailable"},"trace_id":"trace"}\n'
        + "\n工具证据（保留来源和错误）\n" + BODY + "\n\n"
        + "\nRAG 检索证据\nCCO\n\n\n\n## 当前用户问题\n解释")
    assert value == before


def test_rag_atomic_format_and_nonmutation(prompt_api):
    h = handler({"inference": {"rag_input_max_chars": 500}})
    rows = [{"SMILES": "C" * 10000}, {"SMILES": "F/C=C/F", "similarity_score": 0.91234,
                                         "score": 0.5000, "source": {"id": "fixture"}}]
    before = copy.deepcopy(rows)
    assert prompt_api.rag(h, rows) == (
        'Relevant molecular data found:\n1. SMILES: F/C=C/F (similarity: 0.912)'
        '\n   score: 0.5\n   source: {"id": "fixture"}' + NOTICE)
    assert rows == before


def test_rag_formatter_monkeypatch_only_receives_bounded_records(monkeypatch):
    import src.web.chat_handler as chat_module
    calls = []

    class Unserializable:
        def __str__(self):
            pytest.fail("must not format unsupported object")

    def formatter(rows):
        calls.append(rows)
        return "rendered"

    monkeypatch.setattr(chat_module, "format_rag_context", formatter)
    h = handler({"inference": {"rag_input_max_chars": 500}})
    assert h._format_rag_context([{"SMILES": "C" * 10000}, {"bad": Unserializable()},
                                  {"SMILES": "CCO"}]) == "rendered" + NOTICE
    assert calls == [[{"SMILES": "CCO"}]]


@pytest.mark.parametrize("case", ["history", "rag", "tool", "metadata", "malformed-history", "ok-chat", "ok-agent"])
def test_lazy_input_limit_order(prompt_api, case):
    calls = []

    class Override(ChatHandler):
        def _input_limit(self):
            calls.append("limit")
            raise RuntimeError("input limit evaluated")

    config = {"inference": {f"{case}_input_max_chars": -1}} if case in {"history", "rag", "tool"} else {}
    h = handler(config, Override)
    if case in {"history", "rag", "malformed-history", "ok-chat"}:
        history = [None] if case == "malformed-history" else []
        run = lambda: prompt_api.chat(h, "问", "", history)
    else:
        value = {"warnings": ["w" * 5000]} if case == "metadata" else {}
        run = lambda: prompt_api.agent(h, "问", "", "", value)
    error = RuntimeError if case.startswith("ok-") else AttributeError if case == "malformed-history" else InputBudgetExceeded
    with pytest.raises(error):
        run()
    assert calls == (["limit"] if case.startswith("ok-") else [])


def test_exact_question_budget_and_overflow(prompt_api):
    h = handler({"inference": {"input_max_chars": 7000}})
    question = "问" * (7000 - len(required_prompt("")) - len(OMITTED) - STATUS_RESERVE)
    assert prompt_api.chat(h, question, "") == required_prompt(question)
    with pytest.raises(InputBudgetExceeded, match="当前问题超过输入字符预算"):
        prompt_api.chat(h, question + "问", "")


def test_empty_rag_does_not_evaluate_invalid_config(prompt_api):
    assert prompt_api.rag(handler({"inference": {"rag_input_max_chars": -1}}), []) == ""


@pytest.mark.parametrize("name", ["agent_result_presentation", "chat_prompt_builder"])
def test_pure_module_import_does_not_load_runtime(name, tmp_path):
    code = """
import importlib, socket, sys
sys.path.insert(0, sys.argv[1])
def blocked(*args, **kwargs):
    raise AssertionError('pure import must not connect')
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.create_connection = blocked
module = importlib.import_module('src.web.' + sys.argv[2])
for name in ('src.web.app', 'src.web.chat_handler', 'src.web.models', 'src.web.model_lifecycle'):
    assert name not in sys.modules, name
assert not any(name in vars(module) for name in ('logger', 'logging', 'WebSocket', 'model', 'app'))
"""
    env = {key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR", "PATH", "TEMP", "TMP", "COMSPEC")
           if key in os.environ}
    env.update(PYTHONDONTWRITEBYTECODE="1", PYTHONIOENCODING="utf-8")
    result = subprocess.run([sys.executable, "-B", "-c", code, str(Path(__file__).resolve().parents[2]), name],
                            cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("old,new,args", [
    ("_agent_failure_envelope", "failure_envelope", ()),
    ("_sanitize_agent_failure_text", "sanitize_failure_text", ("body",)),
    ("_sanitize_agent_warnings", "sanitize_warnings", (["w"],)),
    ("_agent_presentation_status", "presentation_status", ({"success": True},)),
    ("_partial_agent_projection", "partial_projection", ({"final_answer": BODY},)),
    ("_agent_failure_content", "failure_content", ({"error": "missing"},)),
])
def test_result_wrappers_delegate_current_hooks(monkeypatch, old, new, args):
    module = importlib.import_module("src.web.agent_result_presentation")
    sentinel, calls = object(), []

    def spy(*values, **kwargs):
        calls.append((values, kwargs))
        return sentinel

    monkeypatch.setattr(module, new, spy)
    kwargs = {"max_chars": 17} if new == "sanitize_failure_text" else {}
    assert getattr(ChatHandler, old)(*args, **kwargs) is sentinel
    assert len(calls) == 1
    values, hooks = calls[0]
    assert values == args
    if new in {"sanitize_warnings", "partial_projection", "failure_content"}:
        assert hooks["sanitize_text"] == ChatHandler._sanitize_agent_failure_text
    if new == "partial_projection":
        assert hooks["sanitize_warnings"] == ChatHandler._sanitize_agent_warnings
        assert callable(hooks["on_malformed_step"])
    if new == "sanitize_failure_text":
        assert hooks == kwargs


@pytest.mark.parametrize("kind", ["rag", "chat-default", "chat-empty", "agent"])
def test_prompt_wrappers_delegate_without_eager_limit(monkeypatch, kind):
    import src.web.chat_handler as chat_module
    module = importlib.import_module("src.web.chat_prompt_builder")
    calls, sentinel = [], object()

    class Override(ChatHandler):
        def _input_limit(self):
            pytest.fail("wrapper must not eagerly evaluate limit")

    h = handler(cls=Override)
    h.conversation_history = [{"user": "fallback", "assistant": "CCO"}]
    empty, rows, result = [], [{"SMILES": "CCO"}], {"success": True}

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    name = "format_budgeted_rag_context" if kind == "rag" else "build_agent_prompt" if kind == "agent" else "build_chat_prompt"
    monkeypatch.setattr(module, name, spy)
    if kind == "rag":
        actual = h._format_rag_context(rows)
    elif kind == "agent":
        actual = h._build_prompt_with_agent("question", BODY, "rag", rows, result)
    else:
        actual = h._build_prompt("question", "rag", rows, empty if kind == "chat-empty" else None)
    assert actual is sentinel and len(calls) == 1
    args, kwargs = calls[0]
    assert kwargs["config"] is h.config
    if kind == "rag":
        assert args == (rows,) and args[0] is rows
        assert kwargs["formatter"] is chat_module.format_rag_context
    else:
        assert kwargs["input_limit"] == h._input_limit
        if kind == "agent":
            assert args == ("question", BODY, "rag")
            assert kwargs["agent_result"] is result
        else:
            assert args == ("question", "rag")
            assert kwargs["history"] is (empty if kind == "chat-empty" else h.conversation_history)


def test_direct_diagnostic_callback_is_optional_and_at_failure_point():
    module = importlib.import_module("src.web.agent_result_presentation")
    result = {"final_answer": BODY, "tool_result_sequence": [BrokenStep()]}
    assert module.partial_projection(result)["content"] == (
        "部分完成，并非全部步骤成功。\n\n" + BODY
        + "\n\n部分结果原因未提供；不能据此认为其余步骤已完成。")
    calls = []

    def notify():
        calls.append("malformed")

    class LateError(dict):
        def get(self, key, default=None):
            if key == "metadata":
                calls.append("metadata")
                raise RuntimeError("later failure")
            return super().get(key, default)

    with pytest.raises(RuntimeError, match="later failure"):
        module.partial_projection(LateError(result), on_malformed_step=notify)
    assert calls == ["malformed", "metadata"]

    def failing_notify():
        raise LookupError("diagnostic failure")

    with pytest.raises(LookupError, match="diagnostic failure"):
        module.partial_projection(result, on_malformed_step=failing_notify)
