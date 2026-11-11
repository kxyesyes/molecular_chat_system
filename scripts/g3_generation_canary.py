"""One bounded generation campaign for scripts/run_g3_generation_canary.py.

Import is passive. The launcher owns cold-child containment and compares the
reported source digests with the current checkout before publishing a frame.
"""

import asyncio
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
import hashlib
import io
import json
import logging
import math
import threading

import httpx


_URL = "http://127.0.0.1:11434/api/generate"
_MODEL = "gmm-llama:latest"
_ENTITY_LIMIT = 1024 * 1024
_REPORT_LIMIT = 64 * 1024
_STOPS = frozenset({
    "request_denied", "attempt_limit", "unsupported_encoding", "response_too_large",
    "deadline", "cancelled", "transport_error", "local_cleanup_unresolved", "provider_model_unverified",
})
_COUNTERS = frozenset({
    "transport_entries", "delegate_entries", "response_headers_seen",
    "bodies_completed", "transport_failures", "denied_before_delegate",
})
_ATTEMPT_KEYS = frozenset({
    "ordinal", "prompt_sha256", "prompt_bytes", "max_tokens", "temperature", "num_ctx",
    "http_status", "body_complete", "entity_bytes", "protocol", "usage", "provider_model",
    "error_code", "elapsed_ms", "local_closed",
})
_USAGE_KEYS = frozenset({"prompt_eval_count", "eval_count"})
_WARNINGS = frozenset({"round_failure", "partial_generation", "suppressed_other"})
_HASH_KEYS = frozenset({"canary", "client", "generator", "adapter", "ownership", "harness", "launcher"})
_RESULT_KEYS = frozenset({"row_count", "valid_count", "unique_count", "source_match_count",
                          "typed_success", "typed_status", "error_code"})
_TYPED_STATUSES = frozenset({"succeeded", "partial", "failed", "unavailable", "invalid_input", "rejected", "cancelled"})
_ERRORS = frozenset({
    "validation_error", "invalid_input", "invalid_output", "tool_timeout",
    "tool_unavailable", "external_tool_unavailable", "provider_error",
    "unauthorized_tool", "model_unavailable", "empty_result", "cancelled", "internal_error",
})
_REPORT_KEYS = frozenset({
    "status", "elapsed_ms", "source_hashes", "adapter_entries", "transport",
    "requested_count", "result", "warnings", "local_cleanup", "remote_state",
    "assembly_match", "tokenizer_identity", "enforcement", "fence_scope", "transport_kind",
})
_OVERFLOW = object()
_INVALID_INTEGER = object()
# Strong ownership on unresolved cleanup; the parent must reap the whole child.
# Never reuse this module for a new campaign while an earlier owner is retained.
_RETAINED = []


def _finite(value):
    if type(value) not in (int, float):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def _native(value, maximum=None):
    return (type(value) is int and value >= 0 and value.bit_length() <= 4096
            and (maximum is None or value <= maximum))


def _require(condition):
    if not condition:
        raise ValueError("Invalid canary report")


def _keys(value, keys):
    _require(type(value) is dict and set(value) == keys)


def _hash(value):
    return type(value) is str and len(value) == 64 and all(c in "0123456789abcdef" for c in value)


def _enum(value, choices, *, nullable=False):
    return (nullable and value is None) or (type(value) is str and value in choices)


def _source_hashes(value):
    _require(type(value) is dict and bool(value) and set(value) <= _HASH_KEYS)
    _require(all(_hash(digest) for digest in value.values()))


def _unknown_usage():
    return {key: {"state": "unknown", "value": None} for key in _USAGE_KEYS}


def _parse_int(text):
    # Wire-bounded oversized facts remain overflow, not zero/clipped values.
    # 4096 bits need at most1234 decimal digits. No Python global limit changes.
    if len(text.lstrip("-")) > 1234:
        return _INVALID_INTEGER if text.startswith("-") else _OVERFLOW
    value = int(text)
    return value if value.bit_length() <= 4096 else (_INVALID_INTEGER if value < 0 else _OVERFLOW)


def _project_usage(value):
    if type(value) is not dict:
        return _unknown_usage()
    result = {}
    for key in _USAGE_KEYS:
        item = value.get(key)
        state = ("missing" if key not in value else "overflow" if item is _OVERFLOW
                 else "null" if item is None else "reported" if _native(item)
                 else "invalid")
        result[key] = {"state": state, "value": item if state == "reported" else None}
    return result


class CanaryStop(Exception):
    def __init__(self, code):
        self.code = code if type(code) is str and code in _STOPS else "request_denied"
        super().__init__("Canary stopped")


class CanaryTransport(httpx.BaseTransport):
    """Single-invocation, bounded observer around the sole sync transport."""

    def __init__(self, delegate, *, clock, started_at, outer_deadline, cancelled, require_model=False):
        if (not callable(clock) or not callable(cancelled) or not _finite(started_at)
                or not _finite(outer_deadline) or outer_deadline < started_at):
            raise CanaryStop("request_denied")
        self._delegate = delegate
        self._require_model = require_model
        self._clock, self._cancelled = clock, cancelled
        self._start = started_at
        self._deadline = min(started_at + 180.0, outer_deadline)
        self._last_time = started_at
        self._lock = threading.RLock()
        self._counts = dict.fromkeys(_COUNTERS, 0)
        self._attempts = []
        self._streams = []
        self._stop = None
        self._closed = False
        self.cleanup_failed = False

    def _latch(self, code):
        with self._lock:
            if self._stop is None:
                self._stop = code

    def _raise(self, code):
        self._latch(code)
        raise CanaryStop(self._stop) from None

    def _now(self):
        try:
            now = self._clock()
            valid = _finite(now) and now >= self._last_time
        except Exception:
            valid = False
        if not valid:
            self._raise("request_denied")
        self._last_time = now
        return now

    def _authority(self):
        with self._lock:
            # Sample elapsed time even after a stop, without clearing that stop.
            now = self._now()
            try:
                cancelled = self._cancelled()
            except Exception:
                self._raise("request_denied")
            if type(cancelled) is not bool:
                self._raise("request_denied")
            if cancelled:
                self._raise("cancelled")
            if now >= self._deadline:
                self._raise("deadline")
            if self._stop is not None:
                self._raise(self._stop)

    def check(self):
        with self._lock:
            self._authority()
            if self._closed:
                self._raise("request_denied")

    def _request_prompt(self, request):
        valid = (request.method == "POST" and str(request.url) == _URL
                 and request.headers.get("host") == "127.0.0.1:11434"
                 and not any(key in request.headers for key in
                             ("authorization", "proxy-authorization", "cookie")))
        try:
            data = json.loads(request.content)
            valid = valid and type(data) is dict and set(data) == {"model", "prompt", "stream", "options"}
            valid = valid and data["model"] == _MODEL and type(data["prompt"]) is str and data["stream"] is False
            options = data.get("options") if type(data) is dict else None
            valid = valid and type(options) is dict and set(options) == {"temperature", "num_predict", "num_ctx"}
            valid = valid and type(options["temperature"]) in (int, float) and options["temperature"] == 0.7
            valid = valid and type(options["num_predict"]) is int and options["num_predict"] == 1000
            valid = valid and type(options["num_ctx"]) is int and options["num_ctx"] == 512
            prompt = data["prompt"].encode("utf-8") if valid else None
        except Exception:
            valid = False
        if not valid:
            self._raise("request_denied")
        return prompt

    def handle_request(self, request):
        with self._lock:
            self._counts["transport_entries"] += 1
            try:
                self.check()
                prompt = self._request_prompt(request)
                prompt_digest = hashlib.sha256(prompt).hexdigest()
                self.check()  # Validation/hashing consume the same deadline.
                if self._counts["delegate_entries"] >= 5:
                    self._raise("attempt_limit")
            except CanaryStop:
                self._counts["denied_before_delegate"] += 1
                raise
            self._counts["delegate_entries"] += 1
            fact = dict(
                ordinal=self._counts["delegate_entries"], prompt_sha256=prompt_digest,
                prompt_bytes=len(prompt), max_tokens=1000, temperature=0.7, num_ctx=512,
                http_status=None, body_complete=False, entity_bytes=0,
                protocol="pending", usage=_unknown_usage(), provider_model="unknown", error_code=None,
                elapsed_ms=0.0, local_closed=False,
            )
            begin = self._last_time
            self._attempts.append(fact)
        response = None
        stream = None
        try:
            response = self._delegate.handle_request(request)
            with self._lock:
                fact["http_status"] = response.status_code
                self._counts["response_headers_seen"] += 1
            # Keep original response and stream; inspect raw headers BEFORE
            # returning anything to HTTPX, which otherwise installs decoders.
            stream = _ObservedBody(self, response.stream, fact, begin)
            self._streams.append(stream)
            response.stream = stream
            self.check()
            encodings = [value for key, value in response.headers.raw if key.lower() == b"content-encoding"]
            if encodings and (len(encodings) != 1 or encodings[0].strip().lower() != b"identity"):
                self._raise("unsupported_encoding")
            return response
        except CanaryStop:
            with self._lock:
                fact.update(protocol="interrupted", error_code=self._stop)
        except Exception:
            with self._lock:
                self._counts["transport_failures"] += 1
                self._latch("transport_error")
                fact.update(protocol="interrupted", error_code="transport_error")
        if response is not None:
            try:
                response.close()
            except Exception:
                self.cleanup_failed = True
        elif stream is None:
            # No response handle was returned; delegate.close still must settle.
            fact["local_closed"] = False
        raise CanaryStop(self._stop) from None

    def snapshot(self):
        with self._lock:
            return deepcopy(dict(counters=self._counts, attempts=self._attempts, stop_code=self._stop))

    def close(self):
        with self._lock:
            if self._closed:
                if self.cleanup_failed:
                    raise CanaryStop("local_cleanup_unresolved")
                return
            self._closed = True
        for stream in self._streams:
            try:
                stream.close()
            except Exception:
                self.cleanup_failed = True
        try:
            self._delegate.close()
        except Exception:
            self.cleanup_failed = True
        if self.cleanup_failed:
            self._latch("local_cleanup_unresolved")
            raise CanaryStop("local_cleanup_unresolved") from None


class _ObservedBody(httpx.SyncByteStream):
    def __init__(self, owner, source, fact, begin):
        self.owner, self.source, self.fact, self.begin = owner, source, fact, begin
        self.buffer = bytearray()
        self.closed = False

    def __iter__(self):
        owner, fact = self.owner, self.fact
        try:
            owner.check()
            for chunk in self.source:
                owner.check()  # A blocked read may have returned past the cutoff.
                if type(chunk) is not bytes:
                    owner._raise("transport_error")
                if fact["entity_bytes"] + len(chunk) > _ENTITY_LIMIT:
                    owner._raise("response_too_large")
                with owner._lock:
                    self.buffer.extend(chunk)
                    fact["entity_bytes"] += len(chunk)
                yield chunk
            owner.check()
            with owner._lock:
                fact["body_complete"] = True
                owner._counts["bodies_completed"] += 1
                try:
                    value = json.loads(self.buffer, parse_int=_parse_int)
                except (ValueError, UnicodeError, RecursionError):
                    value = None
                fact["usage"] = _project_usage(value)
                if type(value) is dict:
                    if "model" not in value:
                        fact["provider_model"] = "missing"
                    elif type(value["model"]) is not str:
                        fact["provider_model"] = "invalid"
                    else:
                        fact["provider_model"] = "match" if value["model"] == _MODEL else "mismatch"
                # The bounded usage parser must not certify a response which
                # the unchanged client's normal JSON decoder would reject.
                # Retain overflow usage facts even when native decoding fails.
                native_json_ok = True
                try:
                    json.loads(self.buffer)
                except (ValueError, UnicodeError, RecursionError):
                    native_json_ok = False
                if fact["http_status"] != 200:
                    fact.update(protocol="http_error", error_code="http_error")
                elif (native_json_ok and type(value) is dict and "error" not in value and value.get("done") is True
                      and type(value.get("response")) is str):
                    if owner._require_model and fact["provider_model"] != "match":
                        fact.update(protocol="protocol_error", error_code="protocol_error")
                        owner._raise("provider_model_unverified")
                    fact["protocol"] = "success"
                else:
                    fact.update(protocol="protocol_error", error_code="protocol_error")
        except CanaryStop:
            with owner._lock:
                if not fact["body_complete"]:
                    fact.update(protocol="interrupted", error_code=owner._stop)
            raise
        except Exception:
            with owner._lock:
                owner._counts["transport_failures"] += 1
                owner._latch("transport_error")
                fact.update(protocol="interrupted", error_code="transport_error")
            raise CanaryStop(owner._stop) from None
        finally:
            self.buffer.clear()
            self.close()

    def close(self):
        if self.closed:
            return
        self.closed = True
        self.buffer.clear()
        try:
            self.source.close()
        except Exception:
            self.owner.cleanup_failed = True
            self.owner._latch("local_cleanup_unresolved")
            raise CanaryStop("local_cleanup_unresolved") from None
        else:
            with self.owner._lock:
                self.fact["local_closed"] = True
                # Cleanup timestamp cannot reopen admission or replace its stop.
                self.fact["elapsed_ms"] = max(0.0, self.owner._last_time - self.begin) * 1000


class _DenyAsync(httpx.AsyncBaseTransport):
    def __init__(self, observer):
        self.observer = observer

    async def handle_async_request(self, request):
        self.observer._raise("request_denied")


class _Discard(io.TextIOBase):
    def write(self, text):
        return len(text)

    def flush(self):
        pass


def _result_projection(result, chem):
    projection = dict.fromkeys(_RESULT_KEYS)
    warnings = dict.fromkeys(_WARNINGS, 0)
    if result is None:
        return projection, warnings
    projection["typed_success"] = result.success if type(result.success) is bool else None
    status = getattr(result.status, "value", None)
    projection["typed_status"] = status if status in _TYPED_STATUSES else None
    error = getattr(getattr(result.error, "code", None), "value", None)
    projection["error_code"] = error if error in _ERRORS else None
    if type(result.data) is list:
        projection["row_count"] = len(result.data)
        valid, source_matches, unique = 0, 0, set()
        for row in result.data:
            if type(row) is not dict or type(row.get("smiles")) is not str:
                continue
            if row.get("model") == _MODEL and row.get("source") == "llm":
                source_matches += 1
            molecule = chem.MolFromSmiles(row["smiles"])
            if molecule is not None:
                valid += 1
                unique.add(chem.MolToSmiles(molecule))
        projection.update(valid_count=valid, unique_count=len(unique), source_match_count=source_matches)
    for warning in result.warnings:
        if warning == "Some molecular generation rounds failed.":
            key = "round_failure"
        elif (type(warning) is str and warning.startswith("Only generated ")
              and warning.endswith(" valid unique SMILES out of requested 10.")):
            key = "partial_generation"
        else:
            key = "suppressed_other"
        warnings[key] += 1
    return projection, warnings


async def run_canary(*, delegate=None, real=False, clock, started_at, outer_deadline, cancelled, source_hashes):
    """One fixed adapter call. Only an internally constructed direct transport is live.

    Tests supply finite transports. The portable launcher supplies the cold-child
    deadline and discards diagnostic output before importing scientific modules.
    """
    if _RETAINED:
        raise CanaryStop("local_cleanup_unresolved")
    if type(real) is not bool or (real and delegate is not None):
        raise ValueError("Invalid canary transport selection")
    if real:
        transport_kind = "local_ollama"
        delegate = httpx.HTTPTransport(trust_env=False, retries=0)
    elif delegate is None:
        transport_kind = "scripted"
        fixture = json.dumps({"done": True, "response": "\n".join("C" * n for n in range(3, 13))})
        delegate = httpx.MockTransport(lambda request: httpx.Response(
            200, stream=httpx.ByteStream(fixture.encode("ascii"))))
    else:
        transport_kind = "injected"
    try:
        observer = CanaryTransport(delegate, clock=clock, started_at=started_at,
                                   outer_deadline=outer_deadline, cancelled=cancelled, require_model=real)
    except CanaryStop:
        try:
            delegate.close()
        except Exception:
            _RETAINED.append((delegate,))
        raise
    owner = model = sync = async_client = result = chem = None
    adapter_entries = 0
    cleanup_ok = True
    failed = False
    hashes = {}
    projection = dict.fromkeys(_RESULT_KEYS)
    warning_counts = dict.fromkeys(_WARNINGS, 0)
    previous_logging = logging.root.manager.disable
    # This is a dedicated child, not a shared web-server invocation. Do not
    # retain raw prints/logs in a StringIO. Parent still fences C-level/FD output.
    with redirect_stdout(_Discard()), redirect_stderr(_Discard()):
        logging.disable(logging.CRITICAL)
        try:
            _source_hashes(source_hashes)
            hashes = dict(source_hashes)
            observer.check()
            from rdkit import Chem, rdBase
            from src.agent.tools.llm_molecular_generator import LLMMolecularGenerator
            from src.system.ollama_model import OllamaModel
            from src.agent.tooling.generation_ranking_contract import (
                GenerationToolAdapter, GenerationInput, GenerationOutput,
            )
            from src.agent.tooling.spec import ToolSpec, RetryPolicy
            from src.agent.runtime.worker_ownership import WorkerOwner, retain_until_done
            from src.agent.scientific_session.decision_execution import settle_owned_call

            chem = Chem
            observer.check()
            owner = WorkerOwner()
            model = object.__new__(OllamaModel)
            model.base_url, model.model_name = "http://127.0.0.1:11434", _MODEL
            sync = httpx.Client(transport=observer, timeout=150.0, trust_env=False,
                                follow_redirects=False, headers={"Accept-Encoding": "identity"})
            model.sync_client = sync
            async_client = httpx.AsyncClient(transport=_DenyAsync(observer), timeout=150.0,
                                            trust_env=False, follow_redirects=False)
            model.client = async_client
            generator = LLMMolecularGenerator(model)
            spec = ToolSpec(
                name="llm_molecular_generator", version=str(getattr(generator, "version", "1")),
                description=generator.description, input_schema=GenerationInput,
                output_schema=GenerationOutput, capabilities={"molecule.generate"},
                timeout_seconds=180.0, retry_policy=RetryPolicy(max_attempts=1,
                    retryable_error_codes={"provider_error"}), side_effects="none",
                idempotent=True, sensitive_fields={"api_key", "authorization", "token"},
                owner_agents={"molecular_design"}, max_concurrency=1,
            )
            adapter = GenerationToolAdapter(spec, generator)

            def invoke():
                nonlocal adapter_entries
                observer.check()
                adapter_entries += 1
                return adapter.execute({"query": "generate 10 molecules",
                                        "metadata": {"requested_count": 10}}, allow_retry=False)

            with rdBase.BlockLogs():
                owned_call = settle_owned_call(invoke, worker_owner=owner)
                try:
                    owned_task = asyncio.create_task(owned_call)
                except BaseException:
                    owned_call.close()
                    raise
                # Catch cancellation here, before the existing helper finishes
                # its physical drain. Shield and retain this SAME owned call;
                # repeated cancellation may not abandon it or admit another POST.
                while not owned_task.done():
                    try:
                        await asyncio.shield(owned_task)
                    except asyncio.CancelledError:
                        observer._latch("cancelled")
                result = owned_task.result()
                projection, warning_counts = _result_projection(result, chem)
                observer.check()
        except asyncio.CancelledError:
            observer._latch("cancelled")
        except CanaryStop:
            # The fixed transport stop survives the generator's round catches.
            pass
        except Exception:
            failed = True
        finally:
            try:
                if owner is not None:
                    try:
                        await owner.settle()
                    except asyncio.CancelledError:
                        observer._latch("cancelled")
                    except BaseException:
                        cleanup_ok = False
                    cleanup_ok = cleanup_ok and owner.status == "settled" and owner.pending_roots == 0
                if model is not None and sync is not None and async_client is not None:
                    closing = None
                    close_call = model.close()
                    try:
                        try:
                            closing = asyncio.create_task(close_call)
                        except BaseException:
                            close_call.close()
                            raise
                        await retain_until_done(closing)
                    except asyncio.CancelledError:
                        observer._latch("cancelled")
                        cleanup_ok = (cleanup_ok and closing is not None and closing.done()
                                      and not closing.cancelled() and closing.exception() is None)
                    except BaseException:
                        cleanup_ok = False
                    cleanup_ok = cleanup_ok and sync.is_closed and async_client.is_closed
                else:
                    if async_client is not None:
                        try:
                            await async_client.aclose()
                        except BaseException:
                            cleanup_ok = False
                    if sync is not None:
                        try:
                            sync.close()
                        except BaseException:
                            cleanup_ok = False
                try:
                    observer.close()
                except BaseException:
                    cleanup_ok = False
                try:
                    observer._authority()
                except CanaryStop:
                    pass
            finally:
                logging.disable(previous_logging)
    if not cleanup_ok:
        observer._latch("local_cleanup_unresolved")
        _RETAINED.append((owner, observer, model, sync, async_client))
    snapshot = observer.snapshot()
    attempts = snapshot["attempts"]
    if transport_kind == "injected":
        remote = "unverified"
    elif not real:
        remote = "not_contacted"
    elif not attempts:
        remote = "not_attempted"
    elif all(f["body_complete"] for f in attempts):
        remote = "provider_terminal_reported"
    else:
        remote = "uncertain"
    count = projection["unique_count"]
    accepted_rows = (
        not failed and projection["typed_success"] is True and type(count) is int
        and count == projection["row_count"] == projection["valid_count"] == projection["source_match_count"]
        and any(f["protocol"] == "success" for f in attempts)
    )
    if not cleanup_ok:
        status = "unresolved"
    elif snapshot["stop_code"] is not None:
        status = "stopped"
    elif accepted_rows and count == 10:
        status = "complete10"
    elif accepted_rows and 0 < count < 10:
        status = "partial"
    else:
        status = "failed"
    report = dict(
        status=status, elapsed_ms=max(0.0, observer._last_time - started_at) * 1000,
        source_hashes=hashes, adapter_entries=adapter_entries, transport=snapshot,
        requested_count=10, result=projection, warnings=warning_counts,
        local_cleanup="settled" if cleanup_ok else "unresolved", remote_state=remote,
        transport_kind=transport_kind,
        assembly_match="unverified", tokenizer_identity="unverified", enforcement="unverified",
        fence_scope="child_only_server_downstream_unverified",
    )
    # Revalidate before returning, not only when the caller chooses to encode.
    encode_report(report)
    return report


def _validate_report(report):
    _keys(report, _REPORT_KEYS)
    _require(_enum(report["status"], {"complete10", "partial", "failed", "stopped", "unresolved"}))
    _require(_finite(report["elapsed_ms"]) and report["elapsed_ms"] >= 0)
    _source_hashes(report["source_hashes"])
    _require(_native(report["adapter_entries"], 1))
    _require(type(report["requested_count"]) is int and report["requested_count"] == 10)
    _require(_enum(report["local_cleanup"], {"settled", "unresolved"}))
    live = report["transport_kind"] == "local_ollama"
    _require(_enum(report["transport_kind"], {"scripted", "local_ollama", "injected"}))
    remote_states = {"scripted": {"not_contacted"}, "injected": {"unverified"},
                     "local_ollama": {"provider_terminal_reported", "uncertain", "not_attempted"}}
    _require(_enum(report["remote_state"], remote_states[report["transport_kind"]]))
    _require(all(_enum(report[key], {"unverified"}) for key in ("assembly_match", "tokenizer_identity", "enforcement")))
    _require(_enum(report["fence_scope"], {"child_only_server_downstream_unverified"}))
    _keys(report["warnings"], _WARNINGS)
    _require(all(_native(value) for value in report["warnings"].values()))
    result = report["result"]
    _keys(result, _RESULT_KEYS)
    for key in ("row_count", "valid_count", "unique_count", "source_match_count"):
        _require(result[key] is None or _native(result[key]))
    _require(result["typed_success"] is None or type(result["typed_success"]) is bool)
    _require(_enum(result["typed_status"], _TYPED_STATUSES, nullable=True))
    _require(_enum(result["error_code"], _ERRORS, nullable=True))
    counts = [result[key] for key in ("unique_count", "valid_count", "row_count")]
    _require(all(v is None for v in counts) or (all(type(v) is int for v in counts) and counts == sorted(counts)))
    matched = result["source_match_count"]
    _require((matched is None and counts[2] is None)
             or (type(matched) is int and counts[2] is not None and matched <= counts[2]))
    snapshot = report["transport"]
    _keys(snapshot, {"counters", "attempts", "stop_code"})
    _require(_enum(snapshot["stop_code"], _STOPS, nullable=True))
    c, attempts = snapshot["counters"], snapshot["attempts"]
    _keys(c, _COUNTERS)
    _require(all(_native(value) for value in c.values()))
    _require(type(attempts) is list and len(attempts) <= 5)
    _require(c["delegate_entries"] == len(attempts))
    _require(c["transport_entries"] == c["delegate_entries"] + c["denied_before_delegate"])
    _require(c["bodies_completed"] <= c["response_headers_seen"] <= c["delegate_entries"])
    _require(c["transport_failures"] <= c["delegate_entries"])
    for ordinal, fact in enumerate(attempts, 1):
        _keys(fact, _ATTEMPT_KEYS)
        _require(type(fact["ordinal"]) is int and fact["ordinal"] == ordinal)
        _require(_hash(fact["prompt_sha256"]) and _native(fact["prompt_bytes"]))
        _require(type(fact["max_tokens"]) is int and fact["max_tokens"] == 1000)
        _require(type(fact["num_ctx"]) is int and fact["num_ctx"] == 512)
        _require(type(fact["temperature"]) in (int, float) and fact["temperature"] == 0.7)
        _require(fact["http_status"] is None or (_native(fact["http_status"], 599) and fact["http_status"] >= 100))
        _require(type(fact["body_complete"]) is bool and type(fact["local_closed"]) is bool)
        _require(_native(fact["entity_bytes"], _ENTITY_LIMIT))
        _require(_finite(fact["elapsed_ms"]) and fact["elapsed_ms"] >= 0)
        _require(_enum(fact["protocol"], {"pending", "success", "http_error", "protocol_error", "interrupted"}))
        _require(_enum(fact["provider_model"], {"match", "missing", "mismatch", "invalid", "unknown"}))
        _require(_enum(fact["error_code"], _STOPS | {"http_error", "protocol_error"}, nullable=True))
        _keys(fact["usage"], _USAGE_KEYS)
        for usage in fact["usage"].values():
            _keys(usage, {"state", "value"})
            _require(_enum(usage["state"], {"reported", "missing", "null", "invalid", "unknown", "overflow"}))
            _require(_native(usage["value"]) if usage["state"] == "reported" else usage["value"] is None)
        if fact["body_complete"]:
            _require(fact["http_status"] is not None and fact["protocol"] in {"success", "http_error", "protocol_error"})
        else:
            _require(fact["protocol"] in {"pending", "interrupted"})
        if fact["protocol"] == "success":
            _require(fact["http_status"] == 200 and fact["body_complete"] and fact["error_code"] is None)
            _require(not live or fact["provider_model"] == "match")
        elif fact["protocol"] in {"http_error", "protocol_error"}:
            _require(fact["body_complete"] and fact["error_code"] == fact["protocol"])
            _require((fact["http_status"] == 200) == (fact["protocol"] == "protocol_error"))
    _require(c["bodies_completed"] == sum(f["body_complete"] for f in attempts))
    _require(c["response_headers_seen"] == sum(f["http_status"] is not None for f in attempts))
    if live:
        _require((report["remote_state"] == "not_attempted") == (not attempts))
    if report["remote_state"] == "provider_terminal_reported":
        _require(all(f["body_complete"] and f["protocol"] in {"success", "http_error", "protocol_error"} for f in attempts))
    if report["local_cleanup"] == "unresolved":
        _require(report["status"] == "unresolved")
    if report["status"] == "unresolved":
        _require(report["local_cleanup"] == "unresolved")
    elif snapshot["stop_code"] is not None:
        _require(report["status"] == "stopped")
    if report["status"] == "stopped":
        _require(snapshot["stop_code"] is not None)
    if report["status"] in {"complete10", "partial"}:
        _require(report["adapter_entries"] == 1 and result["typed_success"] is True)
        _require(result["typed_status"] in {"succeeded", "partial"} and result["error_code"] is None)
        _require(matched == counts[2])
        _require(report["local_cleanup"] == "settled" and snapshot["stop_code"] is None)
        _require(any(f["protocol"] == "success" for f in attempts) and all(f["local_closed"] for f in attempts))
        if report["status"] == "complete10":
            _require(counts == [10, 10, 10] and (not live or report["remote_state"] == "provider_terminal_reported"))
        else:
            _require(counts[0] is not None and 0 < counts[0] < 10 and counts[0] == counts[1] == counts[2])


def encode_report(report):
    """Closed projection only; never echo rejected fields or silently truncate."""
    try:
        _validate_report(report)
        encoded = json.dumps(report, ensure_ascii=True, allow_nan=False,
                             separators=(",", ":")).encode("utf-8")
        if len(encoded) <= _REPORT_LIMIT:
            return encoded
    except Exception:
        pass
    raise ValueError("Invalid canary report") from None
