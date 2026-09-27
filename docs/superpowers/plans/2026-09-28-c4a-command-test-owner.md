# C4a Command Test Owner Qualification Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Do not execute platform tests before the qualification gates below.

**Goal:** Run the already approved C4a physical-settlement tests under an independently qualified retained outer process owner.

**Architecture:** Derive an ignored local wrapper from the existing B1 owner. Keep its lifecycle implementation unchanged; change only trusted primitive loading, fixed test root and selector mapping. Reuse the existing three real-Windows qualification cases, not a second process manager.

**Tech Stack:** Existing Python subprocess/threading, Windows Job primitives, pytest and the isolated MedChat interpreter.

## Scope and evidence

- Worktree `docking-physical-settlement`, branch `codex/docking-physical-settlement`, base `07e751a875b94ebdaf3a4adc7399003977febddc`.
- This is an execution-safety prerequisite to the approved physical-settlement plan, not a new production feature, scientific acceptance, permission to change timeouts, or a C4a completion claim.
- Existing API RED `43230/17e112`: one failure at missing CommandOwnershipScope, 8.17s, twelve pins unchanged. It reached no physical-resource fixture.
- Wrapper source: `dynamic-bindings-b1/scratch/b1_web_root_gate_supervisor.py`, SHA256 `AC259293AC752A0F305CFDBAB492BC7932EECF47C7D54CD023B747E0090E5E09`. Its independent qualification `56717/13a0d5` passed 152 tests; actual campaign `64628/15dbd0` stopped at the original 720s watchdog and settled ownership. Neither establishes this derived wrapper until new qualification.
- Trusted primitive source: `docking-consent-execution/src/docking/adapters/base.py`, SHA256 `431EA4AB580A755C8D903F157EA4D9CCF91F6E44FC124491366FA4C6A9042726`. Never load the C4a implementation under test as its own outer safety mechanism.
- C4a inner runner: `scratch/ordinary_chat_offline_runner.py`, SHA256 `0AD88551C1DC20527364D940855ABEE303EFB465DB67A4258FDEB25353EBCD61`; keep its network ban, synthetic environment/cwd, fixed interpreter and explicit selectors.
- Existing qualification source: `dynamic-bindings-b1/tests/agent/test_b1_web_root_supervisor_windows.py`, SHA256 `89E91E073B7C2BED6CF58C009C3794B4FA2E04BB9D4470E53241B87869C30EB1`.

## Allowed files

1. This plan and receipts.
2. New `tests/test_c4a_command_test_owner.py`: local qualification controls; explicit opt-in so ordinary CI does not depend on other local worktrees. A missing prerequisite in an explicitly requested run must fail, not silently skip.
3. Ignored `scratch/c4a_command_gate_supervisor.py`: derived wrapper.
4. Ignored `scratch/c4a_command_gate_run.py`: thin fixed-selector launcher.

Do not edit B1/C2 files, C4a feature tests, production code, scientific assertions, CI or other runners in this task. Parent and independent reviewer must accept this small test-allowlist extension before preparation. Use apply_patch for files; do not commit scratch assets.

## Required derived-wrapper differences

Read the fixed C2 primitive bytes once, validate their complete SHA256, then compile/execute exactly those bytes in a detached module. Missing/mismatched bytes fail before any process starts. Do not hash once and use a second loader read; no fallback to C4a base. Keep loading independent of `src.docking` package imports.

Keep `_ROOT` fixed to the C4a tree. Replace the B1 selector table with only these complete selectors, each mapping to a one-element tuple containing itself:

```text
tests/test_docking_command_cancellation.py::test_c4a_command_scope_api_present
tests/test_docking_command_cancellation.py::test_c4a_scope_registration_and_seal_precede_creation
tests/test_docking_command_cancellation.py::test_c4a_scope_normal_command_settles_real_resources
tests/test_docking_command_cancellation.py::test_c4a_windows_job_configuration_failure_retains_acquired_handle
tests/test_docking_command_cancellation.py::test_c4a_fast_spawn_failure_registered_before_start_returns
tests/test_docking_command_cancellation.py::test_c4a_windows_late_spawn_retains_actual_owner
tests/test_docking_command_cancellation.py::test_c4a_windows_setup_failure_keeps_acquired_resources
tests/test_docking_command_cancellation.py::test_c4a_windows_exited_root_job_close_failure_is_unresolved
tests/test_docking_command_cancellation.py::test_c4a_capture_exit_notification_is_not_join
tests/test_docking_command_cancellation.py::test_c4a_spawner_finally_notification_is_not_thread_exit
tests/test_docking_command_cancellation.py::test_c4a_posix_pending_spawn_and_setup_failure_retains_owner
tests/test_docking_command_cancellation.py::test_c4a_posix_root_exit_does_not_settle_live_group
tests/test_docking_command_cancellation.py::test_c4a_windows_root_exit_does_not_settle_live_job
tests/test_docking_command_cancellation.py::test_c4a_job_attach_failure_after_real_acquisition_cannot_settle
tests/test_docking_command_cancellation.py::test_c4a_popen_after_real_child_failure_cannot_settle
```

Set `_JOIN_NODES = {}`: B1 permanent-join rescue is inaccessible. Do not use that special case to promote forced termination to successful C4a qualification. Reject arbitrary argv, whole modules, B1 selectors and unknown selectors before owner reservation or process creation. Future full regression modules/consumer tests require a reviewed mapping amendment, not runtime discovery.

Preserve the remainder of the AC259 lifecycle byte-for-byte, including its original 720/+5 observations, registration-before-thread-start, late-child no-resume, entire Job termination/query, readers, actual joins, process/Job handle closure, capture bounds and unresolved ownership retention. Prove inverse replacement restores the original complete source hash. Do not reinterpret the +5 observation as permission to abandon an unresolved owner.

The thin launcher calls only `supervise(selected)` and returns zero only for outcome `passed`, returncode zero, ownership `settled`, slot_released true, and no retained owner. An actual timeout, force, test failure or unknown cleanup remains failure. Read the existing receipt vocabulary from source before implementation; do not invent a synonymous success value.

## Task 1 — finite source and rejection controls

- [ ] Independently review this plan/allowlist extension before authoring tests.
- [ ] Add call-phase assertions for wrapper existence/API (no new wrapper imported during collection).
- [ ] Verify trusted source/root/closed selector constants, same-buffer execution, and inverse lifecycle equality. Exercise wrong digest/missing primitive bytes in a finite loader seam that starts no process and never modifies the real C2 file.
- [ ] Verify every fixed selector's ordered argv and reject unknown/B1/module/arbitrary selections before a Popen/owner allocation spy can run.
- [ ] Verify thin-launch success and all negative receipt combinations with finite fixtures; mocks here are harness contract evidence only.
- [ ] Execute the exact finite nodes via the C4a inner runner for expected missing-wrapper/API RED, with frozen inputs and physical terminal receipt. Reviewer confirms failures are intended, not collection/fixture defects.
- [ ] Only then derive the wrapper/launcher; independent source review precedes GREEN.

## Task 2 — reuse real Windows qualification

Use the verified existing qualification source buffer in a detached namespace. Rebind only fixed ROOT/HELPER/RUNNER and the fixed invocation expected by `_WindowsCase`; keep the original fixtures, payloads, event barriers, profiler observations, cleanup and assertions. Do not register or execute its unrelated tests. Hash-check and execute the same source bytes, not a second read. This local reuse is explicit and opt-in; it is not a portable CI dependency.

Expose one parametrized qualification node `test_c4a_owner_windows_qualification[clean|descendant|late]`, calling the existing:

```text
test_windows_clean_exit_has_physical_receipt
test_windows_parent_exit_retains_pipe_held_descendant
test_windows_late_popen_return_never_resumes
```

The qualification Popen seam substitutes only the original harmless payload, with fixed MedChat `-I -S -B`. Real Job assignment/resume/terminate/query, root wait, reader/thread joins and handle close remain genuine. Its controlled observation clock is qualification-only; actual C4a execution uses the unchanged real clock.

- [ ] Independent SOURCE checks reuse, fixed invocation, platform reason and teardown before any real child.
- [ ] Run all three with actual Windows resources; retain the slot if physical cleanup is uncertain. Assert facts before fallback fixture rescue.
- [ ] Report three receipts, counts, exits, skips, timings and before/after pins separately from feature/scientific tests.
- [ ] Only a passing source/contract/Windows qualification releases explicit C4a feature selectors to the parent. No implicit full-module, POSIX or scientific run authorization.

## Task 3 — release record

Record exact new wrapper/test/launcher hashes and trusted dependencies. Record every attempted run and its original failure; never replace a missing result with a plan. The approved C4a production implementation remains separately reviewed by Lovelace. No commits, push, merge, production configuration or real tool execution are implied by this document.

## TEST-FIRST preparation (no execution / wrapper implementation)

Parent released only this plan and new `tests/test_c4a_command_test_owner.py`.
The approved thirteen selectors above are repeated literally in the test oracle,
not discovered from feature tests or from a future wrapper. Source-only inspection
confirmed the existing Windows fixture already provides `fixed_invocation`; no
new process manager or modified fixture/payload is needed. Socrates owns the
feature tests, physical-settlement plan and production adapter independently;
none is edited here. B1 remains frozen during parent run58204/c3f99a.

The finite tests fix these minimal private APIs for the later implementation:

- Derived wrapper preserves AC259 imports and every byte outside three regions:
  `_ROOT =` through before `_WindowsJob =` (fixed root and trusted loader),
  `_NODES =` through before `_JOIN_PREFIX =` (closed nodes / empty join map), and
  the existing `# Closed Task8 manifest;` region through before `_OS_FIELDS =`
  (thirteen ordered singleton targets). Inverse replacement must reconstruct the
  exact complete AC259 source, not merely AST-equivalent Owner code. The B1
  diagnostic names/error strings remaining in this inherited body are unchanged.
- `_TRUSTED_BASE` is the fixed sibling C2 primitive Path and `_TRUSTED_SHA256`
  equals431EA4...; `_load_trusted_primitives()` has no caller-supplied path/argv.
  It reads once, verifies the digest, and compiles/executes that same bytes object
  in a detached ModuleType. Digest mismatch raises fixed
  `RuntimeError('untrusted command primitive')` before compile; missing bytes
  preserve the original FileNotFoundError. There is no fallback to C4a base.
- `_NODES == _TASK8_ORDER == SELECTORS`, `_JOIN_NODES == {}`, and
  `_TASK8_TARGETS == {selector: (selector,) for selector in SELECTORS}`. Existing
  `supervise` and `_launch` retain their signatures/lifecycle. Finite argv controls
  call the real `_launch` with its real `_Owner`, inert Job/flags seams and a
  Popen spy that stops before any real process/thread; these are harness-contract
  facts, never physical qualification. Unknown inputs call actual `supervise`
  with owner/Popen-forbidden spies, proving rejection before reservation.
- Thin launcher exposes `_gate` and `main(argv)` for one explicit selector list;
  CLI wiring will call it with `sys.argv[1:]`. Invalid cardinality/selector/type
  raises ValueError before supervise. It invokes supervise once; zero requires
  outcome `passed`, native-int returncode0, ownership `settled`, literal
  slot_released True and `_active_owner is None`. Failure, timeout, unknown rc,
  bool rc, uncertain ownership, false/non-bool release and retained owner return1
  without changing the raw receipt. Success also requires exactly the four keys
  outcome/returncode/ownership/slot_released; extra or missing keys return1.
  No synonym success or retry.

New file has eight test functions / static49 cases: fixed API1, inverse source1,
trusted buffer3, fixed argv13, rejection10, receipt13, argv rejection5, Windows3
plus one local opt-in fixture which is not a test. No collection or
execution has verified these static counts. The first seven test functions are
finite46 cases. All cross-worktree reads/imports are inside CALL-phase helpers;
ordinary directory-level CI skips this local module via an autouse opt-in fixture
before reading dependencies. Explicit selection of this file or a node is opt-in
and missing/mismatched prerequisites fail instead of skipping. No environment
flag is needed or smuggled through the isolated runner's six-field environment.

Windows reuse reads89E91... once, validates and executes that SAME buffer in a
detached namespace; only ROOT/HELPER/RUNNER and `_WindowsCase.fixed_invocation`
are rebound. `windows_case.__wrapped__(monkeypatch)` runs the original generator;
finally closes that generator to execute its unchanged retained-owner cleanup
and profiler restoration. Only the three approved test bodies are called. Their
actual assertions precede fixture rescue; Windows absence/profiler presence are
failures in an explicit run, not silently skipped qualification. New wrappers
remain absent until actual finite RED and a separate implementation grant.

Exact API-only RED candidate (NOT RUN; source review and parent grant first):

```powershell
C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_c4a_command_test_owner.py::test_c4a_owner_fixed_api_control
```

Remaining finite selection, separately granted after review:

```text
tests/test_c4a_command_test_owner.py::test_c4a_owner_source_inverse_control
tests/test_c4a_command_test_owner.py::test_c4a_owner_trusted_same_buffer_control
tests/test_c4a_command_test_owner.py::test_c4a_owner_fixed_argv_control
tests/test_c4a_command_test_owner.py::test_c4a_owner_rejects_before_reservation_control
tests/test_c4a_command_test_owner.py::test_c4a_owner_launcher_receipt_control
tests/test_c4a_command_test_owner.py::test_c4a_owner_launcher_rejects_arbitrary_argv_control
```

Real-Windows qualification selector (not yet authorized to run):
`tests/test_c4a_command_test_owner.py::test_c4a_owner_windows_qualification`
with exact `[clean]`, `[descendant]`, `[late]`. No actual C4a feature test runs as
part of this qualification; its intercepted command uses the harmless original
payload. Qualification does not authorize the feature campaign itself.

Complete task-owned input manifest for SOURCE/future qualification: this plan,
new test file, the original AC259 B1 supervisor, original89E91 Windows fixture,
trusted431EA C2 primitive, local0AD885 inner runner, and (only after approved
implementation) the two new ignored wrapper/launcher files. Record all complete
hashes and C4a HEAD before/after execution. Missing wrapper/launcher at preparation
is intentional; do not invent their pins. Feature source/test/physical-plan pins
remain parent/Socrates-owned and will be frozen separately for real feature runs.
Only static source/hash/diff inspection and apply_patch were used here. No Python,
collection, imports, compilation, subprocess qualification, commit or network run.

SOURCE follow-up: API-only preparation received GO; full preparation retained a
P2 missing closed-receipt coverage finding. Parent authorized only two new receipt
parameters, `extra_key` and `missing_key`: add one fixed harmless extra key or
remove ownership from the otherwise valid receipt. Both must return native-int1,
invoke supervise exactly once, and leave the supplied receipt unchanged under
the existing assertions. Other tests and all thirteen selectors are unchanged.
This is a test coverage correction, NOT a demonstrated bug in the still-absent
launcher. No Python or implementation was performed; revised SOURCE goes back
to Lovelace before execution.

## Actual API RED and implementation SOURCE freeze

Parent API-only run91378/44390d -> bb20de physically terminal:1failed in1.28s,
exit1, all10 pins unchanged. It reached CALL-phase assertion for the missing
`c4a_command_gate_supervisor.py`; this was the intended absent-wrapper API RED,
not collection failure, physical Windows qualification or a C4a feature failure.
Parent then explicitly released the two ignored wrappers and this receipt,
leaving tests120E671E81C8C49B4A1BB07D196185832CB648CBE86EF3D3B66EF12AC3AE63B2
frozen. No Python execution accompanied this implementation.

The supervisor now derives mechanically from the pinned AC259 bytes, replacing
only the three approved regions. Trusted431EA bytes are read once per load,
hash-checked and passed as the same object to compile/exec in a detached module;
missing/mismatched source cannot fall back to the C4a adapter. The exact thirteen
singleton selectors are installed and `_JOIN_NODES` is empty. Everything outside
the three regions remains byte-identical, including inherited B1 names and
unreachable join-proof code, original720/+5 observations and all actual owner
registration/thread/Job/capture/handle lifecycle mechanics.

The thin launcher loads that fixed local gate and calls supervise exactly once
for its one approved string argument. Invalid arguments are rejected before
supervise. It requires a native dict with exactly the four approved fields,
native field types (nullable rc may remain failure), and retains the raw closed
receipt when rendering `C4A_OUTER_RECEIPT`. Only actual passed/native-int0/settled/
literalTrue/no-retained-owner returns0. Malformed receipt shape/types return1
without rendering arbitrary extra data; failed/watchdog/unknown/retained outcomes
cannot be promoted to success. No receipt mutation, fallback, retry or new owner
framework was added.

Frozen wrapper SHA256:

```text
scratch/c4a_command_gate_supervisor.py
15A99CAB73B506840956D3677F27197A56C161B7DF6FD3B74D8B397F4A9AD90E
scratch/c4a_command_gate_run.py
1043519F52097B63D5D7BF7995175D28F2D6E461E2332B6869F922FF1636764A
```

PowerShell static inverse comparison of the actual on-disk supervisor restored
complete original bytes and SHA256AC259293AC752A0F305CFDBAB492BC7932EECF47C7D54CD023B747E0090E5E09.
Both wrappers are confirmed gitignored. Frozen test120E and inner runner0AD885
match; no B1/C2 dependency, production, feature test or physical-plan edit was
made. C4a HEAD remains07e751a875b94ebdaf3a4adc7399003977febddc. The two additional
feature test functions visible in the concurrently owned file are intentionally
NOT auto-added: they are outside this approved thirteen-selector manifest and
would require a separate reviewed mapping amendment for execution.

Next gate is Lovelace independent implementation SOURCE, then separately granted
finite46 controls and three real-Windows qualification cases. None has been run
by this implementation task; no GREEN, physical settlement or scientific result
is claimed. Parent G3 cold27800 retains the Python slot. No imports, compilation,
collection, local service/model/network calls, commit or push were performed.

## Parent qualification and two-selector amendment

Independent SOURCE cleared wrapper15A99/launcher1043519/test120E/plan421E.
Parent finite controls8015/50585e then completed **46passed5.30s**,exit0;
the separate Windows qualification95575/0f51d4 completed **3passed2.28s**,exit0.
Both had zero warnings/skips and all12 frozen inputs unchanged. Actual processes
were terminal; these receipts qualify the local outer owner, not production
CommandOwnershipScope or scientific docking.

The two P1 feature regressions in test source7D3E received independent TEST
SOURCE READY. Parent now explicitly amends the selector table above from13 to15
with those exact two names, and appends the same names to the existing test
SELECTORS tuple. No other controls, original selector ordering, fixture code,
feature assertions or runtime implementation change. The existing wrapper still
has13 selectors at this TEST-FIRST checkpoint, and base2A664 stays frozen.

- [ ] SOURCE reviews this exact two-line test/table amendment.
- [ ] Run fixed_api_control and the two new fixed_argv_control parameter nodes
  for expected closed-table RED. They remain finite and create no real child.
- [ ] Only after RED, append the exact two names to the ignored wrapper's fixed
  tuple; do not change its lifecycle or thin launcher. SOURCE checks inverse
  recovery to15A99 after deleting only those two lines.
- [ ] Run the resulting finite48 controls and repeat the unchanged three real
  Windows qualifications on the new wrapper hash, with actual terminal receipts.
- [ ] Then separately authorize each real P1 feature selector through the fixed
  outer launcher, preserving720/+5, before any production fix. An outer timeout
  or uncertain cleanup is not a successful reproduction or resource release.

Current test SHA97B0A43C53320E82D4E20238995E8B743C7F1DBF91CCE8E1710CCE32D8FC039D.
Two earlier thirteen-selector paragraphs are historical evidence, superseded
only by this explicit table amendment. No feature execution is claimed here.

Actual mapping RED34462/2092f8->bd6185 completed **3failed1.75s**,exit1,
12pins unchanged. The API control observed13versus15; both new fixed-argv
controls reached missing-key assertions before _launch. No real child was
created by those controls. This is a table-contract RED, not production P1 proof.
Parent then appended only the two exact selectors to the ignored supervisor
tuple. Launcher, lifecycle, feature tests and base remain unchanged. New SOURCE
and finite48/Windows3 qualification are required before the actual feature REDs.

After independent SOURCE of wrapper08682/launcher1043519/test97B0/plan386D,
parent27948/6d6536 completed **48passed5.71s**,exit0, then11901/9baa4a completed
**3passed2.24s**,exit0; both had zero warnings/skips and all12 pins unchanged.
Each original session reached physical terminal before the next run.

The now-explicitly granted feature nodes were run separately through that same
fixed outer launcher, not directly through unprotected pytest:

- 23278/0dc1e0->d8a2bb: Job attachment failure **1failed8.11s**,exit1.
  Real acquisition and zero-Popen prerequisites passed; line1751 observed
  scope.settle() True despite expected False.
- 61945/671119->122347: post-creation Popen failure **1failed8.18s**,exit1.
  Real suspended-child/zero-resume prerequisites passed; line1800 observed the
  same false settled receipt.

Both outer receipts were test_failed/returncode1/ownershipsettled/slot_releasedtrue,
all339 source/runner/test pins matched, and fixture cleanup reported no failure.
The outer receipt describes test-process cleanup, NOT successful production
scope settlement. Parent released only the two minimal base.py fixes plus the
physical-settlement plan receipt; feature tests and owner mechanics remain fixed.
These are genuine resource behavior REDs, not real scientific docking.

## Complete legacy-module mapping amendment (TEST-first)

The two source-reviewed P1 repairs passed individually through the unchanged
qualified owner:3567/afcbee1passed7.04s and85036/f73c0c1passed7.36s, each with
339pins unchanged and physical rc0/settled/slottrue. Original13 selector campaign
66565/d2d845 then completed21passed5skipped (all5 genuinely POSIX-only on Windows),
with339pins unchanged after every invocation and exit0. This is not complete
legacy regression or Linux qualification. New source489D remains under a separate
C-only diagnostic-wording TEST-first follow-up; no broad C4a completion is claimed.

Independent Lovelace SOURCE recommended the minimal complete legacy route:
append exactly `tests/test_docking_command_cancellation.py` as a singleton mapping
to the existing closed table. No new process manager, arbitrary module, wildcard,
subset or raw unowned pytest route. This explicit amendment supersedes the
earlier prohibition only for that ONE complete module. All other modules remain
denied before ownership reservation or process launch.

Parent TEST-first amendment appends the literal module to SELECTORS and changes
the existing gate/launcher forbidden-module examples to the still-unapproved
`tests/test_docking_configuration.py`, retaining all rejection assertions. The
original15 node selectors, platform fixtures and owner lifecycle are unchanged.
The wrapper remains08682 with15 entries until actual finite RED.

- [ ] Independent SOURCE approves this exact test/plan amendment.
- [ ] Run fixed_api_control plus fixed_argv_control for the newly allowed module;
  expect missing mapping in finite seams before actual child creation.
- [ ] Only after RED, append one literal wrapper tuple entry; inverse must restore
  complete08682 source. Thin launcher10435 and all lifecycle code stay fixed.
- [ ] SOURCE and the resulting finite49 controls plus unchanged3 Windows
  qualifications precede any full-module feature run.
- [ ] Run the complete original legacy plus C4a module on its final frozen source,
  preserving720/+5 and original scientific/physical assertions. Keep failure,
  platform skips and incomplete ownership distinct from success.

This amendment does not authorize a full-module run before the separate gates,
nor edit production, feature tests, trusted C2 primitives, B1 files or timeouts.

Actual finite RED21790/700cb6 completed2failed1.49s,exit1,12pins unchanged:
fixed_api observed the missing sixteenth entry; new module fixed_argv reached
KeyError before _launch/Popen. Parent then appended only the literal module to
the ignored wrapper tuple. Launcher, ownership lifecycle and all tests are
unchanged. SOURCE inverse and finite49/Windows3 requalification remain required;
this table amendment has not yet run any whole legacy module.

Independent SOURCE verified the single-line inverse from F37DE363 to08682.
Actual finite qualification44730/3632fb completed49passed5.44s, then the unchanged
three Windows cases55100/fa21eb completed3passed2.39s. Both had zero warnings/
skips, physical exit0 and12pins unchanged. This qualifies the one-module mapping;
it is not the complete feature-module result.

The separately granted existing Popen-after feature node75247/b23b81 then
completed the intended truthful-report RED:1failed8.06s, physical exit1,
339pins unchanged, outer test_failed/settled/slottrue. All prior physical and
original-cause assertions preceded line1808's fixed-text mismatch: the old
helper still claimed termination instead of unconfirmed physical settlement.
Author is released for the minimal C-only message fix and its own plan receipt.
Feature tests82AA remain frozen. Focused GREEN and the complete module still
require that fix's independent SOURCE; no whole-module run has occurred yet.

## Complete module and independent Windows section4.1 QUALITY

Minimal C-only truthful-message source2903F156/physical-plan8B682D06 passed
independent SOURCE with frozen feature test82AA. Parent focused99554/0a1d9e
completed1passed7.32s, followed by the exact whole-module54341/b38790:
72passed6skipped33.76s. Both ended rc0/settled/slottrue with339pins unchanged.

Independent Lovelace then ran the same complete module once through this F37
owner, not bare pytest: session40403, launch50a98b, terminalfb6f52. Result:
72passed6POSIXskipped0warnings33.34s,exit0. Before4ac384/after928bb7 confirmed
339/339 pins unchanged and HEAD07e751a. No edits or retry. Global Python slot
was released only after physical terminal.

This independently qualifies Windows section4.1 and old command-test coverage,
including genuine-resource no-scope compatibility. Six POSIX skips are missing
platform coverage, not passes. Service/tool/runtime/lease sections4.2/4.3 and
real scientific docking are still pending. Earlier REDs and startup uncertainty
remain recorded; no unknown-resource settlement claim is inferred from test
fixture rescue or from an outer process receipt.
