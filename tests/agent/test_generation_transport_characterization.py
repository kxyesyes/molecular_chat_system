"""Strict-generation regressions and shared offline client fixture contexts.

Real generator/helper/Ollama methods and RDKit; HTTP transports are replaced.
Separate fault tests instrument signature inspection and the executor/loop boundary.
The original 46-pass characterization/hash remains in the plan. Bad-behavior
assertions below now express the requested fix; unchanged positive controls stay.
No xfail, optional skip, helper replacement, token grant or production repair.
"""
import asyncio
import inspect
import json
import logging
import sys
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from types import SimpleNamespace

import httpx
import pytest


MODES = ("native-sync", "coroutine-no-loop", "coroutine-running-loop")
TEN = tuple("C" * length for length in range(3, 13))
QUERY = "generate 10 molecules"
TEMPERATURE = 0.23
BASE_URL = "http://generation-probe.invalid"
PROVIDER_MARKER = "probe-private-provider-marker"
QUERY_MARKER = "probe-private-query-marker"
BACKEND_MESSAGE = "Molecular generation request failed"
STRICT_MESSAGE = "Strict molecular generation unavailable"
ROUND_WARNING = "Some molecular generation rounds failed."
CLIENT_MESSAGE = "Ollama generation failed"


@contextmanager
def isolated_production_context(tmp_path):
    # Ollama imports the legacy terminal logger, which may open a relative log.
    # Keep that import in temporary cwd and close only its newly added handlers.
    # Do not construct the app, call get_core_tools, or read host configuration.
    patch = pytest.MonkeyPatch()
    patch.chdir(tmp_path)
    logger = logging.getLogger("MolecularChat")
    previous = tuple(logger.handlers)
    level, propagate = logger.level, logger.propagate
    try:
        from rdkit import Chem  # Required real validation; absence is NOT a skip.
        from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
        from src.web.models import ollama_model
        from src.web.models import generate_for_chat
        from src.web.models.ollama_model import OllamaModel

        yield SimpleNamespace(generator=LLMMolecularGenerator, model=OllamaModel,
                              model_module=ollama_model, chem=Chem, chat=generate_for_chat)
    finally:
        try:
            with ExitStack() as cleanup:
                for handler in tuple(logger.handlers):
                    if handler not in previous:
                        logger.removeHandler(handler)
                        cleanup.callback(handler.close)
        finally:
            logger.handlers[:] = previous
            logger.setLevel(level)
            logger.propagate = propagate
            patch.undo()


@pytest.fixture
def production(tmp_path):
    with isolated_production_context(tmp_path) as value:
        yield value


class FiniteAsyncBody(httpx.AsyncByteStream):
    """Finite synthetic response bytes, with real HTTPX response cleanup."""

    def __init__(self, chunks, request, *, disconnect=False):
        self.chunks = tuple(chunks)
        self.request = request
        self.disconnect = disconnect
        self.close_calls = 0

    async def __aiter__(self):
        for chunk in self.chunks:
            yield chunk
        if self.disconnect:
            raise httpx.ReadError(PROVIDER_MARKER, request=self.request)

    async def aclose(self):
        self.close_calls += 1


class ScriptedHTTP:
    """Count actual mock transport entries, NOT helper intentions/real HTTP sends."""

    def __init__(self, script):
        self.script = tuple(script)
        self.calls = []
        self.responses = []
        self.streams = []
        self.on_response = None
        self.on_request = None

    def handle(self, request, channel):
        index = len(self.calls)
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            has_loop = False
        else:
            has_loop = True
        event = dict(method=request.method, url=str(request.url),
                     payload=json.loads(request.content), channel=channel,
                     thread=threading.get_ident(), has_loop=has_loop,
                     authorization=request.headers.get("authorization"),
                     outcome="unexpected-extra-dispatch")
        self.calls.append(event)
        if self.on_request is not None:
            self.on_request(event)
        if index >= len(self.script):
            # The legacy caller catches exceptions. The final call-count check
            # must still expose this, rather than relying on this raise alone.
            raise AssertionError("probe script exhausted")
        kind, text = self.script[index]
        if kind == "disconnect":
            event["outcome"] = "read-error"
            raise httpx.ReadError(PROVIDER_MARKER, request=request)
        if kind == "type-error":
            event["outcome"] = "type-error"
            raise TypeError(PROVIDER_MARKER)
        if kind == "runtime-error":
            event["outcome"] = "runtime-error"
            raise RuntimeError(PROVIDER_MARKER)
        status = 503 if kind in {"http-error", "stream-http-error"} else 200
        event["outcome"] = "http-503" if status == 503 else "http-200"
        # Deliberately synthetic usage fields: the current text API discards them.
        body = ({"error": PROVIDER_MARKER} if status == 503 else
                {"response": text, "done": True,
                 "prompt_eval_count": 11, "eval_count": 7})
        if kind == "json":
            body = text
        if kind in {"stream-ok", "stream-http-error", "stream-disconnect"}:
            stream = FiniteAsyncBody(text, request, disconnect=kind == "stream-disconnect")
            self.streams.append(stream)
            response = httpx.Response(status, stream=stream, request=request)
        else:
            response = (httpx.Response(200, content=PROVIDER_MARKER, request=request)
                        if kind == "malformed-json" else
                        httpx.Response(status, json=body, request=request))
        self.responses.append(response)
        if self.on_response is not None:
            # A test may change the injected binding while this first real
            # request is completing, never the in-flight helper/client method.
            self.on_response(event)
        return response


def ok(*smiles):
    return ("ok", "\n".join(smiles))


@contextmanager
def mocked_client(production, script):
    transport = ScriptedHTTP(script)
    # Never invoke default assembly, constructor-created transports or discovery.
    model = object.__new__(production.model)
    model.base_url, model.model_name = BASE_URL, "gmm-llama:latest"
    with httpx.Client(transport=httpx.MockTransport(
            lambda request: transport.handle(request, "sync")), trust_env=False) as sync:
        model.sync_client = sync
        model.client = httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: transport.handle(request, "async")), trust_env=False)
        try:
            yield model, transport
        finally:
            # Real close(), including its sync-client finally clause. Even a
            # failing assertion closes both clients; the outer with is a backup.
            asyncio.run(model.close())
    assert model.client.is_closed and model.sync_client.is_closed
    assert all(response.is_closed for response in transport.responses)
    assert all(stream.close_calls == 1 for stream in transport.streams)


@contextmanager
def recorded_ollama_context(production, response, *, max_calls=5, model_name="gmm-llama:latest"):
    """Real bound methods; finite synthetic HTTP text and passive call records."""
    with mocked_client(production, (("ok", response),) * max_calls) as (model, transport):
        model.model_name = model_name
        model.prompts = []
        model.calls = []
        model.temperatures = model.calls
        model.transport_calls = transport.calls

        def record(event):
            model.prompts.append(event["payload"]["prompt"])
            model.calls.append(event["payload"]["options"]["temperature"])

        transport.on_request = record
        try:
            assert model.generate.__func__ is production.model.generate
            assert model.generate_async.__func__ is production.model.generate_async
            yield model
        finally:
            # A swallowed script-exhaustion error must not turn a fixture into a
            # passing partial result. Cleanup still runs in the outer context.
            assert len(transport.calls) <= max_calls, "unexpected offline dispatch"


@pytest.fixture
def offline_models(tmp_path):
    with isolated_production_context(tmp_path) as value, ExitStack() as stack:
        def make(response="CCO", *, max_calls=5, model_name="gmm-llama:latest"):
            return stack.enter_context(recorded_ollama_context(
                value, response, max_calls=max_calls, model_name=model_name))
        yield make


def run_probe(production, mode, script, *, query=QUERY, count=10, invocations=1,
              binding_factory=None, entry="execute", capture_errors=False, configure=None):
    """Use a joined test worker so helper-owned event loops never leak to pytest."""
    def run():
        counts = Counter()
        errors = []
        invocation_spans = []
        worker_thread = threading.get_ident()
        with mocked_client(production, script) as (model, transport):
            try:
                assert model.generate.__func__ is production.model.generate
                assert model.generate_async.__func__ is production.model.generate_async
                if mode == "native-sync":
                    binding = model
                    assert not inspect.iscoroutinefunction(binding.generate)
                else:
                    # The existing helper accepts a coroutine named generate.
                    # This is an explicit injected binding of the UNMODIFIED
                    # real method, not the default Ollama assembly or a wrapper.
                    binding = SimpleNamespace(model_name=model.model_name,
                                              generate=model.generate_async)
                    assert binding.generate.__func__ is production.model.generate_async
                    assert inspect.iscoroutinefunction(binding.generate)
                if binding_factory is not None:
                    binding = binding_factory(binding)
                tool = production.generator(binding)
                if configure is not None:
                    configure(tool, binding, transport)
                watched = {getattr(production.generator, name).__code__: name for name in (
                    "execute", "_generate_with_retry", "_generate_with_llm",
                    "_optimize_with_llm", "_call_llm_sync")}

                def observe(frame, event, arg):
                    # Observe real call frames without mocking/replacing methods.
                    if event == "call" and frame.f_locals.get("self") is tool:
                        name = watched.get(frame.f_code)
                        if name is not None:
                            counts[name] += 1

                request = {"query": query,
                           "metadata": {"requested_count": count, "temperature": TEMPERATURE}}

                def execute():
                    results = []
                    for _ in range(invocations):
                        start = len(transport.calls)
                        try:
                            if entry == "execute":
                                result = tool.execute(request)
                            elif entry == "_call_llm_sync":
                                result = tool._call_llm_sync(QUERY, temperature=TEMPERATURE)
                            else:
                                assert entry in {"_generate_with_llm", "_optimize_with_llm"}
                                intent = dict(requirements=QUERY, count=10, temperature=TEMPERATURE,
                                              target_evidence=None, base_smiles="CCO", objectives=[])
                                result = getattr(tool, entry)(intent)
                        except Exception as exc:
                            if not capture_errors:
                                raise
                            errors.append(exc)
                        else:
                            results.append(result)
                        finally:
                            invocation_spans.append((start, len(transport.calls)))
                    return results

                async def inside_running_loop():
                    # Intentionally exercise the helper's existing new-thread
                    # branch; this is not a recommendation for async Web code.
                    return execute()

                prior_profile = sys.getprofile()
                try:
                    sys.setprofile(observe)
                    results = (asyncio.run(inside_running_loop())
                               if mode == "coroutine-running-loop" else execute())
                finally:
                    sys.setprofile(prior_profile)
            finally:
                asyncio.set_event_loop(None)
        return SimpleNamespace(results=results, calls=transport.calls, counts=counts,
                               worker_thread=worker_thread, tool=tool, errors=errors,
                               invocation_spans=invocation_spans)

    # .result() plus executor exit joins the test worker even on assertion/error.
    # All scripted transports finish immediately; no test timeout hides a worker.
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="generation-probe") as executor:
        return executor.submit(run).result()


def assert_attempts(probe, mode, script, *, optimizing=False, invocations=1):
    attempts = len(script)
    assert probe.errors == []
    assert len(probe.calls) == attempts
    assert probe.counts == Counter({"execute": invocations,
        "_generate_with_retry": invocations, "_call_llm_sync": attempts,
        "_optimize_with_llm" if optimizing else "_generate_with_llm": attempts})
    expected_outcomes = {"http-error": "http-503", "disconnect": "read-error",
                         "type-error": "type-error", "runtime-error": "runtime-error"}
    for event, (kind, _) in zip(probe.calls, script):
        assert event["method"] == "POST"
        assert event["url"] == BASE_URL + "/api/generate"
        assert event["authorization"] is None
        assert event["outcome"] == expected_outcomes.get(kind, "http-200")
        assert event["channel"] == ("sync" if mode == "native-sync" else "async")
        assert event["has_loop"] is (mode != "native-sync")
        assert (event["thread"] == probe.worker_thread) is (mode != "coroutine-running-loop")
        payload = event["payload"]
        assert set(payload) == {"model", "prompt", "stream", "options"}
        assert payload["model"] == "gmm-llama:latest"
        assert payload["stream"] is False
        assert payload["options"] == {"temperature": TEMPERATURE, "num_predict": 1000}
        if optimizing:
            expected_prompt = probe.tool.optimization_prompt_template.format(
                smiles="CCO", objectives="improve drug-likeness", count=10)
        else:
            expected_prompt = probe.tool.generation_prompt_template.format(
                requirements=QUERY, count=10, target_evidence="[]")
        # Every replenishment retains requested10, not a one-call/count1 substitute.
        assert payload["prompt"] == expected_prompt


def assert_raw_result(production, result, expected, *, failed_rounds=False):
    assert "status" not in result  # Raw producer has success/quality, not typed status.
    assert PROVIDER_MARKER not in repr(result)
    if not expected:
        assert result["success"] is False
        assert result["data"] is None
        assert "quality" not in result
        if failed_rounds:
            assert result["message"] == BACKEND_MESSAGE
            assert result["error"] == {"code": "provider_error", "message": BACKEND_MESSAGE,
                                       "details": {"reason": "generation_backend_failed"}}
        else:
            assert "error" not in result
            assert result["message"]
        return
    assert "error" not in result
    assert result["success"] is True
    actual = tuple(row["smiles"] for row in result["data"])
    assert actual == expected
    assert len(set(actual)) == len(actual)
    for smiles in actual:
        molecule = production.chem.MolFromSmiles(smiles)
        assert molecule is not None
        assert production.chem.MolToSmiles(molecule) == smiles
    assert result["quality"] == {"model": "gmm-llama:latest", "requested_count": 10,
                                 "actual_count": len(expected), "partial_generation": len(expected) < 10}
    assert bool(result.get("warnings")) is (len(expected) < 10 or failed_rounds)
    assert (ROUND_WARNING in result.get("warnings", [])) is failed_rounds
    # The fixed warning is not a durable receipt or a numeric usage claim.


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("script,expected", [
    pytest.param((ok(*TEN),), TEN, id="first-round-ten"),
    pytest.param((ok(*TEN[:4]), ok(*TEN[4:7]), ok(*TEN[7:])), TEN, id="three-round-ten"),
    pytest.param(tuple(ok(*TEN[i:i + 2]) for i in range(0, 10, 2)), TEN, id="fifth-round-ten"),
    pytest.param((ok("CCO", "OCC", "C1CC"),) * 5, ("CCO",), id="five-round-duplicate-invalid-shortfall"),
    pytest.param((ok("C1CC"),) * 5, (), id="five-round-invalid"),
    pytest.param((ok(),) * 5, (), id="five-round-empty"),
    pytest.param((("http-error", ""),) * 5, (), id="five-http-errors-no-candidates"),
    pytest.param((("disconnect", ""),) * 5, (), id="five-disconnects-no-candidates"),
    pytest.param((("http-error", ""), ok(*TEN)), TEN, id="http-error-then-clean-ten"),
    pytest.param((("disconnect", ""), ok(*TEN)), TEN, id="disconnect-then-clean-ten"),
    pytest.param((ok(*TEN[:3]), ("http-error", ""), ("disconnect", ""),
                  ok("CCC", "C1CC"), ok()), TEN[:3], id="mixed-five-round-shortfall"),
    pytest.param((ok("I", *TEN[:9]),), ("I", *TEN[:9]), id="genuine-iodine-is-allowed"),
    pytest.param((("http-error", ""), ok(*TEN[:2]), ok(*TEN[2:5]),
                  ok(*TEN[5:7]), ok(*TEN[7:])), TEN, id="failure-then-fifth-round-ten"),
])
def test_real_generation_chain_counts_strict_transport_attempts(production, mode, script, expected):
    probe = run_probe(production, mode, script)
    assert_attempts(probe, mode, script)
    assert_raw_result(production, probe.results[0], expected,
                      failed_rounds=any(kind != "ok" for kind, _ in script))


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("failure", ["http-error", "disconnect"])
def test_optimization_transport_failure_never_adds_seed_or_apology(production, mode, failure):
    script = ((failure, ""),) * 5
    probe = run_probe(production, mode, script, query="optimize 10 molecules; SMILES: CCO")
    assert_attempts(probe, mode, script, optimizing=True)
    assert_raw_result(production, probe.results[0], (), failed_rounds=True)


@pytest.mark.parametrize("mode", MODES)
def test_current_repeated_producer_entry_has_no_durable_logical_slot(production, mode):
    script = (ok(*TEN), ok(*TEN))
    probe = run_probe(production, mode, script, invocations=2)
    assert_attempts(probe, mode, script, invocations=2)
    for result in probe.results:
        assert_raw_result(production, result, TEN)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("optimizing", [False, True])
@pytest.mark.parametrize("first_script,first_expected", [
    pytest.param((("http-error", ""),) * 5, (), id="failed-then-success"),
    pytest.param((("disconnect", ""), ok(*TEN)), TEN, id="recovered-full-then-clean-success"),
    pytest.param((ok(*TEN[:3]), ("http-error", ""), ok(), ok(), ok()), TEN[:3],
                 id="mixed-partial-then-clean-success"),
])
def test_same_generator_failure_state_does_not_leak_into_next_success(
        production, mode, optimizing, first_script, first_expected):
    # One tool/model instance, two execute entries. Disjoint second-round output
    # detects stale candidates as well as leaked errors/partial/round-warning flags.
    second = tuple("C" * length for length in range(13, 23))
    script = first_script + (ok(*second),)
    query = "optimize 10 molecules; SMILES: CCO" if optimizing else QUERY
    probe = run_probe(production, mode, script, query=query, invocations=2)
    assert_attempts(probe, mode, script, optimizing=optimizing, invocations=2)
    split = len(first_script)
    assert probe.invocation_spans == [(0, split), (split, split + 1)]
    assert len(probe.results) == 2 and probe.results[0] is not probe.results[1]
    if optimizing:
        first_expected = ("CCO", *first_expected[:9]) if first_expected else ()
        second = ("CCO", *second[:9])
    assert_raw_result(production, probe.results[0], first_expected, failed_rounds=True)
    assert_raw_result(production, probe.results[1], second, failed_rounds=False)


@pytest.mark.parametrize("mode", MODES)
def test_invalid_count_makes_zero_helper_or_transport_calls(production, mode):
    probe = run_probe(production, mode, (), count=11)
    assert probe.calls == []
    assert probe.counts == Counter(execute=1)
    result = probe.results[0]
    assert result["success"] is False
    assert result["error"]["code"] == "invalid_input"


def test_client_strict_error_seam_is_explicit_opt_in_not_invocation_control(production):
    for name in ("generate", "generate_async"):
        signature = inspect.signature(getattr(production.model, name))
        assert tuple(signature.parameters) == ("self", "prompt", "temperature", "max_tokens", "strict_errors")
        assert signature.parameters["max_tokens"].default == 1500
        assert signature.parameters["strict_errors"].kind is inspect.Parameter.KEYWORD_ONLY
        assert signature.parameters["strict_errors"].default is False
    assert inspect.iscoroutinefunction(production.model.generate_async)
    assert not inspect.iscoroutinefunction(production.model.generate)


BAD_RESPONSES = [
    pytest.param(("http-error", ""), "http_error", id="http503"),
    pytest.param(("disconnect", ""), "transport_error", id="read-error"),
    pytest.param(("type-error", ""), "client_error", id="type-error-no-keyword-retry"),
    pytest.param(("runtime-error", ""), "client_error", id="runtime-error-no-loop-fallback"),
    pytest.param(("malformed-json", ""), "invalid_response", id="malformed-json"),
    pytest.param(("json", ["I", PROVIDER_MARKER]), "invalid_response", id="not-an-object"),
    pytest.param(("json", {"response": "I", "done": True, "error": PROVIDER_MARKER}),
                 "invalid_response", id="http200-error"),
    pytest.param(("json", {"response": "I", "done": True, "error": None}),
                 "invalid_response", id="http200-null-error-key"),
    pytest.param(("json", {"done": True}), "invalid_response", id="missing-response"),
    pytest.param(("json", {"response": None, "done": True}), "invalid_response", id="null-response"),
    pytest.param(("json", {"response": ["I", PROVIDER_MARKER], "done": True}),
                 "invalid_response", id="list-response"),
    pytest.param(("json", {"response": True, "done": True}), "invalid_response", id="bool-response"),
    pytest.param(("json", {"response": 1, "done": True}), "invalid_response", id="number-response"),
    pytest.param(("json", {"response": "I"}), "invalid_response", id="missing-done"),
    pytest.param(("json", {"response": "I", "done": False}), "invalid_response", id="unfinished"),
    pytest.param(("json", {"response": "I", "done": 1}), "invalid_response", id="done-not-bool"),
]


def strict_error_type(production):
    error_type = getattr(production.model_module, "OllamaGenerationError", None)
    assert isinstance(error_type, type), "missing strict OllamaGenerationError API"
    assert issubclass(error_type, Exception)
    assert not issubclass(error_type, RuntimeError), "do not enter helper's RuntimeError loop fallback"
    return error_type


def call_client(model, method_name, **kwargs):
    result = getattr(model, method_name)(QUERY, temperature=TEMPERATURE, max_tokens=1000, **kwargs)
    return asyncio.run(result) if inspect.isawaitable(result) else result


@pytest.mark.parametrize("method_name", ["generate", "generate_async"])
@pytest.mark.parametrize("step,reason", BAD_RESPONSES)
def test_strict_client_raises_fixed_error_instead_of_text(production, method_name, step, reason, caplog):
    error_type = strict_error_type(production)
    caplog.set_level(logging.DEBUG, logger="src.web.models.ollama_model")
    with mocked_client(production, (step,)) as (model, transport):
        with pytest.raises(error_type) as caught:
            call_client(model, method_name, strict_errors=True)
        assert len(transport.calls) == 1
        assert caught.value.args == (CLIENT_MESSAGE,)
        assert caught.value.reason == reason
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert not hasattr(caught.value, "request") and not hasattr(caught.value, "response")
        assert PROVIDER_MARKER not in repr(vars(caught.value))
    relevant = [r for r in caplog.records if r.name == "src.web.models.ollama_model"]
    assert relevant
    assert all(r.getMessage() == "Ollama strict generation failed" and not r.exc_info
               and not r.stack_info and not r.args for r in relevant)


LEGACY_CHAT_CASES = [
    pytest.param(("http-error", ""),
                 "I apologize, but I'm having trouble generating a response right now.", id="http"),
    pytest.param(("disconnect", ""),
                 "I apologize, but I'm experiencing technical difficulties.", id="disconnect"),
    pytest.param(ok("I"), "I", id="valid-iodine"),
    pytest.param(("malformed-json", ""),
                 "I apologize, but I'm experiencing technical difficulties.", id="legacy-malformed"),
    pytest.param(("json", {"error": PROVIDER_MARKER}), "", id="legacy-error-envelope"),
    pytest.param(("json", {"response": ["I"], "done": True}), ["I"], id="legacy-nonstring"),
    pytest.param(("json", {"response": "I", "done": False}), "I", id="legacy-unfinished"),
]


@pytest.mark.parametrize("method_name", ["generate", "generate_async"])
@pytest.mark.parametrize("step,expected", LEGACY_CHAT_CASES)
@pytest.mark.parametrize("options", [{}, {"strict_errors": False}], ids=["omitted", "false"])
def test_default_chat_keeps_legacy_text_behavior(production, method_name, step, expected, options):
    with mocked_client(production, (step,)) as (model, transport):
        assert call_client(model, method_name, **options) == expected
        assert len(transport.calls) == 1


CHAT_POSITIONALS = [
    pytest.param((QUERY,), 0.7, 1500, id="defaults"),
    pytest.param((QUERY, TEMPERATURE), TEMPERATURE, 1500, id="positional-temperature"),
    pytest.param((QUERY, TEMPERATURE, 1000), TEMPERATURE, 1000, id="all-positionals"),
]


def assert_chat_http(transport, channel, temperature, max_tokens, *, streaming=False):
    assert len(transport.calls) == 1
    event = transport.calls[0]
    assert event["method"] == "POST" and event["url"] == BASE_URL + "/api/generate"
    assert event["authorization"] is None
    assert event["channel"] == channel
    assert event["has_loop"] is (channel == "async")
    assert event["payload"] == {"model": "gmm-llama:latest", "prompt": QUERY,
        "stream": streaming, "options": {"temperature": temperature, "num_predict": max_tokens}}


@pytest.mark.parametrize("method_name", ["generate", "generate_async"])
@pytest.mark.parametrize("args,temperature,max_tokens", CHAT_POSITIONALS)
@pytest.mark.parametrize("step,expected", LEGACY_CHAT_CASES)
def test_default_chat_positionals_keep_payload_and_legacy_result(
        production, method_name, args, temperature, max_tokens, step, expected):
    with mocked_client(production, (step,)) as (model, transport):
        result = getattr(model, method_name)(*args)
        if inspect.isawaitable(result):
            result = asyncio.run(result)
        assert result == expected
        assert_chat_http(transport, "sync" if method_name == "generate" else "async",
                         temperature, max_tokens)


@pytest.mark.parametrize("method_name", ["generate", "generate_async"])
def test_chat_cannot_pass_strict_flag_positionally(production, method_name):
    with mocked_client(production, ()) as (model, transport):
        with pytest.raises(TypeError):
            getattr(model, method_name)(QUERY, TEMPERATURE, 1000, True)
        assert transport.calls == []


@pytest.mark.parametrize("binding_mode", ["canonical", "coroutine-generate", "sync-generate"])
@pytest.mark.parametrize("args,temperature,max_tokens", CHAT_POSITIONALS)
@pytest.mark.parametrize("step,expected", LEGACY_CHAT_CASES)
def test_real_generate_for_chat_preserves_legacy_routing_and_stringification(
        production, binding_mode, args, temperature, max_tokens, step, expected):
    caller_thread = threading.get_ident()
    with mocked_client(production, (step,)) as (model, transport):
        # No adapter/client wrapper: fallback bindings expose the same real bound
        # methods. The canonical model has BOTH APIs and must prefer the async one.
        binding = (model if binding_mode == "canonical" else SimpleNamespace(
            generate=model.generate_async if binding_mode == "coroutine-generate" else model.generate))
        result = asyncio.run(production.chat(binding, *args))
        assert result == str(expected)
        synchronous = binding_mode == "sync-generate"
        assert_chat_http(transport, "sync" if synchronous else "async", temperature, max_tokens)
        assert (transport.calls[0]["thread"] != caller_thread) is synchronous


@pytest.mark.parametrize("args,temperature,max_tokens", CHAT_POSITIONALS)
@pytest.mark.parametrize("step,expected", [
    pytest.param(("stream-ok", (b'{"response":"I"}\n{"res',
                                b'ponse":"CCO","done":true}\n')), ["I", "CCO"], id="split-ndjson"),
    pytest.param(("stream-ok", (b'not-json\n{"response":"I","done":true}\n',)),
                 ["I"], id="legacy-malformed-line-skipped"),
    pytest.param(("stream-ok", (b'{"response":"I"}',)), ["I"], id="legacy-tail-no-done"),
    pytest.param(("stream-http-error", (b'offline-http-error',)),
                 ["服务器错误 (HTTP 503): b'offline-http-error'"], id="legacy-http-error"),
    pytest.param(("stream-disconnect", (b'{"response":"I"}\n',)),
                 ["I", "技术错误: ReadError: " + PROVIDER_MARKER], id="legacy-read-disconnect"),
])
def test_real_stream_keeps_positional_payload_chunks_and_legacy_errors(
        production, args, temperature, max_tokens, step, expected):
    with mocked_client(production, (step,)) as (model, transport):
        async def consume():
            stream = model.stream_generate(*args)
            try:
                return [chunk async for chunk in stream]
            finally:
                await stream.aclose()

        assert asyncio.run(consume()) == expected
        assert_chat_http(transport, "async", temperature, max_tokens, streaming=True)
        assert len(transport.streams) == 1 and transport.streams[0].close_calls == 1
        assert all(response.is_closed for response in transport.responses)


def test_real_stream_consumer_close_closes_response_before_client(production):
    step = ("stream-ok", (b'{"response":"I"}\n', b'{"response":"CCO","done":true}\n'))
    with mocked_client(production, (step,)) as (model, transport):
        async def consume_one():
            stream = model.stream_generate(QUERY)
            try:
                assert await stream.__anext__() == "I"
                assert not transport.responses[0].is_closed
            finally:
                await stream.aclose()
            assert transport.responses[0].is_closed
            assert transport.streams[0].close_calls == 1
            assert not model.client.is_closed and not model.sync_client.is_closed

        asyncio.run(consume_one())
        assert_chat_http(transport, "async", 0.7, 1500, streaming=True)


def test_stream_signature_remains_legacy_without_strict_keyword(production):
    signature = inspect.signature(production.model.stream_generate)
    assert tuple(signature.parameters) == ("self", "prompt", "temperature", "max_tokens")
    assert signature.parameters["temperature"].default == 0.7
    assert signature.parameters["max_tokens"].default == 1500
    assert inspect.isasyncgenfunction(production.model.stream_generate)
    with mocked_client(production, ()) as (model, transport):
        with pytest.raises(TypeError):
            model.stream_generate(QUERY, strict_errors=True)
        assert transport.calls == []


@pytest.mark.parametrize("method_name", ["generate", "generate_async"])
@pytest.mark.parametrize("text", ["I", "", " \n"])
def test_strict_client_allows_real_string_content_without_chemistry_filter(production, method_name, text):
    with mocked_client(production, (("ok", text),)) as (model, transport):
        assert call_client(model, method_name, strict_errors=True) == text
        assert len(transport.calls) == 1


@pytest.mark.parametrize("method_name", ["generate", "generate_async"])
@pytest.mark.parametrize("value", [None, 0, 1, "true"])
def test_strict_flag_requires_native_bool_before_any_http(production, method_name, value):
    with mocked_client(production, ()) as (model, transport):
        with pytest.raises(ValueError, match="^invalid_strict_errors$"):
            call_client(model, method_name, strict_errors=value)
        assert transport.calls == []


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("optimizing", [False, True])
@pytest.mark.parametrize("step,reason", BAD_RESPONSES)
def test_rejected_responses_never_reach_candidates_or_seed(production, mode, optimizing, step, reason):
    script = (step,) * 5
    query = "optimize 10 molecules; SMILES: CCO" if optimizing else QUERY
    probe = run_probe(production, mode, script, query=query)
    assert_attempts(probe, mode, script, optimizing=optimizing)
    assert_raw_result(production, probe.results[0], (), failed_rounds=True)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("entry", ["_call_llm_sync", "_generate_with_llm", "_optimize_with_llm"])
def test_strict_exception_crosses_each_broad_helper_except_unchanged(production, mode, entry):
    error_type = strict_error_type(production)
    probe = run_probe(production, mode, (("http-error", ""),), entry=entry, capture_errors=True)
    assert probe.results == []
    assert len(probe.errors) == 1 and type(probe.errors[0]) is error_type
    assert probe.errors[0].reason == "http_error"
    assert str(probe.errors[0]) == CLIENT_MESSAGE
    assert len(probe.calls) == 1
    assert probe.counts["_call_llm_sync"] == 1
    assert probe.counts["_generate_with_retry"] == 0


@pytest.mark.parametrize("site", ["submit-before", "result-after"])
@pytest.mark.parametrize("failure_type", [RuntimeError, TypeError])
def test_running_loop_executor_failure_never_redispatches(production, monkeypatch, site, failure_type):
    error_type = strict_error_type(production)
    original_submit = ThreadPoolExecutor.submit
    original_new_loop = asyncio.new_event_loop
    faults = []
    loop_threads = []

    def observe_new_loop():
        # Delegate to the real loop factory. HTTP count alone would miss a bad
        # fallback that creates a coroutine but fails before awaiting/dispatching.
        loop_threads.append(threading.get_ident())
        return original_new_loop()

    def failing_submit(executor, function, *args, **kwargs):
        # Leave the test's own worker and ALL real helper/client implementations
        # intact. Only the nested executor boundary is fault-injected.
        if getattr(function, "__func__", None) is not production.generator._run_async_in_new_loop:
            return original_submit(executor, function, *args, **kwargs)
        if site == "submit-before":
            faults.append(site)
            raise failure_type(PROVIDER_MARKER)
        future = original_submit(executor, function, *args, **kwargs)

        class FailedResult:
            def result(self, timeout=None):
                # The actual coroutine/helper/HTTP request has finished once.
                # Losing its result is not permission to execute another request.
                future.result(timeout=timeout)
                faults.append(site)
                raise failure_type(PROVIDER_MARKER)

        return FailedResult()

    monkeypatch.setattr(ThreadPoolExecutor, "submit", failing_submit)
    monkeypatch.setattr(asyncio, "new_event_loop", observe_new_loop)
    probe = run_probe(production, "coroutine-running-loop", (ok("I"),),
                      entry="_call_llm_sync", capture_errors=True)
    assert faults == [site]
    assert probe.results == []
    assert len(probe.errors) == 1 and type(probe.errors[0]) is error_type
    assert probe.errors[0].reason == "client_error"
    assert str(probe.errors[0]) == CLIENT_MESSAGE
    assert probe.errors[0].__cause__ is None and probe.errors[0].__context__ is None
    assert len(probe.calls) == (0 if site == "submit-before" else 1)
    assert len(loop_threads) == (0 if site == "submit-before" else 1)
    assert probe.worker_thread not in loop_threads
    assert probe.counts == Counter(_call_llm_sync=1)
    for event in probe.calls:
        assert event["channel"] == "async" and event["has_loop"]
        assert event["thread"] != probe.worker_thread


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("text", ["", " \n", "C1CC"])
def test_no_valid_response_candidate_does_not_manufacture_optimization_seed(production, mode, text):
    script = (("ok", text),) * 5
    probe = run_probe(production, mode, script, query="optimize 10 molecules; SMILES: CCO")
    assert_attempts(probe, mode, script, optimizing=True)
    assert_raw_result(production, probe.results[0], ())


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("script,expected,failed_rounds", [
    pytest.param((ok(*TEN),), ("CCO", *TEN[:9]), False, id="successful-seed-preserved"),
    pytest.param((("http-error", ""), ok(*TEN)), ("CCO", *TEN[:9]), True,
                 id="failed-round-no-seed-then-real-success"),
    pytest.param((ok("I"),) * 5, ("CCO", "I"), False, id="valid-iodine-partial"),
    pytest.param((ok(*TEN[:2]), ok(*TEN[2:4]), ok(*TEN[4:6]), ok(*TEN[6:8]),
                  ok(*TEN[8:])), ("CCO", *TEN[:9]), False, id="fifth-round-optimization-ten"),
])
def test_optimization_positive_controls_preserve_seed_only_after_valid_output(
        production, mode, script, expected, failed_rounds):
    probe = run_probe(production, mode, script, query="optimize 10 molecules; SMILES: CCO")
    assert_attempts(probe, mode, script, optimizing=True)
    assert_raw_result(production, probe.results[0], expected, failed_rounds=failed_rounds)


def assert_unavailable_result(result):
    assert result["success"] is False and result["data"] is None
    assert "quality" not in result
    assert result["formatted"] == ""
    assert result["message"] == STRICT_MESSAGE
    assert result["error"] == {"code": "tool_unavailable", "message": STRICT_MESSAGE,
                               "details": {"reason": "generation_strict_unavailable"}}


def assert_strict_unavailable(probe):
    assert probe.calls == [] and probe.errors == []
    assert probe.counts == Counter(execute=1)
    assert_unavailable_result(probe.results[0])


class MutableGenerationBinding:
    """Expose the original bound method, recording reads without wrapping it."""

    def __init__(self, binding):
        self.model_name = binding.model_name
        self.method = binding.generate
        self.read_threads = []

    @property
    def generate(self):
        self.read_threads.append(threading.get_ident())
        return self.method


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("optimizing", [False, True])
@pytest.mark.parametrize("loss", ["missing", "unverified"])
def test_capability_loss_between_rounds_discards_valid_partial_batch(
        production, mode, optimizing, loss):
    unverified_calls = []
    mutations = []

    def unverified(prompt, temperature=0.7, max_tokens=1000, *, strict_errors=False):
        unverified_calls.append(prompt)
        return "\n".join(TEN)

    def configure(tool, binding, transport):
        def lose_capability(event):
            mutations.append(event["outcome"])
            binding.method = None if loss == "missing" else unverified
        transport.on_response = lose_capability

    query = "optimize 10 molecules; SMILES: CCO" if optimizing else QUERY
    probe = run_probe(production, mode, (ok("CCN"),), query=query,
                      binding_factory=MutableGenerationBinding, configure=configure)
    assert mutations == ["http-200"] and unverified_calls == []
    assert probe.errors == [] and len(probe.results) == 1
    assert_unavailable_result(probe.results[0])
    assert not probe.results[0].get("warnings")
    # Valid CCN (plus a successful optimization's CCO seed) was available after
    # round one. Round two must reject capability, not return that partial success
    # or execute the new function. Real private helpers are still profiled.
    assert probe.counts == Counter({"execute": 1, "_generate_with_retry": 1,
        "_call_llm_sync": 2, "_optimize_with_llm" if optimizing else "_generate_with_llm": 2})
    assert probe.invocation_spans == [(0, 1)]
    assert len(probe.calls) == 1
    event = probe.calls[0]
    assert event["channel"] == ("sync" if mode == "native-sync" else "async")
    assert event["has_loop"] is (mode != "native-sync")
    assert (event["thread"] == probe.worker_thread) is (mode != "coroutine-running-loop")
    assert event["payload"]["options"] == {"temperature": TEMPERATURE, "num_predict": 1000}
    template = (probe.tool.optimization_prompt_template.format(
        smiles="CCO", objectives="improve drug-likeness", count=10) if optimizing else
        probe.tool.generation_prompt_template.format(requirements=QUERY, count=10, target_evidence="[]"))
    assert event["payload"]["prompt"] == template


@pytest.mark.parametrize("step", [ok("I"), ("http-error", "")], ids=["success", "strict-failure"])
def test_running_loop_bridge_uses_captured_method_without_rereading_binding(production, monkeypatch, step):
    original_submit = ThreadPoolExecutor.submit
    holder = {}
    replacements = []
    unverified_calls = []

    async def unverified(prompt, temperature=0.7, max_tokens=1000, *, strict_errors=False):
        unverified_calls.append(prompt)
        return "private-unverified-output"

    def configure(tool, binding, transport):
        holder["binding"] = binding

    def swap_before_child_starts(executor, function, *args, **kwargs):
        if getattr(function, "__func__", None) is production.generator._run_async_in_new_loop:
            # submit is reached on the real running-loop worker AFTER preflight/
            # capture. Deterministically swap the input binding before launching
            # the real child; no timing race, helper/client replacement or sleeps.
            assert asyncio.get_running_loop().is_running()
            replacements.append(threading.get_ident())
            holder["binding"].method = unverified
        return original_submit(executor, function, *args, **kwargs)

    monkeypatch.setattr(ThreadPoolExecutor, "submit", swap_before_child_starts)
    probe = run_probe(production, "coroutine-running-loop", (step,),
                      binding_factory=MutableGenerationBinding, configure=configure,
                      entry="_call_llm_sync", capture_errors=True)
    assert replacements == [probe.worker_thread]
    assert unverified_calls == []
    if step[0] == "ok":
        assert probe.errors == [] and probe.results == ["I"]
    else:
        assert probe.results == [] and len(probe.errors) == 1
        assert type(probe.errors[0]) is strict_error_type(production)
        assert probe.errors[0].reason == "http_error"
        assert str(probe.errors[0]) == CLIENT_MESSAGE
        assert probe.errors[0].__cause__ is None and probe.errors[0].__context__ is None
    assert holder["binding"].read_threads
    assert set(holder["binding"].read_threads) == {probe.worker_thread}
    assert probe.counts == Counter(_call_llm_sync=1)
    assert probe.invocation_spans == [(0, 1)] and len(probe.calls) == 1
    event = probe.calls[0]
    assert event["channel"] == "async" and event["has_loop"]
    assert event["thread"] != probe.worker_thread
    assert event["payload"] == {"model": "gmm-llama:latest", "prompt": QUERY,
        "stream": False, "options": {"temperature": TEMPERATURE, "num_predict": 1000}}


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("kind", ["no-flag", "kwargs-only", "advertised-flag", "coroutine-flag"])
def test_unknown_binding_fails_closed_even_if_it_advertises_strict(production, mode, kind):
    calls = []

    def no_flag(prompt, temperature=0.7, max_tokens=1000):
        calls.append("no-flag")
        return "\n".join(TEN)

    def kwargs_only(prompt, **kwargs):
        calls.append("kwargs-only")
        raise TypeError(PROVIDER_MARKER)

    def advertised(prompt, temperature=0.7, max_tokens=1000, *, strict_errors=False):
        calls.append("advertised")
        return "I"

    async def coroutine(prompt, temperature=0.7, max_tokens=1000, *, strict_errors=False):
        calls.append("coroutine")
        return "I"

    methods = {"no-flag": no_flag, "kwargs-only": kwargs_only,
               "advertised-flag": advertised, "coroutine-flag": coroutine}
    probe = run_probe(production, mode, (), binding_factory=lambda _: SimpleNamespace(
        model_name="gmm-llama:latest", generate=methods[kind], strict_errors_supported=True))
    assert calls == []  # No speculative call, capability flag trust, or TypeError fallback.
    assert_strict_unavailable(probe)


@pytest.mark.parametrize("mode", MODES)
@pytest.mark.parametrize("fault", ["missing", "positional", "required", "kwargs-only",
                                  "extra-required", "uninspectable"])
def test_known_binding_requires_valid_signature_before_helper_entry(production, mode, fault, monkeypatch):
    original = inspect.signature
    seen = []
    known = (production.model.generate, production.model.generate_async)

    def inspect_fault(value, *args, **kwargs):
        if getattr(value, "__func__", value) not in known:
            return original(value, *args, **kwargs)
        seen.append(fault)
        if fault == "uninspectable":
            raise ValueError(PROVIDER_MARKER)
        signature = original(value, *args, **kwargs)
        parameters = [p for p in signature.parameters.values() if p.name != "strict_errors"]
        if fault == "positional":
            parameters.append(inspect.Parameter("strict_errors", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                                default=False))
        elif fault == "required":
            parameters.append(inspect.Parameter("strict_errors", inspect.Parameter.KEYWORD_ONLY))
        elif fault == "kwargs-only":
            parameters.append(inspect.Parameter("kwargs", inspect.Parameter.VAR_KEYWORD))
        elif fault == "extra-required":
            parameters = list(signature.parameters.values())
            parameters.append(inspect.Parameter("unreviewed_required", inspect.Parameter.KEYWORD_ONLY))
        return signature.replace(parameters=parameters)

    # Only corrupt capability inspection, never replace an invoked client/helper.
    monkeypatch.setattr(inspect, "signature", inspect_fault)
    probe = run_probe(production, mode, (ok(*TEN),))
    assert seen
    assert_strict_unavailable(probe)


@pytest.mark.parametrize("mode", MODES)
def test_strict_generation_logs_do_not_expose_query_seed_or_provider_text(production, mode, caplog):
    for name in ("src.agent.tools.llm_molecular_generator", "src.web.models.ollama_model"):
        caplog.set_level(logging.DEBUG, logger=name)
    # A distinctive valid seed makes accidental intent/seed logging observable.
    seed = "CCCCCCCCCCCCCO"
    probe = run_probe(production, mode, (("disconnect", ""),) * 5,
                      query=f"optimize 10 molecules {QUERY_MARKER}; SMILES: {seed}")
    assert len(probe.calls) == 5
    assert_raw_result(production, probe.results[0], (), failed_rounds=True)
    relevant = [r for r in caplog.records if r.name in {
        "src.agent.tools.llm_molecular_generator", "src.web.models.ollama_model"}]
    assert relevant
    allowed = {"Molecular generation started", "Molecular generation round started",
               "Molecular generation round failed", "Molecular generation completed",
               "Molecular generation unavailable", "Ollama strict generation failed"}
    for record in relevant:
        assert record.getMessage() in allowed
        assert not record.exc_info and not record.stack_info and not record.args
        assert all(marker not in record.getMessage() for marker in (PROVIDER_MARKER, QUERY_MARKER, seed))
