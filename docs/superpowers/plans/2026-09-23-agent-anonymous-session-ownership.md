> 历史来源文档：以下设计、实施记录和测试数不是本轮 P05 候选的验收结果。
> 当前精确身份、失败及复测结果见协调工作树 docs/handoff/p05-session-ownership-patch-review.md。

# Agent Anonymous Session Ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make browser-issued anonymous sessions the authority for Agent run replay and Agent task access, without a login prompt or ownership claims from client metadata.

**Architecture:** A small SQLite-backed Web session service issues an opaque HttpOnly Cookie and injects a trusted session ID into HTTP/WebSocket scopes. Supervisor and SQLite run claims use that ID for owner-bound idempotency/trace checks; Agent task rows store the owner atomically and shared task routes hide other sessions' Agent projections. Unowned historical Agent records remain stored but inaccessible from the browser.

**Tech Stack:** Python 3.10+, FastAPI/Starlette, sqlite3, pytest/TestClient; no new dependency, live model, login UI, or production migration.

---

## Baseline and file responsibilities

Read `docs/superpowers/specs/2026-09-23-agent-anonymous-session-ownership-design.md`, `AGENTS.md`, and `docs/PROJECT_STANDARDS.md`. Work only in `D:/MedChat/molecular_chat_system_worktrees/delegated-session-baseline`, branch `codex/delegated-session-baseline`. The original checkout is mixed; do not touch it. This branch already contains uncommitted T02/T04/T07/T08 files; do not use `git add -A` or commit unrelated hunks. The design-only commits are `b3722a2` and `7166e49`.

Files and single responsibilities:

- Create `src/web/agent_session.py`: session repository, Cookie middleware, WebSocket Origin validation; no scientific or task logic.
- Modify `src/web/app.py`, `src/web/chat_handler.py`, `src/web/routes/agent_workflow_routes.py`: inject server-derived session ID into both Agent entry points.
- Modify `src/agent/supervisor.py`, `src/agent/orchestrators/workflow.py`, `src/agent/runtime/delegated_executor.py`, `src/agent/persistence/sqlite_store.py`, `src/agent/persistence/base.py`: scoped key lookup and owner-bound run claim; preserve result contracts.
- Modify `src/task_runtime/database.py`, `src/task_runtime/store.py`, `src/task_runtime/manager.py`, `src/task_runtime/routes.py`: private Agent task owner field and owner-aware projection reads.
- Create `tests/test_agent_session.py`, `tests/test_agent_task_ownership.py`; extend `tests/agent/test_delegated_session_lifecycle.py`, `tests/test_phase2_phase3_routes.py`, and `tests/agent/test_entrypoint_outcome_parity.py` only where behavior changes.
- Update `docs/handoff/latest.md` and a focused handoff note after verified results.

At the start of a PowerShell session set `$py = 'C:/Users/xkx52/.conda/envs/MedChat/python.exe'`; every `& $py` command below uses that interpreter. Temporary session/Agent/task SQLite files belong under pytest `tmp_path`; tests that construct the production app set `MEDCHAT_AGENT_SESSION_DB` to `tmp_path / 'sessions.sqlite'` with `monkeypatch` before construction. Do not read/write real API keys or live scientific assets. All test clients use synthetic Cookie values. Each numbered task ends with a verification checkpoint, not a code commit while overlapping T07/T08 hunks remain unseparated; exact staging and commit are deferred until the integrated branch is reviewed.

### Task 1: Session authority and Cookie boundary

- [x] **Step 1: Write red tests in `tests/test_agent_session.py`.** Test two HTTP clients receiving different nonempty `HttpOnly`, `SameSite=Lax`, `Path=/` Cookie values; same client retaining its session after re-instantiating store over the same `tmp_path` DB; expired/unknown Cookie yielding a new ID; missing/foreign `Origin` WebSocket refusal; database failure returning 503 rather than an unowned scope. Use a minimal `FastAPI` app plus `TestClient`, and only synthetic tokens.

```python
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore

def test_two_browsers_never_share_agent_session(tmp_path):
    store = AgentSessionStore(tmp_path / "sessions.sqlite")
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=store)
    async def who(request: Request):
        return {"id": request.scope["agent_session_id"]}
    app.add_api_route("/who", who)
    first, second = TestClient(app), TestClient(app)
    assert first.get("/who").json()["id"] != second.get("/who").json()["id"]
```

- [x] **Step 2: Run the red test.** `& $py -B -m pytest tests/test_agent_session.py -q -p no:cacheprovider --tb=short`; expected failure is missing `src.web.agent_session`, not a network call.
- [x] **Step 3: Implement `src/web/agent_session.py`.** Define `AgentSessionStore(db_path: Path)` with `issue() -> tuple[str, str]` (raw 32-byte-random URL-safe token and random session ID), `resolve(token: str) -> str | None`, and `touch(token: str) -> None`. Store only `sha256(token)` and timestamps in SQLite WAL, with `BEGIN IMMEDIATE` around mutation and a 90-day idle TTL. Define `AgentSessionMiddleware(app, store)` that adds `agent_session_id` to HTTP/WS scopes; on HTTP response start, set/renew the Cookie; on WS, reject invalid/missing Cookie or wrong/missing Origin before accepting. Only local HTTP development may omit `Secure`; never trust arbitrary forwarded-proto headers. An unavailable database raises a fixed 503/WS denial without leaking paths or tokens.

```python
token = secrets.token_urlsafe(32)
token_digest = hashlib.sha256(token.encode("ascii")).hexdigest()
cookie = f"medchat_agent_session={token}; Max-Age={90 * 86400}; Path=/; HttpOnly; SameSite=Lax"
```

- [x] **Step 4: Re-run the focused test.** Same command; expected all Task 1 tests pass, and DB rows contain no raw Cookie token.
- [x] **Step 5: Checkpoint.** `git diff --check`; inspect only new session files and verify no real credential or token appears in test output.

### Task 2: Owner-bound Supervisor and SQLite claims

- [x] **Step 1: Write red tests in `tests/agent/test_delegated_session_lifecycle.py`.** Run a fixture-backed delegated Supervisor with `session_id="browser-a"`, then try its trace and raw client idempotency key from `browser-b`. Assert failed/rejected without an extra tool call; assert run owner, query, status and checkpoint remain unchanged. Test same-owner retry, changed query/skill rejection, unowned historical trace rejection for a browser, and two concurrent claims with only one winner. Add a nondelegated path assertion so `start_run()` cannot silently overwrite owner.

```python
first = supervisor.run("CCO", skill_name="comprehensive_evaluation",
                       trace_id="owned-trace", session_id="browser-a")
before = store.get_run("owned-trace")
denied = supervisor.run("CCO", skill_name="comprehensive_evaluation",
                        trace_id="owned-trace", session_id="browser-b")
assert denied["status"] != "succeeded"
assert store.get_run("owned-trace")["session_id"] == before["session_id"]
assert tools["property_calculator"].calls == ["CCO"]
```

- [x] **Step 2: Run red tests.** `& $py -B -m pytest tests/agent/test_delegated_session_lifecycle.py -q -p no:cacheprovider --tb=short`; expected new cross-owner tests fail before implementation.
- [x] **Step 3: Implement the owner contract.** Add explicit `session_id: str | None = None` to `SupervisorAgent.run()` and `execute()` and propagate to `AgentContext.session_id`. Never read owner from metadata. For browser calls derive stored key as `sha256("agent-key-v1\0" + session_id + "\0" + validated_client_key)`, so the existing unique column stays usable without storing the raw client key. Both `Supervisor.run()` and `WorkflowOrchestrator._resolve_idempotent_context()` use an owner-scoped store lookup; browser-owned calls reject stores unable to implement it. In SQLite `claim_workflow_run()` compare old `session_id`, query, skill, key and expected status inside `BEGIN IMMEDIATE` before any update. Nondelegated browser-owned starts must use an owner-safe conditional claim/UPSERT and reject `rowcount == 0`; preserve the existing direct `start_run(exclusive=False)` upsert contract for local/legacy callers covered by `tests/agent/test_decision_continuation_store.py`. Reject any browser claim of an existing `NULL`-owner row. Keep trusted local Python `session_id=None` calls compatible but unable to claim a browser-owned row.

```python
if row is not None and (row["session_id"] != data.get("session_id")
                        or row["query"] != data.get("query")
                        or row["skill_name"] != data.get("skill_name")):
    return False
```

- [x] **Step 4: Re-run Task 2 and existing resume tests.** `& $py -B -m pytest tests/agent/test_delegated_session_lifecycle.py tests/agent/test_workflow_resume.py -q -p no:cacheprovider --tb=short`; expected green with unchanged non-idempotent timeout safety.
- [x] **Step 5: Checkpoint.** Inspect `git diff -- src/agent/supervisor.py src/agent/orchestrators/workflow.py src/agent/persistence/sqlite_store.py` and `git diff --check`; confirm neither `metadata` nor tool evidence includes raw Cookie/idempotency input.

### Task 3: Bind HTTP and WebSocket entry points

- [x] **Step 1: Add red entry tests.** In `tests/test_agent_session.py`, load home via `TestClient`, then connect `/ws` with its Cookie and allowed Origin; assert the session ID received by a fake Supervisor `execute()` equals the HTTP principal. Without Cookie or with foreign Origin, assert the socket is refused and the fake Agent is not called. In `tests/test_phase2_phase3_routes.py`, submit `/api/agent/workflows/run` from two clients with forged metadata `session_id` values; assert the fake Supervisor receives the real two distinct session IDs and no forged ID.

```python
assert received[0]["session_id"] != received[1]["session_id"]
assert "forged-owner" not in {item["session_id"] for item in received}
```

- [x] **Step 2: Run red tests.** `& $py -B -m pytest tests/test_agent_session.py tests/test_phase2_phase3_routes.py -q -p no:cacheprovider --tb=short`; expected failures show missing trusted-context propagation.
- [x] **Step 3: Wire entry points.** Install the session middleware once in `src/web/app.py` with a stable, repo-external default session DB and an absolute-path override. Pass `websocket.scope["agent_session_id"]` through `ChatHandler.handle_websocket()` → `_process_message()` → `_execute_agent()` to `Supervisor.execute(session_id=...)`. For direct test fakes, update signatures rather than silently omitting the owner. In `setup_agent_workflow_routes()`, remove client `user_id`/`session_id` from metadata, capture `request.scope["agent_session_id"]` before scheduling, and call `Supervisor.run(session_id=...)` from that server-captured value; missing principal fails closed for browser routes. Keep `plan` free of stored run lookup.
- [x] **Step 4: Re-run focused route, chat, and parity tests.** `& $py -B -m pytest tests/test_agent_session.py tests/test_phase2_phase3_routes.py tests/agent/test_entrypoint_outcome_parity.py -q -p no:cacheprovider --tb=short`; expected green; record any unrelated fixture compatibility updates explicitly.
- [x] **Step 5: Checkpoint.** Confirm WebSocket payloads and task payloads do not contain the Cookie token; only the internal session ID may enter trusted execution context.

### Task 4: Protect Agent task projections atomically

- [x] **Step 1: Write red tests in `tests/test_agent_task_ownership.py`.** Two independent `TestClient`s submit Agent tasks. For other browser and no/invalid Cookie, assert `GET /api/tasks/{id}`, `/events`, `POST /cancel` return 404 and `/api/tasks` omits the Agent row; owner can read, list, observe events and cancel. Seed a legacy `agent_workflow` row with `owner_session_id=NULL` and verify no browser can adopt it. Verify generic demo task behavior remains as before and filtering precedes pagination. Test both route setup with and without injected task runtime.

```python
task_id = browser_a.post("/api/agent/workflows/run", json={"query": "CCO"}).json()["data"]["task_id"]
assert browser_b.get(f"/api/tasks/{task_id}").status_code == 404
assert browser_b.get(f"/api/tasks/{task_id}/events").status_code == 404
assert browser_b.post(f"/api/tasks/{task_id}/cancel").status_code == 404
assert browser_a.get(f"/api/tasks/{task_id}").status_code == 200
```

- [x] **Step 2: Run red tests.** `& $py -B -m pytest tests/test_agent_task_ownership.py -q -p no:cacheprovider --tb=short`; expected cross-browser leakage is demonstrated before implementation.
- [x] **Step 3: Implement atomic ownership and route checks.** Add private nullable `owner_session_id TEXT` to the `tasks` migration in `src/task_runtime/database.py`. Add `owner_session_id` to `TaskStore.create()` INSERT and `TaskManager.submit()` parameter so the row and owner appear in one transaction; do not expose it from `TaskRecord.to_public_dict()`. Add store predicates `get_agent_owner(task_id)` and owner-filtered list query before `LIMIT/OFFSET`. In shared task routes, resolve Agent records through the manager store even when another runtime is installed; check owner before returning data/events or invoking cancel. For unknown or `NULL` owner, return the same 404. Runtime-only Agent records without verifiable owner are excluded; merge visible manager Agent rows and existing non-Agent runtime rows in stable `(updated_at, task_id)` order before limit. Preserve all existing non-Agent response formats.
- [x] **Step 4: Re-run task tests.** `& $py -B -m pytest tests/test_agent_task_ownership.py tests/test_phase2_phase3_routes.py tests/test_temporal_docking_routes.py -q -p no:cacheprovider --tb=short`; expected green and no Agent task leakage.
- [x] **Step 5: Checkpoint.** Inspect task DB migration on a copy of old-schema SQLite, check that owner is not serialized publicly, and run `git diff --check`.

### Task 5: Integrated regression, limits, and handoff

- [x] **Step 1: Add integrated red/green tests where gaps remain.** Cover same-owner idempotent retry, different-owner same raw key producing separate internal keys, old unowned record denial, WebSocket origin rejection, session DB outage, task runtime branch, cookie expiry and sanitized logs. Keep scientific tools as fixtures; do not mark them real-tool acceptance.
- [x] **Step 2: Run focused suite.** `& $py -B -m pytest tests/test_agent_session.py tests/test_agent_task_ownership.py tests/agent/test_delegated_session_lifecycle.py tests/agent/test_workflow_resume.py tests/test_phase2_phase3_routes.py tests/agent/test_entrypoint_outcome_parity.py -q -p no:cacheprovider --tb=short`; all new security cases must pass.
- [x] **Step 3: Run full combined regression.** `& $py -B -m pytest tests/agent tests/test_activity_family_contract.py tests/test_target_search_fallback.py tests/test_phase2_phase3_routes.py tests/test_agent_anti_hallucination_fallbacks.py tests/test_agent_platform_health_check.py -q -p no:cacheprovider --tb=short -rs`; record actual counts and every skip. Then run `& $py -B scripts/run_agent_acceptance.py --mode contract --output outputs/agent_evaluation/agent_session_contract.json`, `& $py -m compileall -q src scripts`, and `git diff --check`. Do not run `real` or use external keys.
- [x] **Step 4: Review security semantics.** Inspect every Agent run/task read, list, event, cancel and resume path for unscoped lookup; verify no Cookie token/key in logs, metadata, task input or reports. Inspect status using `git status --short` and use exact paths for any staging. Do not commit the existing mixed codebase wholesale, push, merge, deploy or enable models.
- [x] **Step 5: Write handoff.** Record actual modified paths, before/after behavior, exact command results, remaining general-task authentication risk, browser-session limitation and the requirement to place Agent/task DBs on stable paths for cross-worktree run continuity. Link it from `docs/handoff/latest.md` only after checks complete.

## Plan self-review

### Execution record (2026-09-23)

All five tasks were implemented and independently reviewed in the approved isolated
worktree. Entry/ownership regressions were placed in dedicated
`tests/test_agent_session_entrypoints.py`, `tests/agent/test_run_session_ownership.py`
and `tests/agent/test_browser_session_integration.py` instead of enlarging all of the
older test modules named in the example steps. A small `agent_session_config.py`
holds app/path wiring. The internal idempotency hash uses an unambiguous JSON tuple
instead of separator concatenation; legacy unowned direct calls keep their contract.
The checklist records equivalent verified task coverage, not literal execution of
every illustrative snippet. Review-driven fixes added Origin/cache protections,
post-lock expiry checks, authorized dynamic-continuation compatibility, durable
non-Agent event reads and bounded/sorted custom-adapter paging.

Final serial Agent regression: **3930 passed / 4 skipped**; Web/session/task
regression: **578 passed / 6 skipped**; contract: **34/34 passed**. Initial failures,
parallel-run timeouts and remaining limits are retained in
[the implementation handoff](../../handoff/agent-anonymous-session-ownership.md).
No code commit, push, PR, merge, deployment or live external-model call was made.

Coverage: session issuance/persistence and WS boundary (Task 1), owner-scoped trace/key/CAS (Task 2), both user entry points (Task 3), shared Agent task projection (Task 4), old records/anti-leak/stability/regression (Task 5). No production model use, no login popup, no migration of old records. The code implementation tasks are ordered so each new behavior is first demonstrated failing. Before claiming readiness, compare the final implementation against the approved spec and stop if any protected route lacks a trustworthy owner.
