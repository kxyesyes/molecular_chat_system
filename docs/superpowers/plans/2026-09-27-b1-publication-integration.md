# B1 Publication Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans when implementation is released. All steps below are future gates, not permission to execute now.

**Goal:** Integrate the reviewed dormant B1 publication prerequisite while preserving the current loop's real cancellation contract and all landed parser/source safeguards.

**Architecture:** Reuse DecisionEvents, AgentEventBus, WorkflowRunSession and the existing WorkerOwner. Separate ordinary preterminal cancellation from invalidation after a terminal publication attempt; release a candidate only after owned drain and final checks. No new event engine, scientific producer or Web activation.

**Tech Stack:** Existing Python/asyncio, LangGraph, SQLite, pytest and isolated Conda runner.

**SOURCE review (2026-09-27):** Parent reported independent Euclid SOURCE APPROVE for plan blob `950cfaaa94d151f8168eb856b4e9bac36902e4b2`, with no P1/P2 findings. This records source review only, not test execution or a test-pass claim. Only this review note was added afterward; parent will separately commit the plan. Implementation remains prohibited until the real loop PR lands, parent aligns this worktree, re-pin is completed and parent releases implementation.

## 1. Authority, base and re-pin gate

Only this plan may be written now. No implementation, tests, Python/imports, runner creation, network, assets/configuration changes, staging, commit or push. The old loop worktree and its frozen plan/files remain untouched. Parent owns alignment, execution-slot allocation and publication.

- Worktree: `D:/MedChat/molecular_chat_system_worktrees/b1-publication-integration`.
- Branch: `codex/b1-publication-integration`.
- Verified planning HEAD: `5d36eee2977152fe5047dc21e260500d6c965c4b` (PR93); initially clean.
- **This base does not contain the pending loop integration.** In particular, `tests/agent/test_decision_binding_loop.py` is absent. Do not copy production from the old dirty worktree or infer landing from a review/CI result.
- Read-only comparison reference, not an integration base: loop eight-file manifest SHA256 `a676a81cc3096e09dc5dbd5d8836ad02909fa5be416a4c077d5a50db1e7b8be0`; loop blob `963682752927ff6e52ce235158699526691129e2`, loop-test blob `d298090d240f441a9739c9da03bb88c99b1c97b1`, adapted Session-test blob `54b12b4cc2d46b93998a6d7911e4d15359f0c66f`.

- [ ] Wait for the real loop PR landing and parent alignment of this worktree. Record reviewed head, actual landed SHA/tree equality, aligned HEAD and parent release here; re-audit any intervening source delta before writing code.
- [ ] Re-pin all eight loop files and preservation files from the aligned tree, not from historical test results. If the approved loop freeze differs, report the exact delta rather than overwrite it.
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

At planning HEAD, execution/event-bus blobs are `bd06d6cebe8f0e70b0752ed8bd80dc783e675682` / `5fa19fa776569e3b7bed1a7dd58435871afcc36d`, equal to donor parent. Loop/Session are still pre-loop blobs `fc90509594b78e64782b0fd620f0715c412e0762` / `488994ce2784eadd16a2a5d49bf841216d185314`: **not extraction-ready**. In the audited pending loop freeze, Session equals donor parent `94b03a668834a2a994bde8157b603ec6d28fde1c`; loop is parent `251cd452ddfc69ad442de82dd4818bcebcb4a2ed` plus the two reviewed cancellation catches. Reconfirm these relationships after landing. Record final adapted hashes honestly; they cannot be called donor-exact.

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

- [ ] After section 1 release, use `apply_patch` to add the donor publication tests with the explicit section 3 correction and boundary pairs. Keep the existing loop test byte-for-byte unchanged. SOURCE-review the adapted expectations before execution.
- [ ] After a separate sole-slot grant, run the focused publication suite on the aligned loop baseline. Preserve actual RED output; absent publication APIs may explain contract RED but are not behavioral barrier evidence. Existing four cancellation cases should remain GREEN, not be presented as new RED. Obtain parent acceptance of the precise failures before production work.
- [ ] Apply only the four production paths: frozen event bus, B DecisionEvents projection, Session correction journal, then loop barriers/phase adaptation. Use exact donor extraction via `apply_patch` only where the re-pinned source permits it; preserve the current loop cancellation behavior rather than discarding its catches wholesale.
- [ ] Preserve pre/post-callback source checks even after commit-then-raise, including terminal metadata, finish/event/status and waiting-status fallback. Result release remains outside `_run`, after actual owner drain/final clock check. Correction remains append-only, failure-only, bounded to two attempts per write and unable to resume ordinary finish; failed durability is reported unconfirmed.
- [ ] With explicit slot permission, run focused GREEN and the unchanged four cancellation nodes. Record real handles to terminal without restarting; capture before/after hashes and failures verbatim. Stop for parent review on failure, without unapproved repairs or scope expansion.
- [ ] After independent SOURCE approval and parent slot grant, run the exact60 selection once. Capture five changed files, all eight loop preservation files, all60 targets, preservation dependencies and approved runner hashes before/after; record exact command, actual session, terminal exit, duration, counts and warnings. Release slot at terminal and await independent fresh QUALITY; no automatic repeat/publication.

The launcher is the existing isolated Conda `-I -S -B` workflow, not bare pytest. Prior loop-runner SHA256 `1317F77937C508A2E99DBBA693A4E046672B8D5B583229B03D074323A3E1508B` identifies the **old-tree** runner, not an approved runnable file for this new tree. Parent must separately authorize any REPO-literal-only relocation, byte-for-byte reverse comparison and new hash before creation/use. No runner is created by this plan.

## 5. Exact60 regression order

This is the prior approved58 in unchanged order, then publication and event-stream modules. Read-only existence audit at planning HEAD: 60 unique paths; 58 exist. Entry39 arrives only with the real landed loop; entry59 is the new publication test. Recheck all60 after alignment/authoring; do not skip either missing path or claim collection from this list.

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
