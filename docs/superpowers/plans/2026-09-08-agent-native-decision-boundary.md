# Native decision boundary clarification implementation plan

> Execute inline using executing-plans and test-driven-development; retain the approved model-driven-agent design and its strict execution boundaries.

**Goal:** Address the observed DeepSeek native response calling `property_calculator` instead of the single declared `agent_decision` function, without accepting or rewriting wrong tool calls.

**Architecture:** Add explicit native wire-protocol instructions in the existing transport. The model must submit an envelope to the harness; scientific tool names belong to `decision.tool_name`. Keep JSON mode, validation, authorization, evidence checks, budgets, existing single schema correction and production routing unchanged.

**Stack:** Existing Python/HTTPX/Pydantic transport, pytest MockTransport, isolated real RDKit acceptance. No dependencies added.

## Scope and alternatives

Base `f7c5e06`; independent scientific-assets worktree; original dirty project untouched.
Evidence: three native runs each passed 2/3; JSON passed 9/9. An extra native read-only probe showed a valid call object/ID but `function.name=property_calculator`, not `agent_decision`.

Prefer clarifying the native instructions first. Silently renaming returned functions is rejected because it would reinterpret an untrusted action. Expanding retry eligibility beyond schema errors is deferred: existing approved design intentionally does not repair unauthorized tool selection or arbitrary protocol failures.

## Tasks

- [x] Add request-level RED test: native outbound messages must explicitly name the only callable function and distinguish `decision.tool_name`; caller messages remain unchanged and native observation IDs remain paired. Keep `tools`/`tool_choice` restricted to `agent_decision`.
- [x] Add parser regression for names `property_calculator`, `run_docking`, `unknown_tool`: valid envelopes under wrong function names must still fail with `invalid_function_call`, one HTTP request, no implicit JSON fallback.
- [x] Add loop regression: `invalid_function_call` must not trigger schema correction or dispatch a tool.
- [x] Run `python -B -m pytest tests/test_agent_decision_model.py tests/agent/test_decision_protocol_recovery.py -q -p no:cacheprovider --tb=short`; observe the new missing native instruction failure before implementation.
- [x] Modify only native payload construction in `src/agent/decision_transport.py`: prepend a fixed system message and clarify the function description. Do not mutate supplied history, schema or parsed decisions. Update the old request-history assertion to compare the unmodified history suffix.
- [x] Rerun focused tests, then native real acceptance. If the same defect remains, investigate evidence before another change; do not weaken gates to force success.
- [x] Run native/JSON three rounds on final code with temporary runtime credentials. Stop on account/quota rejection; record actual pass/failure and latency, not model self-evaluation. Reports go to ignored `outputs/agent_evaluation/`.
- [x] Full Agent/model regression, anti-hallucination checks, contract and compileall; read-only independent review where available.
- [x] Write handoff with real outcomes, limits and exact commands. Stage only this plan, transport, scoped tests and handoff. Commit on the task branch; no production switch, main merge or accumulated-history PR.

Expected native instruction semantics: `Call only agent_decision; put a scientific tool name in decision.tool_name inside the decision envelope; arguments must use input_ref; do not call a scientific tool directly; for chat/clarify/finish still use agent_decision.` JSON continues its existing strict envelope prompt.
