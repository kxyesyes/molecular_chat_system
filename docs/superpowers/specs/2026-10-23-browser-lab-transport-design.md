# Browser reference lab: event-driven test transport

## Scope and approved direction

Repair the intermittent deadline failure in
`tests/agent/test_scientific_reference_browser_lab.py` by changing only its
test transport. Keep production code, dependencies, the 10-second receive
deadline, 60-frame bound, scientific acceptance, and security checks unchanged.
The shared-helper consumers in `test_scientific_reference_web_binding.py` and
`test_main_integration_acceptance.py` must migrate with it. Preserve all their
assertions, including the latter's existing 10-second total receive budget,
128-frame limit, completion/ping barrier, and 30-second child-process limit.
Continue PR #216 on `codex/fix-invalid-smiles-decision`; do not merge or deploy.
The maintainer approved this direction on 2026-10-23.

## Root-cause evidence

The failed Linux job in run 40062006183 uses Starlette 0.27.0, FastAPI 0.104.1,
AnyIO 3.7.1, HTTPX 0.25.2, and Pydantic 2.5.0. Starlette's WebSocket test
receiver repeatedly checks an empty queue and awaits `anyio.sleep(0)`.
Unlike a suspended receive, this continuously schedules work while scientific
workers are active. The local default stack instead uses Starlette 1.0.0.

An isolated installation of the CI versions reproduced two unmodified-test
timeouts. An in-memory diagnostic changing only the old test receiver to a
blocking queue wait passed, reverting it reproduced a timeout, and restoring
the blocking wait passed again. The two successful second turns took 7.015
and 6.440 seconds and each retained all 164 reference revalidations. This
diagnostic is not the implementation: no private Starlette monkeypatch or
thread-blocking receive will be committed. The Linux failure was on the second
turn; the local pinned-stack reproduction also stalled the first turn.

Repeated validation explains sensitivity to contention but is not permission
to remove checks. A separate throwaway ASCII byte-counting experiment had
variable end-to-end benefit and is excluded from this repair.

## Design

- Invoke the real ASGI application with an event-driven `asyncio.Queue`
  receive callable and a bounded outbound queue. Do not stub routes, sessions,
  model decisions, tools, binding checks, or completion messages.
- Use HTTPX `AsyncClient`/`ASGITransport` for HTTP requests, the actual issued
  cookie for WebSocket scope, the existing Origin/host, and the application's
  real lifespan. This follows the direct-ASGI pattern in `test_decision_lab.py`.
- Preserve every original assertion: home and static assets, cookie issuance,
  five generated candidates, pre-confirmation denial, confirmation/restore,
  canonical `CCN` property input, generator call count, reopening the same
  database/session, and denial to a different session.
- Maintain one absolute monotonic deadline per receive-until operation, not a
  fresh timeout per frame. Check the deadline after receives as well. Close,
  unexpected terminal, application failure, and silent timeout must fail.
- Always send disconnect and join the owned application task before leaving
  the socket and application lifespan. Allow 10 seconds for graceful cleanup,
  then cancel and join on a broken fixture; do not suppress application
  exceptions or leak tasks. Cancellation-resistant applications may extend
  this final drain: no in-process helper can both force-stop arbitrary work
  and guarantee physical settlement. The existing outer CI job timeout remains
  the containment boundary; it is not a relaxed receive deadline. Add a
  deterministic regression proving the helper waits for that drain rather
  than abandoning the task or reporting success.

## Regression and verification

Add deterministic regressions for no idle polling, unchanged total deadline,
application exceptions, disconnect/task cleanup, and real cookie/Origin
enforcement. Retain the original missing-candidate-terminal and silent-socket
tests. Establish a failing regression before switching the transport.

Run focused tests repeatedly with the CI-pinned isolated stack and the local
stack, then related reference, browser/chat, lifecycle, and security tests.
Run compileall and diff checks. Request independent review and run the existing
CI after pushing the repair. Record actual results and skipped/unavailable
checks; these offline tests are not live-model or browser automation acceptance.

## Alternatives and exclusions

A private-method Starlette patch is smaller but version-coupled; upgrading
dependencies expands scope. Neither is selected. No workflow timeout, retry,
production performance change, safety-result cache, or relaxed assertion is
part of this design. The user's original checkout remains untouched.
