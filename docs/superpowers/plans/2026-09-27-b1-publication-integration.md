# B1 Publication Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans when implementation is released. All steps below are future gates, not permission to execute now.

**Goal:** Integrate the reviewed dormant B1 publication prerequisite while preserving the current loop's real cancellation contract and all landed parser/source safeguards.

**Architecture:** Reuse DecisionEvents, AgentEventBus, WorkflowRunSession and the existing WorkerOwner. Separate ordinary preterminal cancellation from invalidation after a terminal publication attempt; release a candidate only after owned drain and final checks. No new event engine, scientific producer or Web activation.

**Tech Stack:** Existing Python/asyncio, LangGraph, SQLite, pytest and isolated Conda runner.

**SOURCE review (2026-09-27):** Parent reported independent Euclid SOURCE APPROVE for plan blob `950cfaaa94d151f8168eb856b4e9bac36902e4b2`, with no P1/P2 findings. Parent subsequently committed the plan/review note as `4c5768d`. That approval is not a test-pass claim or approval of the newly prepared test source. Current preparation-only release and re-pin evidence are recorded in section 7; production and execution remain held.

## 1. Authority, base and re-pin gate

Current release permits bounded implementation of the four production files and this plan after parent acceptance of section 8 RED. No test edits, test execution, Python/imports, worker runner creation, network, assets/configuration changes, staging, commit or push. The old loop worktree and its frozen plan/files remain untouched. Parent owns alignment, execution-slot allocation and publication; Bohr owns the next science slot. Source freeze is in section 9.

- Worktree: `D:/MedChat/molecular_chat_system_worktrees/b1-publication-integration`.
- Branch: `codex/b1-publication-integration`.
- Historical planning HEAD: `5d36eee2977152fe5047dc21e260500d6c965c4b` (PR93), which lacked the pending loop integration. No production was copied from the old dirty tree.
- Current parent-aligned HEAD: `7a0418073ca8619ddc347214edf41affdd58185b`, containing actual PR96 landing `aa3cdddbc1bcdf964ae95384c1a8740e44ff4205`. Before preparation it was clean and differed from that landing only by this plan.
- Re-pinned landed loop eight-file manifest SHA256: `a676a81cc3096e09dc5dbd5d8836ad02909fa5be416a4c077d5a50db1e7b8be0`; full blobs are in section 7.

- [x] Verify real loop PR landing and parent alignment locally: section 7 records reviewed/landed tree equality and the limited preparation release.
- [x] Re-pin all eight loop files and fourteen preservation files from the aligned tree; all match actual landing and the audited freeze, with no production delta.
- [ ] Obtain separate parent implementation/runner/sole-science-slot releases. User delegated publication choices within the goal do not grant this worker commit/push/merge authority; parent retains exact-head CI/review/tree gates.

Sources already audited: exact donor `2c7fd00d16bcae0d289e926519d19f98b951b469`, parent `2b2f53c95c0faa2443c9cd2cfdfa894e48b05071`; all four production files and its 932-line publication test. At that donor, `docs/superpowers/specs/2026-09-26-b1-decision-execution-design.md` blob `c8ca87dd78eef2b29fa67c7a6a09d18d519cadd4`, lines 360–445/456–491, supplies publication/ownership/frozen-event requirements; the accompanying execution plan Task6 supplies the prerequisite boundary. These are source references, not documents to copy wholesale. Current normal-Web design `docs/superpowers/specs/2026-09-25-web-decision-runtime-integration-design.md`, lines 111–147, retains real cancellation and five outcomes; waiting is a separate lifecycle state.

## 2. Exact 4+1 source manifest and dependencies

All paths below are repository-relative. Only these four production paths and one new test path belong to future code scope; this plan is the sole documentation path.

| Path | Exact donor Git blob | Responsibility / integration rule |
|---|---|---|
| `src/agent/harness/decision_execution.py` | `582a6fb707c91cfaaa593ac0dd10e8043921552c` | B-only allowlisted provisional terminal summaries and frozen correction delivery; donor-exact candidate after re-pin |
| `src/agent/runtime/event_bus.py` | `d3c580bec2b6c7f428e266b048f5516bba644449` | Keyword-only strict `frozen=False`; detached private journal/copies, stable ID/time and at-most-once callback; donor-exact candidate |
| `src/agent/runtime/run_session.py` | `bf162d1fafef2caf471fa75a74b3165e9b7e84bc` | Opt-in immutable failure-only correction journal, deferred through in-flight positive writes; donor-exact candidate |
| `src/agent/harness/decision_loop.py` | `1616f4082966b03f1d81d237e3c6cbb375a78ba3` | Owned barriers, private finalization record, drain-before-release; adapt cancellation to section 3, never whole-blob overwrite |
| `tests/agent/test_decision_binding_publication.py` (new) | `dc375a29b0418269b528fc5a2c104905b3917595` | Preserve donor barrier suite; one explicit semantic correction plus cross-boundary integration regressions |

After re-pin, execution/event-bus blobs remain `bd06d6cebe8f0e70b0752ed8bd80dc783e675682` / `5fa19fa776569e3b7bed1a7dd58435871afcc36d`, equal to donor parent. Landed Session equals donor parent `94b03a668834a2a994bde8157b603ec6d28fde1c`; landed loop `963682752927ff6e52ce235158699526691129e2` is parent `251cd452ddfc69ad442de82dd4818bcebcb4a2ed` plus the two reviewed cancellation catches. Production extraction is still unauthorized. Record final adapted hashes honestly; they cannot be called donor-exact.

Preserve without edits:

- All other loop files; especially the four native/JSON × tail-acceptance/tail-boundary cases in `test_decision_binding_loop.py`, including pending owner/run, real blocked worker, no premature terminal, physical drain, unchanged calls and single persisted `task_cancelled` assertions. Preserve the adapted Session test and separate `test_decision_owned_call.py`; do not restore duplicate historical tests.
- `src/agent/contracts/target_request.py` (`2ec6087fdf3276a97154fd436a282caf83f44713`), `tests/agent/test_binding_analysis_clause_integration.py` (`6ceed90d4072b77204bfad186d7f1fff853ca149`) and `tests/agent/test_target_analysis_phrase.py` (`8033991b1f346d64f117c23cfaa46aa6eefeaa83`): whole original query, four whole-batch obligations, initially empty journal, no zero-phase science, explicit target guards.
- Current `src/agent/tooling/rag_contract.py` / `target_contract.py` redaction-proof rejection; `src/agent/tools/rag_search_tool.py` formatting-failure normalization; `src/rag/service.py` detached dtype/source-byte matching; `src/reverse_target/owned_source.py` strict TSV structure and cancellation checks; current producers/adapters. No historical dependency rollback.
- Seven actual model-selected tools, whole-batch upstream bindings/evidence IDs, first source/deadline-error latch, Session-only consumed-reference guard exception, and expired consumed-reference protection.

WorkerOwner, adapters, binding acceptance/input helpers and the publication test's source-hooks/acceptance/legacy-loop fixture modules matched donor at source audit. Publication parent differs from `0064c30` only by intervening documentation commits. No revision8 implementation is a prerequisite to this bounded slice.

## 3. Approved cancellation reconciliation

| Boundary | Required result |
|---|---|
| Before terminal finish attempt starts, including final acceptance and post-terminal-metadata owned validation; no invalidation latch | Real worker drain, ordinary `CANCELLED`, one `task_cancelled`, persisted `cancelled`; clear success answer/waiting, acceptance explicitly unsatisfied/not finish-eligible; retain real tool records, no extra model/tool dispatch |
| Successful/waiting terminal attempt has started; cancellation during final validation or owner drain | Failure-only `FAILED`, reason `cancelled`, sanitized invalidation correction; no answer, scientific payload or nonce release; preserve historical provisional attempt |
| Any existing source/deadline/publication invalidation latch | Latch wins; cancellation or source restoration cannot overwrite its reason or resume positive writes |
| Final outward release already completed | Late cancel does not relabel/reopen the final result |

Use the existing private request-local finalization record for explicit phase/cancellation disposition. Mark terminal-attempt entry before `finish_dynamic` can invoke callbacks. Never infer safety from `_terminal_event_emitted`, current database status or an exception: writes can commit then raise. Terminal-metadata writes alone do not mean a terminal event attempt has begun. Repeated cancellation of an already selected ordinary-cancel path must not by itself create a second failed terminal; actual invalidation/cleanup failure remains authoritative. All owned work still drains, owner settles once, and no source work starts after sealing. Do not catch arbitrary `BaseException`, weaken validation or alter timeouts.

**Unique donor-test semantic conflict:** `test_review_cancellation_in_final_acceptance_settles_and_corrects_started_run` (donor lines 812–849) exercises preterminal cancellation but asserts failed/invalidated through `assert_sanitized`. Correct this one case to the approved ordinary-cancel contract while retaining its actual owned acceptance worker, cancellation while blocked, running/pending assertions, drain, one terminal and exact model/tool counts. Assert result/event/store agreement, cancelled acceptance, no success answer/waiting/nonce and no invalidation marker when no failure occurred. This is a documented behavior correction, not unconditional donor-assert replacement to obtain GREEN. Keep the shared failure-only `assert_sanitized` helper unchanged, and keep the donor post-attempt repeated-cancellation test's FAILED assertions.

Preserve every existing source-mutation barrier case: terminal metadata, terminal event, terminal status and waiting status, both normal callback return and commit-then-raise. Keep frozen-copy isolation, retry bounds, immutable history, reentrant invalidation, memory-only durability denial and post-drain clock/latch cases. Add real-worker boundary pairs in the new publication module for preterminal cancel versus post-attempt cancel, repeated cancellation and prior-latch dominance; include commit-then-raise with an unconfirmed event flag. Never delete a barrier, accept either status, fake a cancellation exception instead of blocking real owned work, or abandon the blocked worker.

## 4. Gated implementation and evidence sequence

- [x] After preparation release, use `apply_patch` to add the donor publication tests with the explicit section 3 correction and boundary pairs; existing loop test is byte-for-byte unchanged. Independent Lovelace SOURCE approval and the initial execution are recorded in section 8.
- [x] Run the separately authorized initial selection on the aligned loop baseline. Parent accepted section 8's four behavioral REDs, one API RED and eight passing controls before releasing production work; no full-module baseline result is claimed.
- [x] Apply only the four production paths via `apply_patch`: frozen event bus, B DecisionEvents projection and Session correction journal are donor-exact; loop barriers adapt cancellation explicitly. Section 9 records source hashes, not a GREEN result.
- [x] Source-check preservation of pre/post-callback checks, including commit-then-raise, terminal metadata, finish/event/status and waiting-status fallback. Outward release remains after actual owner drain/final clock check; correction remains append-only, failure-only and bounded. Independent review and execution of this source freeze remain pending.
- [ ] With explicit slot permission, run focused GREEN and the unchanged four cancellation nodes. Record real handles to terminal without restarting; capture before/after hashes and failures verbatim. Stop for parent review on failure, without unapproved repairs or scope expansion.
- [ ] After independent SOURCE approval and parent slot grant, run the exact60 selection once. Capture five changed files, all eight loop preservation files, all60 targets, preservation dependencies and approved runner hashes before/after; record exact command, actual session, terminal exit, duration, counts and warnings. Release slot at terminal and await independent fresh QUALITY; no automatic repeat/publication.

The launcher is the existing isolated Conda `-I -S -B` workflow, not bare pytest. Parent prepared the new-tree ignored `scratch/ordinary_chat_offline_runner.py` with `apply_patch`; its read-only verified SHA256 is `6567BAF7D9AE7C525004EE7D5CD7BC83EDD8DF47136A47FC6CBF33568039207E`. Parent reports full forward/reverse byte equality with approved admission runner `6D49050C00D66642F2045828AAAE927C7254B8C282ACCFC0EC1E24A7AF5623AD`, except the REPO literal. This worker neither created nor executed it. Preparation is not execution/slot authorization.

## 5. Exact60 regression order

This is the prior approved58 in unchanged order, then publication and event-stream modules. After alignment and test preparation, a read-only existence audit confirms all60 paths exist and are unique. Entry39 came from the actual landed loop; entry59 is the new publication test. This is a path audit, not pytest collection or a test result.

Future command prefix, only after the preceding gates: `& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py`. Each line below is one explicit positional argument, in this exact order; no `-k`, directory substitution or shortened selection.

```text
tests/agent/test_decision_binding_inputs.py
tests/agent/test_decision_binding_acceptance.py
tests/agent/test_decision_binding_profiles.py
tests/agent/test_decision_binding_arguments.py
tests/agent/test_decision_binding_requirements.py
tests/agent/test_decision_binding_session.py
tests/agent/test_decision_dynamic_bindings.py
tests/agent/test_decision_owned_call.py
tests/agent/test_decision_target_status.py
tests/agent/test_ordinary_capabilities.py
tests/agent/test_evidence_ledger.py
tests/agent/test_decision_contract.py
tests/agent/test_decision_requirements.py
tests/agent/test_decision_inputs.py
tests/agent/test_binding_resolver.py
tests/agent/test_candidate_alignment.py
tests/agent/test_activity_tool_contract.py
tests/agent/test_analysis_contract.py
tests/agent/test_scientific_reference_store.py
tests/agent/test_workflow_run_session.py
tests/agent/test_dynamic_run_session.py
tests/agent/test_run_session_ownership.py
tests/agent/test_worker_ownership.py
tests/agent/test_delegated_session_lifecycle.py
tests/agent/test_delegated_session_parity.py
tests/agent/test_decision_adapter_retry.py
tests/agent/test_decision_loop.py
tests/agent/test_target_tool_contract.py
tests/agent/test_domain_result_validators.py
tests/agent/test_reverse_target_complete_input.py
tests/agent/test_current_source_tool_hooks.py
tests/agent/test_rag_receipt_consumption.py
tests/agent/test_reverse_receipt_consumption.py
tests/agent/test_rag_tool_contract.py
tests/agent/test_rag_current_eligibility.py
tests/test_rag_retrieval_outcome.py
tests/test_rag_owned_generation.py
tests/test_reverse_target_invocation_receipts.py
tests/agent/test_decision_binding_loop.py
tests/agent/test_decision_continuation.py
tests/agent/test_decision_continuation_store.py
tests/agent/test_decision_history.py
tests/agent/test_decision_clarification.py
tests/agent/test_decision_protocol_recovery.py
tests/agent/test_decision_merge_blockers.py
tests/agent/test_decision_spec_findings.py
tests/agent/test_decision_migration_boundaries.py
tests/agent/test_decision_validator_unavailable.py
tests/agent/test_family_activity_tool.py
tests/agent/test_ordinary_chat_policy.py
tests/agent/test_ordinary_admission_budget.py
tests/agent/test_ordinary_continuation.py
tests/agent/test_decision_transport_boundaries.py
tests/agent/test_web_decision_runtime.py
tests/agent/test_web_decision_runtime_references.py
tests/agent/test_web_decision_runtime_lifecycle.py
tests/agent/test_binding_analysis_clause_integration.py
tests/agent/test_target_analysis_phrase.py
tests/agent/test_decision_binding_publication.py
tests/agent/test_agent_event_stream.py
```

Selection rationale: retain current binding/parser/source/ownership/Session/legacy/Web regressions; add actual publication boundaries and the directly changed shared event bus's legacy contract. Historical donor results and prior loop 6107-pass evidence do not validate this new integrated freeze.

## 6. Explicitly deferred

Revision8 `a5189a5f854050e59c6fc8ed4f8a397313c09067`, authenticated replay/CAS invalidation-marker rejection, reserved tail credit and actual snapshot-publication barrier are a separate batch. B continuation remains disabled here; waiting-status fallback and the historical-snapshot preservation test are not revision8 completion. No normal-Web B enrollment, frontend status handling changes, model/asset/API changes, deployment or complete P7/P8 claim. Future Web enrollment must understand provisional `answer_released=false` events before exposing B results. No test-pass claim is made by this plan.

## 7. Actual re-pin and test-preparation handoff (no execution)

PR96 reviewed head `41972379a993b57ae7859bd3bd3d607e06824378` and actual squash `aa3cdddbc1bcdf964ae95384c1a8740e44ff4205` both resolve locally to full tree `3931f512e6c013c1117f9ec38ca146c93fc45515`. The landing is an ancestor of parent-aligned HEAD `7a0418073ca8619ddc347214edf41affdd58185b`. Parent reports CI36277170168 9/9, core10709 passed/1 skipped and Web224 passed, plus fresh guards; these are parent-supplied upstream evidence, not execution of this publication integration. Release covers re-pin and test preparation only.

Re-pinned loop preservation manifest (all equal to the landed tree):

| Path | Git blob |
|---|---|
| `src/agent/harness/decision_bindings.py` | `0ebd9aa7aa046aaa7a7f9c8275d761638236cf48` |
| `src/agent/harness/decision_continuation.py` | `b44a0cbcbab7d13286c2c3c4fe94a65f4b5750af` |
| `src/agent/harness/decision_loop.py` | `963682752927ff6e52ce235158699526691129e2` |
| `src/agent/harness/decision_policy.py` | `52d723de6901597b9b2e2478d2078ad959444089` |
| `src/agent/runtime/run_session.py` | `94b03a668834a2a994bde8157b603ec6d28fde1c` |
| `tests/agent/test_decision_binding_inputs.py` | `bd377facce7f997572fb9a51a33e46451ed77205` |
| `tests/agent/test_decision_binding_loop.py` | `d298090d240f441a9739c9da03bb88c99b1c97b1` |
| `tests/agent/test_decision_binding_session.py` | `54b12b4cc2d46b93998a6d7911e4d15359f0c66f` |

Its digest remains `a676a81cc3096e09dc5dbd5d8836ad02909fa5be416a4c077d5a50db1e7b8be0`. Additional preservation manifest digest: `5cf65b4604ad232c98bd4dcb93a9965478e158ed64ecccc77504f87dd2361003`, all fourteen paths equal to actual landing. Both digest formats are UTF-8 without BOM, `path<TAB>Git blob<LF>`, final LF, in listed order. Additional preservation order:

```text
src/agent/harness/decision_execution.py
src/agent/runtime/event_bus.py
src/agent/contracts/target_request.py
tests/agent/test_binding_analysis_clause_integration.py
tests/agent/test_target_analysis_phrase.py
src/agent/tooling/adapters.py
src/agent/tooling/rag_contract.py
src/agent/tooling/target_contract.py
src/agent/tools/rag_search_tool.py
src/rag/service.py
src/reverse_target/owned_source.py
src/agent/tools/reverse_target_tool.py
tests/agent/test_decision_owned_call.py
src/agent/runtime/worker_ownership.py
```

Prepared publication test blob: `f2628b6e2e60b95c996e3a97fab32dbb3ab16480`; file SHA256 `1BBDA2868A6C615CFF0980270CE8C6379DA25EE8BFEDABC192A04B6A7F145651`. Text comparison confirms all34 donor test functions retained, with only the approved final-acceptance cancellation block changed before appended additions; all four callback barriers and their raises parameters are unchanged. Six added test functions cover payload release, explicit API contracts, native/JSON pre/post repeated-cancel pairs, real-source invalidation latch precedence, commit-then-raise/false-event-flag cancellation, and late cancel. Function/path counts are source facts, not collected case counts. No production or existing test file changed.

### Proposed exact focus nodes, pending SOURCE review and execution grant

Initial behavioral RED selection, in order (four explicit nodes; each uses existing baseline APIs). Source-expected failure is stated, not claimed observed:

```text
tests/agent/test_decision_binding_publication.py::test_actual_loop_terminal_callback_never_receives_scientific_candidate
tests/agent/test_decision_binding_publication.py::test_actual_loop_rechecks_source_after_each_terminal_callback_even_if_it_raises[False-terminal_event]
tests/agent/test_decision_binding_publication.py::test_actual_loop_rechecks_source_after_each_terminal_callback_even_if_it_raises[True-terminal_event]
tests/agent/test_decision_binding_publication.py::test_real_owned_cancel_respects_publication_attempt_boundary[postattempt-native]
```

Expected causes respectively: full real RDKit candidate fields reach the terminal callback; terminal callback source close is not rechecked before returning success; the same after callback side effect then RuntimeError/retry; repeated cancellation of actual owner drain escapes as CancelledError/leaves succeeded rather than returning a sanitized failed correction. No missing keyword/API or fixture setup error counts as these behavioral failures. All blocking tests release and await their workers/tasks in `finally`; no restart or abandoned worker is permitted.

Preservation/control selection (not advertised as RED), in order; the first function has the four unchanged native/JSON tail cases:

```text
tests/agent/test_decision_binding_loop.py::test_tail_cancellation_drains_owned_worker_and_persists_one_cancelled_terminal
tests/agent/test_decision_binding_publication.py::test_review_cancellation_in_final_acceptance_settles_and_corrects_started_run
tests/agent/test_decision_binding_publication.py::test_real_owned_cancel_respects_publication_attempt_boundary[preterminal-native]
tests/agent/test_decision_binding_publication.py::test_real_owned_cancel_respects_publication_attempt_boundary[preterminal-json]
tests/agent/test_decision_binding_publication.py::test_late_cancel_does_not_relabel_released_result
```

Separate API-only contract node: `tests/agent/test_decision_binding_publication.py::test_publication_optins_are_explicit_contracts`. It asserts signature/method presence without using the new-API Session fixture; an absent opt-in is contract RED only. Do not run the whole donor module as initial preflight and count constructor/setup failures as behavioral evidence.

After API/barrier implementation is separately released, focus additionally on `test_source_invalidation_latch_wins_cancel_during_real_owner_drain`, `test_committed_terminal_with_unconfirmed_flag_cancel_is_postattempt` and the postattempt-json pair in this module, then the full publication module plus unchanged cancellation controls. The false-flag test intentionally needs the new post-callback owned check; failure to reach that gate on the current baseline is not scientific behavioral proof. Follow section 4 review/slot gates before exact60. No test, import, Python compilation or runner execution was performed during this preparation.

## 8. Authorized initial invocation — terminal evidence (2026-09-27)

Parent reported independent Lovelace SOURCE APPROVE for prepared test blob `f2628b6e2e60b95c996e3a97fab32dbb3ab16480` and plan blob `ecf0a183825bca005cb14c6723be130185d37b5e`, preserving all34 donor test functions/all barriers/the existing four loop-cancel cases and exact60 selection. Parent then granted the sole Python slot for one invocation only. This section records that run, not approval to implement or run again.

**Invocation:** approved Conda `-I -S -B` / new-tree runner `6567BAF7D9AE7C525004EE7D5CD7BC83EDD8DF47136A47FC6CBF33568039207E`; all four behavioral nodes in section 7, then its five preservation/control entries, then the separate API-contract node, in exactly that order. Ten explicit node arguments expanded to13 executed cases. No shortened selection, second invocation or restart.

**Actual terminal:** session `40590`, launch chunk `9cd048`, terminal chunk `fce405`; process exit1 and `ORDINARY_PYTEST_EXIT=1`. Pytest reported **5 failed,8 passed,3 warnings in20.10s**. All five failure reports were `phase=call`; no setup/import/teardown error or skipped case was reported. The first four failures are behavioral RED; the last is API-contract RED only.

Every failing node below is in `tests/agent/test_decision_binding_publication.py` (prefix it plus `::` to obtain the exact node ID):

| Exact node suffix | Actual assertion / cause |
|---|---|
| `test_actual_loop_terminal_callback_never_receives_scientific_candidate` | Line972: `set(payload) == expected_keys` failed. Terminal callback received candidate fields including `final_answer`, `evidence`, `artifacts`, `metadata`, `tool_results_by_step` and `tool_result_sequence`, instead of the four-field pending summary. Real result success/tool count checks had passed before this assertion. |
| `test_actual_loop_rechecks_source_after_each_terminal_callback_even_if_it_raises[False-terminal_event]` | Line659 → helper268: expected `RunOutcome.FAILED`, actual `RunOutcome.COMPLETED`. Actual terminal callback closed the RAG source; source-close and one-tool/two-model-call assertions passed, but no final invalidation was reflected in the returned result. |
| `test_actual_loop_rechecks_source_after_each_terminal_callback_even_if_it_raises[True-terminal_event]` | Same line659/helper268 completed-versus-failed assertion after actual source-close plus callback RuntimeError following its side effect. Callback failure/retry did not supply the missing post-callback source verification. |
| `test_real_owned_cancel_respects_publication_attempt_boundary[postattempt-native]` | Line1082: `not isinstance(result, BaseException)` failed with diagnostic `('CancelledError', 'succeeded')`. Before that assertion, the blocked real worker had exited, its root finished, owner was settled with zero roots and exactly one owner-drain invocation. Repeated cancellation did not produce the required failure-only correction; SQLite still said succeeded. |
| `test_publication_optins_are_explicit_contracts` | Line983: missing `binding_profile` in `signature(DecisionEvents).parameters`; actual signature `(bus)`. This is an explicit API assertion, not a constructor/setup failure. Later assertions in this node were not reached, so this run does not separately prove the remaining API gaps. |

All8 controls passed: the four unchanged native/JSON × tail-acceptance/tail-boundary loop cases; the adapted donor final-acceptance cancellation case; native and JSON preterminal repeated-cancel pairs; and late-cancel preservation. This does not claim the full publication module or exact60 passed.

Warnings were exactly three `DeprecationWarning` entries at `<frozen importlib._bootstrap>:241`: builtin types `SwigPyPacked`, `SwigPyObject` and `swigvarlink` have no `__module__` attribute.

### Before/after manifest evidence

Pre-run snapshot and terminal post-run snapshot each contained25 unique files. Every Git blob **and** SHA256 matched pairwise, with zero differences: test/plan/runner + eight loop files + fourteen additional preservation paths, covering all four production targets. The plan row below is the executed/reviewed plan **before this subsequently authorized documentation append**; only this plan is now being updated. Test blob stayed `f2628b6e2e60b95c996e3a97fab32dbb3ab16480`, executed plan blob stayed `ecf0a183825bca005cb14c6723be130185d37b5e` across the run, and runner blob stayed `a5dc4987674c7e2d3cfd3a69f979d9177afabe4a`. Eight-loop and preservation14 blob digests remain those in section 7.

| File | Before = after SHA256 |
|---|---|
| `docs/superpowers/plans/2026-09-27-b1-publication-integration.md` | `6BF47F39A10676C4DB77C80B53216C562A4A47CB83200CE9D9D98520F9A91B19` |
| `tests/agent/test_decision_binding_publication.py` | `1BBDA2868A6C615CFF0980270CE8C6379DA25EE8BFEDABC192A04B6A7F145651` |
| `scratch/ordinary_chat_offline_runner.py` | `6567BAF7D9AE7C525004EE7D5CD7BC83EDD8DF47136A47FC6CBF33568039207E` |
| `src/agent/harness/decision_bindings.py` | `D9490F70E8389163668852E2A65F59FE57A42872B84E94D88D96C8EF0A3C9A15` |
| `src/agent/harness/decision_continuation.py` | `CA2506DFC83D8294A51255907C34A9D525B826B60A9D8AED50C3745171B1DC8C` |
| `src/agent/harness/decision_loop.py` | `881C5EBBABB07BB176BC65C6D068A63A37896F71E161E078D691507EC854F794` |
| `src/agent/harness/decision_policy.py` | `ADD42F61EB883554D846BAC84A26EE4D6297E17E7C5E2D7050C83CBEB6CC3B78` |
| `src/agent/runtime/run_session.py` | `7324C9BEB5240F0AB75A866705642F5671C18FA8E55B8B21907A804535DBA734` |
| `tests/agent/test_decision_binding_inputs.py` | `1F8092007DD9C2132435D47ABB507865F32799784835A1C537726E238A031D30` |
| `tests/agent/test_decision_binding_loop.py` | `B91F60FF053D4315DB07DD9960116A4AA4AD37462217553E86274EA23839D490` |
| `tests/agent/test_decision_binding_session.py` | `793A9500D45A6E0BD7D352C1465BCC32DC1BA02A5536594D06856586B2903071` |
| `src/agent/harness/decision_execution.py` | `E1A648747E69EF58818CF23B5217F0D76E320B06D45FE10423440036A0E199AA` |
| `src/agent/runtime/event_bus.py` | `B08FA00EEAA0BB85D2ED33873EDFF40349D27BAF602080AF9A0E4FAA00165B56` |
| `src/agent/contracts/target_request.py` | `E9083A1520D2FEC6537BBE2ACC43EB2C89161C699A5ED332B695CFDA9C496793` |
| `tests/agent/test_binding_analysis_clause_integration.py` | `7A5E11F3616638EFE70096158F60310CC6865E22A8C58316A3B355C8AE7C1863` |
| `tests/agent/test_target_analysis_phrase.py` | `94321B9FCE3A5C43272CFC09211D122CCB32B525BD1DBE85C65DB5283EFB4A10` |
| `src/agent/tooling/adapters.py` | `9486DC0E1D4EEAF05864460DD0F44A27D5C09D61A8588D6C234E3EAE25759E8E` |
| `src/agent/tooling/rag_contract.py` | `3CF44775CE5BFC90CAD01D200C803D699081C2B3E7BE10A0F4EE4FDDF80FB00D` |
| `src/agent/tooling/target_contract.py` | `F597826CA34AA43B20BBCBBCDEAD5C2F8D0DC563F0D2F1F76D5CD1B470423991` |
| `src/agent/tools/rag_search_tool.py` | `9CE093A5F4FAA8E6045A0CC2970CDC706AA618688BB30CE6BB128D1A08C864B9` |
| `src/rag/service.py` | `7168B1FF0FD37FDE1E1C42A476DC2E7CEC2D8B175CB7D2E3DA1CE5ED32327809` |
| `src/reverse_target/owned_source.py` | `D07E51CE68DFB901A309B520462EF1B1133B13A76D9965712E42C24866DF9D28` |
| `src/agent/tools/reverse_target_tool.py` | `D22E4192AAB3DED68722432CD4FF3E6639FA2B30EDAE0505D2AEEAC8D2FA1A44` |
| `tests/agent/test_decision_owned_call.py` | `5C3142863F6F97D5A06357488A67499AC60237E895B3DD9110E5C1FEE2A4B216` |
| `src/agent/runtime/worker_ownership.py` | `C9CC39B684AB42D8D3559C8EC66CFB50F6854641005B309885E9F7C6C3663CA7` |

The authoritative terminal was observed before slot release; **sole Python slot RELEASED**, no live handle retained, no retry. Production and tests were not edited; no implementation/commit/push authorization follows. Current permission is this documentation-only terminal record, pending parent acceptance.

**Coverage limitation retained:** there is no explicit direct source-close → source-restore publication scenario in this test preparation/run. Do not infer that coverage from source-close failures, latch design or other donor tests. The new source-invalidation-latch/false-event-flag cases and full publication/exact60 suites were not part of this invocation.

## 9. Implementation-only source freeze — review/slot pending

Parent accepted section 8 and released only the four production targets plus this plan. All writes used `apply_patch`; no tests, imports, Python compilation, runner invocation, commit or push occurred in this implementation phase. No source-close → restore test was added: the existing donor invalidation latch was retained, and that direct coverage caveat remains open rather than claimed satisfied.

| File | Frozen Git blob | SHA256 |
|---|---|---|
| `src/agent/harness/decision_execution.py` | `582a6fb707c91cfaaa593ac0dd10e8043921552c` | `D8C66637F9A6BBC744F69791F8E96CE0382719D8026AE8A61B757B1196C815A8` |
| `src/agent/runtime/event_bus.py` | `d3c580bec2b6c7f428e266b048f5516bba644449` | `FC0E167F1D9C8324EBE0A13FC6E67382AB979827609B7C1BC9EC65A833749E30` |
| `src/agent/runtime/run_session.py` | `bf162d1fafef2caf471fa75a74b3165e9b7e84bc` | `5F606B9F97A95A01BEDC667951A6C343F1C7B62770E12138C06FCE7AEF76105B` |
| `src/agent/harness/decision_loop.py` | `beb9ce7e4de27b725b128e3af0c1421881427a13` | `75A0C78143C7C6F4D287C93FD2FBDF1E6FF634F49E990BBC2629A76AE8D8089F` |
| `tests/agent/test_decision_binding_publication.py` | `f2628b6e2e60b95c996e3a97fab32dbb3ab16480` | `1BBDA2868A6C615CFF0980270CE8C6379DA25EE8BFEDABC192A04B6A7F145651` |

Five-file manifest SHA256: `531d9465517ee7f5293e1fc244f65cee92651d6535b4cea35f6083cb2c73f94c`, using the table order and section 7's `path<TAB>Git blob<LF>` encoding. The first three production files equal their exact donor blobs; the loop is explicitly adapted, not donor-exact. Publication test remains `f2628b6e...`; existing four-cancel loop test remains `d298090d240f441a9739c9da03bb88c99b1c97b1`.

Loop adaptation is confined to private terminal finalization:

- Explicit phases `preterminal`, `terminal_attempt`, `cancelled_attempt`, `released`; phase is entered before finish callbacks, never inferred from a delivery flag or database status. No wire DTO, provider, continuation or activation change.
- Pure preterminal cancellation projects CANCELLED, clears answer/waiting and records unsatisfied/non-finish-eligible acceptance. Metadata cancelled after its first write is rewritten inside the bounded metadata allowance with pre/post checks; cancellation at finish's precheck flushes the cancelled projection before attempting finish. Finish freezes current metadata, not the earlier successful acceptance snapshot.
- `publication_check` retains actual owned validation. A CancelledError is handled only after owned work/root settlement and the existing `owned()` finally first-error check; it does not bypass validation. Source/deadline/runtime/publication failure prevents ordinary-cancel projection. The guarded first-error latch and donor irreversible invalidation journal are unchanged.
- Post-attempt cancellation remains sanitized failure-only. An already verified ordinary-cancel candidate survives repeated cancellation during final owner drain only if there is no failure/invalidation; cleanup failure and final deadline checking remain authoritative. Owner is never reopened, and no source check runs after sealing.
- Commit-then-raise still runs post-callback validation, positive writes remain bounded, correction attempts are never reset, and old provisional events are never rewritten. Session/event-bus correction and frozen-delivery code are exact donor.

Read-only scope check against the initial25-file snapshots found changes only in the four authorized production paths and ownplan: the other20 files, including both test files, runner and all remaining preservation dependencies, were unchanged. Three-file donor diff is empty; `git diff --check` passed. These are source/hash checks, not scientific test evidence or independent SOURCE approval.

**Proposed precise focused GREEN command — NOT EXECUTED; requires independent review and a new parent slot grant:**

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py 'tests/agent/test_decision_binding_publication.py' 'tests/agent/test_decision_binding_loop.py::test_tail_cancellation_drains_owned_worker_and_persists_one_cancelled_terminal'
```

This runs the complete unchanged/adapted publication test module (including newly prepared latch/false-flag/postattempt-json cases) and the four unchanged PR96 cancellation cases. Use runner SHA256 `6567BAF7D9AE7C525004EE7D5CD7BC83EDD8DF47136A47FC6CBF33568039207E`, capture before/after hashes and retain the actual handle through terminal; no automatic retry or exact60 expansion. No execution slot is held by this worker; Bohr fresh strict QUALITY owns the next slot. This source freeze is ready for independent review, not a GREEN or publication claim.

## 10. Aligned freeze, independent SOURCE and focused GREEN

Parent's disjoint alignment evidence reference `fc3ff5` identifies aligned HEAD `3d3c43a819d28d269f657c1da5921456b34a988c`, merging landed `c612c9873a04823c936d710861bf890ad30b844a` into the publication branch. HEAD was re-read before and after the focused run; it was unchanged. The seven frozen owned/source/test/plan/runner paths had no alignment overlap: the five files in section 9, ownplan at executed blob `653b1fae9b986b0b19b0ad6b3ef685eb99f3f748` (SHA256 `C5394B679674E99BE4255BCE0D2F26CC718EBF718A5F72806FC64C66053DD440`), and runner blob `a5dc4987674c7e2d3cfd3a69f979d9177afabe4a` / SHA256 `6567BAF7D9AE7C525004EE7D5CD7BC83EDD8DF47136A47FC6CBF33568039207E`. This authorized append changes only ownplan after that verified run.

**Independent SOURCE evidence (separate from tests):** parent reported Lovelace APPROVE, no P1/P2, for production blobs `582a6fb707c91cfaaa593ac0dd10e8043921552c`, `d3c580bec2b6c7f428e266b048f5516bba644449`, `bf162d1fafef2caf471fa75a74b3165e9b7e84bc`, `beb9ce7e4de27b725b128e3af0c1421881427a13`, with unchanged publication test `f2628b6e2e60b95c996e3a97fab32dbb3ab16480`. SOURCE approval is not execution evidence.

**Actual focused GREEN:** exactly one approved offline invocation, in this order:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py 'tests/agent/test_decision_binding_publication.py' 'tests/agent/test_decision_binding_loop.py::test_tail_cancellation_drains_owned_worker_and_persists_one_cancelled_terminal'
```

Actual session `72548`, launch `c1895b`, authoritative terminal `64eab6`: exit **0**, `ORDINARY_PYTEST_EXIT=0`, **97 passed, 3 warnings in 43.81s**. The three warnings were SWIG DeprecationWarnings for `SwigPyPacked`, `SwigPyObject` and `swigvarlink` lacking `__module__`. Full publication module and all four unchanged PR96 cancellation parameters passed; no retry, expanded60 run, code/test edit, commit or push occurred.

Before/after snapshots compared all **25 unique paths**, using the same scope/order as section 8 (ownplan, publication test, runner, loop8, additional14): **25/25 Git blobs and SHA256 values identical**, zero differences. Selected publication test remained `f2628b6e2e60b95c996e3a97fab32dbb3ab16480` / SHA256 `1BBDA2868A6C615CFF0980270CE8C6379DA25EE8BFEDABC192A04B6A7F145651`; selected loop test remained `d298090d240f441a9739c9da03bb88c99b1c97b1` / SHA256 `B91F60FF053D4315DB07DD9960116A4AA4AD37462217553E86274EA23839D490`. The runner remained the approved6567 hash above. These comparisons precede this documentation-only append.

**Slot:** released immediately upon authoritative terminal, before documentation edits. Parent explicitly transferred it to Kant fresh ASCII exact49; this worker holds no Python slot. Section 9's pending-review/run language is historical and superseded only by the SOURCE and focused evidence above.

**Next selection, not executed:** section 5's existing ordered list was re-read: exactly **60 paths, 60 unique, zero missing**. Preserve entries 1–58 unchanged, then entry59 `tests/agent/test_decision_binding_publication.py` and entry60 `tests/agent/test_agent_event_stream.py`; any later invocation must pass all60 explicitly in that exact order, with fresh before/after hashes. This read-only path check is not collection or test evidence. Exact60 remains gated on fresh ASCII / priority original-Web gate and explicit parent authorization/slot transfer; no automatic execution.

**Historical RED and coverage caveat retained:** section 8's four genuine behavioral REDs, separate API-contract RED and eight GREEN controls remain unchanged. Neither focused97 nor independent SOURCE supplies an explicit direct source-close → source-restore publication scenario; do not claim that coverage. No provider/activation/revision8 scope is released.

## 11. Author exact60 terminal — independent fresh QUALITY pending

After explicit parent sole-slot release, exactly one offline invocation used approved runner6567 and all60 paths from section 5 as explicit arguments in their unchanged order, with the same Conda `-I -S -B` prefix as section 10. Actual session **46845**, launch `9ee52f`, authoritative terminal **4a5ecf**: exit **0**, `ORDINARY_PYTEST_EXIT=0`, **6208 passed, 7 warnings in 1015.62s (0:16:55)**. Warnings were three SWIG missing-`__module__` DeprecationWarnings and four FastAPI `on_event` DeprecationWarnings. The same handle was polled through terminal; no restart, retry, narrowed selection or concurrent worker invocation occurred.

Before/after evidence covers **78 unique files**: section 8's25-path scope followed by all section 5 targets, deduplicated by first occurrence. All60 targets existed, their order was unchanged, and **78/78 Git blobs plus SHA256 values matched exactly, zero differences**. Combined manifest SHA256 before and after: `BA37B05826BE3B82A4F338BFB4B5EC0B08DCCAA96CF3D4FBBEDA4620C9C7EA76` (UTF-8 without BOM; each ordered row is `path<TAB>Git blob<TAB>uppercase file SHA256<LF>`, including final LF). This includes all four production files, publication test, loop/preservation dependencies, plan and runner.

HEAD remained `3d3c43a819d28d269f657c1da5921456b34a988c`. Executed plan remained blob `e7c0ff40e3bafef11c163ceb50b9f763c6c0a29c` / SHA256 `14C11B0D0C4F84D03C0ACC16ACDC49BCD5458F6ED8A656239C2682C5178D9A33`; runner remained `6567BAF7D9AE7C525004EE7D5CD7BC83EDD8DF47136A47FC6CBF33568039207E`. Those are the run's before/after values, not the new plan hash after this authorized documentation append.

**Slot RELEASED at actual terminal**, before documentation work. Parent accepted author exact60; Web short-control RED now owns the slot, with Socrates fresh60 queued under separate authorization. This worker holds no Python slot and performed no further tests/imports/code edits/commit/push. Fresh QUALITY must independently freeze the updated plan; author GREEN is not fresh QUALITY or production/Web activation evidence.

**Preserved evidence and limitation:** all prior RED evidence remains unchanged, including section 8's four behavioral failures, separate API-contract failure and eight passing controls. An explicit direct source-close → source-restore publication scenario is still **not covered**; neither 6208 passes nor SOURCE approval closes that gap. No assertion was weakened or deleted, and revision8/provider/activation work remains deferred.

## 12. Independent fresh QUALITY and publication preparation

Socrates independently reviewed and executed the same exact ordered60 selection
once: session42930, launchf87cc2, terminal0f32a0, exit0 and ORDINARY_PYTEST_EXIT=0.
Actual result: **6208 passed, 0 skipped, 7 warnings, 1061.14s**. Warnings remain
three SWIG and four FastAPI deprecations. Independent QUALITY APPROVE has no
P1/P2 blocker for this frozen publication slice; it is not activation authority.

All78 Git blobs/SHA256 values, selection order and Git status matched before and
after, manifest D19F819BD5D2A7D00C3306E80553CAEB8654D8DAF132752D4FDB86C966A99969.
Executed plan SHA256 was D9BBFBDDD2A503725D03370413E152F8C62D83C2773E65C5428DC4B350FB6EB6;
runner6567 and all four production files plus publication test retain section9
identities. Parent independently rechecked those five hashes (eda59e). This
append changes only the plan after terminal; no rerun or source/test edit.

Slot released at the actual terminal. Latest-head full CI and exact reviewed/
merged-tree checks remain mandatory. PR98's separate sandbox-core CI failure is
retained and being investigated independently, not solved by these results.
Direct source-close→restore is still not demonstrated here. Revision8, ordinary
Web timing/dataflow, B2/C, real provider/science/browser acceptance and deployment
are not claimed by this slice; the last item remains out of scope.
