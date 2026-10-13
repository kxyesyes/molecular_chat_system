# Runtime Failure Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Ollama, reference-projection, legacy RAG, and generation-bridge failures terminate truthfully without becoming successful answers, empty searches, or hidden waits.

**Architecture:** Add strict behavior at existing provider and retrieval boundaries, preserving the opt-in legacy APIs and the Agent receipt contract. Keep `agent_result` intermediate and acknowledge terminal delivery only after the socket send; presentation failure must not rewrite persisted scientific execution. Replace only the bridge executor's blocking context-manager exit, not the provider, retry algorithm, or scientific workflow.

**Tech Stack:** Python 3.10+, asyncio, concurrent.futures, HTTPX, FastAPI WebSocket, pytest, existing RDKit/FAISS test environments.

---

## Scope and working rules

Approved design: `docs/superpowers/specs/2026-10-14-runtime-failure-contract-design.md` (commit `512fa148`). This is PR 1 of the approved three-PR split, covering original findings 1, 2, 8, and 9. Optimization seeds, recommendation fallback, `fsp3`, target filtering, and docking labels belong to separate PRs.

Use branch `fix/runtime-failure-contract`, based on `origin/main` at `93769841`. Preserve unrelated untracked runtime assets and presentation files. Do not add assets, secrets, machine-specific paths, `.env` files, or test outputs. Stage named paths only. No merge is authorized.

Commands below assume the repository root and an activated Python environment with the repository's scientific dependencies. First verify it:

```powershell
git status --short --branch
python -c "import sys, pytest, httpx, rdkit, numpy, pandas; print(sys.version); print('test dependencies available')"
```

Do not install or upgrade dependencies as part of this PR. If the current interpreter lacks them, use an existing suitable environment and report that interpreter in the verification result. All new failure probes use synthetic transports or temporary stores, not production endpoints or credentials.

## File responsibilities

| File | Responsibility in this PR |
|---|---|
| `src/system/ollama_model.py` | Strict streaming; bounded failure reasons and optional numeric HTTP status |
| `src/web/models/__init__.py` | Strict opt-in forwarding to known Ollama bound methods, preserving legacy adapters |
| `src/web/chat_handler.py` | Failed completion envelopes, no strict-provider redispatch, status-aware legacy retrieval |
| `src/web/decision_chat.py` | Document intermediate result/projection ordering; retain owned projection drain |
| `src/web/decision_runtime.py` | Separate cancellation latch from terminal acknowledgment; queue presentation failure terminal |
| `src/rag/service.py` | Add status-bearing async retrieval; preserve legacy list and strict receipt APIs |
| `src/agent/tools/llm_molecular_generator.py` | Nonblocking executor shutdown after the caller-visible bridge timeout |
| `tests/agent/test_generation_transport_characterization.py` | Real Ollama methods with synthetic HTTP and executor-boundary fault injection |
| `tests/agent/test_chat_runtime_failures.py` (new) | Web completion and retrieval integration contracts |
| `tests/agent/test_decision_chat_transport.py` | Intermediate/terminal state and transport ownership contracts |
| `tests/agent/test_web_decision_runtime_references.py` | Actual `/ws` projection-failure terminal and socket reuse |
| `tests/test_rag_query_status.py` (new) | Retrieval outcome classification and legacy compatibility |
| `tests/agent/test_chat_handler_agent_events.py` | Update legacy-RAG test double to expose the status-bearing boundary |
| `tests/agent/test_chat_input_budget.py` | Update the RAG input-budget test double without changing its fixture records |
| `tests/agent/test_chat_local_cleanup.py` | Preserve local cleanup controls while updating its RAG test double |
| `tests/agent/test_routing_prompt_matrix.py` | Update the routing-matrix RAG test double without changing route assertions |

Existing tests are acceptance controls, not a license to preserve known bad behavior. Change an old assertion only when it explicitly asserts the behavior this approved design replaces. Keep cancellation, closed-transport, receipt, history-commit, and worker-retention controls.

## Task 1: Strict Ollama streaming and bounded error classification

**Files:** Modify `src/system/ollama_model.py`; test `tests/agent/test_generation_transport_characterization.py`.

- [ ] **Step 1: Add failing strict-stream tests beside the existing legacy stream tests.**

Use the existing `production`, `mocked_client`, `FiniteAsyncBody`, `QUERY`, and `PROVIDER_MARKER` fixtures. Replace `test_stream_signature_remains_legacy_without_strict_keyword` with a signature test that requires a keyword-only `strict_errors=False`; retain the positional/default legacy tests unchanged.

```python
def test_stream_has_explicit_opt_in_strict_keyword(production):
    signature = inspect.signature(production.model.stream_generate)
    assert tuple(signature.parameters) == (
        'self', 'prompt', 'temperature', 'max_tokens', 'strict_errors')
    strict = signature.parameters['strict_errors']
    assert strict.kind is inspect.Parameter.KEYWORD_ONLY
    assert strict.default is False
    assert inspect.isasyncgenfunction(production.model.stream_generate)


@pytest.mark.parametrize('step,reason', [
    (('stream-http-error', (PROVIDER_MARKER.encode(),)), 'http_error'),
    (('disconnect', ''), 'transport_error'),
    (('type-error', ''), 'client_error'),
    (('stream-ok', (b'not-json\n',)), 'invalid_response'),
    (('stream-ok', (b'{"error":"private-body"}\n',)), 'invalid_response'),
    (('stream-ok', (b'{"response":"I"}\n',)), 'invalid_response'),
    (('stream-disconnect', (b'{"response":"I"}\n',)), 'transport_error'),
])
def test_strict_stream_never_yields_failure_text(production, step, reason, caplog):
    caplog.set_level(logging.DEBUG, logger='src.web.models.ollama_model')
    chunks = []
    with mocked_client(production, (step,)) as (model, transport):
        async def consume():
            async for chunk in model.stream_generate(QUERY, strict_errors=True):
                chunks.append(chunk)
        with pytest.raises(strict_error_type(production)) as caught:
            asyncio.run(consume())
        assert caught.value.reason == reason
        assert caught.value.__cause__ is None
        assert caught.value.__context__ is None
        assert all(chunk == 'I' for chunk in chunks)
        assert len(transport.calls) == 1
        assert all(response.is_closed for response in transport.responses)
    assert PROVIDER_MARKER not in caplog.text
    assert QUERY not in caplog.text


def test_strict_stream_accepts_done_tail_without_newline(production):
    step = ('stream-ok', (b'{"response":"I"}\n',
                          b'{"response":"CCO","done":true}'))
    with mocked_client(production, (step,)) as (model, transport):
        async def consume():
            return [chunk async for chunk in model.stream_generate(QUERY, strict_errors=True)]
        assert asyncio.run(consume()) == ['I', 'CCO']
        assert len(transport.calls) == 1
```

Add timeout coverage without changing the production transport. The test must use the real model and `mocked_client`, set `transport.on_request` to each exception, and assert one call, the fixed exception message, `reason == 'timeout'`, no raw diagnostics, and closed responses:

```python
@pytest.mark.parametrize('fault', [httpx.ReadTimeout, httpx.ConnectTimeout])
def test_strict_stream_timeout_is_fixed_failure(production, fault, caplog):
    caplog.set_level(logging.DEBUG, logger='src.web.models.ollama_model')
    with mocked_client(production, ()) as (model, transport):
        transport.on_request = lambda event: (_ for _ in ()).throw(fault(PROVIDER_MARKER))
        async def consume():
            return [chunk async for chunk in model.stream_generate(QUERY, strict_errors=True)]
        with pytest.raises(strict_error_type(production)) as caught:
            asyncio.run(consume())
        assert caught.value.reason == 'timeout'
        assert caught.value.args == (CLIENT_MESSAGE,)
        assert len(transport.calls) == 1
    assert PROVIDER_MARKER not in caplog.text

```

Add HTTP 500 explicitly at the transport response boundary, not by replacing a production model method:

```python
def test_strict_stream_http500_preserves_only_numeric_status(production):
    with mocked_client(production, (('stream-http-error', (PROVIDER_MARKER.encode(),)),)) as (model, transport):
        transport.on_response = lambda event: setattr(transport.responses[-1], 'status_code', 500)
        async def consume():
            return [chunk async for chunk in model.stream_generate(QUERY, strict_errors=True)]
        with pytest.raises(strict_error_type(production)) as caught:
            asyncio.run(consume())
        assert caught.value.reason == 'http_error'
        assert caught.value.http_status == 500
        assert PROVIDER_MARKER not in repr(vars(caught.value))
        assert len(transport.calls) == 1
```

Include strict consumer `aclose()` after one chunk and verify response closure before client closure, using the existing consumer-close test structure. The strict parser must reject EOF without `done`; a valid final record may omit a newline.

- [ ] **Step 2: Run RED.**

```powershell
python -m pytest tests/agent/test_generation_transport_characterization.py -q -k "strict_stream or stream_has_explicit"
```

Expected: new tests fail because `stream_generate` does not accept `strict_errors`; do not accept collection/import failure as RED.

- [ ] **Step 3: Implement a separate strict stream path before the legacy logging/parser.**

Extend `OllamaGenerationError.__init__` with an optional keyword-only `http_status=None`; allow `timeout` in its fixed reasons. Store status only if `type(http_status) is int and 100 <= http_status <= 599`, otherwise `None`. Never retain a request, response, exception, or provider body. Preserve its fixed exception message.

```python
def __init__(self, reason: str, *, http_status=None):
    self.reason = reason if type(reason) is str and reason in {
        'http_error', 'timeout', 'transport_error', 'invalid_response', 'client_error'
    } else 'client_error'
    self.http_status = (http_status if type(http_status) is int
                        and 100 <= http_status <= 599 else None)
    super().__init__('Ollama generation failed')
```

At the final raise in `_strict_generation_text`, pass `http_status=response.status_code` only for `reason == 'http_error'`. In the strict exception branches of both nonstream methods, classify `httpx.TimeoutException` before `httpx.TransportError`:

```python
reason = ('timeout' if isinstance(e, httpx.TimeoutException) else
          'transport_error' if isinstance(e, httpx.TransportError) else 'client_error')
```

Add this private method to `OllamaModel`:

```python
async def _stream_generate_strict(self, prompt, temperature, max_tokens):
    reason, status = 'invalid_response', None
    try:
        async with self.client.stream(
            'POST', f'{self.base_url}/api/generate',
            json={'model': self.model_name, 'prompt': prompt, 'stream': True,
                  'options': {'temperature': temperature, 'num_predict': max_tokens}},
            timeout=150.0,
        ) as response:
            if response.status_code != 200:
                reason, status = 'http_error', response.status_code
            else:
                response.encoding = 'utf-8'
                async for line in response.aiter_lines():
                    if not line.strip():
                        continue
                    try:
                        data = json.loads(line)
                    except (ValueError, UnicodeError):
                        break
                    if (type(data) is not dict or 'error' in data
                            or type(data.get('response')) is not str
                            or ('done' in data and type(data['done']) is not bool)):
                        break
                    if data['response']:
                        yield data['response']
                    if data.get('done') is True:
                        return
    except httpx.TimeoutException:
        reason = 'timeout'
    except httpx.TransportError:
        reason = 'transport_error'
    except Exception:
        reason = 'client_error'
    logger.error('Ollama strict generation failed')
    raise OllamaGenerationError(reason, http_status=status)
```

The public generator's new signature and preamble are:

```python
async def stream_generate(self, prompt: str, temperature: float = 0.7,
                          max_tokens: int = 1500, *, strict_errors: bool = False):
    if type(strict_errors) is not bool:
        raise ValueError('invalid_strict_errors')
    if strict_errors:
        stream = self._stream_generate_strict(prompt, temperature, max_tokens)
        try:
            async for chunk in stream:
                yield chunk
        finally:
            await stream.aclose()
        return
```

Keep the existing legacy body after this preamble. Strict mode must branch before raw request/chunk logging. Do not catch `BaseException`, swallow cancellation, or issue another HTTP request. Validate integer/bool status sanitization, malformed records, EOF without `done`, and split UTF-8 in the tests.

- [ ] **Step 4: Run GREEN and compatibility controls.**

```powershell
python -m pytest tests/agent/test_generation_transport_characterization.py -q
git diff --check
```

Expected: all characterization tests pass, including unchanged non-strict text behavior, single dispatch, strict log redaction, and response cleanup.

- [ ] **Step 5: Commit only this task.**

```powershell
git add src/system/ollama_model.py tests/agent/test_generation_transport_characterization.py
git commit -m "fix: expose strict Ollama stream failures"
```

## Task 2: Web chat consumes strict provider failures

**Files:** Modify `src/web/models/__init__.py` and `src/web/chat_handler.py`; create `tests/agent/test_chat_runtime_failures.py`.

- [ ] **Step 1: Add a real-model/real-handler failing regression.**

Import the existing `production` fixture and `mocked_client` helper from `test_generation_transport_characterization`; use only their offline transport. New module setup and core regression:

```python
import asyncio
import json
from types import SimpleNamespace

import pytest

from src.web.chat_handler import ChatHandler
from test_generation_transport_characterization import production, mocked_client


class Socket:
    scope = {}

    def __init__(self):
        self.messages = []

    async def send_text(self, text):
        self.messages.append(json.loads(text))


@pytest.mark.parametrize('streaming', [True, False])
def test_ollama_http_failure_is_one_failed_chat_completion(production, streaming):
    step = (('stream-http-error', (b'private-provider-body',)) if streaming else
            ('http-error', ''))
    with mocked_client(production, (step,)) as (model, transport):
        handler = ChatHandler(model, SimpleNamespace(is_initialized=False), None,
                              {'inference': {'stream': streaming}})
        socket = Socket()
        asyncio.run(handler._process_message(socket, 'Explain logP', False, False))
        complete = [f for f in socket.messages if f['type'] == 'complete']
        assert len(complete) == 1
        assert complete[0]['status'] == 'failed'
        assert complete[0]['error']['code'] == 'ollama_http_error'
        assert not [f for f in socket.messages if f['type'] == 'stream']
        assert 'private-provider-body' not in json.dumps(socket.messages)
        assert not handler.conversation_history
        assert len(transport.calls) == 1
```

Parameterize the same integration with timeout, transport failure, and malformed NDJSON. For a midstream disconnect, permit the genuine earlier chunk but require one failed terminal, no fallback dispatch, and no successful history entry. Add successful stream/nonstream positive controls requiring `status='succeeded'`. Add old-style sync and async test adapters whose signatures omit `strict_errors`; ensure their successful operation remains supported.

- [ ] **Step 2: Run RED.**

```powershell
python -m pytest tests/agent/test_chat_runtime_failures.py -q
```

Expected: missing failed status / wrong success completion on provider error, not a fixture or import error.

- [ ] **Step 3: Add strict-aware web wrappers without speculative invocation.**

Add to `src/web/models/__init__.py`:

```python
def _strict_options(method, enabled):
    known = (OllamaModel.generate, OllamaModel.generate_async, OllamaModel.stream_generate)
    if (enabled and isinstance(getattr(method, '__self__', None), OllamaModel)
            and getattr(method, '__func__', None) in known):
        return {'strict_errors': True}
    return {}


def stream_for_chat(model, prompt, temperature=0.7, max_tokens=1500):
    method = model.stream_generate
    return method(prompt, temperature=temperature, max_tokens=max_tokens,
                  **_strict_options(method, True))
```

Replace `generate_for_chat` with this compatible implementation and export `stream_for_chat`:

```python
async def generate_for_chat(model: Any, prompt: str, temperature: float = 0.7,
                            max_tokens: int = 1500, *, strict_errors=False) -> str:
    method = getattr(model, 'generate_async', None)
    preferred_async = callable(method)
    if not preferred_async:
        method = getattr(model, 'generate')
    kwargs = {'temperature': temperature, 'max_tokens': max_tokens,
              **_strict_options(method, strict_errors)}
    if preferred_async or inspect.iscoroutinefunction(method):
        result = method(prompt, **kwargs)
    else:
        result = await asyncio.to_thread(method, prompt, **kwargs)
    if inspect.isawaitable(result):
        result = await result
    return str(result)
```

No retry after `TypeError`; compatibility is chosen before dispatch. Keep `generate_for_chat`'s default relaxed for direct legacy callers; ChatHandler passes `strict_errors=True`.

- [ ] **Step 4: Map provider failures into one bounded completion.**

Import `OllamaGenerationError` from `src.system.ollama_model` and `stream_for_chat` from `src.web.models`. Add this helper to `ChatHandler`:

```python
@staticmethod
async def _send_model_failure(websocket, error):
    messages = {
        'http_error': 'Ollama 返回 HTTP 错误',
        'timeout': 'Ollama 请求超时',
        'transport_error': '无法连接或读取 Ollama 服务',
        'invalid_response': 'Ollama 返回无效响应',
        'client_error': 'Ollama 客户端请求失败',
    }
    reason = error.reason if error.reason in messages else 'client_error'
    message = messages[reason]
    if reason == 'http_error' and error.http_status is not None:
        message = f'Ollama 返回 HTTP {error.http_status}'
    await websocket.send_text(json.dumps({
        'type': 'complete', 'status': 'failed',
        'content': '模型服务暂时不可用，请稍后重试。',
        'error': {'code': f'ollama_{reason}', 'message': message},
    }, ensure_ascii=False))
```

Replace only the generation block after `_model_max_tokens(request_model)` and before history append. Keep prompt construction, finalization, model snapshot, and request-lease cleanup intact. Use the following control flow so socket send failures cannot trigger a second provider call:

```python
use_nonstream = not self.config.get('inference', {}).get('stream', True)
failure = None
if not use_nonstream:
    try:
        stream = stream_for_chat(request_model, prompt, temperature, model_max_tokens)
    except OllamaGenerationError as exc:
        failure = exc
        stream = None
    except Exception:
        # An unknown legacy adapter may fail while constructing its iterator.
        # Only that adapter path may fall back to non-streaming generation.
        logger.error('Legacy stream setup failed; details omitted')
        use_nonstream = True
        stream = None
    if stream is not None:
        chunk_count = 0
        try:
            while True:
                try:
                    chunk = await stream.__anext__()
                except StopAsyncIteration:
                    break
                except OllamaGenerationError as exc:
                    failure = exc
                    break
                except Exception:
                    # Preserve the existing fallback for non-Ollama legacy adapters.
                    # Known Ollama strict failures are always handled above.
                    logger.error('Legacy stream generation failed; details omitted')
                    use_nonstream = True
                    break
                if chunk:
                    full_response += chunk
                    chunk_count += 1
                    await websocket.send_text(json.dumps({'type': 'stream', 'content': chunk}))
                    if chunk_count % 5 == 0:
                        await asyncio.sleep(0.001)
        finally:
            close = getattr(stream, 'aclose', None)
            if callable(close):
                await finish_on_cancel(close())
    if failure is not None:
        await self._send_model_failure(websocket, failure)
        return

if use_nonstream:
    try:
        full_response = await generate_for_chat(
            request_model, prompt, temperature=temperature,
            max_tokens=model_max_tokens, strict_errors=True)
    except OllamaGenerationError as exc:
        failure = exc
    if failure is not None:
        await self._send_model_failure(websocket, failure)
        return

full_response, warning_chunk, completion_meta = self._finalize_model_response(
    full_response, request_model)
if warning_chunk and not use_nonstream:
    await websocket.send_text(json.dumps({'type': 'stream', 'content': warning_chunk},
                                         ensure_ascii=False))
await websocket.send_text(json.dumps({
    'type': 'complete', 'content': full_response,
    **completion_meta, 'status': 'succeeded',
}, ensure_ascii=False))
```

Inspect legacy adapters for synchronous exceptions during stream creation; keep their existing fallback behavior in a provider-only catch, without enclosing socket sends. Do not broaden strict claims to unknown provider adapters. Model-request lifecycle tests must prove that response closure still precedes lease release. Failure terminal delivery itself must propagate socket failure, not retry it.

- [ ] **Step 5: Run GREEN and commit.**

```powershell
python -m pytest tests/agent/test_chat_runtime_failures.py tests/agent/test_generation_transport_characterization.py tests/agent/test_chat_handler_agent_events.py tests/agent/test_chat_handler_partial_results.py tests/test_model_request_lifecycle.py -q
git diff --check
git add src/web/models/__init__.py src/web/chat_handler.py tests/agent/test_chat_runtime_failures.py
git commit -m "fix: terminate chat on strict provider failures"
```

## Task 3: Separate intermediate decision delivery from terminal acknowledgment

**Files:** Modify `src/web/decision_runtime.py`, `src/web/decision_chat.py`, `tests/agent/test_decision_chat_transport.py`, and `tests/agent/test_web_decision_runtime_references.py`.

- [ ] **Step 1: Establish failing intermediate-state and actual-route tests.**

In `test_settled_candidate_fixture_uses_one_projection_before_complete`, after `_deliver_result` and before manually sending `pending_complete`, add:

```python
assert not turn.finishing
assert not turn.terminal_sent
assert turn.pending_complete is not None
```

Add this actual-route test to the reference module, reusing its imported fixtures:

```python
@pytest.mark.parametrize('error', [RuntimeError, ConnectionError, asyncio.TimeoutError])
def test_projection_failure_finishes_failed_and_keeps_socket_usable(actual_app, monkeypatch, error):
    async def run():
        async with actual_app(mode='decision_a2') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                def fail_project(*args, **kwargs):
                    raise error('private-projection-diagnostic')
                original = b.app.decision_runtime.references.project
                monkeypatch.setattr(b.app.decision_runtime.references, 'project', fail_project)
                await socket.send({'message': 'Explain logP'})
                frames = []
                while not frames or frames[-1]['type'] != 'complete':
                    frames.append(await asyncio.wait_for(socket.receive(), 3))
                result = next(f for f in frames if f['type'] == 'agent_result')
                terminal = frames[-1]
                assert result['status'] == 'completed'
                assert terminal['status'] == 'failed'
                assert terminal['error']['code'] == 'result_projection_failed'
                assert terminal['turn_id'] == result['turn_id']
                assert terminal['trace_id'] == result['trace_id']
                assert sum(f['type'] == 'complete' for f in frames) == 1
                assert 'private-projection-diagnostic' not in json.dumps(frames)
                assert b.app.agent_state_store.get_run(result['trace_id'])['status'] == 'succeeded'
                sender, = b.app.decision_runtime.sockets
                assert not sender.memory and sender.waiting is None
                assert not b.app.decision_runtime.active_owners
                assert b.app.model_request_gate._readers == 0
                monkeypatch.setattr(b.app.decision_runtime.references, 'project', original)
                await socket.send({'type': 'ping', 'timestamp': 13})
                assert await socket.receive() == {'type': 'pong', 'timestamp': 13}
    asyncio.run(run())
```

Narrow `test_actual_projection_exception_closes_socket_without_rewriting_result` to `asyncio.CancelledError` only; keep all its close-fault cases. Ordinary service exceptions now belong to the new failed-terminal test. Existing actual socket-send failure tests must still require no false terminal acknowledgment. Add a second successful request on the same socket after restoring `project` to prove a new turn is admitted, not merely that ping works.

- [ ] **Step 2: Run RED.**

```powershell
python -m pytest tests/agent/test_decision_chat_transport.py tests/agent/test_web_decision_runtime_references.py -q -k "settled_candidate_fixture or projection_failure_finishes"
```

Expected: early `finishing=True` assertion and socket close instead of a failed terminal.

- [ ] **Step 3: Preserve cancellation semantics with a distinct intermediate latch.**

Add `result_started: bool = False` beside `_Turn.finishing`. Keep `finishing` false until successful physical terminal delivery. Replace the first guard in `_Turn.cancel`:

```python
if self.result_started or self.finishing or self.cancel_event.is_set():
    return
```

In `_TurnSender.send_text`, replace the `agent_result` assignment to `finishing` with:

```python
self.turn.result_started = True
```

This retains the established rule that a matching user cancellation after result delivery begins does not cancel the settled result publication. It must not be used to suppress a failed presentation terminal. Keep `pending_complete` as deferred delivery, not acknowledgment. In the existing `completed()` callback, set both:

```python
turn.finishing = True
turn.terminal_sent = True
```

For the turn-task scheduling-failure path that directly sends `pending_complete`, use an `on_sent` callback to set the same two fields only after the actual send:

```python
if turn.pending_complete is not None:
    def mark_terminal_sent():
        turn.finishing = True
        turn.terminal_sent = True
    await sender.send_text(turn.pending_complete, on_sent=mark_terminal_sent)
```

Do not mark either field before send or when send fails.

- [ ] **Step 4: Queue a failed presentation terminal without replacing the scientific result.**

Add this method to `WebDecisionRuntime`:

```python
async def _projection_failed(self, sender, turn):
    if turn.terminal_sent or not sender.writable:
        return
    sender.waiting = None
    turn.next_waiting = None
    turn.history_update = None
    turn.pending_complete = None
    await _TurnSender(sender, turn).send_text(json.dumps({
        'type': 'complete', 'trace_id': turn.trace_id, 'status': 'failed',
        'content': '结果展示未完成，请稍后重试。已完成的计算记录仍保留。',
        'error': {'code': 'result_projection_failed', 'message': '结果投影失败'},
    }, ensure_ascii=False))
```

In `_execute`'s `except Exception` branch, replace only the post-display `close_failed_delivery()` call with `_projection_failed(sender, turn)`. Keep the existing pre-display `_terminal(...FAILED...)` path. In the `(ConnectionError, WebSocketDisconnect, asyncio.TimeoutError)` branch, call `_projection_failed` when `sender.writable` and `turn.displayed_answer is not None`; actual failed physical sends already set `sender.writable=False`, and must remain undeliverable. Keep `CancelledError` and semantic-deadline paths unchanged; they retain their established drain/close behavior.

In `decision_chat._deliver_result`, update the docstring and scheduling comment to state the actual contract:

```python
"""Publish an intermediate result, drain its projection, then queue completion."""
```

Keep `_create_owned_task`, `asyncio.shield`, and `finish_on_cancel` unchanged. Do not cancel a `to_thread` wrapper and claim its store worker stopped. No new AgentResult or database write is needed for a presentation-only failure; the failed completion overrides the UI turn state, not scientific execution provenance.

- [ ] **Step 5: Run GREEN, cancellation and physical-ack controls; commit.**

```powershell
python -m pytest tests/agent/test_decision_chat_transport.py tests/agent/test_web_decision_runtime_references.py tests/agent/test_web_decision_runtime.py tests/agent/test_web_decision_runtime_lifecycle.py -q
git diff --check
git add src/web/decision_chat.py src/web/decision_runtime.py tests/agent/test_decision_chat_transport.py tests/agent/test_web_decision_runtime_references.py
git commit -m "fix: complete decision turns after projection failures"
```

Expected: one failed terminal on a writable socket, no false acknowledgment on broken sockets, no duplicate result, unchanged persisted science, no failed history pair, and unchanged continuation/worker/cancellation behavior.

## Task 4: Status-bearing legacy RAG and truthful Chat failure

**Files:** Modify `src/rag/service.py`, `src/web/chat_handler.py`, `tests/agent/test_chat_runtime_failures.py`, `tests/agent/test_chat_handler_agent_events.py`, `tests/agent/test_chat_input_budget.py`, `tests/agent/test_chat_local_cleanup.py`, and `tests/agent/test_routing_prompt_matrix.py`; create `tests/test_rag_query_status.py`.

- [ ] **Step 1: Add failing retrieval-outcome tests.**

Use a `RAGSystem` created with `object.__new__` so this contract test cannot initialize host assets; replace only the readiness/embedding/search boundaries. It verifies status classification, not real retrieval quality (existing manifest/receipt suites cover that).

```python
import asyncio
import logging
from unittest.mock import AsyncMock, Mock

import httpx
import pytest

from src.rag.service import RAGSystem
from src.rag.index import RAGIndexCompatibilityError


@pytest.mark.parametrize('records,status', [([], 'no_hits'), ([{'smiles': 'CCO'}], 'succeeded')])
def test_query_status_distinguishes_hits(records, status):
    service = object.__new__(RAGSystem)
    service._require_retrieval_ready = Mock()
    service.get_embedding = AsyncMock(return_value=[1.0])
    service._search_embedding = Mock(return_value=records)
    result = asyncio.run(service.search_similar_molecules_with_status('query', 2))
    assert result == {'status': status, 'records': records}


@pytest.mark.parametrize('stage,code', [('ready', 'rag_not_ready'),
                                        ('embedding', 'rag_embedding_failed'),
                                        ('index', 'rag_index_failed')])
def test_query_status_does_not_disguise_failure_as_no_hits(stage, code, caplog):
    caplog.set_level(logging.WARNING, logger='src.rag.service')
    service = object.__new__(RAGSystem)
    service._require_retrieval_ready = Mock()
    service.get_embedding = AsyncMock(return_value=[1.0])
    service._search_embedding = Mock(return_value=[])
    boundary = {'ready': service._require_retrieval_ready,
                'embedding': service.get_embedding, 'index': service._search_embedding}[stage]
    boundary.side_effect = RuntimeError('private-retrieval-diagnostic')
    result = asyncio.run(service.search_similar_molecules_with_status('query', 2))
    assert result == {'status': 'unavailable', 'records': [], 'error': {'code': code}}
    assert 'private-retrieval-diagnostic' not in caplog.text
    assert asyncio.run(service.search_similar_molecules('query', 2)) == []
```

Add `httpx.TimeoutException` and `httpx.TransportError` embedding cases requiring `rag_transport_failed`. Add cancellation requiring propagation, not an `unavailable` result. Keep the legacy list call returning records/`[]` for old callers; the new Chat path must not use it.

In `tests/agent/test_chat_runtime_failures.py`, add this Chat-level test using the existing `Socket`:

```python
def test_requested_legacy_rag_unavailable_is_not_an_empty_success():
    from unittest.mock import AsyncMock
    model = SimpleNamespace(generate_async=AsyncMock(return_value='must not be used'))
    rag = SimpleNamespace(is_initialized=False, search_similar_molecules_with_status=AsyncMock(
        return_value={'status': 'unavailable', 'records': [], 'error': {'code': 'rag_not_ready'}}))
    handler = ChatHandler(model, rag, None, {'inference': {'stream': False}})
    handler._should_use_legacy_rag = lambda message: True
    socket = Socket()
    asyncio.run(handler._process_message(socket, 'search local knowledge base', True, False))
    complete = [f for f in socket.messages if f['type'] == 'complete']
    assert len(complete) == 1 and complete[0]['status'] == 'failed'
    assert complete[0]['error']['code'] == 'rag_unavailable'
    assert complete[0]['retrieval_status'] == 'unavailable'
    model.generate_async.assert_not_called()
    rag.search_similar_molecules_with_status.assert_awaited_once()
    assert not handler.conversation_history
```

Add `no_hits` and `succeeded` positive controls using the real RAG presentation fixture records from `tests/agent/test_chat_handler_agent_events.py`, not invented receipt fields. Confirm explicit knowledge-base queries routed to the Agent remain on their existing strict receipt path.

- [ ] **Step 2: Run RED.**

```powershell
python -m pytest tests/test_rag_query_status.py tests/agent/test_chat_runtime_failures.py -q
```

Expected: missing `search_similar_molecules_with_status` and the old initialization gate silently skipping retrieval failure.

- [ ] **Step 3: Add the status boundary and retain the list wrapper.**

```python
async def search_similar_molecules_with_status(self, query: str, k: int = 2):
    stage = 'rag_not_ready'
    try:
        self._require_retrieval_ready()
        stage = 'rag_embedding_failed'
        embedding = await self.get_embedding(query)
        stage = 'rag_index_failed'
        records = self._search_embedding(embedding, k)
    except httpx.TransportError:
        code = 'rag_transport_failed'
    except Exception:
        code = stage
    else:
        return {'status': 'succeeded' if records else 'no_hits', 'records': records}
    logger.warning('RAG retrieval unavailable; no unverified records returned')
    return {'status': 'unavailable', 'records': [], 'error': {'code': code}}


async def search_similar_molecules(self, query: str, k: int = 2) -> List[Dict[str, Any]]:
    """Legacy list compatibility; status-aware callers use the additive API."""
    result = await self.search_similar_molecules_with_status(query, k)
    return result['records']
```

Do not touch `search_similar_molecules_sync_with_receipt`, `_query_receipt`, receipt validation, or source eligibility. Cancellation must escape because it is not an ordinary `Exception` on supported Python versions.

- [ ] **Step 4: Replace only the legacy Chat retrieval branch.**

Remove `self.rag_service.is_initialized` from the outer condition. Keep `not agent_used`, `enable_rag`, and `_should_use_legacy_rag(message)`. This lets a requested but uninitialized service return explicit `unavailable`.

Use a callable status method when present; a missing status API or a raised exception must be unavailable, never fall back to the ambiguous list API:

```python
await self._send_status(websocket, f'🔍 搜索相关分子数据 (top {rag_count})...')
outcome = {'status': 'unavailable', 'records': []}
try:
    search = getattr(self.rag_service, 'search_similar_molecules_with_status', None)
    if callable(search):
        outcome = await search(message, rag_count)
except Exception:
    logger.warning('Legacy RAG unavailable; details omitted')
valid = (type(outcome) is dict and type(outcome.get('records')) is list
         and outcome.get('status') in {'succeeded', 'no_hits'}
         and (outcome['status'] == 'succeeded') == bool(outcome['records']))
if not valid:
    await websocket.send_text(json.dumps({
        'type': 'complete', 'status': 'failed', 'retrieval_status': 'unavailable',
        'content': '知识库检索暂时不可用，未生成基于检索的回答。',
        'error': {'code': 'rag_unavailable', 'message': '知识库检索未完成'},
    }, ensure_ascii=False))
    return
retrieved_molecules = outcome['records']
if retrieved_molecules:
    rag_context = self._format_rag_context(retrieved_molecules)
    await websocket.send_text(json.dumps(self._rag_info_payload(
        retrieved_molecules, f'✅ 找到 {len(retrieved_molecules)} 个相关分子'),
        ensure_ascii=False))
    await self._send_status(websocket, f'📊 基于 {len(retrieved_molecules)} 个分子数据生成回答...')
else:
    await self._send_status(websocket, '知识库检索完成，未找到匹配分子。')
```

Do not enclose socket sends in the retrieval exception handler. The failure path must return before model invocation or history append. Disabled RAG remains disabled.

Update the existing test doubles listed in the file map so their legacy-path behavior is explicit rather than accidentally treated as unavailable. In each class, keep the old method only for direct compatibility tests and add the status method used by ChatHandler:

```python
async def search_similar_molecules_with_status(self, query, count):
    records = await self.search_similar_molecules(query, count)
    return {'status': 'succeeded' if records else 'no_hits', 'records': records}
```

Apply this adapter to `RecordingLegacyRagService` in `tests/agent/test_chat_handler_agent_events.py`, the local `Rag` class in `tests/agent/test_chat_input_budget.py`, the `Rag` class in `tests/agent/test_chat_local_cleanup.py`, and `_RecordingRagService` in `tests/agent/test_routing_prompt_matrix.py`. For fakes that intentionally model an unavailable service, leave `is_initialized = False` and add a status method returning `{'status': 'unavailable', 'records': [], 'error': {'code': 'rag_not_ready'}}`. Update their call assertions to observe the new method's delegated call exactly once; do not make the production ChatHandler call the old list method as a fallback.

- [ ] **Step 5: Run GREEN and strict receipt controls; commit.**

```powershell
python -m pytest tests/test_rag_query_status.py tests/test_rag_service_boundary.py tests/test_rag_index_manifest.py tests/test_rag_retrieval_outcome.py tests/test_rag_owned_generation.py tests/agent/test_rag_tool_contract.py tests/agent/test_rag_receipt_consumption.py tests/agent/test_rag_current_eligibility.py tests/agent/test_chat_runtime_failures.py tests/agent/test_chat_handler_agent_events.py tests/agent/test_chat_input_budget.py tests/agent/test_chat_local_cleanup.py tests/agent/test_routing_prompt_matrix.py -q
git diff --check
git add src/rag/service.py src/web/chat_handler.py tests/test_rag_query_status.py tests/agent/test_chat_runtime_failures.py tests/agent/test_chat_handler_agent_events.py tests/agent/test_chat_input_budget.py tests/agent/test_chat_local_cleanup.py tests/agent/test_routing_prompt_matrix.py
git commit -m "fix: distinguish unavailable legacy retrieval from no hits"
```

## Task 5: Return from bridge timeout without an executor join

**Files:** Modify `src/agent/tools/llm_molecular_generator.py` and `tests/agent/test_generation_transport_characterization.py`.

- [ ] **Step 1: Add an event-controlled executor regression with finite cleanup.**

Use the real model methods and HTTPX transport; inject only the executor/Future boundary. Do not shorten the production 30-second value or sleep for 30 seconds in the test. Keep the client open until the provider future has been released and joined.

```python
def test_bridge_timeout_does_not_join_still_running_provider(production, monkeypatch):
    import concurrent.futures
    from concurrent.futures import TimeoutError as FutureTimeout
    entered, release, exited, returned = (threading.Event() for _ in range(4))
    real_submit = ThreadPoolExecutor.submit
    real_shutdown = ThreadPoolExecutor.shutdown
    futures, shutdowns, errors = [], [], []
    with mocked_client(production, (ok('I'),)) as (model, transport):
        binding = SimpleNamespace(model_name=model.model_name, generate=model.generate_async)
        tool = production.generator(binding)

        def blocked(event):
            entered.set()
            try:
                assert release.wait(5), 'test watchdog released blocked provider'
            finally:
                exited.set()

        transport.on_request = blocked

        def submit(executor, function, *args, **kwargs):
            future = real_submit(executor, function, *args, **kwargs)
            if getattr(function, '__func__', None) is not production.generator._run_async_in_new_loop:
                return future
            futures.append((executor, future))
            class DeadlineFuture:
                def result(self, timeout=None):
                    assert timeout == 30
                    assert entered.wait(3)
                    raise FutureTimeout()
            return DeadlineFuture()

        def shutdown(executor, wait=True, *, cancel_futures=False):
            if any(owner is executor for owner, _ in futures):
                shutdowns.append((wait, cancel_futures))
            return real_shutdown(executor, wait=wait, cancel_futures=cancel_futures)

        def caller():
            async def invoke():
                try:
                    tool._call_llm_sync('generate 1 molecule')
                except Exception as exc:
                    errors.append(exc)
            try:
                asyncio.run(invoke())
            finally:
                returned.set()

        monkeypatch.setattr(concurrent.futures.ThreadPoolExecutor, 'submit', submit)
        monkeypatch.setattr(concurrent.futures.ThreadPoolExecutor, 'shutdown', shutdown)
        thread = threading.Thread(target=caller)
        thread.start()
        try:
            assert entered.wait(3)
            assert returned.wait(1), 'caller blocked joining the provider after timeout'
            assert not exited.is_set()
            assert shutdowns == [(False, True)]
            assert len(errors) == 1 and errors[0].reason == 'timeout'
        finally:
            release.set()
            thread.join(5)
            for executor, future in futures:
                try:
                    future.result(timeout=5)
                finally:
                    real_shutdown(executor, wait=True)
        assert not thread.is_alive()
        assert len(futures) == 1
        assert len(transport.calls) == 1
```

This test enters the running-loop branch through `asyncio.run` while retaining the real `_capture_strict_generate`, `_call_llm_sync`, and `_run_async_in_new_loop`. It deliberately keeps the provider callback blocked until after the caller returns, then releases and joins the actual provider Future before `mocked_client` closes the HTTP clients. The contract is prompt caller return plus eventual explicit test cleanup, not provider cancellation or client reuse safety.

- [ ] **Step 2: Run RED.**

```powershell
python -m pytest tests/agent/test_generation_transport_characterization.py -q -k "bridge_timeout_does_not_join"
```

Expected: `caller blocked joining the provider after timeout`. The `finally` release must prevent a hung pytest process even in RED.

- [ ] **Step 3: Replace only the executor context manager and classify timeout.**

Inside `_call_llm_sync`'s `if running` branch:

```python
import concurrent.futures
executor = concurrent.futures.ThreadPoolExecutor()
try:
    future = executor.submit(self._run_async_in_new_loop, prompt, temperature,
                             generate_method=method)
    return future.result(timeout=30)
finally:
    # Pending work may be cancelled, but a running provider is not terminated.
    # Do not turn caller timeout into a blocking executor join.
    executor.shutdown(wait=False, cancel_futures=True)
```

At module scope import `TimeoutError as FutureTimeout` from `concurrent.futures`. Add this branch before `_call_llm_sync`'s final generic exception branch, and use the fixed reason after the try/except so raw context is not retained:

```python
except (OllamaGenerationError, _StrictGenerationUnavailable):
    raise
except (FutureTimeout, asyncio.TimeoutError):
    reason = 'timeout'
except Exception:
    reason = 'client_error'
raise OllamaGenerationError(reason)
```

Python 3.10+ supports `cancel_futures`. Keep `_run_async_in_new_loop`'s 30-second async wait and loop cleanup unchanged. Preserve captured method identity and all single-dispatch tests. This does not introduce a second dispatch, change the existing molecule-round retry policy, or guarantee provider-thread termination. Record the lingering-provider limitation in the PR.

- [ ] **Step 4: Run GREEN and executor failure controls; commit.**

```powershell
python -m pytest tests/agent/test_generation_transport_characterization.py -q
git diff --check
git add src/agent/tools/llm_molecular_generator.py tests/agent/test_generation_transport_characterization.py
git commit -m "fix: avoid joining provider thread after generation timeout"
```

## Task 6: Integrated verification and draft PR

- [ ] **Step 1: Run all focused contract and lifecycle suites.**

```powershell
python -m pytest tests/agent/test_generation_transport_characterization.py tests/agent/test_chat_runtime_failures.py tests/agent/test_chat_handler_agent_events.py tests/agent/test_chat_handler_partial_results.py tests/agent/test_decision_chat_transport.py tests/agent/test_web_decision_runtime.py tests/agent/test_web_decision_runtime_references.py tests/agent/test_web_decision_runtime_lifecycle.py tests/test_model_request_lifecycle.py tests/test_rag_query_status.py tests/test_rag_service_boundary.py tests/test_rag_index_manifest.py tests/test_rag_retrieval_outcome.py tests/test_rag_owned_generation.py tests/agent/test_rag_tool_contract.py tests/agent/test_rag_receipt_consumption.py tests/agent/test_rag_current_eligibility.py -q
```

Expected: zero failures. Record actual counts and any skips; never claim real provider acceptance from these synthetic transport probes.

- [ ] **Step 2: Run broad regression and compilation.**

```powershell
python -m pytest tests -q
python -m compileall -q src scripts
node tests/home_agent_task_panel_test.js
node tests/reverse_target_broad_recall_test.js
node tests/reverse_target_pagination_test.js
git diff --check
```

No JavaScript changes are planned. The Node tests are compatibility controls for existing frontend consumption. If JavaScript changes become necessary to interpret failed completion correctly, first obtain scope approval and add a reproducing Node test. Deployment/data files are unchanged, so no production health check or live external request is necessary for this code-only PR. Report environment-limited broad-test failures separately from regressions; do not silently skip them.

- [ ] **Step 3: Review the entire task diff and run the requesting-code-review skill.**

```powershell
git diff origin/main...HEAD --stat
git diff origin/main...HEAD -- src/system/ollama_model.py src/web/models/__init__.py src/web/chat_handler.py src/web/decision_chat.py src/web/decision_runtime.py src/rag/service.py src/agent/tools/llm_molecular_generator.py
git status --short --branch
git log --oneline origin/main..HEAD
```

Review specifically: raw diagnostic leakage; duplicate terminal or fallback dispatch; canceled store workers; premature model close after bridge timeout; history/continuation commits on failure; strict receipt changes; untracked user assets accidentally staged. Correct regressions with new RED/GREEN tests, not weakened positive controls.

- [ ] **Step 4: Publish one draft PR targeting `main`, without merging.**

Use the connected GitHub tool or configured CLI after confirming the remote and branch. PR title: `fix: make runtime failures explicit at chat boundaries`. Include goal, exact file scope, executed verification results, risks, and review focus. State that the executor timeout does not forcibly cancel running provider work, and that scientific run success may coexist with a failed presentation terminal. Attach the created PR to this chat using the app artifact tool. Do not include any runtime credentials or raw user/provider logs.

- [ ] **Step 5: Report outcome to the user.**

Report branch, final commit, PR URL, test commands/counts, remaining operational limitations, and that PR 2 and PR 3 remain separate follow-up work. Do not claim all nine findings are fixed by this PR.

## Plan self-review

- Spec coverage: Task 1 strict stream/classification; Task 2 failed Chat completion; Task 3 physical terminal acknowledgment and projection failure; Task 4 three-way RAG status; Task 5 bounded caller timeout; Task 6 compatibility and publication.
- Types and names: `strict_errors` remains keyword-only/default false; Chat uses strict true for known Ollama methods; `http_status` is optional numeric metadata; `result_started` controls cancellation only; `finishing` and `terminal_sent` acknowledge the actual terminal send; new RAG method is `search_similar_molecules_with_status` everywhere.
- Preserved contracts: direct relaxed model APIs, Agent receipt APIs, scientific persistence, disabled RAG, legacy adapter signatures, non-cooperative worker drain, cancellation, history and continuation acknowledgment.
- No production implementation is included in this planning commit. RED/GREEN evidence must be collected during execution.
