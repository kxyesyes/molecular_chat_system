"""Finite HTTP fixtures exercise the G3 observer, real adapter and worker ownership."""

import asyncio
import builtins
from collections import Counter
from contextlib import contextmanager
import hashlib
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
from types import ModuleType, SimpleNamespace

import httpx
import pytest

from tests.agent.test_generation_transport_characterization import (
    TEN, isolated_production_context, mocked_client, ok,
)
from tests.test_g3_ollama_metadata import NoEnvironment


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts/g3_generation_canary.py"
URL = "http://127.0.0.1:11434/api/generate"
LIMIT = 1024 * 1024
PRIVATE = "SYNTHETIC_CANARY_PRIVATE_e927"
COUNTERS = {
    "transport_entries", "delegate_entries", "response_headers_seen",
    "bodies_completed", "transport_failures", "denied_before_delegate",
}
STOP_CODES = {
    "request_denied", "attempt_limit", "unsupported_encoding", "response_too_large",
    "deadline", "cancelled", "transport_error", "local_cleanup_unresolved", "provider_model_unverified",
}
ATTEMPT_KEYS = {
    "ordinal", "prompt_sha256", "prompt_bytes", "max_tokens", "temperature", "num_ctx",
    "http_status", "body_complete", "entity_bytes", "protocol", "usage", "provider_model",
    "error_code", "elapsed_ms", "local_closed",
}


class ImportGuard:
    """Sticky forbidden-I/O control active before subject code executes."""

    def __init__(self):
        self.violations = []

    def deny(self, code="forbidden_io", *args, **kwargs):
        self.violations.append("forbidden_io")
        raise AssertionError("forbidden_io")

    def assert_clean(self):
        assert not self.violations, "forbidden_io"

    @contextmanager
    def installed(self):
        with pytest.MonkeyPatch.context() as patch:
            patch.setattr(os, "environ", NoEnvironment(self))
            for target, name in (
                (builtins, "open"), (io, "open"), (io, "open_code"),
                (os, "open"), (os, "listdir"), (os, "scandir"),
                (os, "system"), (os, "popen"), (subprocess, "Popen"),
                (socket, "create_connection"), (socket, "getaddrinfo"),
                (socket.socket, "connect"), (socket.socket, "connect_ex"),
                (threading.Thread, "start"),
                (httpx.Client, "__init__"), (httpx.AsyncClient, "__init__"),
            ):
                patch.setattr(target, name, self.deny)
            yield self


def load_subject():
    # Only this exact source read occurs before the import guard. No guessed
    # search path, project import, client constructor or execution during collect.
    assert SCRIPT.is_file(), "missing_canary_api"
    source = SCRIPT.read_bytes()
    name = "_g3_canary_test_subject"
    module = ModuleType(name)
    module.__file__ = str(SCRIPT)
    guard = ImportGuard()
    with pytest.MonkeyPatch.context() as patch:
        patch.setitem(sys.modules, name, module)
        try:
            with guard.installed():
                exec(compile(source, str(SCRIPT), "exec"), module.__dict__)
        finally:
            guard.assert_clean()
    return module


class Clock:
    def __init__(self):
        self.now = 0.0

    def __call__(self):
        return self.now


class Body(httpx.SyncByteStream):
    def __init__(self, chunks, before_chunk=None, disconnect=False):
        self.chunks = tuple(chunks)
        self.before_chunk = before_chunk
        self.disconnect = disconnect
        self.offered = 0
        self.closed = 0

    def __iter__(self):
        for chunk in self.chunks:
            if self.before_chunk:
                self.before_chunk(self.offered)
            self.offered += 1
            yield chunk
        if self.disconnect:
            raise httpx.ReadError(PRIVATE)

    def close(self):
        self.closed += 1


class FiniteHTTP(httpx.BaseTransport):
    """No guard/deadline/generation policy; records every delegate attempt."""

    def __init__(self, responses, *, close_error=False):
        self.responses = tuple(responses)
        self.calls = []
        self.bodies = []
        self.violations = []
        self.closed = 0
        self.close_error = close_error

    def handle_request(self, request):
        self.calls.append(request)
        index = len(self.calls) - 1
        if index >= len(self.responses):
            self.violations.append("extra_delegate")
            raise AssertionError("extra_delegate")
        status, headers, body = self.responses[index]
        self.bodies.append(body)
        return httpx.Response(status, headers=headers, stream=body, request=request)

    def close(self):
        self.closed += 1
        if self.close_error:
            raise RuntimeError(PRIVATE)

    def assert_clean(self):
        assert not self.violations, "extra_delegate"
        assert all(body.closed >= 1 for body in self.bodies), "unclosed_body"


def payload(text="\n".join(TEN), **extra):
    return {"response": text, "done": True, "prompt_eval_count": 11,
            "eval_count": 7, **extra}


def response(value=None, *, status=200, headers=(), chunks=None,
             before_chunk=None, disconnect=False):
    if chunks is None:
        chunks = (json.dumps(payload() if value is None else value).encode(),)
    return status, headers, Body(chunks, before_chunk, disconnect)


def request(*, method="POST", url=URL, headers=None, data=None):
    return httpx.Request(method, url, headers=headers, json=data or {
        "model": "gmm-llama:latest", "prompt": PRIVATE, "stream": False,
        "options": {"temperature": 0.7, "num_predict": 1000, "num_ctx": 512},
    })


@contextmanager
def observed(api, responses, *, clock=None, cancel=None, outer_deadline=210.0, require_model=False):
    clock, cancel = clock or Clock(), cancel or threading.Event()
    delegate = FiniteHTTP(responses)
    observer = api.CanaryTransport(
        delegate, clock=clock, started_at=0.0, outer_deadline=outer_deadline,
        cancelled=cancel.is_set, require_model=require_model,
    )
    try:
        yield observer, delegate
    finally:
        observer.close()
        delegate.assert_clean()
        assert delegate.closed >= 1


def consume(observer, req=None):
    result = observer.handle_request(request() if req is None else req)
    try:
        return b"".join(result.stream)
    finally:
        result.close()


def expect_stop(api, operation, code):
    with pytest.raises(api.CanaryStop) as caught:
        operation()
    assert caught.value.code == code
    assert str(caught.value) == "Canary stopped"
    assert PRIVATE not in repr(caught.value)


def assert_snapshot(snapshot):
    assert set(snapshot) == {"counters", "attempts", "stop_code"}
    assert set(snapshot["counters"]) == COUNTERS
    assert all(type(n) is int and n >= 0 for n in snapshot["counters"].values())
    assert snapshot["stop_code"] is None or snapshot["stop_code"] in STOP_CODES
    assert len(snapshot["attempts"]) == snapshot["counters"]["delegate_entries"]
    for ordinal, fact in enumerate(snapshot["attempts"], 1):
        assert set(fact) == ATTEMPT_KEYS
        assert fact["ordinal"] == ordinal
        assert fact["num_ctx"] == 512 and fact["max_tokens"] == 1000 and fact["temperature"] == 0.7
        assert set(fact["usage"]) == {"prompt_eval_count", "eval_count"}
        for count in fact["usage"].values():
            assert set(count) == {"state", "value"}
            assert count["state"] in {"reported", "missing", "null", "invalid", "unknown", "overflow"}
            assert (type(count["value"]) is int and count["value"] >= 0
                    if count["state"] == "reported" else count["value"] is None)


@pytest.mark.parametrize("operation", [
    lambda: os.environ.get(PRIVATE), lambda: os.environ.copy(),
    lambda: os.environ.items(), lambda: os.environ.keys(),
    lambda: os.environ.values(), lambda: open(PRIVATE),
    lambda: subprocess.Popen([PRIVATE]), lambda: socket.create_connection((PRIVATE, 1)),
])
def test_import_guard_retains_swallowed_violation(operation):
    guard = ImportGuard()
    with guard.installed():
        try:
            operation()
        except AssertionError:
            pass
    with pytest.raises(AssertionError, match="forbidden_io"):
        guard.assert_clean()


def test_delegate_guard_retains_swallowed_extra_attempt():
    delegate = FiniteHTTP([])
    try:
        delegate.handle_request(request())
    except AssertionError:
        pass
    assert len(delegate.calls) == 1
    with pytest.raises(AssertionError, match="extra_delegate"):
        delegate.assert_clean()


def test_script_import_is_passive_and_has_fixed_narrow_api():
    api = load_subject()
    for name in ("CanaryTransport", "CanaryStop", "run_canary", "encode_report"):
        assert callable(getattr(api, name, None)), "missing_canary_api"


@pytest.mark.parametrize("fault", [
    "method", "host", "hostname", "port", "path", "query", "credentials",
    "authorization", "cookie", "proxy-authorization", "model", "stream",
    "temperature", "num_predict", "num_ctx", "extra_option", "extra_field",
])
def test_request_fence_latches_before_delegate(fault):
    api = load_subject()
    data = json.loads(request().content)
    kwargs = {}
    if fault == "method":
        kwargs["method"] = "GET"
    elif fault in {"host", "hostname", "port", "path", "query", "credentials"}:
        kwargs["url"] = {
            "host": "http://192.0.2.1:11434/api/generate",
            "hostname": "http://localhost:11434/api/generate",
            "port": "http://127.0.0.1:11435/api/generate",
            "path": "http://127.0.0.1:11434/api/show",
            "query": URL + "?private=" + PRIVATE,
            "credentials": URL.replace("127.0.0.1", PRIVATE + "@127.0.0.1"),
        }[fault]
    elif fault in {"authorization", "cookie", "proxy-authorization"}:
        kwargs["headers"] = {fault: PRIVATE}
    elif fault in {"model", "stream", "extra_field"}:
        data[{"extra_field": "system"}.get(fault, fault)] = PRIVATE
    else:
        data["options"][{"extra_option": "seed"}.get(fault, fault)] = 1
    with observed(api, []) as (observer, delegate):
        expect_stop(api, lambda: consume(observer, request(data=data, **kwargs)), "request_denied")
        expect_stop(api, lambda: consume(observer), "request_denied")
        state = observer.snapshot()
        assert_snapshot(state)
        assert state["counters"]["transport_entries"] == 2
        assert state["counters"]["denied_before_delegate"] == 2
        assert not delegate.calls


def test_exact_five_delegate_budget_not_erasable_by_swallowing():
    api = load_subject()
    with observed(api, [response() for _ in range(5)]) as (observer, delegate):
        for _ in range(5):
            consume(observer)
        for _ in range(2):
            expect_stop(api, lambda: consume(observer), "attempt_limit")
        state = observer.snapshot()
        assert_snapshot(state)
        assert state["counters"] == dict(transport_entries=7, delegate_entries=5,
            response_headers_seen=5, bodies_completed=5, transport_failures=0,
            denied_before_delegate=2)
        assert len(delegate.calls) == 5


@pytest.mark.parametrize("headers", [
    [("content-encoding", "gzip")], [("content-encoding", "br")],
    [("content-encoding", "deflate")], [("content-encoding", "")],
    [("content-encoding", "identity,gzip")],
    [("content-encoding", "identity"), ("content-encoding", "identity")],
])
def test_encoding_rejected_before_httpx_decoding_or_body_read(headers):
    api = load_subject()
    item = response(headers=headers, chunks=(b"not-compressed-" + PRIVATE.encode(),))
    with observed(api, [item]) as (observer, delegate):
        expect_stop(api, lambda: consume(observer), "unsupported_encoding")
        assert item[2].offered == 0 and item[2].closed >= 1
        expect_stop(api, lambda: consume(observer), "unsupported_encoding")
        assert len(delegate.calls) == 1


@pytest.mark.parametrize("headers", [[], [("content-encoding", "identity")]])
def test_entity_exact_cap_passes_unchanged_and_over_chunk_is_not_forwarded(headers):
    api = load_subject()
    exact = b"{}" + b" " * (LIMIT - 2)
    with observed(api, [response(headers=headers, chunks=(exact,))]) as (observer, _):
        assert consume(observer) == exact
        assert observer.snapshot()["attempts"][0]["entity_bytes"] == LIMIT
    first, offending = b"{" * (LIMIT - 1), b"xx"
    with observed(api, [response(headers=headers, chunks=(first, offending))]) as (observer, delegate):
        result = observer.handle_request(request())
        stream = iter(result.stream)
        try:
            assert next(stream) == first
            expect_stop(api, lambda: next(stream), "response_too_large")
        finally:
            result.close()
        state = observer.snapshot()
        assert state["attempts"][0]["entity_bytes"] == LIMIT - 1
        assert state["attempts"][0]["body_complete"] is False
        expect_stop(api, lambda: consume(observer), "response_too_large")
        assert len(delegate.calls) == 1


@pytest.mark.parametrize("field", ["prompt_eval_count", "eval_count"])
@pytest.mark.parametrize("value,state", [
    (0, "reported"), (11, "reported"), (None, "null"),
    (True, "invalid"), (-1, "invalid"), ("11", "invalid"),
    ((1 << 4096) - 1, "reported"), (1 << 4096, "overflow"),
])
def test_usage_retains_zero_invalid_and_bounded_overflow(field, value, state):
    api = load_subject()
    with observed(api, [response(payload(**{field: value}))]) as (observer, _):
        consume(observer)
        snapshot = observer.snapshot()
        assert_snapshot(snapshot)
        assert snapshot["attempts"][0]["usage"][field] == {
            "state": state, "value": value if state == "reported" else None,
        }
        assert snapshot["stop_code"] is None  # Observation is not generation policy.


@pytest.mark.parametrize("kind,state", [("missing", "missing"), ("malformed", "unknown"),
                                       ("disconnect", "unknown")])
def test_usage_missing_and_interrupted_are_not_zero(kind, state):
    api = load_subject()
    item = response({"response": "\n".join(TEN), "done": True})
    if kind != "missing":
        item = response(chunks=(b'{"response":',), disconnect=kind == "disconnect")
    with observed(api, [item]) as (observer, delegate):
        if kind == "disconnect":
            expect_stop(api, lambda: consume(observer), "transport_error")
            expect_stop(api, lambda: consume(observer), "transport_error")
            assert len(delegate.calls) == 1
        else:
            consume(observer)
        fact = observer.snapshot()["attempts"][0]
        assert fact["usage"] == {key: {"state": state, "value": None}
                                  for key in ("prompt_eval_count", "eval_count")}


@pytest.mark.parametrize("value", [
    {"error": PRIVATE}, {"done": False, "response": PRIVATE},
    {"done": True, "response": 1}, {"done": True}, [PRIVATE],
])
def test_complete_protocol_failure_is_retained_without_forcing_new_retry_policy(value):
    api = load_subject()
    with observed(api, [response(value), response()]) as (observer, delegate):
        consume(observer)
        first = observer.snapshot()["attempts"][0]
        assert first["protocol"] == "protocol_error" and first["body_complete"] is True
        consume(observer)
        snapshot = observer.snapshot()
        assert snapshot["stop_code"] is None
        assert snapshot["attempts"][0] == first
        assert snapshot["attempts"][1]["protocol"] == "success"
        assert len(delegate.calls) == 2


@pytest.mark.parametrize("now,outer,allowed", [
    (179.999, 210.0, True), (180.0, 210.0, False),
    (99.999, 100.0, True), (100.0, 100.0, False),
])
def test_dispatch_deadline_uses_earlier_absolute_limit(now, outer, allowed):
    api, clock = load_subject(), Clock()
    clock.now = now
    with observed(api, [response()] if allowed else [], clock=clock,
                  outer_deadline=outer) as (observer, delegate):
        if allowed:
            consume(observer)
        else:
            expect_stop(api, lambda: consume(observer), "deadline")
        assert len(delegate.calls) == int(allowed)


@pytest.mark.parametrize("kind", ["deadline", "cancelled"])
def test_late_body_or_cancel_cannot_forward_or_resume(kind):
    api, clock, cancel = load_subject(), Clock(), threading.Event()

    def change(_):
        if kind == "deadline":
            clock.now = 180.0
        else:
            cancel.set()

    with observed(api, [response(before_chunk=change)], clock=clock, cancel=cancel) as (observer, delegate):
        expect_stop(api, lambda: consume(observer), kind)
        clock.now = 0.0
        cancel.clear()
        expect_stop(api, lambda: consume(observer), kind)
        assert len(delegate.calls) == 1
        assert observer.snapshot()["attempts"][0]["entity_bytes"] == 0


def test_cancel_before_dispatch_is_zero_attempt_and_sticky():
    api, cancel = load_subject(), threading.Event()
    cancel.set()
    with observed(api, [], cancel=cancel) as (observer, delegate):
        expect_stop(api, lambda: consume(observer), "cancelled")
        cancel.clear()
        expect_stop(api, lambda: consume(observer), "cancelled")
        assert not delegate.calls
        assert observer.snapshot()["counters"]["denied_before_delegate"] == 2


def test_snapshot_is_detached_and_contains_only_digest_of_prompt():
    api = load_subject()
    with observed(api, [response()]) as (observer, _):
        consume(observer)
        snapshot = observer.snapshot()
        assert_snapshot(snapshot)
        fact = snapshot["attempts"][0]
        assert fact["prompt_sha256"] == hashlib.sha256(PRIVATE.encode()).hexdigest()
        assert fact["prompt_bytes"] == len(PRIVATE.encode())
        assert PRIVATE not in json.dumps(snapshot)
        fact["usage"]["eval_count"]["value"] = 999
        snapshot["counters"]["delegate_entries"] = 0
        assert observer.snapshot()["attempts"][0]["usage"]["eval_count"]["value"] == 7
        assert observer.snapshot()["counters"]["delegate_entries"] == 1


@contextmanager
def real_call_records(production):
    # Observe code frames, never wrap strict methods or replace strategy/RDKit.
    from src.agent.tooling.adapters import ToolAdapter
    from src.agent.tooling.generation_ranking_contract import GenerationToolAdapter
    from src.agent.scientific_session.decision_execution import settle_owned_call
    from src.agent.runtime.worker_ownership import WorkerOwner
    watched = {
        production.generator.execute.__code__: "generator",
        production.generator._generate_with_retry.__code__: "rounds",
        production.generator._call_llm_sync.__code__: "helper",
        production.model.generate.__code__: "client",
        production.model.close.__code__: "close",
        GenerationToolAdapter.execute.__code__: "adapter",
        settle_owned_call.__code__: "settle",
        WorkerOwner.settle.__code__: "owner_settle",
    }
    calls = Counter()
    models = []
    owners = []
    bindings = []
    observer_errors = []

    def profile(frame, event, arg):
        try:
            if event != "call":
                return
            name = watched.get(frame.f_code)
            if name:
                calls[name] += 1
            # The original adapter entry is inherited LegacyPythonToolAdapter
            # execute(**kwargs). Only the base frame binds allow_retry locally.
            if frame.f_code is ToolAdapter.execute.__code__:
                adapter = frame.f_locals["self"]
                if isinstance(adapter, GenerationToolAdapter):
                    bindings.append((True, frame.f_locals["allow_retry"], adapter.spec))
            if name == "client":
                calls["strict_client"] += int(frame.f_locals["strict_errors"] is True)
                models.append(frame.f_locals["self"])
            if name == "owner_settle":
                owners.append(frame.f_locals["self"])
        except BaseException:
            # Never inject observer failures into the actual producer/adapter.
            # Latch only a fixed marker; assert after profiling and cleanup.
            observer_errors.append("observer_error")

    previous, previous_thread = sys.getprofile(), threading.getprofile()
    sys.setprofile(profile)
    threading.setprofile(profile)
    try:
        yield calls, models, owners, bindings
    finally:
        threading.setprofile(previous_thread)
        sys.setprofile(previous)
        # Rescue leaked clients after a failing assertion without turning a leak
        # into a passing test. Worker drain remains the subject's responsibility.
        try:
            for model in {id(m): m for m in models}.values():
                if not model.client.is_closed or not model.sync_client.is_closed:
                    asyncio.run(model.close())
        finally:
            assert not observer_errors, "observer_error"


def run(api, delegate, **kwargs):
    clock = kwargs.pop("clock", Clock())
    cancel = kwargs.pop("cancel", threading.Event())
    try:
        result = asyncio.run(api.run_canary(
            delegate=delegate, clock=clock, started_at=0.0, outer_deadline=210.0,
            cancelled=cancel.is_set, source_hashes={"canary": "0" * 64}, **kwargs,
        ))
        assert delegate.closed >= 1, "subject_did_not_close_delegate"
        delegate.assert_clean()
        return result
    finally:
        # These are finite in-memory fixture resources only. Check subject
        # ownership above BEFORE fallback cleanup, including on assertion failure.
        for body in delegate.bodies:
            if not body.closed:
                body.close()
        if not delegate.closed:
            try:
                delegate.close()
            except RuntimeError:
                pass


@pytest.mark.parametrize("kind,expected,rounds", [
    ("first", "complete10", 1), ("recover", "complete10", 2),
    ("partial", "partial", 5),
])
def test_real_chain_preserves_prompts_counts_and_original_recovery(tmp_path, kind, expected, rounds):
    api = load_subject()
    scripts = {
        "first": [ok(*TEN)],
        "recover": [("http-error", PRIVATE), ok(*TEN)],
        "partial": [ok(*TEN[:2])] * 5,
    }
    script = scripts[kind]
    with isolated_production_context(tmp_path) as production:
        with mocked_client(production, script) as (model, baseline):
            original = production.generator(model).execute({
                "query": "generate 10 molecules", "metadata": {"requested_count": 10},
            })
        responses = [response({"error": PRIVATE}, status=503) if item[0] == "http-error"
                     else response(payload(item[1])) for item in script]
        delegate = FiniteHTTP(responses)
        with real_call_records(production) as (calls, models, owners, bindings):
            report = run(api, delegate)
            assert models and all(m.client.is_closed and m.sync_client.is_closed for m in models)
            assert owners and all(o.status == "settled" and o.pending_roots == 0 for o in owners)
        assert calls["adapter"] == calls["generator"] == calls["rounds"] == 1
        assert calls["helper"] == calls["client"] == rounds
        assert calls["strict_client"] == rounds
        assert len(bindings) == 1
        actual_type, allow_retry, spec = bindings[0]
        assert actual_type and allow_retry is False
        assert spec.timeout_seconds == 180 and spec.retry_policy.max_attempts == 1
        assert calls["settle"] >= 1 and calls["owner_settle"] >= 1 and calls["close"] >= 1
        assert [json.loads(r.content) for r in delegate.calls] == [c["payload"] for c in baseline.calls]
        assert report["status"] == expected and report["adapter_entries"] == 1
        assert report["result"]["row_count"] == len(original["data"])
        assert report["result"]["valid_count"] == report["result"]["unique_count"] == len(original["data"])
        assert report["result"]["typed_success"] is True
        assert report["local_cleanup"] == "settled"
        assert report["transport_kind"] == "injected" and report["remote_state"] == "unverified"
        assert report["transport"]["counters"]["delegate_entries"] == rounds
        if kind == "recover":
            assert report["transport"]["attempts"][0]["protocol"] == "http_error"
            assert report["warnings"]["round_failure"] >= 1
        assert delegate.closed >= 1
        delegate.assert_clean()


@pytest.mark.parametrize("fault", ["encoding", "disconnect", "cancelled", "cleanup"])
def test_real_chain_never_upgrades_safety_or_cleanup_unknown(tmp_path, fault):
    api = load_subject()
    cancel = threading.Event()
    item = response()
    if fault == "encoding":
        item = response(headers=[("content-encoding", "gzip")])
    elif fault == "disconnect":
        item = response(disconnect=True)
    elif fault == "cancelled":
        item = response(before_chunk=lambda _: cancel.set())
    delegate = FiniteHTTP([item], close_error=fault == "cleanup")
    with isolated_production_context(tmp_path) as production:
        with real_call_records(production) as (_, models, owners, _):
            report = run(api, delegate, cancel=cancel)
            assert models and all(m.client.is_closed and m.sync_client.is_closed for m in models)
            assert owners and all(o.status == "settled" for o in owners)
    assert len(delegate.calls) == 1
    assert report["status"] == ("unresolved" if fault == "cleanup" else "stopped")
    assert report["local_cleanup"] == ("unresolved" if fault == "cleanup" else "settled")
    assert report["remote_state"] == "unverified"
    assert PRIVATE not in json.dumps(report)
    delegate.assert_clean()


def test_report_closed_bounded_redacted_and_not_token_authority(tmp_path, capsys):
    api = load_subject()
    with isolated_production_context(tmp_path) as production:
        with real_call_records(production) as (_, models, _, _):
            report = run(api, FiniteHTTP([response()]))
            assert models and all(m.client.is_closed and m.sync_client.is_closed for m in models)
    assert set(report) == {
        "status", "elapsed_ms", "source_hashes", "adapter_entries", "transport",
        "requested_count", "result", "warnings", "local_cleanup", "remote_state",
        "assembly_match", "tokenizer_identity", "enforcement", "fence_scope", "transport_kind",
    }
    assert report["requested_count"] == 10
    assert report["assembly_match"] == report["tokenizer_identity"] == report["enforcement"] == "unverified"
    assert report["fence_scope"] == "child_only_server_downstream_unverified"
    assert set(report["result"]) == {"row_count", "valid_count", "unique_count", "source_match_count", "typed_success",
                                     "typed_status", "error_code"}
    encoded = api.encode_report(report)
    assert type(encoded) is bytes and len(encoded) <= 64 * 1024
    assert json.loads(encoded) == report
    captured = capsys.readouterr()
    assert PRIVATE not in encoded.decode() + captured.out + captured.err
    assert "\n".join(TEN) not in encoded.decode()
    for mutate in (
        lambda r: r.update({"raw_body": PRIVATE}),
        lambda r: r["result"].update({"smiles": PRIVATE}),
        lambda r: r["result"].update({"source_match_count": 0}),
        lambda r: r["source_hashes"].update({PRIVATE: "0" * 64}),
        lambda r: r["warnings"].update({PRIVATE: 1}),
        lambda r: r.update({"enforcement": "verified"}),
        lambda r: r["transport"]["attempts"][0].update({"error_code": PRIVATE}),
        lambda r: r.update({"raw_body": PRIVATE * (64 * 1024)}),
    ):
        changed = json.loads(encoded)
        mutate(changed)
        with pytest.raises(ValueError, match="^Invalid canary report$") as caught:
            api.encode_report(changed)
        assert PRIVATE not in repr(caught.value)


def test_injected_transport_cannot_claim_local_provider():
    api = load_subject()
    delegate = FiniteHTTP([])
    with pytest.raises(ValueError, match="Invalid canary transport selection"):
        asyncio.run(api.run_canary(delegate=delegate, real=True, clock=Clock(),
                                   started_at=0.0, outer_deadline=210.0, cancelled=lambda: False,
                                   source_hashes={"canary": "0" * 64}))
    assert not delegate.calls


def test_result_projection_exposes_wrong_candidate_origin():
    api = load_subject()
    rows = [
        {"smiles": "CCO", "model": "gmm-llama:latest", "source": "llm"},
        {"smiles": "CCC", "model": "different-model", "source": "llm"},
        {"smiles": "CCCC", "model": "gmm-llama:latest", "source": "different-source"},
    ]
    result = SimpleNamespace(success=True, status=None, error=None, data=rows, warnings=[])
    chemistry = SimpleNamespace(MolFromSmiles=str, MolToSmiles=str)
    projection, _ = api._result_projection(result, chemistry)
    assert projection["valid_count"] == 3
    assert projection["source_match_count"] == 1


@pytest.mark.parametrize("last,expected", [
    ({}, "missing"), ({"model": "other-model"}, "mismatch"), ({"model": None}, "invalid"),
])
@pytest.mark.parametrize("valid_first", [False, True])
def test_direct_provider_identity_must_match_each_success(last, expected, valid_first):
    api = load_subject()
    matching = response(payload(model="gmm-llama:latest"))
    replies = ([matching] if valid_first else []) + [response(payload(**last))]
    with observed(api, replies, require_model=True) as (observer, delegate):
        if valid_first:
            consume(observer)
        expect_stop(api, lambda: consume(observer), "provider_model_unverified")
        expect_stop(api, lambda: consume(observer), "provider_model_unverified")
        attempts = observer.snapshot()["attempts"]
        assert len(delegate.calls) == len(replies)
        assert attempts[-1]["provider_model"] == expected
        assert attempts[-1]["protocol"] == "protocol_error"
        if valid_first:
            assert attempts[0]["provider_model"] == "match"


def test_local_mode_requires_the_fixed_real_transport_and_propagates_connect_failure(tmp_path, monkeypatch):
    api = load_subject()
    attempted = []

    def refuse(address, *args, **kwargs):
        attempted.append(address)
        raise ConnectionRefusedError()

    async def scenario():
        # The loop (including its Windows wakeup socketpair) already exists.
        # Intercept before OS connection creation. No server/model is involved.
        monkeypatch.setattr(socket.socket, "connect", lambda *args: pytest.fail("unexpected socket"))
        monkeypatch.setattr(socket, "create_connection", refuse)
        return await api.run_canary(real=True, clock=Clock(), started_at=0.0, outer_deadline=210.0,
                                    cancelled=lambda: False, source_hashes={"canary": "0" * 64})

    with isolated_production_context(tmp_path):
        report = asyncio.run(scenario())
    assert attempted == [("127.0.0.1", 11434)]
    assert report["transport_kind"] == "local_ollama"
    assert report["status"] == "stopped"
    assert report["remote_state"] == "uncertain"
    assert report["transport"]["stop_code"] == "transport_error"
    assert report["transport"]["counters"]["response_headers_seen"] == 0


def test_actual_task_cancel_stops_before_second_delegate_and_drains(tmp_path):
    """Cancel the actual campaign Task, not its external cancellation callback.

    Only the first finite HTTP body waits. No launcher or strategy/helper patch.
    The second response could complete ten, so later dispatch cannot hide behind
    fixture exhaustion. All five original round slots have finite responses.
    """
    api = load_subject()
    release = threading.Event()
    external_cancel = threading.Event()  # Deliberately stays false throughout.
    fixture_errors = []
    workers = []
    task_receipt = {}

    async def scenario():
        loop = asyncio.get_running_loop()
        entered = asyncio.Event()

        def first_body(_):
            workers.append(threading.current_thread())
            loop.call_soon_threadsafe(entered.set)
            if not release.wait(timeout=5.0):
                # Fail outside the producer, even if its broad except catches.
                fixture_errors.append("first_body_release_timeout")

        delegate = FiniteHTTP([
            response(payload("\n".join(TEN[:2])), before_chunk=first_body),
            *[response() for _ in range(4)],
        ])
        task_receipt["delegate"] = delegate
        task = asyncio.create_task(api.run_canary(
            delegate=delegate, clock=Clock(), started_at=0.0, outer_deadline=210.0,
            cancelled=external_cancel.is_set, source_hashes={"canary": "0" * 64},
        ))
        task_receipt["task"] = task
        try:
            await asyncio.wait_for(entered.wait(), timeout=5.0)
            assert len(delegate.calls) == 1 and not external_cancel.is_set()
            assert task.cancel() is True
            # Deliver cancellation while the real invocation is still blocked;
            # a single scheduler yield, not a sleep-based race or helper mock.
            await asyncio.sleep(0)
            assert not task.done(), "campaign_returned_before_worker_drain"
            release.set()
            return await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
        finally:
            # Always unblock before waiting, including failed preconditions.
            # wait_for(shield(...)) never injects a second cancellation into the
            # production task. The finite fixture cannot leave a blocked read.
            release.set()
            if not task.done():
                await asyncio.wait_for(asyncio.shield(task), timeout=10.0)
            else:
                # Retrieve a failure even if an earlier assertion preempted await.
                if not task.cancelled():
                    task.exception()

    with isolated_production_context(tmp_path) as production:
        with real_call_records(production) as (calls, models, owners, bindings):
            try:
                report = asyncio.run(scenario())  # Also joins default executor.
                delegate = task_receipt["delegate"]
                assert task_receipt["task"].done()
                assert not fixture_errors
                assert workers and all(not worker.is_alive() for worker in workers)
                assert models and all(m.client.is_closed and m.sync_client.is_closed for m in models)
                assert owners and all(o.status == "settled" and o.pending_roots == 0 for o in owners)
                assert delegate.closed >= 1
                delegate.assert_clean()
                assert calls["adapter"] == calls["generator"] == calls["rounds"] == 1
                assert len(bindings) == 1 and bindings[0][1] is False
                assert not external_cancel.is_set()
                assert report["status"] == "stopped"
                assert report["transport"]["stop_code"] == "cancelled"
                assert report["local_cleanup"] == "settled"
                assert len(delegate.calls) == 1, "task_cancel_admitted_later_delegate"
                assert report["transport"]["counters"]["delegate_entries"] == 1
            finally:
                release.set()
                # Rescue only after the assertions above; never fake cleanup
                # evidence. No OS resources exist in unused synthetic bodies.
                delegate = task_receipt.get("delegate")
                if delegate is not None:
                    for body in delegate.bodies:
                        if not body.closed:
                            body.close()
                    if not delegate.closed:
                        delegate.close()
