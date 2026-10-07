# Security Hardening Phase 1 Implementation Plan
> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish one security boundary for browser-triggered computation so task data, model configuration, external requests, persisted artifacts, and rendered results cannot cross users or silently downgrade failures.

**Architecture:** Reuse the existing browser session identity at the application boundary. Add a small shared security policy layer for ownership, outbound URL validation, strict message validation, safe subprocess environments, and safe persistence. Domain routers will receive the authenticated session ID and pass it to task stores/adapters; public projections will never resolve a task before ownership is checked. The first implementation slice covers task ownership and its regression tests, then the same boundary is applied to model configuration and sensitive task families.

**Tech Stack:** FastAPI/Starlette, Pydantic 2, SQLite, Python 3.10+, native browser JavaScript, pytest/pytest-asyncio, Node.js static tests.

**Implementation status (2026-10-07):** Tasks 1–8 have been implemented in
scoped commits on the working branch and their focused regression suites pass.
The full parametrized suite is intentionally not claimed green yet; the
remaining verification items are recorded below.

---

## Task 1: Preserve the current baseline and inventory all task entry points

**Files:** `src/task_runtime/routes.py`, `src/task_runtime/store.py`, `src/task_runtime/manager.py`, `src/web/app.py`, `src/web/agent_session.py`, relevant `src/web/routes/*`, `tests/`.

- [x] Record the current branch/status and keep the pre-existing untracked manifest untouched.
- [x] Enumerate every route that creates, lists, reads, streams, cancels, reports, deletes, or downloads docking, reverse-target, activity, ADMET, and Agent tasks.
- [x] Identify the durable owner field and all adapter paths; document gaps in the task store tests before changing code.
- [x] Run focused existing task/session tests as the baseline.

## Task 2: Bind every task family to the browser session and enforce ownership (TDD)

**Files:** `src/task_runtime/models.py`, `src/task_runtime/store.py`, `src/task_runtime/manager.py`, `src/task_runtime/routes.py`, domain routers under `src/web/routes/`, `src/web/app.py`, plus focused tests.

- [x] Add failing tests proving anonymous requests cannot read/cancel another session's task, including task detail, durable events, reports/artifacts, delete, and download paths.
- [x] Add failing tests proving list results contain only the current session's records and that an unknown/foreign ID has a stable not-found response.
- [x] Add an explicit owner/session field to new task records and a migration-safe store query; preserve existing records as ownerless and deny them from browser access unless an explicit local-admin policy applies.
- [x] Thread the authenticated session ID through all creation and lookup paths, including adapter-backed runtimes, without trusting a client-supplied owner field.
- [x] Add equivalent ownership checks to docking, reverse-target, activity, ADMET, and Agent workflow projections; reports and files must resolve from an owned task record, never from a raw path or job ID.
- [x] Verify the focused tests and the existing task-runtime regression suite.

## Task 3: Enforce one outbound URL policy for LLM and downloads (TDD)

**Files:** new `src/web/security/url_policy.py` (or the existing shared security module), `src/web/app.py`, LLM config/model adapters, target/docking download routes, tests.

- [x] Add tests for allowed trusted hosts, rejected localhost/loopback/private/link-local/metadata addresses, credentials in URLs, non-HTTP schemes, ports, and arbitrary redirects.
- [x] Implement an allowlist of exact configured provider hosts, reject IP literals and unsafe resolved addresses, disable automatic redirects, and re-check every resolved destination to reduce DNS-rebinding risk.
- [x] Validate `base_url` before persistence and before every request; require the same policy for remote structure/download URLs.
- [x] Protect LLM configuration mutation/test routes with the same session boundary and add size/rate limits.
- [x] Add regression tests that prove rejected URLs never reach the HTTP client.

## Task 4: Strict WebSocket and API input contracts

**Files:** `src/web/chat_handler.py`, `src/web/decision_runtime.py`, `src/web/agent_session.py`, `src/web/api_response.py`, relevant schemas/tests.

- [x] Add failing tests for non-object messages, oversized payloads, unknown commands, invalid IDs, and string booleans such as `"false"`.
- [x] Use strict Pydantic/input checks at the receive boundary, cap message and field sizes, and return structured errors without echoing unsafe content.
- [x] Preserve valid legacy message shapes through one compatibility adapter and add Node/browser regression coverage where appropriate.

## Task 5: Make tool outcomes and task states lossless

**Files:** `src/agent/`, `src/task_runtime/`, ADMET/activity/docking/reverse-target adapters and routers, frontend task panels, tests.

- [x] Add contract tests for success, partial, failed, rejected, canceled, timeout, warnings, evidence, and quality fields.
- [x] Route every tool through the shared adapter/ToolResult contract; remove string truthiness checks and map provider errors without losing warnings or provenance.
- [x] Persist partial as partial, never succeeded; make completion events durable and idempotent so refresh/reconnect cannot miss the terminal state.
- [x] Add model/data/input/source evidence to scientific results without fabricating values.

## Task 6: Stop timeout and subprocess leaks

**Files:** ADMET, reverse-target 3D, docking batch/runtime modules, subprocess helpers, tests.

- [x] Add tests that distinguish coroutine cancellation from actual worker/process termination.
- [x] Use isolated subprocesses or killable worker processes for untrusted/long native work; enforce per-item and overall deadlines, bounded queues, cancellation, and concurrency limits.
- [x] Pass a minimal allowlisted environment to Vina/ADFRsuite/Meeko/ADMET subprocesses and prove secrets are absent in child environments.

## Task 7: Validate scientific outputs and cache integrity

**Files:** activity rankers, ADMET validators, docking parsers/exporters, target/reverse-target/pharm3d cache modules, tests.

- [x] Reject conflicting activity fields and non-finite/out-of-range qED instead of clamping invalid values.
- [x] Require model/data/input/source provenance and explicit unavailable/stale states.
- [x] Validate cache hash, format, size, version, species, shape, dtype, alignment, and fingerprint before use; stale/unavailable structures cannot enter docking.
- [x] Preserve stable target ID plus organism/taxon, 2D fallbacks, and explicit failed/partial batch states.

## Task 8: Remove dangerous rendering and harden deployment defaults

**Files:** `src/web/static/js/`, templates, deployment/nginx/systemd configuration, serialization loaders, tests.

- [x] Replace user/remote-data `innerHTML` and unsafe URL/style/SVG insertion with text/DOM APIs and explicit allowlists.
- [x] Replace `eval`, unsafe pickle, and permissive `torch.load`/NumPy pickle paths with safe formats and integrity checks.
- [x] Disable docs/OpenAPI in production, add HTTPS/WSS/security headers/body/rate limits, and enforce database/result directory permissions and retention.
- [x] Add security-focused static tests and run the focused regression and compile checks; the full suite and strict health check remain environment-gated below.

## Verification gates

- [x] No anonymous or cross-session task read/cancel/report/download succeeds.
- [x] No rejected URL reaches an outbound HTTP client; internal/private/metadata targets are blocked.
- [x] Partial, stale, unavailable, and failed states remain explicit end to end.
- [x] Scientific outputs retain traceable input/model/data/source evidence.
- [x] No secret is written to source, logs, reports, browser storage, or child process environments.
- [x] Report exact files, commands, results, branch, commit, and remaining risks; do not merge to `main` without explicit PR authorization and passing quality gates.

## Remaining verification items

- The full parametrized suite collected 22,042 tests; a long rerun was interrupted after 2,610 passed and 5 skipped. Focused suites remain green.
- `scripts/health_check.py --strict` reports 22/23 checks because this checkout has no registered activity model; two stale registry metadata files are skipped. No model was fabricated or registered to make the check pass.
