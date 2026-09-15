# Isolated Chat Bridge Implementation Plan

> **For agentic workers:** Use subagent-driven-development and test-driven-development. Review specification compliance before code quality.

**Goal:** Integrate the server-only decision chat bridge without changing production routes, the homepage, or model activation.

**Architecture:** ChatHandler delegates an explicitly supplied server-owned context and ModelDecisionLoop to a separate transport module. The harness remains the only authority for decisions, tools, evidence, continuation and scientific final text. Transport uses a bounded thread-safe event buffer with a coalesced wakeup; overflow fails explicitly rather than silently losing events or reporting success. Socket sends have deadlines and cancellation settles the owned harness task. No browser-provided permissions, model fallback, environment loading or global runtime is introduced.

**Tech Stack:** Existing Python asyncio, ChatHandler, AgentEventBus, ModelDecisionLoop, pytest and RDKit.

## Approved scope and alternatives

The user confirmed server-side bridge first. Integrating lab UI and CLI simultaneously would enlarge the review boundary; replacing production chat would activate experimental behavior. Both are deferred. Historical source is selectively ported from agent-integration-blockers; main revision-4 continuation and evidence safeguards remain intact.

## Task 1: Contract tests (RED)

- [x] Create tests/agent/test_decision_chat.py from the historical bridge contracts, adapted to current harness helpers. Test chat model dispatch without scientific tools, real RDKit batches and downstream evidence, owner-bound clarification, denied tools, provider-error redaction and exactly one final complete message.
- [x] Add tests/agent/test_decision_chat_transport.py for bounded event delivery, worker-thread bursts, slow/disconnected sockets, outer cancellation, bounded display, failure status and no pending bridge tasks. Use explicit synchronization rather than arbitrary sleeps.
- [x] Run `python -B -m pytest tests/agent/test_decision_chat.py tests/agent/test_decision_chat_transport.py -q -p no:cacheprovider --tb=short` and retain missing-entry failures before implementation.

## Task 2: Minimal implementation (GREEN)

- [x] Add src/web/decision_chat.py and a server-only ChatHandler.process_decision_message delegation method; do not register an HTTP/WebSocket route.
- [x] Keep event buffers and scheduled wakeups bounded, preserve event order, and never regenerate scientific final text with an LLM. On overflow cancel execution and return a fixed failed transport result if the socket remains usable.
- [x] Bound socket sends; on disconnect/timeout cancel and await the harness rather than replaying tools. Do not claim cancellation stops an uncooperative external calculation; retain the harness's uncertain-state semantics.
- [x] Reuse existing privacy utilities; if extending key sanitizer limits, preserve existing defaults. Always retain a valid terminal envelope; mark display truncation/redaction explicitly. Provider exceptions must never appear in client errors.
- [x] Re-run new tests and existing ChatHandler tests; fix only demonstrated integration gaps.
- [x] QUALITY-driven regression: terminal aggregate events use bounded compact status payloads, while final agent_result preserves all accepted molecular rows. Real RDKit batches of 8/12/20 test this boundary.

## Task 3: Verification and review

- [x] Run Agent and model/fallback regressions, all existing Node tests, compileall and contract acceptance in the MedChat environment. Report opt-in skips honestly; no production model or API-key reads.
- [x] Independent SPEC review, repair proven defects with RED/GREEN tests, then independent QUALITY review.
- [x] Update docs/handoff/historical-integration-status.md and add a scoped handoff with commands, results and remaining lab/real-weight/presentation work.
- [ ] Precisely stage task files, check diff and credentials without printing matches, commit and create a focused draft PR. Do not merge without explicit numbered PR authorization and green required CI.

## Acceptance boundary

This batch proves server transport contracts and real RDKit integration with explicit model doubles. It does not prove real external-model availability, trained activity-model performance, browser lab behavior or production deployment readiness. Original dirty worktree and local assets remain untouched.
