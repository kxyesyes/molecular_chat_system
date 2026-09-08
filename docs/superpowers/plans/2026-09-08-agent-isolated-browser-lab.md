# Isolated browser lab implementation plan

> Implement with test-driven-development and scoped parallel workers; user approved the loopback-only temporary-session design. No production auth or default app changes.

**Goal:** Exercise the existing decision ChatHandler through a real browser/WebSocket with server-bound ownership and cancellation.

**Architecture:** A separate FastAPI app, launched only by an explicit CLI on 127.0.0.1, serves a small plain-JS acceptance page. HttpOnly/SameSite=Strict opaque cookies bind expiring in-memory sessions; exact Host/Origin checks reject cross-site requests. The server owns fixed case prompts, request kind, tool allowlists, requirements, traces and continuation contexts. Only a case ID or continuation ID plus clarified text arrives from the browser. This fixture-driven entry tests model decisions; it is not a new intent router, scientific workflow or production login system.

**Stack:** Existing FastAPI/HTTPX, ModelDecisionLoop/ChatHandler/SQLiteAgentStateStore, RDKit, vanilla HTML/JS, pytest/TestClient, Node tests, real browser.

## Scope

Base 242d497 in scientific-assets worktree; original project dirty changes untouched. Default lab port 6012; only loopback and exact matching Origin/Host; no proxy header trust. Sessions expire after 30 minutes with a bounded count; one active WebSocket per session. Continuations belong to their session; no browser-defined user/trace/tool permissions. No API key UI, admin prompt, external CDN, generated molecule cards or model-authored HTML rendering.

Cases: chat (no tools); CCO+CCN properties; missing input then CCN continuation; invalid SMILES rejection. Scientific tools limited to real PropertyCalculator. Scientific values rendered from existing structured result, not regenerated. UI distinguishes waiting/complete/failed/disconnected, stops busy state and does not claim a disconnected server tool has physically stopped.

## Steps

- [x] Server RED: exact origin/host, no cookie, session expiry/limit, strict input and rejected forged identity/permissions, per-session busy guard, same-owner continuation, cross-session refusal, disconnect cancellation.
- [x] Implement `src/web/decision_lab.py` as explicit standalone app factory; reuse existing bridge and loop. Parallel UI worker owns `src/web/static/decision_lab/` and `tests/decision_lab_ui_test.js` with safe text rendering and socket lifecycle tests.
- [x] Implement `scripts/run_decision_browser_lab.py`: runtime env only, explicit native/json mode, loopback-only CLI, temporary DB and bounded graceful cleanup, no production app import or .env load.
- [x] Run focused server/CLI/Node tests; then read-only independent code/security review and correct reproduced issues using TDD.
- [x] Start the isolated server with temporary runtime credentials, open actual browser, test chat/properties/clarification/invalid input/connection close. Capture only nonsecret UI state and report evidence; do not equate MockTransport tests with actual API success.
- [x] Run full Agent/model and related regressions, contract, compileall, Node syntax and diff checks. Record real failures or missing browser capabilities honestly.
- [x] Stop lab, clean temporary state, write handoff. No production restart, permissions changes, main merge or cumulative-history PR. Exact-stage and task-branch commit are the final repository closeout.

Results and limitations: [browser lab handoff](../../handoff/agent-isolated-browser-lab.md). Final Python regression: 2038 passed / 1 optional performance skip; lab server/CLI 34 passed; UI 27 passed. Live native DeepSeek/RDKit browser checks and real cross-session protocol checks completed. Last send-side cleanup fix verified through deterministic fault injection rather than claiming a reproduced real provider failure.

## Wire contract for isolated UI

Prefix `/decision-lab/`. GET page/assets are local public resources. POST `session` establishes/replaces the cookie and responds `{"success":true}`; no session identity or credentials returned. WebSocket `ws` requires same Origin and cookie. Outbound JSON: `{"action":"start","case_id":"chat|properties|clarify|invalid"}` or `{"action":"resume","continuation_id":"...","query":"SMILES: CCN"}`. Unknown/extra fields fail closed.

Inbound bridge messages remain `agent_event`, `agent_result`, `complete`; rejected requests use `error` with a fixed code/message. Page renders dynamic text with textContent only. It displays trace, tool names, warnings and structured data without executing model HTML. A reconnect does not replay work. Reset session clears any client continuation; copying another session's continuation fails server ownership checks.
