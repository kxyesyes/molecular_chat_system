# Runtime Failure Contract Design

**Date:** 2026-10-14

**Scope:** PR 1 of 3 — Ollama/chat transport, decision_a2 terminal delivery, RAG compatibility retrieval, and molecular-generation timeout settlement.

## Goal

Ensure provider, projection, retrieval, and timeout failures remain explicit failures at the public boundary. A user-facing `complete` frame may be successful only when the requested operation completed successfully; an error string, empty retrieval list, or fallback response must not be treated as success.

## Existing root causes

1. `OllamaModel.stream_generate()` yields a human-readable string for non-200 responses and then ends normally. `ChatHandler` therefore publishes a normal completion.
2. `decision_chat._deliver_result()` sends `agent_result` before reference projection finishes. `_TurnSender` marks the turn as `finishing` at `agent_result`, so a later projection exception skips the runtime failure terminal path.
3. The legacy async RAG helper catches every exception and returns `[]`, conflating no hits with unavailable retrieval. The strict receipt/agent path already has its own failure contract and must remain compatible.
4. `_call_llm_sync()` uses a `ThreadPoolExecutor` context manager around `future.result(timeout=30)`. Exiting the context waits for the still-running provider call, defeating the caller-visible timeout.

## Public contracts

### Chat completion

Successful completion remains compatible with existing fields:

```json
{"type":"complete","status":"succeeded","content":"..."}
```

Provider failures use one terminal frame and no assistant-content success frame:

```json
{
  "type": "complete",
  "status": "failed",
  "content": "模型服务暂时不可用，请稍后重试。",
  "error": {"code": "ollama_http_error", "message": "Ollama 返回 HTTP 500"}
}
```

The public message is bounded and does not include raw provider bodies, prompts, credentials, or stack traces. Stable codes distinguish HTTP errors, timeouts, transport failures, and client errors. The relaxed legacy `generate()` behavior remains available for callers that explicitly request non-strict compatibility; the web chat path uses strict generation.

### decision_a2 terminal delivery

`agent_result` is an intermediate projection. `complete` is the terminal frame. The turn becomes `finishing` only after the terminal completion send is physically acknowledged by the socket. A reference/candidate projection error must produce a failed terminal result, and the runtime must not remain in an in-progress state. Existing cancellation, continuation, and worker-retention semantics remain unchanged.

### RAG compatibility retrieval

Add a status-bearing legacy query boundary with:

- `succeeded` and `records` for usable hits;
- `no_hits` and an empty `records` list for a ready index with no matches;
- `unavailable`, empty `records`, and a bounded error code for initialization, embedding, index, or transport failures.

The existing strict receipt API remains authoritative for Agent execution. The legacy Chat path consumes the new status-bearing boundary and never silently treats `unavailable` as `no_hits`.

### Molecular-generation timeout

When the async provider bridge exceeds 30 seconds, the caller returns a timeout failure without waiting for the provider thread to settle. The executor is shut down with `wait=False` and `cancel_futures=True` where supported. No second dispatch or fallback generation is attempted. The underlying provider may continue to settle independently; this is recorded as a non-blocking ownership limitation rather than advertised as cancellation.

## Implementation boundaries

- Modify `src/system/ollama_model.py` only for strict stream error signaling and bounded error classification.
- Modify `src/web/chat_handler.py` and `src/web/models/__init__.py` only to map strict provider failures into the completion envelope and preserve test doubles/legacy adapters.
- Modify `src/web/decision_chat.py` and `src/web/decision_runtime.py` to separate intermediate result delivery from terminal completion delivery.
- Modify `src/rag/service.py` and the legacy Chat call site for the additive status-bearing retrieval method.
- Modify `src/agent/tools/llm_molecular_generator.py` only for non-blocking timeout settlement.
- Do not change Agent strict receipt schemas, database schemas, scientific result values, or provider retry policy in this PR.

## Verification

Tests must first reproduce each failure and fail for the expected reason, then pass after the minimal fix:

- Ollama stream HTTP 500, timeout, and transport failures produce failed completion frames and never normal completion frames.
- decision_a2 reference projection failure produces a terminal failed completion and does not leave the turn waiting forever.
- RAG returns distinct `succeeded`, `no_hits`, and `unavailable` outcomes; strict receipt tests remain green.
- A blocking async generation provider returns from `_call_llm_sync()` at the caller timeout without waiting for the executor context.

Validation includes the focused pytest modules, `python -m compileall -q src scripts`, the existing JavaScript checks where touched, and `git diff --check`.

## Non-goals

This PR does not add automatic retries, create a new docking task, change model timeout values, remove legacy APIs, or claim that the provider thread can be forcibly terminated. Those concerns belong to later design work or explicit operational controls.
