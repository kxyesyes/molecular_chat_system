# Isolated Decision Lab Implementation Plan

> **For agentic workers:** Use subagent-driven-development and test-driven-development. Review SPEC before QUALITY. No production activation.

**Goal:** Integrate the approved loopback-only acceptance page and CLI scripts on main's decision harness and server bridge.

**Architecture:** Explicit FastAPI factory, not imported by production app. Server-selected fixed acceptance cases supply permissions and requirements, never browser identities/tool scopes. Host, Origin, loopback peer and HttpOnly SameSite cookie gates protect this local test app. One active request per session, expiring/revocable sessions, bounded pending continuations and global run count; old tasks remain tracked and are drained on reset and shutdown. Scientific final text stays harness-owned. UI is a safe text-only observation viewer, not a new scientific renderer.

**Tech Stack:** Existing FastAPI, asyncio, native JS/CSS/HTML, pytest/Node, RDKit, OpenAI-compatible transport.

## Approved design and constraints

- Separate local page and CLI, rather than scripts-only (misses browser interactions) or production-page activation (larger risk).
- Preserve historical layout, no visual redesign. Four preset page cases: chat, RDKit properties, clarification/resume and invalid SMILES. Preset cases are evaluation obligations, not replacement workflows for the agent.
- External API only via explicit runtime CLI invocation; secrets only from runtime environment and never in reports/console. Offline tests substitute the model explicitly and run real RDKit. No production service, weights, datasets, credentials or .env reads during this integration.
- Binding is 127.0.0.1, default port6012, no proxy headers; temporary SQLite state separate from production. No claim that loopback sessions constitute deployment authentication.
- Keep 40fcd2a/PR27 bridge semantics, revision4 continuation guards and current scientific evidence validation; selectively port source9312bf5, never merge historical tree wholesale.

## Task 1: Backend (parent)

Files: src/web/decision_lab.py; tests/agent/test_decision_lab.py; tests/agent/test_decision_lab_lifecycle.py.

Evidence-based compatibility addition: src/web/decision_chat.py shares a cancellation-safe I/O deadline with the lab. Deterministic Python3.10 regressions proved that wait_for could swallow parent cancellation when socket send/receive completed concurrently; no production entry changes. Shutdown cleanup also owns session-lock acquisition across repeated cancellation.

- [x] Port historical tests first; run `python -B -m pytest tests/agent/test_decision_lab.py -q -p no:cacheprovider --tb=short` and record missing-module baseline.
- [x] Port explicit factory/session/socket logic only; preserve existing test expectations where they match main contracts.
- [x] Add RED tests for rotation during active cleanup, expiry/shutdown ownership, final flush timeout, rejected/busy error send timeout and disconnected sockets; avoid arbitrary sleeps.
- [x] Fix demonstrated gaps: track/drain cancelled tasks before replacing sessions, keep shutdown accountable for all tasks; bound terminal flush/error/close sends. No queued or replayed browser commands, no access to another session's continuation.
- [x] Exercise real ASGI/WebSocket and RDKit with model doubles; tool failure/invalid input must not fabricate success or visualization.

## Task 2: Page (independent worker)

Files: src/web/static/decision_lab/{app.js,index.html,style.css}; tests/decision_lab_ui_test.js.

- [x] Port Node behavior/static tests before assets, record RED, then port assets using apply_patch.
- [x] Verify text-only rendering/XSS, stale socket callbacks, no automatic replay, start/resume disabled until complete, truncation/warnings, reconnect/session reset, bounded 200-event display.
- [x] Run `node tests/decision_lab_ui_test.js` and `node --check src/web/static/decision_lab/app.js`. Keep existing layout; clearly label isolated preset-case acceptance and clarify fixture input CCN.
- [x] Add RED/GREEN hung session-header/body tests; abort after30seconds, restore manual retry without late connection or automatic replay.

## Task 3: CLI acceptance (independent worker)

Files: scripts/run_decision_browser_lab.py; scripts/run_decision_chat_acceptance.py; tests/agent/test_decision_lab_cli.py; tests/agent/test_decision_chat_acceptance.py.

- [x] Port tests first, run RED, then adapt historical scripts to main transport and bridge. No real provider call during integration.
- [x] Browser launcher enforces HTTPS provider endpoint configuration, loopback bind, private temporary state, bounded WebSocket frames, disabled access logs/proxy headers.
- [x] CLI report must record structured scientific checks/events/status without keys/provider exception payloads. Missing config yields explicit skipped/nonzero, not passed. Invalid SMILES joins chat, batch properties and clarification coverage where missing.
- [x] Test CLI using synthetic runtime variables and model doubles, safe report paths and temporary state removal. All real API invocation remains explicit opt-in.

## Task 4: Integration, review and PR

- [x] Focused Python and Node tests, then Agent/model/fallback regressions, all Node scripts, compileall and contract; no unnecessary production mutations.
- [x] Independent SPEC then QUALITY review, add RED/GREEN regression for each reproduced issue.
- [x] Update historical-integration-status (PR27 now merged) and a scoped handoff with exact evidence and deferred real-weight/presentation/host work.
- [ ] Precise staging, diff/credential checks, focused commit and draft PR to main. Required CI must pass; numbered PR merge needs explicit user authorization.

## Completion boundary

The local acceptance interface and scripts are integrated only after review/CI/approved merge. Offline green is not external-model or real-weight validation, and does not certify public deployment. The original dirty worktree remains unchanged.
