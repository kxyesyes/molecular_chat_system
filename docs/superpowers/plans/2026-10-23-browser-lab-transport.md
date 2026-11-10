# Browser Lab Transport Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. The maintainer selected execution in the current session.

**Goal:** Remove test-client busy polling without changing scientific validation, authentication, assertions, or deadlines.

**Architecture:** A test-owned ASGI socket uses bounded asyncio queues and owns one application task. HTTPX calls the same real app inside its actual lifespan; issued cookies and Origin headers enter the real WebSocket middleware. Receive and cleanup paths propagate failures and join owned tasks.

**Tech Stack:** Existing Python 3.10, asyncio, pytest, HTTPX, FastAPI, Starlette; no new dependency.

---

### Task 1: Establish the failing transport regression

**Files:** Modify `tests/agent/test_scientific_reference_browser_lab.py` only.

- [ ] Instrument zero-delay AnyIO sleeps in a new regression that invokes the existing silent-socket test. The instrumentation delegates every call; it must not alter scheduling or assertions.

```python
def test_idle_transport_does_not_busy_poll(monkeypatch):
    import anyio
    original = anyio.sleep
    zero_delays = []
    async def observe(delay):
        if delay == 0:
            zero_delays.append(None)
        await original(delay)
    monkeypatch.setattr(anyio, 'sleep', observe)
    test_silent_socket_deadline_is_bounded_and_session_is_drained()
    assert not zero_delays, 'idle WebSocket transport must suspend, not poll'
```

- [ ] Run the new case with the isolated CI-pinned dependency stack. Expect failure from nonempty `zero_delays`, not an import error. Record the original end-to-end timeout separately.

### Task 2: Replace only the test transport

**Files:** Modify `tests/agent/test_scientific_reference_browser_lab.py`.

- [ ] Replace the capture middleware/Starlette WebSocket transport with a test-owned async socket. Retain bounded queues (256 frames); pass `incoming.get` and `outgoing.put_nowait` to the real ASGI app. Construct standard ASGI scope with the existing host, Origin, and actual cookie from `AsyncClient.build_request`.
- [ ] Start one application task, send `websocket.connect`, and require `websocket.accept` before exposing the socket. For every read, wait for either an outbound frame or application-task completion. Propagate application exceptions and unexpected application exit. Cancel and await each temporary queue-wait task in `finally`.
- [ ] On every socket exit, send `websocket.disconnect`, allow 10 seconds for graceful task completion, and cancel/join on cleanup failure. Cancellation-resistant work must still drain: document that only the graceful phase is locally bounded and the existing CI job timeout contains a truly stuck app. Never suppress task failures or abandon owned work. Application lifespan surrounds HTTP and WebSocket operations.
- [ ] Convert the existing socket tests to `asyncio.run` exercises, preserving every original assertion and the silent socket's 0.05-second probe. Keep the HTTP-only configuration-route test intact.
- [ ] Adapt the two shared-helper consumers: `tests/agent/test_scientific_reference_web_binding.py::test_actual_pi_generation_reference_ack_and_reopen_without_followup` and `tests/agent/test_main_integration_acceptance.py::exchange`. Keep their public synchronous scenario functions by wrapping async exercises with `asyncio.run`. Remove the obsolete capture wrapper, await the same real HTTP calls, and forward the actual issued cookie/Origin to `DeadlineSocket`. Preserve every assertion, the integration suite's 10-second total deadline/128 frames/ping barrier, and its existing child-process deadline.
- [ ] Keep receive-until's absolute monotonic deadline and 60-frame bound. Pass only remaining time to each receive, check time again after receiving, and fail immediately on close or unexpected terminal. No retries or deadline increases.

### Task 3: Cover failure and authority boundaries

**Files:** Modify `tests/agent/test_scientific_reference_browser_lab.py`.

- [ ] Test an ASGI endpoint that raises after accept: the reader must surface the exception, the application task must be done, and no queue-wait task may survive.
- [ ] Test timeout/body-exception cleanup by waiting in the endpoint for disconnect and checking its `finally` marker and the owned task's terminal state.
- [ ] Force the graceful-cleanup timeout without a real 10-second sleep; let a cancellation-resistant app wait on a test-controlled release event. Assert cleanup remains pending until release, then propagates timeout and leaves no task behind.
- [ ] Test a completion frame returned after the absolute deadline: it must be rejected even if event-loop scheduling delayed delivery of the timeout callback.
- [ ] Exercise the real lab with absent session cookie and foreign Origin; neither connection may be accepted. Do not set `agent_session_id` directly in scope.
- [ ] Assert the event-driven receive never calls `get_nowait` on an empty inbound queue and never schedules a zero-delay polling loop. Retain the red regression from Task 1.

### Task 4: Validate and deliver

- [ ] Run `python -B -m pytest tests/agent/test_scientific_reference_browser_lab.py -q --disable-warnings --tb=short` under both installed stacks; repeat the CI-stack end-to-end case in separate runs.
- [ ] Run all `tests/agent/test_scientific_reference_*.py`, related browser/chat/lifecycle coverage, `tests/test_llm_security_policy.py`, and `tests/test_runtime_boundary_contracts.py`. Record exact commands/results and any environment skips.
- [ ] Run `python -m compileall -q src scripts tests/agent/test_scientific_reference_browser_lab.py` and `git diff --check`.
- [ ] Obtain independent review of assertion parity, deadline semantics, authentication, exception propagation, and cleanup. Fix actionable findings and rerun affected cases.
- [ ] Verify only this test and task documentation changed; explicitly stage those files. Commit, push to the existing draft PR #216, and inspect all existing CI gates. Never merge or deploy.
- [ ] Record root cause, A/B evidence, final tests, commit/PR, and residual limitations in `docs/superpowers/validation/2026-10-23-browser-lab-transport.md`.
