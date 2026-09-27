# C4a Docking Physical Settlement Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retain C-owned command resources through late creation and actual physical settlement, before own-job cleanup/history mutation and input snapshot/lease release.

**Architecture:** Implement the approved design §5.2 in the existing CommandAdapter, per-invocation tool/service, DockingExecution lease, and retained TaskRuntime execution worker. One private scope owns command receipts and deferred own-job cleanup; it is not a scheduler, execution authority, scientific result, or public DTO.

**Tech Stack:** Existing Python threading/Condition, subprocess, Windows Job objects, POSIX process groups, asyncio-owned worker, SQLite TaskRuntime and pytest. No new dependency.

---

## 1. Authority, baseline and exclusions

This is SOURCE-only plan refinement under the master plan C4a row, not authorization to write production/tests or execute commands that import Python. Parent releases test preparation, each RED/GREEN run, and implementation separately after independent SOURCE review. Kant holds the scientific/Python slot at drafting time. No runner is created or executed here.

- Worktree: `docking-physical-settlement`; branch `codex/docking-physical-settlement`.
- Base commit: `07e751a875b94ebdaf3a4adc7399003977febddc`; tree: `b0dfa5e95ef0c78930ca45795d457638678dc37e`.
- Authority: `docs/superpowers/specs/2026-09-27-docking-consent-integration-design.md` §5.2; `docs/superpowers/plans/2026-09-27-docking-consent-integration.md` C4a row (base line 153).
- Accepted C2 evidence remains separate: independent Lovelace session 26566 / terminal 8c3e0b, 1148 passed / 15 skipped / 0 warnings, 773.33s, exit 0; 16 pins unchanged (f105e5/94106c). Skips: 9 symlink privilege, 1 open-file replacement, 4 POSIX, 1 opt-in performance. These are not C4a coverage and not real scientific execution.
- C1/C2 authority, CAS rowcounts, unknown-claim retention, cancellation first-error, durable dispatch reservation, deadlines and capacity accounting remain unchanged. No automatic replay/recovery or authority from a receipt.
- No C3/HTTP activation, C4b bounded Web feedback, C5 download grants, UI changes, OpenSandbox activation, schema migration, new queue/process manager, or B1 changes. Ordinary eight-stage/public integration remains separately gated. No real Vina/model/training/config/assets work here.
- Preserve scientific preparation, formulae, grid/options, validators, artifact/completion checks, 8 MiB command-output cap, timeout anchors and existing timeout values. A physical settlement wait is not a new science budget or a promise to clean up within 300 seconds.

Prospective production allowlist: `src/docking/adapters/base.py`, `src/agent/tools/molecular_docking.py`, `src/docking/molecular_docking_service.py`, `src/task_runtime/docking_execution.py`, and only the existing scope-injection/retention seam in `src/task_runtime/runtime.py`. Subclass adapter wrappers, staging/store/database, backend, history implementation and all Web files remain read-only unless a concrete necessity is separately approved.

Prospective new tests are appended to `tests/test_docking_command_cancellation.py` and `tests/task_runtime/test_docking_consent.py`. Existing tests/assertions remain intact. Other test modules below are regression inputs, not an editing grant.

## 2. Actual source seams at this base

| File / base location | Observed behavior | Required C-only seam |
| --- | --- | --- |
| `src/docking/adapters/base.py:194,206` | Constructor owns only executable; `run` validates controls then chooses platform. | Optional keyword-only `ownership_scope`; reserve before any thread/process creation. Invalid control/pre-cancel still cannot spawn. |
| `base.py:131-150` | `_WindowsJob.__init__` acquires a real handle before SetInformationJobObject; configuration failure calls close, which can itself fail before the constructor returns. | Attach C ownership immediately after successful CreateJobObjectW, BEFORE configuration or other fallible setup, not after construction returns. Preserve first configuration failure plus secondary close failure; zero Popen on failed Job setup. |
| `base.py:260,338` | Windows success finally suppresses Job.close failure when root process already exited. | Record close failure regardless of root.poll; an open/uncertain Job cannot settle even after successful root exit. Do not change the legacy no-scope result/error contract. |
| `base.py:411` | Job created before Popen; capture readers attached before Job assignment; nested cleanup can fail before tuple return. | Receipt acquires Job at creation, process immediately on Popen return, reader references before start. It must survive failure before the outer worker sees a ready tuple. |
| `base.py:440` | Bounded-spawn daemon publishes ready/claim/cleanup_done via Condition; timeout may return before spawn; cancel observes cleanup_done, not actual thread join. | Register spawner before start; existing lock arbitrates stop/claim; late attachment remains legal after sealing. Settlement joins the actual thread and does not use finally notification as proof. |
| `base.py:347` | POSIX Popen followed by capture setup without an enclosing setup-failure ownership boundary. | Register owned caller/reservation before Popen, process/group before readers; retain setup failures and resources, including partial reader startup. |
| `base.py:616,696,767` | Reader state attaches to process only after both starts: reader2 start failure can lose reader1 via process attributes. Drain suppresses stream.close errors; capture_taken is set before joins and repeat reads return fallback. `_close_process_handle` throwing in cleanup can skip Job.close. | Register all acquired resources at creation, readers before each start; retain actual reader1/streams/process/Job independently of attributes/flags and skipped cleanup calls. Setup/join/close failures remain sticky unresolved facts. |
| `src/agent/tools/molecular_docking.py:85,193` | New MolecularDockingService per execute; no private scope/root constructor channel. Bridge at line 20 joins its own coroutine thread, not a late command spawner. | Constructor-only trusted scope/output-root channel to this new service; never shared registry mutation or payload authority. Bridge completion alone is insufficient. |
| `src/docking/molecular_docking_service.py:65,89` | Constructor creates four adapters; configure calls refresh and replaces them. work_dir defaults to cwd/temp_docking. | Same scope on Vina/ADFR/Meeko/OpenBabel initially and after refresh; private constructor output-root, established before directory creation. Legacy defaults unchanged. |
| `service.py:412` | `prepare_protein` creates another ADFRAdapter for a candidate receptor command, replacing the original adapter during the actual phase. | This third constructor site also receives the exact C scope; test the original phase and replacement adapter/run identity, not a stubbed preparation phase. |
| `service.py:834,945,966,984,994` | `_failed_job_response` deletes job for phase-false and scientific-validation-false paths. | Same scoped cleanup gate as exceptions, not a top-level-exception-only fix. |
| `service.py:1040-1139` | History upsert/removal and cancel/uncertain/timeout/progress/generic cleanup assume return means resources stopped. | Gate downstream/history and all own-job cleanup on physical facts; defer pending cleanup to scope. Keep first primary error and separate cleanup diagnostics. |
| `src/task_runtime/docking_execution.py:119,125,160,350` | Fresh local tool; raw call under task_execution lease then validation/finalization; lease exits when callable returns. | Per-call C factory binding, settle inside lease on every path and before accepting completion. Never replace shared raw_executor for an individual request. |
| `src/task_runtime/runtime.py:519,558` | C worker is retained asyncio.to_thread; backend/worker drain precedes staged-input deletion. | Create/retain scope in existing operation before C tool factory; pass to run_verified, retain worker while physical settlement is pending. No new task registry/cleanup worker. |

The four concrete adapter subclasses inherit the base constructor and forward to `self.run`; no wrapper API expansion is needed. Service.cleanup currently removes the entire work_dir: it is not a C own-job cleanup primitive and must not be called by this integration.

## 3. Narrow internal contract for test-first review

### 3.1 Scope and command receipts

Proposed server-only API in `adapters/base.py`: `CommandOwnershipScope`, `reserve_command()`, `seal()`, `snapshot()`, `settle()`. Scope creation binds private task/output-root identity and the existing cancellation signal. `CommandAdapter(..., ownership_scope=None)` is the optional trusted injection point. Internal attachment helpers may stay private; tests observe genuine resources, not a manually fabricated settled receipt.

`reserve_command()` returns a private per-command receipt. Registration occurs inside `run`, before starting a spawner or calling Popen, and failure means zero creation. Scope sealing is idempotent and rejects all new reservations. Previously reserved operations may attach late resources after seal; sealing cannot discard or reset their obligation. Scope state is local, never a process-global/current-tool field.

Receipt state vocabulary: `reserved`, `spawn_pending`, `running`, `cleanup_pending`, `settled`, `unresolved`. Keep primary scientific/control outcome separate from physical state. Scope snapshot contains only fixed states, resource counts and fixed error codes, no command/argument/path/PID/handle/output/exception repr. Private resource objects never enter ToolResult, logs, SQLite metadata or scientific evidence.

Record each acquisition before the next fallible operation: CreateJobObjectW succeeds -> immediate receipt ownership attachment inside Job construction -> SetInformationJobObject/configuration -> Popen -> process/group attach -> capture state and each reader registration -> reader start -> assignment/resume/normal supervision. Returning a constructed Job object is too late to begin ownership. Pass the existing private command receipt into the C Job construction boundary so an acquired handle remains owned even when configuration and its cleanup fail before return. Registration failure after acquisition must still retain/clean that handle and prohibit Popen; no scope-less escape. Track even not-started/failed-start threads explicitly; never join an unstarted thread as if it ran. A fast-finishing worker remains registered before it can finish. Keep already acquired resources when setup and its immediate cleanup both raise.

Specifically, reader2.start failure cannot discard the already-running reader1 or either pipe just because `_medchat_capture_state` was never attached. C capture must record stream.close failures currently suppressed by drain, and join/setup failures before control can escape. `_medchat_capture_taken=True` and a subsequent fallback return prove neither stream closure nor reader exit. If process-handle close throws and prevents the existing subsequent Job.close call, both ownership records survive; independent safe cleanup steps must not be lost through exception short-circuiting. Preserve first primary failure and sticky physical uncertainty rather than reporting the later fallback as repair.

Use the existing Windows Condition claim/stop ordering, not a second competing killer. The same stop latch is checked before claim/resume. One cleanup owner closes handles exactly once. A failed CloseHandle is not proof of closure, including `_WindowsJob.close` clearing its local handle before a failed OS call: retain the uncertainty at the acquisition owner; do not blindly retry an uncertain close or erase it because root.poll is non-null. POSIX group ownership is the actual created session/group, not a scan by PID/name.

`settle()` is a blocking server-worker operation, never event-loop work or a call from a spawner/reader being joined. It proves command callable completion, resolved spawn reservations, actual spawner joins, stopped owned process tree, joined readers/cleanup operations, and successful one-time handle closure. Successful thread join cannot erase a preceding cleanup/close failure. Failure of physical proof leaves unresolved, never settled or success. Notifications wake the retained owner; no busy retry or extra scheduler.

Differentiate command physical facts from scope lifecycle: service may inspect/complete already-ended command receipts while the tool is still executing; it must not seal the whole scope between receptor/ligand/Vina commands. Final scope seal/settlement occurs after the outer raw call ends, with a lease-contained finally safety net. No deadlock waiting for the service itself to return from inside that service.

### 3.2 Construction, propagation and compatibility

Runtime's existing C operation creates and retains one scope before constructing a C-owned MolecularDocking. Pass it as an optional server-only `command_scope` to `run_verified`, then use a per-call local tool factory/binding. Do not mutate `DockingExecution.raw_executor`, `_execute_production_tool` compatibility identity, shared registry tool, environment or global service instance. Scope and trusted output root travel through MolecularDocking constructor to MolecularDockingService constructor, all refreshed adapters, and the candidate-specific ADFRAdapter replacement inside the original prepare_protein phase at line 412; never through model/query/runtime_config authority. All three service adapter-construction sites must retain identical scope ownership.

Keep the existing non-C constructor defaults, raw callable signatures, CompletedProcess/ToolResult formats and error mappings. Existing C2 injected raw fixtures remain supported without extra required keywords; they are explicitly injected/offline, not proof of production scope propagation. A C production-local path must check the actual constructor/adapters retain the exact scope and bound root before any command; dropped/mismatched scope fails closed. Do not silently treat the OpenSandbox backend as local scope-compliant or claim its release.

Use the existing allowed_output_root and per-job `docking_<task_id>` identity. The service must establish its C root before making directories and must not create/use the legacy root first then change it. Own-job cleanup requires the actual job created by this invocation, not an existing conflicting directory, neighbor, whole root or arbitrary supplied path. C5 access-control convergence is still later; this is private ownership plumbing, not publication.

### 3.3 Service obligation and lease ordering

One small scope-owned, server-private deferred-cleanup obligation represents the current invocation's job/history cleanup. Service supplies the existing own-job action; scope does not become a generic callback framework. Register/deduplicate before returning non-success when physical state is pending/unresolved. Exceptions and all four `_failed_job_response` call sites use this boundary. If changing that classmethod internally, retain no-scope call compatibility and test it.

Before starting a downstream phase or writing/removing history, C checks prior command physical settlement. Pending/uncertain ownership gives fixed non-success, no new science and no deletion/quarantine/history mutation. If commands are already physically settled, existing own-job cleanup may run immediately. Preserve the primary cancel/timeout/preparation/scientific/progress/generic failure; attach only sanitized cleanup uncertainty. A late clean exit cannot replace it with success.

DockingExecution seals after the raw call returns/raises and consumes the scope before interpreting a success as acceptable and before completion publication. A finally boundary INSIDE `task_execution` covers raw construction/call, progress, validation and prepared-completion errors as well. First prove command resources settled, then run the deferred own-job/history obligation exactly once in the SAME owned execution worker, then allow snapshot/lease exit. Never drain from the child that must be joined.

If physical proof is permanently unresolved, keep the worker/context/private owner retained; close/shutdown observation may report unresolved, not clean completion. Do not throw out of the lease merely to expose an error. Pending finite barriers can settle normally after release; an irrecoverable close/cleanup error is not auto-cleared. Physical settlement and filesystem cleanup are distinct facts: deletion failure can use existing quarantine only after physical settlement and must preserve truthful failure/retention via existing runtime cleanup accounting, not fabricate successful deletion.

The existing runtime settlement path still drains backend and execution worker before staged-input cleanup/capacity release. No additional cleanup thread, second backend submit, renewed deadline, successful receipt from worker.finally, or late terminal upgrade. Crash leaves durable C2 dispatch uncertainty; absent Python owners grant neither deletion nor replay.

## 4. Bounded TDD sequence (future separate grants)

All selectors in 4.1-4.3 are proposed NEW tests, not currently collected or run. Preserve existing prefixes/assertions. First API lookup happens in a test body via module attribute lookup, not a missing top-level import causing collection failure. Parent chooses exact explicit nodes/parameters after SOURCE freeze and grants a single run; record observed feature RED separately from reached behavior RED. No predicted pass counts.

Release boundary: following this plan's re-review, the first TEST preparation grant is limited to §4.1 and still requires explicit parent approval. §4.2, including the receptor replacement branch, and §4.3 remain later separately released batches; documenting their tests here is not permission to prepare them now.

### 4.1 Receipt and platform ownership

- [ ] Append `tests/test_docking_command_cancellation.py::test_c4a_command_scope_api_present`; SOURCE then parent API RED before production release.
- [ ] Add `::test_c4a_scope_registration_and_seal_precede_creation` (registration failure, pre-cancel, sealed new reservation, existing reservation late attachment); zero threads/Popen on denied creation.
- [ ] Add `::test_c4a_windows_job_configuration_failure_retains_acquired_handle` with explicit `close-success` and `close-failure` cases: delegate genuine CreateJobObjectW, inject SetInformationJobObject failure after acquisition, then exercise the actual constructor cleanup boundary. Both require zero Popen and the first configuration failure retained. Successful actual close releases the resource exactly once without converting the command failure to success; failed close retains the acquired handle and a separate fixed close-error fact, remains unresolved even though no Job object returned, and cannot become settled through an empty ready tuple. In the close-failure fixture fail before OS closure, retain the genuine handle independently, and finally invoke the real close once for safe teardown without clearing receipt uncertainty or causing double close; do not replace the entire Job constructor with a fake.
- [ ] Add `::test_c4a_windows_late_spawn_retains_actual_owner` (cancel/timeout, normal control). Hold real Popen at a finite pre-return barrier; use genuine suspended process/Job. Assert stop before resume, no marker execution, spawner actually joined, readers/handles accounted, settled only afterward.
- [ ] Add `::test_c4a_windows_setup_failure_keeps_acquired_resources` (reader2-start failure with reader1 alive before assignment, assignment failure, nested cleanup failure before ready tuple, process-handle close throws before Job.close, fast completion before outer attachment). Preserve primary error plus unresolved cleanup; inspect actual created resources, including Job still owned when its close was skipped. Do not mock the whole spawn helper.
- [ ] Add `::test_c4a_windows_exited_root_job_close_failure_is_unresolved`: actual root already exited; inject close failure at the close boundary. The no-scope legacy suppression is not the C oracle. Scope remains unresolved regardless root.poll; no false settled snapshot or double close. Test cleanup must retain the actual handle independently for safe teardown, not mutate receipt truth.
- [ ] Add `::test_c4a_capture_exit_notification_is_not_join` (spawner finally notification with thread still held, reader2 start failure after reader1 starts, drain stream.close failure, failed reader join, repeated capture after taken-before-failed-join). Bounded test barriers and real thread liveness demonstrate notification != exit; process attribute absence, capture_taken and repeat fallback cannot erase unfinished readers or sticky setup/close/join errors. Use transparent stream wrappers delegating actual reads/closure, not fake capture algorithms.
- [ ] Add `::test_c4a_posix_pending_spawn_and_setup_failure_retains_owner` (cancel/timeout pending Popen, reader setup failure, group descendants after root exit, normal control). Real start_new_session child/group and readers; caller stays owned during Popen, then actual process/group cleanup before receipt settles.
- [ ] SOURCE review all platform fixtures before parent RED. Implement only the receipt/platform seam after observed RED and explicit grant; retain no-scope legacy tests unchanged.

### 4.2 Service/tool consumption

- [ ] Add `tests/test_docking_command_cancellation.py::test_c4a_service_refresh_keeps_exact_scope` and `::test_c4a_tool_scope_and_root_are_private_constructor_inputs`: all four actual adapters before/after configure; missing/dropped scope denies C creation; ordinary defaults and query cannot authorize/replace scope.
- [ ] Later §4.2 grant: add `::test_c4a_receptor_candidate_adapter_keeps_exact_scope`. Enter the original `prepare_protein` with a non-PDBQT input and an existing candidate command that reaches its real ADFRAdapter construction; observe the replacement instance and inherited run's exact scope identity/reservation. Use the harmless executable boundary for command I/O, not a whole-phase stub or fake ADFRAdapter. Include the dropped-scope negative case with zero unowned spawn; constructor/refresh-only assertions cannot satisfy this test.
- [ ] Add `::test_c4a_service_failure_defers_own_job_cleanup` with explicit cases: cancellation, ownership uncertain, timeout, progress exception, generic exception, receptor false, ligand false, Vina false, no validated poses. Use actual service with an adapter boundary that drives a real harmless command and a finite pending physical owner. Phase-false injection must actually traverse `_failed_job_response`, not a hand-built failure dictionary.
- [ ] Assert deletion AND rename/quarantine AND history-remove entry counts zero while pending, no downstream adapter call, job bytes remain, neighbor untouched. Release barrier, join actual resources, then same-owner cleanup once; repeat cancel/settlement does not duplicate cleanup. Include settled normal/control and post-settlement delete/history failure preserving primary error.
- [ ] Add `::test_c4a_service_history_waits_for_physical_settlement`: normal upsert only after proof; cancel after history persistence removes only own entry after proof; uncertain ownership cannot mutate history. Synthetic output is labeled fixture evidence, never real Vina success.
- [ ] SOURCE -> parent observed RED -> narrowly released tool/service implementation -> focused GREEN. Do not broaden exception messages or change scientific preparation/parse/score behavior to make tests pass.

### 4.3 Existing runtime/lease integration

- [ ] Append `tests/task_runtime/test_docking_consent.py::test_c4a_pending_command_retains_real_lease_and_capacity` (cancel/timeout and normal control). Reuse C2 real SQLite/LocalTaskBackend/stager rig; bind real DockingExecution, per-call tool/service and actual CommandAdapter. Substitute only external scientific executable boundary with a harmless child/explicit fixture; do not replace whole runtime/service/receipt with fake settled objects.
- [ ] Observe actual raw-return boundary while late Windows spawner is held; input snapshot/lease exit and cleanup entry remain uncalled, independent database capacity remains held, own job and neighbor remain. Test POSIX pending Popen honestly: its caller may not return until Popen does; do not pretend identical early-return timing.
- [ ] Add `::test_c4a_repeated_cancel_shutdown_retains_command_settlement`: original operation/worker retained, repeated cancellation shares stop/cleanup; close not successful while physical owner held. Release barrier in finally, actually join worker/spawner/readers/process resources; only then cleanup/lease exit, no late success/nonce/extra raw dispatch.
- [ ] Add `::test_c4a_raw_failure_and_validation_paths_settle_before_lease_exit` for raw error/non-success, progress/validation error and successful prepared completion. Verify settlement/finalization order and original C2 first error. Existing completion reuse performs zero commands and does not spuriously create/reuse a closed scope.
- [ ] SOURCE review before integrated RED; implementation only in approved injection/lease seam. No C3 or C4b workaround to release a still-owned worker. Independent SOURCE and fresh QUALITY required before calling C4a accepted.

### 4.4 Finite test cleanup and genuine platform evidence

Each blocking test owns finite Event/Condition escape barriers and finally releases them and joins actual created threads/tasks/processes. Record assertions before fallback teardown can repair a failure. No artificial receipt state mutation, OS-name monkeypatch, daemon-thread assumption, fabricated join, or killing unrelated processes. Recoverable pending cases can fully drain after barrier release. Permanent-error receipt cases are tested at the resource seam with independent real resource teardown; do not intentionally strand an unresolvable full runtime worker and call pytest completion proof.

Windows coverage must execute actual CREATE_SUSPENDED/Job assignment/resume/termination/active-process query/handle close on Windows, with finite actual child tree. POSIX coverage must execute actual start_new_session/killpg/group liveness on POSIX. OS-specific tests skip only with explicit platform reason; Windows success cannot replace POSIX coverage and vice versa. Permission/environment limitations are reported as unavailable/skip, not faked success. Future authorized platform runs can use harmless children, not scientific executables. Genuine command cleanup is distinct from a real docking science acceptance gate.

## 5. Existing regression inventory (confirmed source names)

Run only under a later parent-approved interpreter/runner, exact explicit selector list and before/after freeze; no wildcard collection substituted for the agreed list. Runner derivation/new-tree root review remains a separate parent action. Full-module union and platform split are to be frozen at test SOURCE handoff, before any run.

Mandatory existing module inputs:

- `tests/test_docking_command_cancellation.py` (entire module, including old assertions).
- `tests/task_runtime/test_docking_consent.py` (entire C1/C2 plus appended C4a tests).
- `tests/task_runtime/test_docking_execution.py` and `tests/task_runtime/test_local_backend.py`.
- `tests/task_runtime/test_staging.py` and `tests/task_runtime/test_secure_snapshot_boundary.py`.
- `tests/test_docking_configuration.py`, `tests/test_docking_history_index.py`.
- `tests/agent/test_docking_tool_contract.py`, `tests/agent/test_docking_contract_integration.py`.

High-signal existing selectors, not replacements for full approved union:

| Module | Exact existing test names |
| --- | --- |
| `tests/test_docking_command_cancellation.py` | `test_pre_cancelled_command_does_not_spawn`; `test_cancel_event_terminates_spawned_process_tree`; `test_cancellation_precedes_timeout_when_observed_first`; `test_verified_normal_completion_wins_over_late_cancel`; `test_cleanup_failure_has_distinct_ownership_error`; `test_windows_cancel_during_bounded_spawn_never_resumes`; `test_posix_normal_parent_exit_does_not_abandon_orphaned_child` |
| Same command module | `test_all_command_adapters_forward_cancel_event`; `test_service_cancellation_after_parse_removes_pose_and_skips_history`; `test_partial_artifacts_are_quarantined_when_delete_fails`; `test_history_cleanup_failure_is_explicit_on_cancellation`; `test_service_rejects_existing_job_directory_without_overwrite`; `test_failed_workflow_removes_partial_directory_and_allows_retry`; `test_command_adapter_stops_output_flood_at_fixed_limit`; `test_receptor_preparation_never_uses_simplified_scientific_fallback`; `test_tool_execute_works_inside_existing_event_loop_without_coroutine_leak`; `test_tool_redacts_structured_inputs_and_success_provenance` |
| `tests/task_runtime/test_local_backend.py` | `test_close_waits_for_real_sync_worker_before_cancel_terminal` (1557); `test_shutdown_timeout_preserves_worker_tracking_and_nonterminal_state` (1635); `test_runtime_close_can_retry_after_backend_shutdown_timeout` (3335) |
| `tests/task_runtime/test_docking_execution.py` | `test_concurrent_call_waits_while_prepared_completion_holds_lease`; `test_owner_unlink_failure_cannot_expose_active_attempt_to_concurrent_cleanup`; `test_exception_or_cancellation_writes_no_completion`; `test_final_snapshot_exit_integrity_failure_revokes_completion`; `test_lease_release_failure_after_commit_returns_verified_success`; `test_lease_release_failure_before_commit_is_not_reported_as_success` |
| `tests/task_runtime/test_docking_consent.py` | `test_c2_repeat_cancel_running_signals_before_blocked_sql_and_retains_capacity`; `test_c2_cancel_or_expire_under_actual_lease_before_raw`; `test_c2_zero_update_cannot_authorize_submit_or_raw`; `test_c2_committed_claim_with_failed_readback_retains_unresolved_ownership`; remaining C1/C2 tests unchanged |

Legacy service fixtures often replace phase methods; they prove compatibility, not real late-spawn settlement. Adapter forwarding mocks prove argument forwarding, not physical ownership. Report these distinctions and genuine platform skip counts in final receipts. Any discovered unsafe legacy test teardown is reported before amendment; do not silently delete/weaken it.

## 6. Source freeze and review checklist

SHA-256 at plan drafting (read-only):

| Input | SHA-256 |
| --- | --- |
| `src/docking/adapters/base.py` | `431EA4AB580A755C8D903F157EA4D9CCF91F6E44FC124491366FA4C6A9042726` |
| `src/agent/tools/molecular_docking.py` | `0745B4267AF20C6BD27D6FFACC15EBC2544D64C8D835E409D763D096A6509368` |
| `src/docking/molecular_docking_service.py` | `89AF4463DC0C40A4547A95E6DAD08826CF497AAD8A2F03129F1ABADA43366C38` |
| `src/task_runtime/docking_execution.py` | `892048164F2C0425EEE1177566EF97FF70417F3F4D293E69B42EF6EF04BE586E` |
| `src/task_runtime/runtime.py` | `3539A8014E83E48E9C6D18D5EC866004FDA3C11899EFD530F9F1D1A2A58F7794` |
| `tests/test_docking_command_cancellation.py` | `C273125261946E9C557B50E95DEC21360EF19B8F10BFAFCD0C780B50AACCC075` |
| `tests/task_runtime/test_docking_consent.py` | `0B96D1767433EB011D37F17AB59661B08184657A04274A4BCA1A9BC5F9291F07` |
| Approved design spec | `97B61082201E2703BE460C3C592212ABA8E8E348B8FA3DD58FC04C5EB82FE0EF` |
| Approved master plan | `66AADE7819A27B53B3708B2E3F2B901E6F0C750B2303A4FA5BC0A6E6925F5F1B` |

- [ ] Independent Lovelace SOURCE: construction propagation/private root, per-resource acquisition, unresolved-close retention, no service deletion bypass, lease-contained consumption, non-C compatibility, genuine OS test evidence.
- [ ] Parent releases bounded test preparation; exact new API node and behavioral selectors/hashes frozen before execution.
- [ ] Parent runs authorized RED, records actual handle/terminal/pins; only then releases narrow production step. Repeat SOURCE gate for each seam, never infer all planned faults fail.
- [ ] Focused GREEN, approved legacy union, genuine platform evidence and independent fresh QUALITY; counts/skips/errors reported honestly. No C4a acceptance or full-C activation claimed by this document.

Drafting record: source inspection and this new plan only; no Python/import/tests/compile/scientific call, no production/test/runner edit, no commit/push. Parent's three concrete acquisition/close negative cases are explicitly included in §2 and §4.1. C2/B1 trees remain untouched. This plan awaits independent SOURCE review and does not itself release implementation.

SOURCE amendment after Lovelace HOLD on plan `51897185`: verified and incorporated both P2 plan omissions (prepare_protein's third adapter constructor and pre-return Job-handle acquisition/configuration cleanup). Prior HOLD is preserved, not treated as execution evidence; re-review is pending. Only this plan changed, and initial future TEST scope remains §4.1.

## 7. §4.1 test-source checkpoint and explicit execution HOLD

Parent accepted Lovelace SOURCE GO for plan `2AE008B3DE56D063F89029EE2A323289B88863CAE1D70F84B3915DB4859E358D` and released §4.1 TEST preparation only. Initial test source `85AB0040B0E0FE4AF70699D07C2346FF52D5519A47A51B40AFE8B674E9B152C8` received **API-only SOURCE GO**, not platform execution approval. Exact API selector remains `tests/test_docking_command_cancellation.py::test_c4a_command_scope_api_present`; lookup is in the test body, with no resource fixture or new fallible top-level import. No actual API RED has been run by this worker. Parent will schedule it separately after the current slot owner terminates; no production release is inferred.

Lovelace's four wider-OS P2 findings are preserved and addressed by TEST-only amendments for re-review:

1. Every new root child and descendant Python argv explicitly includes `-I`, `-S`, `-B` before `-c`; historical test prefix stays unchanged.
2. Fixture teardown attempts every independent release, join, process stop/wait, stream close, process-handle close and Job close. It collects fixed operation/type error facts and reports only after all close actions and patch restoration; a failed join/assert/close does not skip other resources. Failed physical cleanup is not relabeled success. A blocking OS/stream operation is not made bounded merely by catching exceptions.
3. Late Windows spawn observes the actual `_WindowsJob.resume` boundary with transparent delegation to the original NtResumeProcess path. Cancel/timeout require a real late process, zero resume calls AND no marker; normal control requires one real resume and the marker. Marker absence alone is insufficient.
4. Nested cleanup fault requires a reached event AFTER original `_communicate_after_termination` delegation and BEFORE injected failure. Receipt must retain first `job_assignment_failed` plus distinct fixed secondary `command_cleanup_failed`, not merely an unresolved status.

**Platform execution remains HOLD.** The tests contain blocking `scope.settle()` calls, including direct calls on the pytest thread. Finite fixture Events, join observation timeouts, a non-daemon test thread, or the repo-only offline runner do not establish a qualified outer retained owner and cannot make a stuck settlement safe. Before any platform selector/full-module run, parent and independent SOURCE must qualify an outer retained test-process/resource owner: actual process/Job/group ownership from launch, bounded observation, retention during blocked settlement, safe tree cleanup and actual exit/join evidence, explicit unresolved outcome if cleanup cannot be proven. Reuse a separately approved supervisor; this amendment neither implements a new framework nor authorizes one. Outer test termination is not a successful production settlement receipt. No broad OS grant, no changes to science deadlines, and no claim that non-daemon threads fix this boundary.

Parent-prepared ignored runner SHA-256 `0AD88551C1DC20527364D940855ABEE303EFB465DB67A4258FDEB25353EBCD61` is unchanged; its inverse/root-only check is parent evidence, not platform-owner qualification. This amendment changes only appended §4.1 tests and this own-plan checkpoint. Production, §4.2/§4.3, C2/B1, runner and all original test assertions remain untouched. No Python/import/test/compile/network/commit execution by this worker. Await independent SOURCE of amended tests and a separate exact parent run grant.

## 8. Actual API RED and §4.1 implementation SOURCE handoff

Parent receipt: **43230 / 17e112**, physically terminal exit **1**, **1 failed in 8.17s**, **12 pins unchanged**. The exact API node reached missing callable `CommandOwnershipScope` in the test body. No resource fixture/native execution occurred. This is feature/API RED, not reached platform-behavior RED. Parent released the slot and then authorized only `src/docking/adapters/base.py` receipt/platform implementation plus this receipt. No independent execution was performed by this worker.

Candidate source SHA-256: `2A664B84232630823BBCF4B869F9D91E159F6E7814855887C39DA1CE8BEB5970` (`src/docking/adapters/base.py`). Tests remain frozen at `B00DD8DA341EA1C0906E33F46901E1E740BFD28333B1250CC678ABD31E9B997F`; runner remains `0AD88551C1DC20527364D940855ABEE303EFB465DB67A4258FDEB25353EBCD61`.

Implemented SOURCE candidate, not a GREEN/acceptance claim:

- Private CommandOwnershipScope/per-command receipt; closed lifecycle snapshot, first fixed error and sticky secondary physical failures. Reservation precedes creation; seal denies new reservations but existing late resources remain attachable.
- Optional adapter scope leaves no-scope branches/defaults in place. Windows constructor attaches the acquired Job handle before configuration; process/pipe handles and each reader are retained before fallible setup/start. Unstarted-reader pipes have a separate safe close path after termination; reader failure does not lose the first reader.
- Windows controlled spawn reuses the existing Condition/claim flow and a scope stop latch. Actual resume is checked under that ownership condition; startup failure stops a possibly started worker. Existing timeout values/anchors are not increased. POSIX blocked Popen remains owned; returned process is registered and interruption latched before supervision.
- Actual spawner and reader joins are required by settle, independently of cleanup_done/capture_taken. C capture keeps its original state on failures/repeated reads. C handle-close failures remain unresolved even after root exit; close attempts are not blindly replayed. Physical close calls do not hold the shared stop Condition while blocked.
- Single-owner C cleanup retains and attempts remaining resources after a failed cleanup/process-handle close, including Job close, with sanitized independent failure codes. Non-C cleanup implementation remains the legacy branch; the POSIX supervision loop is shared without altering its no-scope conditions.

`settle()` is a blocking synchronous-owner API (event-loop and self-join use rejected). It returns true only on proven settlement; false preserves unresolved receipts and is **not permission to unwind a lease or delete a job**. §4.2/§4.3 consumers must retain their existing worker/context on that outcome or a settlement exception; those mandatory consumers are NOT implemented by this base-only grant. No service deletion/history/runtime/lease integration or full-C safety claim is made yet.

Checks performed: source reading, `git diff --check`, branch/status and SHA-256 comparison only. No Python/import/compile/GREEN, no native process test, no test/runner changes, no consumer expansion, no commit/push. Await independent Lovelace SOURCE before any GREEN; the outer-owner qualification and broad OS execution HOLD in §7 remain in force. Existing C2 branch/B1 are untouched.

## 9. Two acquisition-gap SOURCE findings: TEST-first follow-up, production frozen

Lovelace SOURCE HOLD reported two P1 candidates against base.py `2A664B84232630823BBCF4B869F9D91E159F6E7814855887C39DA1CE8BEB5970`. Parent released append-only regression tests plus this checkpoint, NOT a production fix or an execution grant. These remain SOURCE candidates until an authorized real platform run establishes the prerequisite facts and behavioral verdict; do not label them actual RED based on inspection.

Exact new selectors in `tests/test_docking_command_cancellation.py`:

- `test_c4a_job_attach_failure_after_real_acquisition_cannot_settle`: genuine CreateJobObjectW succeeds, then `_CommandReceipt.attach_job` throws before delegating/writing any receipt fields. Require the actual attachment boundary and independently recorded real handle, zero Popen/resume, and no successful/settled empty receipt after seal/settle. Existing fixture finally independently closes the acquired handle, including on assertion failure; it neither fabricates an acquisition receipt nor clears uncertainty.
- `test_c4a_popen_after_real_child_failure_cannot_settle`: reuse `resources.popen_after` to throw only after real suspended Popen has created the child and the fixture saved its process/pipes, but before production receives the reference. A transparent underlying factory delegate verifies actual CREATE_SUSPENDED and `-I -S -B` argv. Require actual live child at injection, zero resume, no marker, and no successful/settled receipt omitting this child. The existing fixture finally kills/waits/closes the independently held child/pipes/handles even when the production receipt lacks a process. No whole-Popen fake or receipt mutation.

The complete prior test source `B00DD8DA341EA1C0906E33F46901E1E740BFD28333B1250CC678ABD31E9B997F` is retained as a byte-identical prefix. No existing assertions, fixtures or production code are changed. Both new nodes are genuinely Windows-specific; non-Windows skips are not coverage. Blocking settle calls retain the explicit §7 outer-owner execution HOLD. Parent must first obtain independent Lovelace SOURCE and qualify the outer owner before scheduling any exact behavioral RED. API-only evidence in §8 remains valid but does not cover these fault paths. No Python/import/platform execution by this worker; no §4.2/§4.3 expansion or edits to parent's outer-owner plan/runner.

## 10. Qualified-outer actual two-P1 RED and minimal fix SOURCE

Parent subsequently qualified the outer owner and supplied these actual terminal receipts against source `2A664B84` and tests `7D3E557D`:

| Exact regression | Parent handle / terminal | Actual result |
| --- | --- | --- |
| `test_c4a_job_attach_failure_after_real_acquisition_cannot_settle` | `23278 / d8a2bb` | exit 1; 1 failed in 8.11s. Reached line 1751: expected settled False, actual True. |
| `test_c4a_popen_after_real_child_failure_cannot_settle` | `61945 / 122347` | exit 1; 1 failed in 8.18s. Reached line 1800: expected settled False, actual True. |

Both reached the genuine handle/child prerequisites, retained all 339 pins unchanged, and physically terminated. Parent reported outer `test_failed` / rc 1 / settled / slot true and successful fixture-finally resource cleanup. The outer's settled state describes the supervised test process, NOT the incorrect production receipt. These are now actual behavioral RED, superseding the prior SOURCE-candidate-only classification; no GREEN is inferred.

Parent authorized only the two-P1 fix in `src/docking/adapters/base.py` and this physical-plan receipt. Candidate source SHA-256 is `489D3D88B44AA56403E37516AF87C0AD331CA18D41048F2E42EF6A6A5ADDC12F`:

- Job acquisition's protected region now includes `attach_job` itself. If attachment throws, record fixed `job_attachment_failed` as sticky uncertain, directly retain the known Job/handle without replaying the failed attachment hook, and use the existing close-once path. A secondary close failure is retained; even successful cleanup cannot turn failed acquisition registration into empty-receipt settlement. The original exception remains the primary raised error.
- An exception from the Popen return boundary records fixed sticky `spawn_outcome_uncertain` before any known-Job cleanup. An empty process field or closing an empty Job is not evidence that no child was created. The same conservative C-only rule applies at the existing POSIX Popen boundary; that platform was NOT exercised by these Windows receipts. No unknown child is discovered/killed by PID heuristics, no blind close retry and no automatic clearing/replay. No-scope exceptions continue through the original paths without adding uncertainty state.

Frozen feature tests remain `7D3E557D2D0FECDB8E4AEB2D34FA494E5BF9809C263B425EFCBED9E4CDAB0B43`. Parent owner helper `086…`, launcher `10435…`, owner plan/tests and ignored runner were not touched. No producer/downstream, tool/service/runtime or §4.2/§4.3 changes. Checks here were SOURCE inspection, SHA comparison and diff-check only; this worker ran no Python/import/compile/platform tests. Await independent Lovelace SOURCE, then parent alone schedules the two focused GREEN nodes and original §4.1 regression under qualified ownership and an explicit grant; no broad platform execution authorization is assumed.

## 11. Actual §4.1 incremental GREEN and P2 truthful-report TEST-first handoff

Parent supplied physically terminal qualified-outer results after independent Lovelace SOURCE GO on base `489D3D88` / plan `A8CD7EAE`:

- Job-attachment P1: `3567 / afcbee`, 1 passed in 7.04s.
- Popen-after P1: `85036 / f73c0c`, 1 passed in 7.36s.
- Both outer reports: passed / rc 0 / settled / slot true, 339 pins unchanged.
- Original 13-selector sequential, stop-first-failure campaign: `66565 / d2d845`, physical terminal exit 0, **21 passed / 5 skipped / zero warnings across 13 node calls**, 339 pins unchanged on each call. Skips were genuine POSIX-only cases (4 + 1).
- Combined with the two separately run P1 nodes: **23 passed / 5 skipped §4.1 incremental evidence**. This is NOT full legacy regression, Linux execution, full C4a acceptance or independent fresh QUALITY. Prior API/behavioral failures remain recorded above.

Reviewer additionally identified a P2 at base line 850: the inherited startup wrapper says the suspended child was terminated even when Popen outcome is unknown. Parent now released TEST-only preparation; production remains frozen at `489D3D88B44AA56403E37516AF87C0AD331CA18D41048F2E42EF6A6A5ADDC12F` until actual RED and separate implementation authority.

Proposed exact SOURCE/RED selectors, both in `tests/test_docking_command_cancellation.py`:

1. Existing `test_c4a_popen_after_real_child_failure_cannot_settle`: retain all actual child/flags/zero-resume/non-settlement/marker/finally checks. Narrowly capture a single injected exception object and assert it remains the helper wrapper's original `__cause__`; require the C-only fixed helper text `Windows command startup failed; physical settlement is unconfirmed.` and no terminated/private-sentinel claim. Physical assertions precede the new message assertion, distinguishing this P2 from the already-fixed P1.
2. NEW `test_c4a_no_scope_popen_after_keeps_legacy_report_and_cause`: use the same existing genuine resource fixture, isolated child argv and transparent Popen-after fault, but directly instantiate the legacy adapter without any scope. Require the original outer/helper messages and the identical original injected exception object in the cause chain, alongside actual child/zero resume/no marker and independent finally rescue. This preserves no-scope compatibility; it is not an assertion that the legacy termination claim is scientifically/physically sound.

No new fixture or framework and no production/runner/owner-helper edit. The new compatibility selector may require a **separate parent amendment/SOURCE review of the closed outer-owner selector list** before execution; this test grant does not modify or authorize that list. Parent holds the Python slot for G3 RED. Await Lovelace SOURCE on these exact tests, then an explicit parent run grant under qualified ownership. No Python/import/compile/native execution by this worker, no §4.2/§4.3 expansion, and no GREEN inferred for this new text boundary.

## 12. Actual P2 message RED and minimal C-only text SOURCE handoff

Lovelace TEST SOURCE READY covered tests `82AA11DD8E965E973C013AF49EC9ECFB080B0CCD36A1956001C522BA6E5E2263` / plan `FB55572A5CC903A22CDEDCC3737A33FC9D580F6D2993ADB51553E40C6CFAC65E`. Parent clarified that the SOURCE-approved whole-module closed mapping naturally includes the new no-scope compatibility node; a second individual-selector mapping is not needed. The existing Popen-after selector remains the exact focused P2 RED node.

Parent supplied actual physical terminal **75247 / b23b81**, exit **1**, **1 failed in 8.06s**, **339 pins unchanged**. At test line 1808 the expected unconfirmed-settlement message differed from the actual terminated claim. All preceding physical ownership and original-cause identity assertions were reached successfully. Outer `test_failed` / settled / slot true describes the supervised test process, not a successful production settlement receipt. This establishes the text defect without reopening the repaired P1 findings.

Parent also reported qualification of the F37 whole-module owner: finite controls **44730 / 3632fb**, **49 passed in 5.44s**, and Windows qualification **55100 / fa21eb**, **3 passed in 2.39s**, with **12 pins qualified**. These are owner-qualification evidence, not feature-module GREEN or Linux coverage. Parent then released only `base.py` C startup text and this receipt; no execution was delegated to this worker.

Minimal candidate: after the existing startup cleanup returns, a non-null C receipt raises `RuntimeError("Windows command startup failed; physical settlement is unconfirmed.") from error`. The original error object remains the cause; receipt uncertainty, cleanup operations and secondary cleanup-error handling are untouched. The no-scope branch retains its exact original text and exception behavior. No new settlement claim, timeout change, retry, consumer integration or test modification.

Feature tests remain frozen at `82AA11DD8E965E973C013AF49EC9ECFB080B0CCD36A1956001C522BA6E5E2263`. SOURCE reading, hash checks and `git diff --check` only; no Python/import/compile/network/native execution, no commit, no owner-helper/runner changes, and no §4.2/§4.3 work. Freeze this candidate for independent Lovelace SOURCE before parent GREEN. All prior failures and platform skips remain recorded; no new GREEN or full-C4a acceptance is claimed.

## 13. §4.1 parent GREEN and independent fresh QUALITY checkpoint

After Lovelace SOURCE READY on base `2903F15665463A5BA98E34744CA03EE6B70D1EF5D971108F2CE7D77A2FC0262E` / plan `8B682D06D6B30280BA0CA1439E28234D1C5224B617A6F3C5351AFC1450FC2A1E`, parent supplied these physically terminal results with tests frozen at `82AA11DD8E965E973C013AF49EC9ECFB080B0CCD36A1956001C522BA6E5E2263`:

| Run | Actual handle / terminal | Actual result |
| --- | --- | --- |
| Parent focused P2 GREEN | `99554 / 0a1d9e` | 1 passed in 7.32s. |
| Parent whole command module through qualified F37 | `54341 / b38790` | Physical exit 0; 72 passed, 6 POSIX skips, 0 warnings in 33.76s; all 339 pins unchanged. The no-scope compatibility control passed. |
| Independent Lovelace fresh §4.1 whole-module QUALITY through F37 | `40403 / fb6f52` | Physical exit 0; 72 passed, 6 POSIX skips, 0 warnings in 33.34s; all 339 pins unchanged; outer passed / settled / slot true. |

HEAD at this checkpoint is `07e751a875b94ebdaf3a4adc7399003977febddc`. Independent fresh §4.1 QUALITY is PASS; the global Python slot was released. The six POSIX skips remain absent platform coverage, not passes. These results cover the command module / §4.1 checkpoint only, not Linux execution, scientific Vina acceptance, §4.2 service/tool consumption, §4.3 lease integration or full C4a completion. Prior actual RED and cleanup evidence remain intact above.

This receipt-only amendment changes no tests, production, runner or owner files. This worker ran no Python/import/compile/native tests and made no commit. Freeze the updated own physical plan for the parent's scoped §4.1 checkpoint commit; §4.2 test preparation still requires its separate release.
