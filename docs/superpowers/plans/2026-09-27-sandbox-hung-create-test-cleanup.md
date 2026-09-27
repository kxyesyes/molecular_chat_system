# Sandbox Hung-Create Test Cleanup — Design and TDD Plan

> **For agentic workers:** Use executing-plans and test-driven-development after parent SOURCE review and explicit authoring/execution releases. This document is a review proposal, not permission to implement or run.

**Goal:** Make the existing hung-create test clean up its cancellation-resistant fixture on failure, while preserving the first error and any cleanup error.
**Architecture:** Extract only the existing test-local client and scenario into private helpers in the same test module. Add unconditional release/real shutdown and deterministic failure-path regressions whose outer controller rescues the old broken helper before asserting.
**Tech Stack:** Python 3.10-compatible exception chaining, asyncio, pytest, existing real BrokerStore/SQLite and FakeSandboxClient.

## 1. Authority, baseline and evidence

Current permission: create this document only; no source/tests/imports/Python/runner/network/commit/push. Worktree `D:/MedChat/molecular_chat_system_worktrees/sandbox-hung-create-test-cleanup`, branch `codex/sandbox-hung-create-test-cleanup`, clean initial HEAD `c612c9873a04823c936d710861bf890ad30b844a`. Publication's frozen worktree is outside scope.

Read AGENTS/PROJECT_STANDARDS and brainstorming/writing-plans. User requested one combined concise design/plan; no separate spec or commit. Parent reviews this proposal before authoring.

Read-only source pins, equal to PR98 head `3e5e2195c0e4910295283b3822b9e1609db7aa85`:

| Path | Git blob |
|---|---|
| `tests/sandbox_broker/test_service.py` | `dc8cb531e786c6266d947d056a41f9f220300987` |
| `src/sandbox_broker/service.py` | `c17bf7b52a534fc07d937848fffa30c3dccb2502` |
| `src/sandbox_broker/store.py` | `9b7c26f573aeba2e8f5c40141ba01932af12ccc8` |
| `tests/sandbox_broker/controlled_clock.py` | `a432be15cb62b1d37f0b1ea5aed7f7ce3333729e` |

Parent-provided CI evidence: run36282797393/job108517744221, one failed/1972 passed/four skipped/one warning,109.86s. Failure block363a73 shows first `asyncio.TimeoutError` at test3584's `wait_terminal` observer, then `asyncio.run` → `_cancel_all_tasks` → gather → selector.poll → pytest timeout beyond60s. The fixture suppresses repeated cancellation; release at3590 is success-only. **Failure-cleanup leakage is confirmed; the first .5s timeout's cause remains UNKNOWN.** No evidence attributes it to ASCII.

Service keeps ownership of late create (2018–2123), persists cleanup/terminal state before notification (1762–1846,1497–1506), and stop shields shared shutdown (754–885). Store calls are synchronous. These facts describe possible observation stages, not a diagnosis of the first timeout.

## 2. Design decision and preserved boundaries

Considered:
1. **Recommended:** minimal helper extraction plus explicit failure cleanup and outer rescue regression. Exercises the same lifecycle and preserves observable pre-rescue failure.
2. Inline finally alone: smaller diff, but does not establish deterministic first-error/cleanup-error regression.
3. Replace wall-clock test with ControlledClock: tests a different timing contract; excluded.

Later implementation may edit only `tests/sandbox_broker/test_service.py` and this plan. No production, clock helper, CI or runner changes.

Preserve the original test name, real service/store, cancellation-resistant create loop, and all original assertions: FAILED, cleanup failed, remote-auto-expiry warning, one create, one destroy, exact destroyed sandbox id, retained ownership before release, StopIncomplete before release, no leaked broker tasks. Preserve **.03 create / .03 destroy / .5 terminal observer / .05 incomplete stop / .5 final stop** exactly. Do not pre-wait terminal notification, change deadline origin, add sleeps to pass the observer, convert a timeout into success, or remove a science/ownership/destroy/leak assertion.

Minimal extraction:
- Move `CancellationResistantCreate` to private `_HungCreateClient` in this module, with unchanged create algorithm. Record `asyncio.current_task()` as `create_task` on entry only for test-side rescue ownership checks.
- Extract `_run_hung_create_case(service, sdk, config, *, after_started=None)`. The hook is synchronous, called immediately after the existing `await sdk.started.wait()`; default None adds no await or notification barrier.
- Existing test constructs the same service/client and invokes the helper via asyncio.run; original post-run assertions remain.
- First perform a behavior-preserving extraction **without** adding finally, so regression RED exercises the actual missing-cleanup behavior rather than absent APIs.
- GREEN moves success-path release + final stop into unconditional finally, after all pre-release assertions. The normal path performs final stop once; leak snapshot remains after shutdown. Early failure skips only assertions it never reached; it must not skip cleanup.

Exception rule: cleanup must not replace the first exception. Catch BaseException only in this test helper to preserve even cancellation, never in production, and always re-raise. Save the actual exception object and traceback. If both body and cleanup fail, re-raise the first object with its original traceback and explicit cleanup cause; if only cleanup fails, raise that cleanup error. Do not stringify, suppress, xfail or return exceptions as successful scenario results.

Proposed helper control flow (the existing outer assertions remain unchanged):
```python
async def _run_hung_create_case(service, sdk, config, *, after_started=None):
    service._create_hard_timeout_seconds = 0.03
    service._destroy_hard_timeout_seconds = 0.03
    first = first_tb = cleanup_error = None
    try:
        await service.start()
        job = await service.submit(_prepared(config), "idem-hung-create")
        await sdk.started.wait()
        if after_started is not None:
            after_started()
        terminal = await asyncio.wait_for(
            service.wait_terminal(job.job_id), timeout=0.5
        )
        with pytest.raises(StopIncomplete, match="shutdown is incomplete"):
            await service.stop(timeout=0.05)
        assert service._create_tasks
    except BaseException as exc:
        first, first_tb = exc, exc.__traceback__
    finally:
        sdk.release_create.set()
        try:
            await service.stop(timeout=0.5)
            await asyncio.sleep(0)
        except BaseException as exc:
            cleanup_error = exc
    if first is not None:
        if cleanup_error is not None:
            raise first.with_traceback(first_tb) from cleanup_error
        raise first.with_traceback(first_tb)
    if cleanup_error is not None:
        raise cleanup_error
    leaked = [
        task.get_name()
        for task in asyncio.all_tasks()
        if task is not asyncio.current_task()
        and task.get_name().startswith("sandbox-broker-")
        and not task.done()
    ]
    return terminal, sdk, leaked
```

The authoring review must compare the original body at3557–3609 and every preserved assertion with the pinned blob. This code is a design proposal in documentation, not an implemented helper.

## 3. Safe, genuinely behavioral RED

New regression: `test_hung_create_failure_cleanup_preserves_first_error`, parameter `cleanup_fault=False/True`. Use a distinct preconstructed `AssertionError("injected_after_create_started")` raised by the synchronous hook after sdk.started, not a fabricated CI TimeoutError. This proves failure cleanup only, not the origin of the CI timeout.

Run the helper inside an **outer controller coroutine**, not directly as asyncio.run's top-level coroutine. The outer controller catches the actual exception and snapshots, before any rescue:
- exception identity, original injection traceback frame and explicit cause;
- release flag; sdk.create_task completion;
- create/destroy counts and ids, run_count;
- unfinished named broker tasks and service ownership dictionaries.

Then its unconditional finally releases the fixture, invokes the saved **real bound stop method** with timeout=.5 (bypassing any test-side fault wrapper), and yields one loop turn. Assertions use the immutable **pre-rescue** snapshot; rescue must never make a defective helper appear correct. After rescue assert actual create task done, destroy once/exact id, zero scientific run calls, empty ownership dictionaries and no unfinished broker tasks. Record cleanup/rescue failure separately without erasing the first exception.

For cleanup_fault=True, monkeypatch only this service instance's stop method with a wrapper that **awaits the saved real stop first**, then raises a distinct preconstructed `RuntimeError("injected_cleanup_after_real_drain")`. This gives a real drained worker plus a deterministic cleanup error; it does not simulate failed physical cleanup. Keep actual real-drain failures visible separately.

Old helper expected RED: hook exception is correctly caught, but pre-rescue release remains false and create remains owned/pending; assertions fail after the outer controller has rescued it. With cleanup_fault=True, the old helper also never executes cleanup, so the explicit cause is missing. Accept only these call-phase behavioral failures, not import/setup/timeout errors.

Safety invariants:
- Rescue ownership is installed before service.start; even hook/setup/assertion failures release the only cancellation-resistant wait.
- Never let this intentionally broken helper escape to asyncio.run before rescue.
- Do not await an unreleased resistant task, or rely on pytest60s/CI termination to clean it.
- If the first bounded real-stop rescue fails, preserve it, keep release set, cancel only captured scenario-owned tasks and use bounded `asyncio.wait(..., timeout=.5)` to collect their completion; no unbounded gather. Report any still-pending tasks as a separate safety failure and stop this validation batch. With release set, the fixture's loop no longer resists cancellation indefinitely.
- No global all-task cancellation, detached worker, swallowed cleanup fault or background rescue timer. A runner crash/timeout is not acceptable RED evidence.

Add `test_hung_create_cleanup_error_without_primary_is_visible`: run the original body with no injected first error and the same post-real-drain stop wrapper; expect the exact cleanup sentinel to propagate, with release/task/destroy/leak checks. It can already pass on the extracted old helper; report that control honestly, not as missing-behavior RED.

## 4. TDD sequence and review gates

- [ ] Parent SOURCE-approve this design before test authoring. No implementation or execution permission is implied.
- [ ] Author only the minimal behavior-preserving extraction and the two regression functions above (three parameterized cases). Source-review the safety controller and exact unchanged timeouts/assertions before any run.
- [ ] With explicit single-slot grant, run only the two new nodes once. Expected: two behavioral cleanup RED cases; cleanup-only control may pass. Keep full call-phase assertions, warnings, true handle/terminal and before/after hashes. Rescue must finish inside the run. Stop at terminal, release slot, no auto-retry.
- [ ] Parent accepts real RED, then separately releases helper-only finally/exception preservation. No service/store fix.
- [ ] With another explicit slot grant, run the original hung-create node followed by both new nodes, exactly once. Require all original gates and both exception identities/tracebacks/cleanup evidence. If original .5s fails again, retain that failure and UNKNOWN root cause; successful cleanup is not a GREEN claim.
- [ ] Only after review and separate grant, use the source-selected nearby controls below. No blind full-suite or CI rerun.
- [ ] Independent SOURCE/fresh QUALITY and publication remain parent-controlled; do not commit/push under this plan's present authority.

Future pytest argument sets (commands are documentation only; use parent-approved environment/entry point when released, no runner creation implied):

RED:
```text
tests/sandbox_broker/test_service.py::test_hung_create_failure_cleanup_preserves_first_error
tests/sandbox_broker/test_service.py::test_hung_create_cleanup_error_without_primary_is_visible
```

GREEN adds first:
```text
tests/sandbox_broker/test_service.py::test_hung_create_hits_hard_deadline_and_never_claims_cleanup_success
```

Nearby existing controls, in order:
```text
tests/sandbox_broker/test_service.py::test_late_cancellation_resistant_create_is_owned_and_cleaned_before_stop
tests/sandbox_broker/test_service.py::test_hung_shutdown_retains_real_sqlite_state_with_delayed_storage
tests/sandbox_broker/test_service.py::test_hung_run_and_destroy_keep_shutdown_pending_after_hard_deadlines
```

Capture before/after test/service/store/controlled_clock/plan hashes; source files must stay pinned, and document-only follow-up gets a new plan hash. New node names are proposed, not claimed to exist or collect now. Existing nodes were verified by source.

## 5. Diagnostics, deferral and completion criterion

At most test-side in-memory bounded diagnostics: first/cleanup exception type and traceback location, fixed lifecycle timestamps, captured task name/done state, ownership counts and the already-read job status. Never dump credentials/payloads, add polling or a new blocking SQLite read just to diagnose timeout. No diagnostics may suppress a failure.

ControlledClock comparison and first-.5s timing investigation are separate follow-up work, only if newly authorized; do not import/use the clock in this fix. The existing clock executes real timers but intentionally excludes synchronous SQLite wall time, so it cannot replace the wall-clock assertion.

Success means the original unchanged behavioral gates pass, injected early failure exits with the same first exception after real cleanup, a secondary cleanup error remains observable, and no resistant task survives the scenario. It does **not** establish the original CI timeout's root cause, fix ASCII performance, or claim CI/fresh QUALITY success. No tests were run while drafting this document.

## 6. SOURCE-approved design; RED test preparation only

Parent reports independent Lovelace SOURCE READY/no blockers for design SHA256 `CA2079C652CC6291BA3D1F8B31094E89B261EACFDB460A3658B7B826018F49F3`. Parent then released only test-source preparation plus ownplan. Current phase does **not** implement section 2's finally/exception fix and has **no execution slot**; Socrates fresh publication60 owns the slot.

Prepared `tests/sandbox_broker/test_service.py`: Git blob `6d3dc3d8592087a783deb5c7a3538c496ca63cb3`, SHA256 `CEB8A01B97F00B67726687548D6B80BD042A43E98316D22FFB4451B72A122471`. Diff from pinned baseline:240 insertions/41 deletions, confined to two imports and the original hung-create block plus adjacent private observation/controller helpers and two regression functions (three cases).

Equivalence mapping (source-only text comparison, not executed):
- Old client core3567–3574 → `_HungCreateClient`3558 onward: identical create algorithm after indentation normalization. New task/job-id fields only record fixture ownership and exact expected destroy identity; no await added.
- Old scenario3579–3600 → `_run_hung_create_case`3579 onward: identical statements after indentation normalization and removal of the optional synchronous two-line hook. Default None adds no wait. **No finally added:** release and stop remain success-only for real behavioral RED.
- Original outer assertions3603–3609 → original test3612 onward: all seven assertion lines exactly unchanged. Existing `pytest.raises(StopIncomplete)`, ownership assertion, leak comprehension, timeout values/order and asyncio.sleep(0) remain in the extracted body.
- Three production/reference pins in section 1 (service/store/ControlledClock) remain exact. No production or other tests changed.

Controller exit paths (`_observe_hung_create_cleanup`,3684):
1. Hook exception, ordinary helper exception, regression-watchdog timeout or helper completion: capture actual exception; freeze `_HungCreateExit` values before rescue. The regression-only2s outer watchdog does not alter the original .5s observer or any service deadline. Unexpected watchdog/setup failure is **not acceptable behavioral RED**.
2. Cleanup-fault branch: instance-local stop wrapper awaits the real bound stop, then raises the cleanup sentinel. Original .05s StopIncomplete passes through; no synthetic drain. With the old helper and early injection, this wrapper is never reached: that omission remains observable.
3. Snapshot or monkeypatch/setup failure inside the guarded region: nested finally still releases the fixture and invokes saved real stop. Construction before the guarded region has started no asyncio worker.
4. Every ordinary exit: release first, bypass fault wrapper, await real stop(.5), yield once. Real-rescue failure is retained alongside the caught first error; only captured new broker tasks/create root are cancelled, then bounded asyncio.wait(.5), consuming completed errors and recording pending tasks. No global cancellation or unbounded gather. Release remains set so the fixture cannot keep resisting cancellation indefinitely.
5. Only after rescue, assert actual task completion, exact one create/destroy/id, zero run calls, empty ownership and no pending broker tasks. Then the regression asserts the **pre-rescue** frozen snapshot, first exception identity/injection traceback and cleanup cause. Rescue cannot satisfy those pre-rescue assertions. Any safety failure remains a failure and is not cleanup-behavior RED evidence.

Exact SOURCE-ready RED nodes, unchanged from section 4:
```text
tests/sandbox_broker/test_service.py::test_hung_create_failure_cleanup_preserves_first_error
tests/sandbox_broker/test_service.py::test_hung_create_cleanup_error_without_primary_is_visible
```

Expected, not observed: two early-injection cases fail `before.released` after real rescue; cleanup-only control should preserve its sentinel. Do not run the original still-unsafe test before the separately released GREEN fix. First CI .5s cause remains UNKNOWN; injection is explicitly not a reproduction of that cause.

Read-only `git diff --check` passed; textual equivalence checks above passed. No Python/import/compile/test/runner execution, CI rerun, commit or push occurred. Await independent SOURCE and explicit RED slot grant. Plan append changes this document hash only; no test pass claim.

## 7. Lovelace P2 controller safety correction — SOURCE re-review pending

Independent review of section 6 found one P2: cancellation of the fallback asyncio.wait could escape before diagnostics/completion checks, and snapshot errors could overwrite the first caught exception. Other equivalence/assertion/deadline/pin checks passed per parent. Parent released this **controller-only** test correction plus ownplan, still no Python/slot.

New test Git blob `b463d45cc873acfd12864d26d01924512dab26d6`; SHA256 `304E78EA5FC4D176151F2E442AB48F773BD07586A60A7211E558B1123A3A2F95`. This supersedes section 6's test freeze, not its historical authoring evidence.

Changes confined to `_observe_hung_create_cleanup`:
- Capture the first exception object and original traceback, including setup/instance-patch failures. Before/after snapshot faults become named secondary records with their own exception objects/tracebacks; the before snapshot is never rebuilt after rescue.
- Release still precedes real_stop(.5). On real-stop failure/cancellation, establish **one** fallback deadline `loop.time() + .5`; each asyncio.wait uses only the remaining budget. Repeated CancelledError is recorded and cannot escape this wait or reset its deadline. Other wait errors remain visible; no unbounded await/gather.
- Retain owned task references, including late real-destroy tasks discovered during drain; never clear ownership dictionaries. At deadline, unfinished owners are explicitly recorded as safety failures, not claimed drained.
- Actual SDK/task/destroy/ownership/leak completion checks run independently of successful diagnostic snapshots. Their failures join the report rather than masking the captured first error.
- If controller faults exist, re-raise the original first object with its saved traceback, explicitly caused by the secondary report. Any existing helper cleanup cause is retained beneath that report. With no helper error, the first controller error is re-raised. Ordinary regression paths retain the same first/cause/pre-rescue assertions and cannot become GREEN through rescue.
- No change to `_HungCreateClient`, the still-unfixed `_run_hung_create_case`, either regression's assertions, original seven assertions, timeouts, production or clock. The helper's finally fix remains unauthorized until accepted real RED.

Runner is read-only/unused: SHA256 `3CEC21C4DB04FE3054D111DED19C623F262201ABD69DE5AB5148A747B57C1180`. Earlier full forward/reverse byte comparison proved only the REPO literal differs from approved6D490; no runner write or execution in this revision.

Source-only checks: diff whitespace check and preserved-body/pin comparisons. These do not verify repeated-cancellation behavior by execution; SOURCE re-review is required. No imports/Python/tests/runner/CI/commit/push. First CI .5s timeout root cause remains UNKNOWN.

## 8. Accepted actual behavioral RED

After Lovelace SOURCE READY closed P2, parent explicitly granted one offline invocation of the **two new nodes only**, with MedChat `-I -S -B` and approved runner3CEC:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py 'tests/sandbox_broker/test_service.py::test_hung_create_failure_cleanup_preserves_first_error' 'tests/sandbox_broker/test_service.py::test_hung_create_cleanup_error_without_primary_is_visible'
```

The command returned authoritative terminal **6fff87** directly (no live session handle), exit **1**, `ORDINARY_PYTEST_EXIT=1`: **2 failed, 1 passed in 2.24s**, no warnings reported. Both parameterized failures were **call-phase** at executed line3836: `assert before.released`, actual False, message `helper leaked its fixture before outer rescue`. The first exception identity and injection traceback checks passed before that assertion. Before returning the observation, the controller's real rescue/completion checks passed: create completed, one create/one destroy/exact id, zero run calls, empty ownership and no pending broker tasks. Cleanup-only control passed. This is not timeout, import/setup failure or unsafe-rescue RED; assertions after the failing pre-rescue release assertion were not reached and are not claimed passed.

Before/after all **6 Git blobs and file SHA256 values were identical**, including executed test304E/b463, planAF081/cc1f, unchanged service/store/clock and runner3CEC. Exact executed SHA256 pins:

| File | Before = after SHA256 |
|---|---|
| `tests/sandbox_broker/test_service.py` | `304E78EA5FC4D176151F2E442AB48F773BD07586A60A7211E558B1123A3A2F95` |
| `src/sandbox_broker/service.py` | `3793B846CDF54CDAB3F0062E21ECE36F9BB49815B39F48B8BEF3232970AA01DB` |
| `src/sandbox_broker/store.py` | `16F202400E39EE00E27EEB3BDF07763389E2D697AD10550D5C677BD0A5C1858E` |
| `tests/sandbox_broker/controlled_clock.py` | `2E7F4215D9900093DD43281F3656809E043DE9FB39AF47A7EBB19A7A96991963` |
| This plan | `AF081B89D14C88E489A3406E21101A48E9ACFE4C067965EC7B6FA3CC36F6CAA3` |
| `scratch/ordinary_chat_offline_runner.py` | `3CEC21C4DB04FE3054D111DED19C623F262201ABD69DE5AB5148A747B57C1180` |

HEAD stayed `c612c9873a04823c936d710861bf890ad30b844a`. Slot was explicitly released immediately on terminal. Parent accepted this RED; no retry or original-node execution occurred. The first CI .5s root cause remains UNKNOWN; after-start injection demonstrates failure cleanup, not that CI trigger.

## 9. Helper-only GREEN source preparation — execution pending

Parent subsequently released only `_run_hung_create_case` and this evidence append. New test blob `07cbcdc83abc8e884e535b25254e0309e912d658`, SHA256 `A0E93B446D42DD7A7A5F6EADFF769245AE50E7FA3FDFC00ED8D66DA8EF7EADE1`.

Minimal helper change matches section 2: save body exception object/traceback, unconditionally release and real stop(.5) in finally, then re-raise the same first error with cleanup error as explicit cause if both fail. Cleanup-only error propagates. Ordinary success still executes final stop and the original leak snapshot; all .03/.03/.5/.05/.5 values, StopIncomplete/ownership assertions and seven original outer assertions are unchanged. BaseException handling is test-only and never turns exceptions into success.

Source text comparison against executed304E confirms **everything outside this helper is unchanged**, including the P2-corrected outer controller, frozen observation and both regression functions/assertions. Production/store/clock/runner remain pinned. This is a source check, not runtime GREEN.

After SOURCE re-review and a separate explicit slot grant, the proposed one-shot GREEN selection is the original plus the two new nodes (three arguments/four cases), exactly:
```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py 'tests/sandbox_broker/test_service.py::test_hung_create_hits_hard_deadline_and_never_claims_cleanup_success' 'tests/sandbox_broker/test_service.py::test_hung_create_failure_cleanup_preserves_first_error' 'tests/sandbox_broker/test_service.py::test_hung_create_cleanup_error_without_primary_is_visible'
```

**Not executed.** B2 API RED owns the next slot per parent; this worker holds none. No Python/import/compile/tests/runner execution, production edit, commit or push occurred during this helper implementation phase. No automatic GREEN, nearby controls or CI rerun follows.

## 10. Focused GREEN and independent full-module QUALITY

The preceding pending state is superseded by actual execution, not erased.
Lovelace independently approved helper A0E93B / plan952F13 SOURCE: no blockers;
reversing the helper change in memory exactly recovered the executed RED test304E78.
All original deadlines, seven assertions and helper-external bytes were preserved.

After explicit sole-slot transfer, the exact section9 three-node command finished
directly at terminalbc6991, exit0: **4 passed in1.78s**, zero failed/skipped and no
warnings reported. Six Git blobs/SHA256 values matched before and after; no retry.
The original0.5s observer passed this time, which does not establish the cause of
the earlier CI observer timeout. Slot was released at terminal.

Socrates then performed independent fresh QUALITY and one complete, unfiltered
module run using the same approved runner:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py 'tests/sandbox_broker/test_service.py'
```

Actual session55971, launch6161dc, terminal3c1ee6, exit0: **187 passed in96.46s**,
zero failures, warnings or skips. All6 controlled Git blobs and SHA256 hashes were
unchanged; executed HEADc612c987. The full module included the nearby late-create,
hung-shutdown and hung-run/destroy cases, without duplicate-name shadowing or
collection exclusion. Independent QUALITY APPROVE reported no blocking finding.
Slot released before any later work. This is test-fixture cleanup verification,
not scientific execution or proof that the first CI0.5s trigger has been fixed.

Parent subsequently fast-forwarded this isolated branch to PR99's actual landing
108df5d8acbdc1881a8f09c31b8b450acdfb247b. The incoming six publication files do not
overlap this two-file fix or its six frozen inputs. Parent rechecked testA0E93B,
service3793B8, store16F202, clock2E7F42 and runner3CEC21 unchanged. This documentation
append is the only post-verification content change. Latest-head CI, review-thread
guards and reviewed/landed tree equality remain required before any merge.

Retain PR98 run36282797393's failure. This fixes its demonstrated secondary test
cleanup leak; the initial0.5s timeout root cause remains **UNKNOWN**. No production
code, scientific assertion, clock, timeout or CI threshold is changed.
