# Agent Decision Protocol Implementation Plan

> **For agentic workers:** Use executing-plans and test-driven-development for the coupled implementation; use requesting-code-review for independent final review. Track the steps below.

**Goal:** Deliver the stage A1 model-decision boundary without changing the running chat entrypoint or executing proposed tools.

**Architecture:** Add one strict decision contract and one focused OpenAI-compatible transport module, exposed by a small additive method on the existing model adapter. Both native function arguments and explicitly configured JSON text use the same envelope. This batch does not claim an autonomous runtime: stage A2 must consume this protocol through the controlled LangGraph loop; stage A3 must integrate Web/state/real acceptance.

**Tech Stack:** Existing Python/Pydantic 2, HTTPX, asyncio, pytest; no new dependencies.

## 1. Contract — first RED, then GREEN

Files: create `src/agent/contracts/decision.py`, `tests/agent/test_decision_contract.py`.

- [x] Add failing tests with this concrete accepted envelope and strict rejection matrix:

```python
payload = '{"decision":{"version":"1","action":"tool","tool_name":"property_calculator","arguments":{"query":"CCO"},"purpose":"Calculate properties"}}'
decision = parse_decision_json(payload)
assert decision.action == "tool"
assert decision.arguments == {"query": "CCO"}
```

Reject duplicate JSON keys (including nested), NaN/Infinity, list roots, unknown actions, extra fields, coerced types, oversized/deep documents, invalid tool names, and a scientific finish with no evidence references. Exception text must be fixed safe codes, not Pydantic input dumps.

- [x] Run `python -B -m pytest tests/agent/test_decision_contract.py -q -p no:cacheprovider`; confirm RED due to missing contract.
- [x] Implement `ToolDecision`, `ClarifyDecision`, `FinishDecision` under `DecisionEnvelope`, all strict/extra-forbid. Version is required literal `"1"`. Tool fields: bounded tool_name, JSON arguments, purpose; clarify: question/missing_fields; finish: response_kind/text/evidence_ids. Scientific references are structurally required but actual existence/value/authorization is the future gateway's responsibility. Expose `parse_decision_json(raw)` and `decision_json_schema()`.
- [x] Re-run contract tests GREEN; inspect schema JSON serialization and distinct action branches.

## 2. Transport — protocol errors are not answers

Files: create `src/agent/decision_transport.py`, `src/agent/decision_privacy.py`, `tests/test_agent_decision_model.py`; modify `src/agent/openai_compatible_model.py` with this additive entry:

```python
async def decide(self, messages, *, mode="native", max_tokens=1500, timeout_seconds=60.0):
    from src.agent.decision_transport import request_decision
    return await request_decision(self, messages, mode=mode,
                                  max_tokens=max_tokens, timeout_seconds=timeout_seconds)
```

- [x] Add HTTPX MockTransport tests for native and JSON text, asserting exact message history and tool-call ID preservation, one request only, and no invocation of generate/stream_generate or a workflow planner.
- [x] Run new tests RED before adding the entrypoint/transport.
- [x] Use one native function name `agent_decision` with `decision_json_schema()`; request exactly one function call. Native responses require a single matching function, usable ID and finish_reason=tool_calls, with no simultaneous free answer. JSON text mode requires finish_reason=stop and no tool calls; it never activates as an automatic retry of native failures.
- [x] Return a per-call `DecisionResponse` with decision/error/tool_call_id/metadata, no shared last-response mutable state. Preserve only locally generated request ID, configured provider/model, explicit mode, bounded usage integers, latency, attempts and allowlisted finish reason in metadata. Do not retain reasoning_content, HTTP bodies, arbitrary usage fields or exception text. An AgentExecutionError uses only fixed message/reason fields.
- [x] Bound request options, messages and decision document before use. Reuse `_payload` and `_headers` so existing provider settings remain consistent. Use asyncio deadline around the entire decision request and one HTTP POST; propagate cancellation and close only internally owned clients. Invalid configuration/protocol input makes no network call. Protocol/HTTP/timeout/JSON errors never return success and never retry implicitly.
- [x] Run both new test files GREEN, plus `tests/test_openai_compatible_model.py` for old generate/stream compatibility.

## 3. Adversarial checks and review

- [x] Add tests for 401/403/429/5xx, missing credentials, timeout/cancellation, wrong/multiple choices or function calls, truncated length/content_filter, malformed JSON/schema, duplicate keys, arbitrary-secret error bodies and reasoning. Assert errors/logs/metadata do not expose test secret markers.
- [x] Concurrent responses must retain their own decision/call ID/usage. The method must not mutate caller messages or contaminate old `last_response_metadata`.
- [x] External API is not needed for these contract tests. Use fixture-only fake keys; no environment values are printed/read and no actual request is sent to an external provider.
- [x] Run focused tests, `python -B -m pytest tests/agent -q -rs -p no:cacheprovider --tb=short`, existing OpenAI-compatible model tests and compileall. Run diff checks and independent review; fix verified findings via RED/GREEN.
- [x] Update this checklist and handoff with exact results and remaining A2/A3 work; stage named files and commit on `codex/agent-decision-protocol`. No push/main merge/model activation/service restart.

## Scope self-review

This plan implements only the model protocol subsystem of approved stage A. It cannot prove observation-driven tool execution, input provenance, completed research obligations, recovery, or frontend behavior; those are explicitly not claimed until A2/A3 integration. It does not weaken validation or remove existing workflows prematurely.

## Implemented hardening and results

TDD reproduced missing interfaces, strict decoding failures, nonfinite numeric overflow, invalid message role types, ambiguous legacy/native calls, unbounded body consumption, HTTP error-body reads, oversized timeout overflow, escaped invalid Unicode, dependency log leakage, compressed-response expansion and in-flight configuration races.

The transport now snapshots endpoint/credentials/client before awaiting, requests identity encoding, rejects compressed bodies before reading, bounds the streamed body to 128 KiB, and never reads non-200 bodies. The privacy helper installs request-context filters on the installed HTTPX/HTTPCore logger emitters without changing unrelated request logs or logger levels. Cancellation and client ownership are tested.

Independent spec/quality review: no remaining A1 blockers. Final focused tests: 107 passed. Final combined Agent + decision + legacy model suite: 1744 passed, 1 skipped (disabled performance test), 7 existing warnings. Contract: 34 passed. Compileall and diff checks passed. No real external API, production model activation, runtime switch or main merge.
