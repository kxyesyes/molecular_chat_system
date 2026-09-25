# Owned-loop scheduling test isolation

## Scope and evidence

Independent test-only branch from landed A2 `ed284b7`. This is a bounded verification repair needed by remaining package 7, not a production ownership or model change. The user delegates recommended choices and gated merges through package 8; no deployment.

During ordinary-chat policy review the unchanged existing owned-loop test failed its first five-second tool-entry barrier twice (502 passed/1 failed; later 587 passed/1 failed). A corrected temporary instrumented run passed once. Historical failure causes remain UNKNOWN. Source inspection proves a permitted schedule: the inner adapter Future can still be pending when its real 0.03-second wait expires; successful cancellation then prevents tool entry. The original test assumes entry before timeout without enforcing that setup. Its outer workflow uses the remaining loop budget, not that same 0.03 seconds.

An earlier diagnostic hid `worker_owner` from signature introspection and must not be used as production failure evidence. Correcting it preserved the signature and physical drain/join order. Neither diagnostic supplies unique attribution of the historical failures.

## Requirements

1. No production changes. Limit edits to `tests/agent/test_decision_loop.py`, an optional small helper in `tests/agent/test_worker_ownership.py`, and this evidence document.
2. Reproduce the original test's unsupported scheduling assumption before repairing it, using a bounded real-thread schedule. No fabricated Future, timeout, scientific result or success.
3. For the running-worker lifetime branch, control inner submit-to-entry ordering before returning the original Future to the adapter. Preserve the actual 0.03-second `Future.result` timeout, existing repeated cancellation, actual worker-exit and join barriers, one model/tool invocation, failed outcome, `TOOL_TIMEOUT`, retained ownership and final zero roots.
4. Add a separate pending-before-start branch using a real inner executor initializer barrier. Assert actual timeout and Future cancellation, zero tool calls, no terminal while the real thread/join is held, then release and physically join; outcome remains failed `TOOL_TIMEOUT` with settled owner and zero roots. Cover cancellation consistently when applicable.
5. Every path must release test barriers and physically join real threads in `finally`, including failed assertions. Use bounded waits as deadlock guards, not production deadline changes. Do not fake wall-clock performance guarantees or remove existing scientific/error/lifecycle assertions.
6. Avoid broad helper changes affecting unrelated tests. Do not modify global time functions or shared production adapter instances.
7. Preserve the original failures and distinguish controlled reproduction from unique historical attribution. Do not use repeated passing runs to erase failures.

## Execution and gates

- Read local AGENTS and relevant test/runtime paths. Establish RED from a controlled schedule, implement minimal test-only split, and record exact commands/results.
- Use only the ignored `scratch/owned_loop_offline_runner.py`, copied from the already-reviewed ordinary offline runner with only the repo constant changed. Run `C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/owned_loop_offline_runner.py` with explicit test paths or node IDs. It clears inherited configuration, redirects stores/config to a temporary directory, blocks networking and cleans up. No credentials, real models or production assets.
- Focused scope: the changed loop test nodes, the complete `test_decision_loop.py`, `test_worker_ownership.py`, and `test_decision_protocol_recovery.py` modules.
- Serialize heavy tests. Independent SPEC review then QUALITY review; all findings must close. Explicit staging and a local commit only after parent review. Publication/CI8/merge handled separately by parent; not implementation authority for the worker.
- The ordinary policy remains separately frozen/unpublished. This branch does not complete ordinary output gating, continuation budgets, Web integration, B/C or final real acceptance.

## Results

### Implementation and frozen scope

Implemented on `codex/owned-loop-scheduling-test`, unchanged HEAD
`67f1efb33423af678d79e54479106f333f3f5fd6`; local `origin/main` remains
`ed284b7baed2024d10de80583d524c828683585a`. Only
`tests/agent/test_decision_loop.py` and this results section changed. The optional
`test_worker_ownership.py` helper was reused without changes.

- Running case: a test-local subclass of the instrumented real inner executor
  waits at most the existing five-second guard for tool entry before returning
  the original Future to the adapter. No Future methods or clocks are replaced;
  the adapter still executes its real `Future.result(timeout=0.03)`. The outer
  workflow still uses the remaining loop budget. Tool invocation is counted at
  entry; its eventual discarded return is an explicit unavailable lifecycle
  fixture, not fabricated scientific data.
- Pending case: a real inner executor initializer blocks before the queued
  callable can run. After outer Future completion, the test checks that the
  original inner Future is done/cancelled and the tool was never entered. It
  first checks retained ownership with the test join gate held, then opens that
  gate and checks again while real `shutdown(wait=True)` is blocked by the live
  initializer thread. Only releasing the initializer permits terminal settlement.
- Both cases exercise no cancellation and three successive cancellation requests.
  While held they assert an unfinished task, running persisted status, no terminal
  events and one pending root. They preserve actual exit/join checks, one model
  call, one/zero tool calls respectively, unsuccessful outcomes, failed persisted
  status (cancelled in cancellation variants), the adapter's exact
  `Tool timed out after 0.03 seconds` / `TOOL_TIMEOUT`, and settled/zero roots.
- Each `finally` releases every test barrier, gathers the loop task and, in a
  nested `finally`, calls the real base executor shutdown and verifies no owned
  executor thread remains alive. The deliberate RED assertion also exercised
  this cleanup path. Five-second waits are deadlock guards, not widened production
  deadlines or timing/performance assertions.

### RED and GREEN ledger

All commands below ran from the task worktree using the unchanged reviewed
offline launcher. There were exactly three test invocations, serialized; no blind
reruns, direct pytest launches, project config loads or network/service calls.
Durations below are pytest-reported suite durations, not performance evidence.

1. **RED, expected:** `1 failed in 22.96s`, exit 1, process session `76369` ended.
   The temporary version of the pending test verified a real cancelled/done inner
   Future and zero tool calls, then deliberately retained the old setup assumption
   with `assert entered.is_set(), 'unsupported entry-before-timeout assumption'`.
   That exact assertion failed, not a timeout stub or signature/ownership error.
   Cleanup joined all created executors even on this assertion failure.
   The temporary assertion was discarded, not xfailed or skipped.

   ```powershell
   C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/owned_loop_offline_runner.py 'tests/agent/test_decision_loop.py::test_owned_loop_pending_worker_has_no_terminal_until_join[False]'
   ```

2. **Focused GREEN:** `4 passed in 6.16s`, exit 0; launcher completed in the
   initial command response (no retained process session).

   ```powershell
   C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/owned_loop_offline_runner.py tests/agent/test_decision_loop.py::test_owned_loop_has_no_terminal_until_nested_workers_join tests/agent/test_decision_loop.py::test_owned_loop_pending_worker_has_no_terminal_until_join
   ```

3. **Complete requested regression, once on the same GREEN code revision:**
   `128 passed in 45.30s`, exit 0, process session `11506` ended. No additional
   failures, skips or warnings were reported by these test invocations.

   ```powershell
   C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/owned_loop_offline_runner.py tests/agent/test_decision_loop.py tests/agent/test_worker_ownership.py tests/agent/test_decision_protocol_recovery.py
   ```

The two historical entry-barrier failures remain **UNKNOWN in cause** (earlier
502 passed/1 failed and 587 passed/1 failed, durations not supplied). The earlier
diagnostic that hid the `run` signature and omitted `worker_owner` remains invalid
evidence; the corrected single diagnostic pass neither fixes the test nor uniquely
explains those failures. This controlled RED proves only that the unsupported
entry-before-timeout assumption can fail under a valid schedule. The GREEN runs
verify the bounded test repair, not production performance or real model acceptance.

### Revision identifiers and final checks

SHA-256 values (file bytes):

| File / revision | SHA-256 |
| --- | --- |
| `test_decision_loop.py`, discarded RED version | `080882984EC6092AB756C40DE3F3C368798774D9AFE2A5A358A8CF63474B8BCC` |
| `test_decision_loop.py`, frozen GREEN version | `FFCF18EEE29E35437B88483E06DD9127849E157743C84180ADE87DEE22CA0AA4` |
| `test_worker_ownership.py`, unchanged | `E8B3D27DB2FBA059A8EB1C1F217178950BE64258B88BA53E6670995AAB755F42` |
| `test_decision_protocol_recovery.py`, unchanged | `EC1AE90E566F27AF457BACE41125D303028119FA53DCC539028EF8CF30BC3F93` |
| `scratch/owned_loop_offline_runner.py`, unchanged / ignored | `D6ABFB330F212C98684EF6B724CDCAC0B1CB3009B8DB489753DF0D498F6A40AB` |

`git diff --check` passed. `git diff --cached --name-only` was empty. A read-only
Python-process inventory after regression found zero processes matching this
worktree or its launcher; both retained command sessions had returned their exit
codes. The exclusive heavy-test slot is released. One initial read-only source
search used the nonexistent `src/agent/runtime/decision_loop.py` path and returned
exit 1; inspection was corrected to `src/agent/harness/decision_loop.py` before RED.

No production, config, environment file, key, model, asset, service or shared
helper edits; no commit, push, network operation or PR. General `compileall`,
health checks and broader acceptance were not run because this task explicitly
permits only the safe launcher and selected test scope. No claim of completion
for Task 6 integration or other package work. Implementation and evidence are
frozen for the parent's independent **SPEC, then QUALITY** reviews; neither review
has been claimed complete here.

### Independent review and parent verification

Independent SOURCE/SPEC and then SOURCE/QUALITY reviews approved the exact
frozen test SHA above without P1/P2 findings. Both were read-only reviews, not
independent test runs; production and the shared helper remain unchanged.

After the exclusive heavy-test slot was released, the parent independently ran
the two changed parameterized nodes with the exact focused command above:
**4 passed in 4.20s, exit 0**, direct completion `4a49b2`. This adds execution
evidence for both schedules/cancellation states; it is not a new full-suite run.
The implementation's 128-pass three-module regression and all historical
failures remain separately recorded. No real provider/model was called.

Local review and focused verification gates are now satisfied. Publication,
all required CI checks and reviewed/merged tree equality are still required
before squash merge. This branch changes tests only and does not activate
ordinary semantic admission or complete package 7/8.
