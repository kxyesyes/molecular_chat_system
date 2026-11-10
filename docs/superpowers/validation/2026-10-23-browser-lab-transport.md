# Browser reference lab transport repair

## Diagnosis

The [failed quality run](https://github.com/kxyesyes/molecular_chat_system/actions/runs/40062006183)
reported a 10-second wait for the second turn's `complete` frame. The production
binding/reference validation code and the test were unchanged by the preceding
prompt repair.

CI pins Starlette 0.27.0 / FastAPI 0.104.1 / AnyIO 3.7.1 / HTTPX 0.25.2 /
Pydantic 2.5.0. The local default stack instead has Starlette 1.0.0 and
Pydantic 2.12.5. The old Starlette test WebSocket receiver checks an empty
queue repeatedly with `anyio.sleep(0)`, competing with scientific workers
instead of suspending until an incoming message exists.

An isolated installation of the five CI versions was made under ignored
`outputs/timeout_probe/pydantic250`; installed project environments and
requirements were not changed. This is a Windows reproduction using CI's
relevant package versions, not a claim of a local Linux reproduction.
The installed Ubuntu environment could not start; it was not repaired.

Evidence:

- Original pinned-stack browser test file: **1 failed, 4 passed**; second-turn
  `complete` deadline reproduced (full test 20.48 seconds).
- New no-idle-poll regression, before repair: **1 failed**, specifically from
  observed zero-delay sleeps while the input queue was empty.
- A/B/A diagnostic changed only the old test receive operation, in memory:
  blocking receive passed; original busy-poll receive timed out; blocking
  receive passed again. Successful second turns took **7.015 / 6.440 seconds**,
  with **164 reference revalidations each**. No safety checks were disabled.
- The committed implementation does not patch Starlette internals. It uses
  event-driven ASGI queues. A discarded ASCII validation optimization had
  variable benefit and was not included.

## Change boundary

Only three test files and task documentation change:

- `tests/agent/test_scientific_reference_browser_lab.py`: real ASGI queue
  transport, original UI/reference assertions, and transport regressions.
- `tests/agent/test_scientific_reference_web_binding.py`: adapt the existing
  generation/confirmation/reopen consumer without changing assertions.
- `tests/agent/test_main_integration_acceptance.py`: adapt the existing
  transport consumer, preserving its science assertions, completion/ping
  barrier, 10-second total receive budget, 128-frame cap, and 30-second
  child-process containment.

The browser helper keeps its 10-second absolute receive budget and 60-frame
cap; it additionally rejects completion delivered after the deadline. Real
Cookie/Origin middleware, Pi, RDKit, SQLite, source validation, and worker
settlement remain in the path. No production source, dependency version,
workflow timeout, retry, acceptance criterion, or safety-result cache changes.

## Verification record

Commands run from the isolated PR worktree using the MedChat Python runtime:

```powershell
python -B -m pytest tests/agent/test_scientific_reference_browser_lab.py -q --disable-warnings --tb=short --durations=5
python -m compileall -q src scripts tests/agent/test_scientific_reference_browser_lab.py
git diff --check
```

For the CI-pinned diagnostic process, the following selects the complete
reference suite and security boundaries without altering installed packages:

```python
import sys
from pathlib import Path
sys.path.insert(0, 'outputs/timeout_probe/pydantic250')
import pytest
targets = sorted(str(p) for p in Path('tests/agent').glob('test_scientific_reference_*.py'))
targets += ['tests/test_scientific_reference_target_context.py',
            'tests/test_llm_security_policy.py', 'tests/test_runtime_boundary_contracts.py']
raise SystemExit(pytest.main(targets + ['-q', '--disable-warnings', '--tb=short', '--durations=5']))
```

Initial repaired transport and added boundary tests: **16 passed** on both
the local default and CI-pinned stack. A later cleanup regression independently
passed on the pinned stack. Final suite and repeat results are recorded below
once complete; intermediate results are not final acceptance.

The complete reference suite initially exposed an unadapted shared-helper
consumer (**1 failed, 485 passed**). Its failure was `app.state.inbox` removal,
not a relaxed scientific assertion. The two consumers were then migrated and
the affected suite rerun.

After adapting the consumers, that suite completed with **487 passed,
11 warnings** in 152.91 seconds. The subsequent change only strengthens the
cleanup regression to require the exact injected timeout object, so its
one-second watchdog cannot falsely satisfy the assertion.

The local default-stack three-file run used:

```powershell
python -B -m pytest tests/agent/test_scientific_reference_browser_lab.py tests/agent/test_main_integration_acceptance.py tests/agent/test_scientific_reference_web_binding.py -q --disable-warnings --tb=short --durations=5
```

It produced **1 failed, 51 passed**: the second browser turn once again
exceeded 10 seconds (whole two-turn test 16.77 seconds). This failure is
retained in the record: removing busy polling does not prove the full Windows
scientific path fits every scheduling/load condition. No limit was raised,
no retry was added, and no user process was stopped. The other two adapted
consumers passed. Repetitions and the actual Linux CI are checked separately;
do not conflate a later pass with elimination of all latency risk.

Five separate CI-pinned processes then ran the original two-turn case plus
the final cancellation-resistant cleanup regression: **2 passed in each of
5 runs**. Whole two-turn durations were 16.01, 15.78, 16.25, 15.89, and 16.16
seconds; these include both turns, HTTP confirmation, reopen, and teardown,
not a single receive deadline. Every original 10-second receive deadline was
enforced. Compileall over `src`, `scripts`, and all three edited tests and
`git diff --check` passed.

Final independent review found no remaining actionable issue after the
timeout-identity assertion. Actual Linux CI results are pending at commit
creation and will be linked in PR #216; they are not inferred from these
local tests. No separate local full repository/browser/lifecycle sweep,
JavaScript execution, deployment health check, or live-model acceptance was
run for this test-only change.

## Cleanup boundary and review

Independent review identified that a cancellation request cannot force
physical settlement of cancellation-resistant application work. The helper
allows 10 seconds for graceful disconnect, then cancels and **continues to
drain** rather than leaving an orphan or claiming success. Only the graceful
phase is locally bounded; a truly stuck application remains subject to the
existing outer CI job limit. The integration acceptance consumer additionally
retains its existing 30-second process deadline.

A deterministic regression forces the graceful timeout, holds a cancellation-
resistant app on a release event, proves cleanup stays pending, then releases
it and checks timeout propagation and no leaked tasks. No production worker
settlement rule was changed.

These are offline application/transport tests, not browser automation or live
model acceptance. No paid model calls, merge, or deployment were performed.
