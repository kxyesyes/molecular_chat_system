# B1 Continuation Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans only after parent implementation release. Steps use checkboxes for future gates, not present execution authority. Parent reviews this document before any implementation.

**Goal:** Integrate bounded revision8 waiting-state continuation into the existing B1 loop, preserving the publication cancellation contract, scientific source authority, and legacy revision6/7 behavior.

**Architecture:** Add the donor's version-specific codec beside the existing continuation reader; integrate its replay, claim, restoration and reserved-tail boundaries into the publication-adapted loop, never overwrite that loop with the donor. Reuse the current SQLite CAS, Session, input journal, resolver, frozen events and worker ownership. Missing revision8 behavior/API establishes feature/API RED, separately from reproduction of the two source-reported donor defects. Require behavioral RED evidence only where an explicitly authorized prior-donor harness or a naturally reachable baseline actually demonstrates the defect. Otherwise retain the source findings and desired-behavior regressions with safe integration, reporting the first observed result honestly, including GREEN. Never introduce or deliberately retain a known defect solely to obtain RED; no defective intermediate checkpoint is required.

**Tech Stack:** Existing Python/asyncio, LangGraph, SQLite, pytest, real RDKit and temporary RAG/reverse sources, native/JSON scripted model decisions, parent-controlled isolated Conda runner.

## 1. Authority, source evidence and landing gate

Current release is **DOC-ONLY**: create/update this file with `apply_patch`. No source/test/runner changes, Python, imports, compilation, tests, assets, network, staging, commit or push. Reading local source/Git objects and hashing this document is allowed. Do not modify the dynamic-bindings or publication worktrees. Do not allocate a runner or science slot.

- Worktree: `D:/MedChat/molecular_chat_system_worktrees/b1-continuation-integration`.
- Branch: `codex/b1-continuation-integration`.
- Planning HEAD: `c612c9873a04823c936d710861bf890ad30b844a`; initially clean.
- Publication is **not landed for this integration**. This tree still has loop blob `963682752927ff6e52ce235158699526691129e2`, continuation blob `b44a0cbcbab7d13286c2c3c4fe94a65f4b5750af`, and loop-test blob `d298090d240f441a9739c9da03bb88c99b1c97b1`. It lacks the revision8 codec, continuation test and publication test. Do not copy publication code here to bypass landing.
- Exact continuation donor: `a5189a5f854050e59c6fc8ed4f8a397313c09067`. It is a source reference, not an approved whole-tree transplant or integrated test result.
- Approved Task5 sources read in `D:/MedChat/molecular_chat_system_worktrees/dynamic-bindings-b1`: `docs/superpowers/specs/2026-09-26-b1-decision-execution-design.md`, blob `765b42ef697715605dfaaa05254f174108059b1f`; `docs/superpowers/plans/2026-09-26-b1-decision-execution.md`, blob `736ba999db6b2a35a52e0d32ef514ef623c0f6de`. Both match those files at the donor. Relevant sections: pre-claim replay isolation, revision8 acceptance/continuation, segment clock/reserved tail, Task5 whole-live-observation comparison, Task6 publication prerequisites. Historical run reports there are not evidence for this integration.
- Parent reports Plato's SOURCE-only findings below; no independent runtime reproduction is claimed. Reading the donor shows the relevant snapshot await/exception and postclaim-start branches, but does not turn that report into RED execution evidence.

Read-only publication source reference, verified in its separate worktree at HEAD `3d3c43a819d28d269f657c1da5921456b34a988c`:

| Publication dependency | Reviewed Git blob |
|---|---|
| `src/agent/harness/decision_execution.py` | `582a6fb707c91cfaaa593ac0dd10e8043921552c` |
| `src/agent/runtime/event_bus.py` | `d3c580bec2b6c7f428e266b048f5516bba644449` |
| `src/agent/runtime/run_session.py` | `bf162d1fafef2caf471fa75a74b3165e9b7e84bc` |
| `src/agent/harness/decision_loop.py` | `beb9ce7e4de27b725b128e3af0c1421881427a13` |
| `tests/agent/test_decision_binding_publication.py` | `f2628b6e2e60b95c996e3a97fab32dbb3ab16480` |

The publication source review covered plan freeze `653b1fae9b986b0b19b0ad6b3ef685eb99f3f748`. This is provenance, not a claim that its current plan or eventual landing must have that hash. Its accepted direct source-close-to-restore publication test coverage caveat remains; do not quietly declare it closed.

- [ ] Parent reviews this document and its exact hash.
- [ ] Parent identifies actual publication landing commit/tree and aligns this worktree. Read-only check that the landing is an ancestor of the aligned HEAD; record both full hashes here.
- [ ] Re-pin the five dependencies above, three-plus-three scope below, publication's eight-loop/fourteen-preservation manifests, every regression target, and the parent-approved runner. Explain any difference from the reviewed source; obtain review for overlapping changes instead of resetting or replacing them.
- [ ] Confirm source/ownership/acceptance helpers and all test fixtures against the landed baseline, including changes since the donor. Preserve the current JSON bounds and parser/source repairs; do not restore historical dependencies.
- [ ] Obtain separate test-preparation, implementation and execution releases. No code/test release is effective before actual landing and re-pin. Parent alone assigns the sole execution slot and publication authority.

## 2. Exact future scope: three production and three test paths

All paths are repository-relative. This plan is the sole documentation path. The three shared publication production files above are dependencies to preserve, not additional edit scope.

| Action/path | Donor Git blob | Integration responsibility |
|---|---|---|
| Create `src/agent/harness/decision_binding_continuation.py` | `6d500e275af2dc671cb30230b5a0665182e8ae90` | Revision8 codec, detached unstarted projection, ordered historical replay, complete live-observation comparison; donor candidate after dependency audit |
| Modify `src/agent/harness/decision_loop.py` | `531025d0f7d0f650e9091afe07ba847aa81e6fb5` | Integrate donor continuation into landed publication loop; retain cancellation fields/phase, add snapshot discard and first-error/start reconciliation; explicitly adapted |
| Modify `src/agent/harness/decision_continuation.py` | `18c6714b1dda531768203d7722ca35e9cbfd1ded` | Minimal B-only revision8 fingerprint conditional; leave both revision6/7 readers, CAS publisher and nonce policy unchanged |
| Create `tests/agent/test_decision_binding_continuation.py` | `3e1990665a4e1e64ef996672c4a81e988fd5feb4` | Retain donor continuation regressions and add the two required desired-behavior regression families, durable-reopen matrix and real two-claimant coverage |
| Modify `tests/agent/test_decision_binding_loop.py` | `59221d7bbad8a4152d3a5ad1a6b12059cd44567f` | Only reconcile owned initial clarification: mint revision8, never legacy6/7; retain bogus nonce rejection and zero-action counts. Preserve all current cancellation tests exactly |
| Modify `tests/agent/test_decision_binding_publication.py` | `81516145a6cf5e91ae2ffdc9047c182576bb05b5` | Add waiting-publication coverage without replacing waiting-status coverage or current cancellation assertions; base is current `f2628b6...`, not historical donor publication test |

The donor changes seven paths including its own historical plan. Do not copy that plan. Do not whole-blob overwrite either existing test: donor loop tests predate the four landed cancellation cases; donor publication tests predate the six integration additions and contain the superseded preterminal FAILED expectation.

Preserve all34 original publication functions/barriers plus the six integration additions, including the approved ordinary-cancel helper and postattempt FAILED assertions. Retain the four native/JSON x tail-acceptance/tail-boundary cases byte-for-byte. The only approved pre-existing behavior change in the loop test is the no-legacy-nonce assertion becoming an explicit revision8 assertion for an authenticated owner. No either-status assertions, skips, xfails, deletion of barriers, or weakened tool/model counts.

Out of scope: Session/API changes, event engine, SQLite schema/CAS implementation, WorkerOwner, source services, registry/factory, provider/model transport, B2, normal Web routing, frontend, deployment, assets. A discovered dependency outside this six-file scope stops implementation for parent review.

## 3. Contract and design choices

### 3.1 Private replay before CAS, live authority only after confirmed claim

Use the donor's `validate_continuation` and `ValidatedReplay` contracts. Authenticate trusted-store owner/session/skill/original query, waiting status, exact nonce, checksum, revision8/profile, obligations and current configuration. Reject invalidation markers regardless of status. Bound the entire envelope at512KiB, including `claimed_by` in the proposed claim; observations retain their existing64KiB/native limits. A checksum is corruption detection, not protection against a malicious database writer.

The private projection is an actual unstarted `WorkflowRunSession`, with a fresh local ledger, detached observations and explicit validated proof authority. Never call start/restore/append/execute or toggle lifecycle flags on this projection; no events, writes, source loading/repair, tool calls or model calls. Preserve authenticated proofless preparation diagnostics without inventing proof or scientific authority. Replay original journal prefixes/proposals in order; future observations cannot supply earlier inputs. Reconstruct every role, reverse record handle, action/final key, model observation pair, acceptance and counter.

Before CAS, run owned source/closure verification and waiting-record reauthentication, allocate/copy/bound the complete claim, then check the original deadline immediately before the single real `transition_decision_continuation(..., claim=True)`. A false or uncertain CAS does not start the live Session, install results, admit the reply or dispatch. A commit-then-raise may leave a consumed/running row; never reset it or retry the claim as recovery.

After confirmed CAS, recheck source/store/configuration/deadline before and after start/restore callbacks. Restore only via the existing `restore_observations(..., binding_proofs=...)` API. Authenticate the complete ordered actual live sequence against detached canonical originals, then establish fresh seals in **one synchronous block with no await**:

```python
replay.verify_restored(session.results)
session._decision_observation_seals = MappingProxyType({})
for observed in session.results:
    seal_observation(observed, session)
```

Only afterwards install validated action records and verify closure. Do not trust a supplied seal or the staging seal map. Full comparison includes warnings/message/formatted/error/native types/count/order, not merely ledger/proof equality. Preserve legitimate key-order/JSON roundtrip semantics; distinguish False/0/0.0 and native container types. Only validated `error.details` None/exact-dict wire equivalence is permitted, never falsey coercion of False/0/[]/subclasses. Admit the new reply once through the journal after historical closure; preserve original subject/target/count and unchanged caller context.

### 3.2 Two clocks: segment budget and fixed finalization reservation

Capture `segment_start` before continuation read/decode/source work. Compute the initial B deadline from that instant. After validating saved finite native numeric credit, cap at `min(initial_deadline, segment_start + saved_credit)`; carry any already-authorized cap if such a path is separately enabled. This slice does not admit ordinary Web root-carry into B. Never reset to `clock() + saved_credit` after expensive restoration.

At snapshot start t, require R=deadline-t>0; choose F=min(30.0,R/4). Save `pre_finalization_remaining_seconds=R`, `finalization_reserve_seconds=F`, `remaining_seconds=R-F`. Set the tail cap before the owned snapshot call so source checks/copy/encoding consume F. Revision8 replay validates exact policy/arithmetic, finite native numbers and root bounds. All later finish/publish/check/drain work must fit `min(root_deadline,t+F)`. Do not refund unused reserve, increase credit, re-mint in a retry loop, or lengthen any timeout.

R100/F25 with20s tail grants at most75 to the next segment;30s tail fails sanitized and releases no nonce. Human waiting time between completed segments is not charged by this core budget. This is **not** the later Web inclusive-TTL/root-carry contract.

### 3.3 Required regression family A: owned snapshot cancellation and stale nonce

Plato reported that the donor snapshot await catches `DecisionBoundaryError` but not pure cancellation, and that naïvely retaining publication's cancel handler after adding a snapshot can leave `waiting_payload`/nonce alive. Retain this source finding and require desired-behavior regressions for both subcases below. Behavioral RED is required only where an explicitly authorized prior-donor harness or naturally reachable baseline actually demonstrates the defect; absent that evidence, safely integrate the protections and report the first observed result honestly, including GREEN. Missing revision8 behavior/API or incidental setup failure does not reproduce this donor defect. Do not introduce or deliberately retain the gap to obtain RED; no defective intermediate checkpoint is required.

1. Cancellation while the real owned snapshot worker is blocked, after graph clarification and before finish.
2. Cancellation after a valid snapshot exists but during finish's precheck, including after result metadata has already been assembled with a nonce.

Retain `ordinary_cancelled`, `cancelled_metadata` and `phase` (`preterminal`, `terminal_attempt`, `cancelled_attempt`, `released`). Initialize pending snapshot state before any closure can cancel it. Extend successful preterminal cancel projection to discard `waiting_payload`, clear snapshot creation bookkeeping, remove any already-built `continuation_id`, clear answer/waiting and set unsatisfied/non-finish-eligible acceptance. Do **not** refund/remove an established tail cap to manufacture time. Rebuild finish metadata from current state rather than retaining a stale nonce-bearing dict. Explicitly guard positive waiting publication on both still-waiting state and absence of ordinary cancellation.

The local discard operation is part of the existing cancellation handler, not a new Session API:

```python
# Pending snapshot variables must be initialized before cancel_terminal closes over them.
waiting_payload = None
checkpoint_created_at = None

# On successful ordinary preterminal cancellation:
waiting_payload = None
checkpoint_created_at = None
state.answer, state.waiting_for_input = '', False
# At finish, rebuild metadata and omit continuation_id unless still authorized to wait.
# At publication, require state.waiting_for_input and not _finalization.ordinary_cancelled.
```

Catch snapshot `CancelledError` only after owned worker/root settlement; use the existing first-error check before selecting ordinary cancellation. If `cancel_terminal()` refuses because a terminal attempt or failure exists, return the failure-only correction. A source/deadline/cleanup failure dominates cancellation. No fake cancellation exception replaces the real blocked-worker test.

| Boundary | Required outward and durable behavior |
|---|---|
| Pure snapshot or post-snapshot/pre-finish cancellation | Retain real tool records; ordinary CANCELLED, one `task_cancelled`, stored cancelled, acceptance cancelled; no continuation ID, no new waiting CAS/publish, no invalidation marker without failure |
| Terminal finish attempt entered, including commit-then-raise/false event flag | FAILED, reason cancelled unless a prior latch wins; sanitized correction, immutable provisional history, no outward payload/nonce |
| Already ordinary-cancelled candidate; repeated cancel during drain | Drain exactly once; remain CANCELLED absent actual source/deadline/cleanup failure; no duplicate failed terminal |
| Prior invalidation or source/deadline failure | First reason wins through source restoration, repeated cancel and callback exceptions; never resume positive writes |
| Released result | Late cancel does not reopen/relabel it |

For a resumed run, discard only the new pending snapshot. Never undo a consumed prior claim or erase historical continuation records. Postattempt waiting publication that already committed retains its historical snapshot plus failure marker; nonce is suppressed and future claim rejects.

### 3.4 Required regression family B: first postclaim error versus start commit-then-raise

Plato reported that an observed postclaim source/deadline failure is replaced by `continuation_start_failed` or `continuation_start_unconfirmed` when `session.start` then raises. Store the first bounded reason/boundary separately **before start**, preferring the existing guarded `failures[0]`. Later start/restore exceptions may describe secondary failure but cannot overwrite that first reason. Do not rely on the exception's raw text or on a later generic outer catch to reconstruct it.

Retain the source finding and required desired-behavior regressions independently of reproduction. Behavioral RED is required only where an explicitly authorized prior-donor harness or naturally reachable baseline actually demonstrates this overwrite. Otherwise include the latch in safe integration and report the first observed result honestly, including GREEN; missing revision8 behavior/API and incidental setup failure are not this reproduction. Never introduce or deliberately retain the overwrite solely for RED, and do not require a defective intermediate checkpoint.

Use two distinct authorities:

| Start outcome after confirmed CAS | Handling |
|---|---|
| Actual Session started, then wrapper raises | Bind finalization to this started Session; use the earliest source/deadline reason for failure-only correction. No restore/model/tool dispatch; stored failed if correction confirms |
| Start failed/committed side effect but Session remains unstarted | Return a detached sanitized FAILED fallback with the earliest reason and unconfirmed correction durability. Do not call Session invalidation/finish, invent lifecycle flags, bind correction authority, retry start or repair the running row |
| No earlier failure | Keep honest start-failed/start-unconfirmed diagnostics according to actual started state; neither means successful resume |

Exercise the unstarted case with a real start event/store callback that commits then raises before `_started` becomes true, as well as the started case with a wrapper calling real start then raising. A confirmed CAS does not grant Session correction authority. A prior-error latch is diagnostic authority, not permission to write.

### 3.5 Preserve donor safe correction fallback while integrating phases

The publication loop's present `invalidate` collects IDs from `session.results[*].quality`; that is unsafe once restore callbacks can tamper with quality. Adopt the donor `_BindingFinalization.invalidate` safe detached FAILED fallback **before** any diagnostics/callbacks, with empty optional evidence IDs and bounded native counters. Do not traverse rejected results/quality. Retain its frozen `_correction_request` and `_correcting` reentry guard; merge these fields with publication's phase/cancellation fields, not instead of them.

The outer post-drain path must retry the same frozen request if private failure exists even when Session correction was not installed. Session's existing journal owns the bounded per-write attempts; never reset its attempts, emit another correction ID, mutate previous positive results, or allow reentry to return the old candidate. Existing Session invalidation reason remains authoritative. An unstarted Session uses the separate fallback in section3.4 and never invokes this started-Session correction path.

### 3.6 Durable scope and legacy compatibility

Durability here means reopening the same waiting SQLite state in a fresh store/loop while authenticating still-eligible source authority. Donor `reopen` creates a new store and loop but reuses the registry; label that accurately.

- Same eligible attached RAG/reverse generation: positive resume/reuse with zero repeated scientific work; source validation is allowed, reconstruction/recomputation is not.
- Fresh registry/property-only or no-action waiting journal: positive cases may proceed with identical effective configuration/obligations. Count zero duplicate prior property calls and zero science during replay; genuinely new authorized actions after replay are separately counted.
- Identical source bytes in a **new RAG/reverse generation**: reject before CAS with unchanged waiting row/nonce, no live start/events/model/tool/search/recompute. Do not weaken generation matching to obtain restart positives.
- Unknown running, interrupted/unconfirmed claim, or crashed execution: no recovery claim. No inference that all producers survive process restart.
- Keep legacy6/ordinary7 readers and nonce policy unchanged, including owner/status gates, one-time claim and new nonce on subsequent waiting publication. The fingerprint delta is only:

```python
'decision_protocol_revision': (
    8 if getattr(loop, 'binding_profile', None) is not None else
    SEMANTIC_PROTOCOL_REVISION if admission_binding is not None else PROTOCOL_REVISION)
```

## 4. Gated tasks and concrete verification contracts

### Task 0: Parent landing/re-pin and test-source release

**Files:** this plan only until section1 gates close.

- [ ] Record actual publication landing/aligned HEAD and re-pinned blobs, retaining any subsequently landed JSON/parser fixes. Do not cherry-pick the historical whole donor or restore old loop/test blobs.
- [ ] Audit all donor continuation test functions/parameters against the exact blob; retain every safe-fallback, live-restore, marker, claim-size, source-mutation and clock case. Record the function inventory before additions; function counts are not collected counts.
- [ ] Parent separately releases the three test paths. Prepare tests with `apply_patch`, then independent SOURCE review before execution. Keep the four cancellation cases unchanged.

### Task 1: Feature/API RED versus donor-defect evidence and desired behavior

**Files:** `tests/agent/test_decision_binding_continuation.py`, the two existing test paths in section2. Prepare desired-behavior regressions before implementation under parent release; donor-defect reproduction is conditional on the evidence rule below, not a prerequisite for safe integration.

- [ ] On the landed publication baseline, use the donor no-action actual-loop path to assert owner-bound waiting must return a revision8 nonce. Its missing-behavior assertion establishes feature RED on existing loop APIs. The donor scientific-chain test imports the absent codec: confirmed absence can establish API RED, but neither result reproduces either Plato donor defect. Incidental import/setup failures are not defect reproduction.
- [ ] With a separate slot grant, run only source-reviewed explicit nodes/controls through the parent-approved isolated runner; retain terminal output, before/after hashes, warnings, duration and actual handles. Distinguish missing-feature/API evidence, actual donor-defect reproduction, desired-behavior GREEN and incidental setup failure.
- [ ] Parent reviews the feature/API evidence and releases safe integration of snapshot/claim/restore together with the section3.3/3.4 protections. Preserve publication cancellation semantics while introducing donor hooks; do not overwrite them with donor FAILED assertions. No defective intermediate checkpoint is required or authorized solely to obtain RED. Freeze and source-review the safe integration before its own execution slot.
- [ ] Prepare both required regression families below before implementation. Require behavioral RED only where an explicitly authorized prior-donor harness or naturally reachable baseline actually demonstrates the defect, recording the exact reached barrier and failed desired-behavior assertion. Otherwise retain the source finding and regressions with safe integration and report the first observed result honestly, including GREEN. Absent nonce/API, incidental setup failure or failure to reach the physical barrier is not reproduction. Never introduce or deliberately retain a known defect solely to obtain RED; prior-donor harness work itself requires explicit parent authorization, not implied permission from this plan.

Proposed new test functions (names reserved by this plan, not claimed to exist yet):

| Test function in the new continuation module | Parameters, real hook and decisive assertions |
|---|---|
| `test_snapshot_owned_cancellation_discards_preterminal_nonce` | native/json; owned snapshot closure blocks using threading events and actual WorkerOwner root. Repeated cancel while blocked: task pending, running SQLite row, no terminal/publish, fixed calls; finally release/join. Then ordinary CANCELLED, one terminal, cancelled acceptance, no ID/new snapshot/invalidation |
| `test_postsnapshot_preterminal_cancellation_discards_assembled_nonce` | native/json; real codec returns payload, then block actual owned finish-precheck after metadata assembly. Assert snapshot was created; after cancel/drain no waiting CAS, no result/event nonce, stored cancelled and current acceptance. No fabricated payload or mocked settle |
| `test_postattempt_continuation_cancel_is_failure_only` | native/json; finish-event commit then RuntimeError/false flag, postcallback owned barrier or actual owner drain blocked. Repeated cancel retains worker; FAILED correction, no positive publish/retry after invalidation, superseded provisional preserved |
| `test_postclaim_first_error_survives_start_commit_then_raise` | native/json x source/deadline x started/unstarted. Commit real CAS; close actual attached source or advance controlled clock during claim callback; prove first postclaim error observed before start exception. Assert exact first reason, no restore/dispatch/nonce; started correction fails closed, unstarted has zero correction attempts and no fabricated failed-row durability |
| `test_sqlite_reopen_fresh_registry_property_or_no_action` | native/json x property/no-action; new store and new actual registry with identical config on same database. Real RDKit reuse or journal-only admission works without prior-action recomputation; close all created registries |
| `test_sqlite_reopen_new_source_generation_rejects_preclaim` | native/json x rag/reverse; independently initialized temporary source from same bytes has different generation. Spy real writes/current-source hooks and execute/search calls after fixture setup. Reject preCAS, no new science, old waiting row/nonce/events unchanged |
| `test_two_revision8_claimants_have_one_sqlite_winner` | native/json; two new stores/loops/owners on one waiting SQLite row, synchronized immediately before real CAS after both validate. One true claim and one false claim; one live start/dispatch path, loser has no events/restore/science; nonce consumed once, no mock CAS result |

Use existing `loop_case`, `sources`, donor `invoke`/`reopen`/`start_property_waiting`/`spy_writes`, `WorkerOwner`, and actual SQLite methods. The new registry cases must not call donor `reopen` and then claim that the registry was rebuilt. For two claimants use separate execution threads/event loops or equivalent barrier-safe scheduling; never block both on a threading barrier in the same event-loop thread. Do not share mutable loop/Session/owner objects. Forward to the real SQLite CAS transaction.

For owned-cancellation tests, use the established physical-worker harness from publication (real `_binding` ownership, `entered`/`release`/`exited`, `signalled`/`pending`). Every assertion path has `finally: release.set()` and awaits run/worker tasks with `gather(..., return_exceptions=True)`. Assert root finished, zero pending roots, owner settled exactly once, unchanged model/tool counts and no source work after sealing. No runner restart or abandoned worker.

### Task 2: Codec and minimal fingerprint integration

**Files:** create `src/agent/harness/decision_binding_continuation.py`; modify only the fingerprint conditional in `src/agent/harness/decision_continuation.py`.

- [ ] After parent release, use `git show a5189a5f854050e59c6fc8ed4f8a397313c09067:src/agent/harness/decision_binding_continuation.py` as the complete candidate source and apply it with `apply_patch`, subject to the re-pinned dependency audit. Do not copy files with shell redirection or change revision6/7 parsing.
- [ ] Apply the literal B fingerprint conditional in section3.6. Preserve configured model/provider/spec/adapter/obligation identity and native context bounds; never inspect credentials or treat bytes alone as source identity.
- [ ] Independently source-review native bounds, reconstructed history, explicit proof/diagnostic handling and `_original_observations` freezing. Check every donor negative still targets an actual semantic boundary, not missing setup.

### Task 3: Integrate replay/CAS/restoration and charged clocks

**File:** `src/agent/harness/decision_loop.py` only.

- [ ] Use donor source hunks, not its full loop: segment start/deadline cap; configuration verifier; preclaim projection/waiting verification; bounded claim allocation; one CAS; postclaim/start/restore checks; complete live comparison/fresh seals; admitted reply; restored counters/deadline.
- [ ] Keep publication's current reference/source latch, scientific acceptance, owned wrappers, consumed-reference guard and phase fields. Keep legacy branches unchanged. B admission may now accept explicit revision8 nonce/reply but still rejects ordinary admission carry/exchange and invalid WorkerOwner.
- [ ] Preserve donor safe detached fallback/frozen correction request/reentry behavior using section3.5, merged with the current phase fields. Verify tampered `quality=None`, hostile types and reentrant correction cannot make invalidation inspect rejected observations or return a positive candidate.
- [ ] Add owned snapshot preparation and fixed reserve before finish, then `waiting_publication` through the existing guarded publication-write boundary. Do not lose `waiting_status` for callers lacking owner identity. Integrate Task4's protections in the same safe change; do not introduce or deliberately retain either known defect for a RED checkpoint. Freeze and source-review safe integration for Task1's required desired-behavior regressions, reporting their first observed results honestly. Apply Task1's conditional evidence rule to any donor-defect reproduction; do not claim integrated correctness from source review alone.

### Task 4: Integrate protections for both source-reported regression families

**Files:** `src/agent/harness/decision_loop.py`; new continuation test module only for approved regressions, not weakening assertions.

This task is part of Task3's safe integration, not a required later repair of a deliberately defective checkpoint. Keep both source findings and all desired-behavior assertions. Require behavioral RED evidence only when an explicitly authorized prior-donor harness or naturally reachable baseline actually demonstrates the defect; otherwise the first observed regression result may honestly be GREEN. Feature/API RED and incidental setup failure do not establish donor-defect reproduction. Never introduce or deliberately retain a known defect solely to obtain RED.

- [ ] Implement section3.3's local pending-snapshot discard and snapshot cancellation catch. Initialize closure variables early, clear stale nonce-bearing metadata at finish, and suppress waiting publish after ordinary cancel. Keep phase entry before callbacks and owned postchecks after commit-then-raise.
- [ ] Implement section3.4's first postclaim reason/boundary latch before start. Choose the same reason for started correction and unstarted detached fallback; retain no-correction authority for unstarted Session and no retry of unknown lifecycle side effects.
- [ ] Review all exits: snapshot worker cancelled, successful snapshot/precheck cancelled, finish attempted/cancelled, callback raises before/after commit, source restored after invalidation, drain cancelled repeatedly, cleanup fails, tail expires, late cancel. Check returned result, terminal events, persisted status/acceptance/marker and zero new scientific calls together.
- [ ] Freeze production/test hashes; independent SOURCE approval and a separate slot grant precede focused verification. Report the first observed result without presupposing RED or GREEN; no defective intermediate checkpoint is required. No edits in another dependency to obtain passing tests without parent scope approval.

### Task 5: Preserve and extend publication tests, not replace barriers

**Files:** existing `tests/agent/test_decision_binding_publication.py` and `tests/agent/test_decision_binding_loop.py`.

- [ ] In the source-mutation callback matrix retain `terminal_metadata`, `terminal_event`, `terminal_status`, **`waiting_status`**, each with raises false/true, and add **`waiting_publication`** with both values. Use ownerless `AgentContext(query, trace)` only for waiting-status cases so they still hit `update_run_status`; use authenticated owner/session for waiting publication through real CAS. Never substitute one parameter value for the other as the historical donor diff did.
- [ ] For waiting publication, mutate the real source after the real publish commit; preserve historical revision8 snapshot, require marker/failed correction, no outward nonce and rejection of subsequent claim. Repeat with RuntimeError after commit; postcheck must still run.
- [ ] Keep `test_waiting_status_callback_cannot_commit_over_its_reentrant_failure` on the ownerless fallback. Retain original reentrant event/status correction assertions, bounded writes and no science payload. Add waiting-publication reentry checks if needed within this same file, without dropping existing functions.
- [ ] Preserve all current cancelled assertions: final-acceptance ordinary cancel, preterminal native/json pairs, postattempt pairs, false-event-flag, source-latch dominance, late cancel and the four existing loop cases. Do not restore the donor's preterminal failure expectation.
- [ ] Update only `test_initial_clarification_never_mints_legacy_nonce` to assert revision8 nonce for authenticated owners, zero tool calls and legacy/bogus nonce rejection. Retain its name/intent or document an explicit rename; no unrelated test rewrite.

### Task 6: Focused verification, then exact61, all separately gated

- [ ] Parent supplies/reviews a runner for this worktree after releases. The publication runner's REPO literal targets another tree; its hash is not authority to execute it here. Record the actual new-tree runner hash, isolated environment, source/test freeze and exact commands before any launch. No runner creation in this doc-only phase.
- [ ] With a sole-slot grant run the approved explicit nodes/controls once as Task1 specifies. Record feature/API RED separately from donor-defect reproduction. Behavioral RED is required only where an explicitly authorized prior-donor harness or naturally reachable baseline actually demonstrates the defect; otherwise retain the source finding and desired-behavior regressions on safe integration and report the first observed result honestly, including GREEN. Incidental setup failure is not reproduction. Never introduce or deliberately retain known defects for RED, and require no defective intermediate checkpoint. Return the slot at actual terminal, no automatic repeat.
- [ ] After implementation SOURCE approval and another slot grant, run all three affected test modules. Future command, not executed:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py 'tests/agent/test_decision_binding_continuation.py' 'tests/agent/test_decision_binding_publication.py' 'tests/agent/test_decision_binding_loop.py'
```

- [ ] On failure, preserve exact reports/handles/hashes, release the slot at terminal and stop for review; no broad repair, timeout increase or second run without grant.
- [ ] After fresh source review and parent grant, run section5's exact61 arguments once, in order. Capture before/after hashes for all six code/test paths, all61 targets, publication preservation manifests, runner and this plan; report actual session/terminal exit/duration/counts/warnings. Historical donor/publication runs do not validate this integrated freeze.
- [ ] Await independent fresh QUALITY. Only parent-authorized later publication may stage exact paths, commit on the task branch, create a draft PR or merge under repository rules. No staging/commit commands are authorized by this plan preparation.

## 5. Exact61 regression selection, explicit ordered table

Entries1-60 are publication plan section5's exact60 unchanged in set/order; entry61 is the new continuation module. Use each path as one positional argument after the isolated command prefix in Task6. No directory substitution, `-k`, deduplication from the entire document or shortened union. At planning HEAD, entries59 and61 are absent as expected; landing/test preparation must establish their existence. Do not claim collection or test counts from this table.

| Order | Exact module argument |
|---|---|
| 1 | `tests/agent/test_decision_binding_inputs.py` |
| 2 | `tests/agent/test_decision_binding_acceptance.py` |
| 3 | `tests/agent/test_decision_binding_profiles.py` |
| 4 | `tests/agent/test_decision_binding_arguments.py` |
| 5 | `tests/agent/test_decision_binding_requirements.py` |
| 6 | `tests/agent/test_decision_binding_session.py` |
| 7 | `tests/agent/test_decision_dynamic_bindings.py` |
| 8 | `tests/agent/test_decision_owned_call.py` |
| 9 | `tests/agent/test_decision_target_status.py` |
| 10 | `tests/agent/test_ordinary_capabilities.py` |
| 11 | `tests/agent/test_evidence_ledger.py` |
| 12 | `tests/agent/test_decision_contract.py` |
| 13 | `tests/agent/test_decision_requirements.py` |
| 14 | `tests/agent/test_decision_inputs.py` |
| 15 | `tests/agent/test_binding_resolver.py` |
| 16 | `tests/agent/test_candidate_alignment.py` |
| 17 | `tests/agent/test_activity_tool_contract.py` |
| 18 | `tests/agent/test_analysis_contract.py` |
| 19 | `tests/agent/test_scientific_reference_store.py` |
| 20 | `tests/agent/test_workflow_run_session.py` |
| 21 | `tests/agent/test_dynamic_run_session.py` |
| 22 | `tests/agent/test_run_session_ownership.py` |
| 23 | `tests/agent/test_worker_ownership.py` |
| 24 | `tests/agent/test_delegated_session_lifecycle.py` |
| 25 | `tests/agent/test_delegated_session_parity.py` |
| 26 | `tests/agent/test_decision_adapter_retry.py` |
| 27 | `tests/agent/test_decision_loop.py` |
| 28 | `tests/agent/test_target_tool_contract.py` |
| 29 | `tests/agent/test_domain_result_validators.py` |
| 30 | `tests/agent/test_reverse_target_complete_input.py` |
| 31 | `tests/agent/test_current_source_tool_hooks.py` |
| 32 | `tests/agent/test_rag_receipt_consumption.py` |
| 33 | `tests/agent/test_reverse_receipt_consumption.py` |
| 34 | `tests/agent/test_rag_tool_contract.py` |
| 35 | `tests/agent/test_rag_current_eligibility.py` |
| 36 | `tests/test_rag_retrieval_outcome.py` |
| 37 | `tests/test_rag_owned_generation.py` |
| 38 | `tests/test_reverse_target_invocation_receipts.py` |
| 39 | `tests/agent/test_decision_binding_loop.py` |
| 40 | `tests/agent/test_decision_continuation.py` |
| 41 | `tests/agent/test_decision_continuation_store.py` |
| 42 | `tests/agent/test_decision_history.py` |
| 43 | `tests/agent/test_decision_clarification.py` |
| 44 | `tests/agent/test_decision_protocol_recovery.py` |
| 45 | `tests/agent/test_decision_merge_blockers.py` |
| 46 | `tests/agent/test_decision_spec_findings.py` |
| 47 | `tests/agent/test_decision_migration_boundaries.py` |
| 48 | `tests/agent/test_decision_validator_unavailable.py` |
| 49 | `tests/agent/test_family_activity_tool.py` |
| 50 | `tests/agent/test_ordinary_chat_policy.py` |
| 51 | `tests/agent/test_ordinary_admission_budget.py` |
| 52 | `tests/agent/test_ordinary_continuation.py` |
| 53 | `tests/agent/test_decision_transport_boundaries.py` |
| 54 | `tests/agent/test_web_decision_runtime.py` |
| 55 | `tests/agent/test_web_decision_runtime_references.py` |
| 56 | `tests/agent/test_web_decision_runtime_lifecycle.py` |
| 57 | `tests/agent/test_binding_analysis_clause_integration.py` |
| 58 | `tests/agent/test_target_analysis_phrase.py` |
| 59 | `tests/agent/test_decision_binding_publication.py` |
| 60 | `tests/agent/test_agent_event_stream.py` |
| 61 | `tests/agent/test_decision_binding_continuation.py` |

The full donor suite must also retain tests for independent proof/role/input/producer/obligation tampering, repaired history, bounds/native types, marker insertion before/after CAS, false/uncertain claims, configuration drift, future parents/reverse handles, proofless diagnostics, complete live mutation, fallback reentry/frozen retry and fixed-tail callback/drain expiry. All are within the new module; exact61 is not permission to omit its parameters.

## 6. Explicit deferrals and parent handoff

This is a bounded core continuation integration, not blanket B1/B2 completion, root-budget completion, token-governance completion, crash recovery, source-generation persistence, normal-Web activation or deployment. No claim that all six/seven scientific tools remain restart-eligible merely because property/no-action or same-generation reopen passes. Legacy nonce policy remains unchanged.

Web inclusive TTL and cumulative root carry/admission integration remain a later reviewed slice; the core segment budget and fixed reserve do not close them. Normal `/ws` B enrollment, provisional-event consumer handling, real authenticated transport, final P7/P8 positive scientific/UI acceptance and repeat gates remain required. Scripted native/JSON decisions and synthetic typed target/ADMET/activity fixtures are not live-model or experimental scientific acceptance.

Current blockers to code/test work: actual publication landing and re-pin, parent document approval, then distinct preparation/implementation/execution releases. There is no blocker to delivering this document. Parent chooses the next bounded release after reviewing its hash; no automatic execution follows delivery.

- [ ] Parent SOURCE review of this document closes the planning gate.
- [ ] Record actual landing/re-pin, approved test source and the separate execution ledger only when those events really occur.
- [ ] Report exact adapted hashes and verification evidence; never label the merged loop/tests donor-exact or carry historical passing counts forward.

## 7. Actual PR99 landing re-pin and TEST PREPARATION release

Parent reports PR99 actual squash `108df5d8acbdc1881a8f09c31b8b450acdfb247b`, CI36284700147 nine checks successful, collection11472=11248+224, Agent11247 passed/1 skipped and Web224 passed. These are upstream parent evidence, not continuation integration test results.

Local read-only verification: reviewed publication branch commit `3658416a04f4626d80fdd879c15e3c9e2aa72fc2` and actual landing both resolve to full tree `9ddfae2dc26cefb2a14f584328e696a0f4477cb4`. Landing is an ancestor of parent-aligned HEAD `b4252801cbf2396957de445ec563dcfc815e953e`; initial working tree is clean. Compared with origin/main, only this ownplan is added. No publication files were copied from another working tree.

Re-pinned section1 dependencies remain exact: execution582a6fb, eventbusd3c580b, Sessionbf162d1, loopbeb9ce7, publicationtestf2628b6. Existing loop test is `d298090d240f441a9739c9da03bb88c99b1c97b1`; continuation reader is `b44a0cbcbab7d13286c2c3c4fe94a65f4b5750af`. The complete76-file union of publication's exact60 targets and source/preservation dependencies (excluding its plan/runner) matches the accepted publication after-run blobs, zero differences. This covers loop8 and additional14 preservation paths, parser2ec6087, empty-journal7 and parser71 modules. The absent new codec/test are expected feature scope, not a failed dependency audit.

Against donor parent `656330e1908aa31b8499341b7e1c392cf55f9ffc`, Session/CAS/WorkerOwner, journal/resolver/acceptance, adapters and JSON-bounds dependencies are unchanged. Historical parser/RAG/target/reverse and publication phase/test differences are preserved, not reverted. The separately landed molecular-generator difference is outside B's seven selected tools and is untouched. No discovered dependency requires edits beyond the approved six paths.

Parent now releases only the three test paths in section2 plus this plan. No production/runner/Python/import/compilation/test/staging/commit/push authorization. Sandbox cleanup worktree remains frozen and untouched. Missing revision8 nonce is feature RED; an explicit absent-codec assertion/import is API evidence, not either reported donor-bug reproduction. No known donor bug will be deliberately installed for RED. This section supersedes the historical doc-only/landing-pending state solely for re-pin and test preparation; implementation/execution remain separately gated.

## 8. Prepared test source freeze — pending independent SOURCE/execution release

| Prepared test | Git blob | SHA256 |
|---|---|---|
| `tests/agent/test_decision_binding_continuation.py` | `1d8e9e770376dab1a3cc78103e38e2d17c53a089` | `ACC337B40EEEC2EFB5D8635D0021B9FEC82B853BE28C362DCD07ED10250ECFA6` |
| `tests/agent/test_decision_binding_loop.py` | `03149a596c6894e342eee483c8636a55f9361152` | `5C6394E9F5873703364D3C356A715BF6AAB83CF49AC5920CA1691184000D13D1` |
| `tests/agent/test_decision_binding_publication.py` | `9274535ce828e6f1b34fa2c4cd4a85efdce16e6f` | `F406808AD9DFBFCB2A16DAF1A16227A6973595BA480BA0A52680966A3EC919B7` |

The new module retains the complete donor text as an unchanged prefix (26 test functions), then adds the seven section4 families (24 parametrized cases) and one explicit API-contract function. Missing codec import is not a collection dependency: codec imports remain inside individual tests. This is source preparation, not a collected/passing count.

The cancellation tests use the publication physical-worker harness, real closure/snapshot/finish and SQLite/event callbacks. All blocked-worker assertion paths release and join in `finally`; they assert repeated cancellation retains the pending owner, fixed scientific/model calls and running row until physical exit, then one real drain and no source checks after sealing. Post-snapshot coverage targets the first owned finish precheck after the real codec returns and the loop assembles metadata. Postattempt coverage commits a real partial event then raises with the terminal flag still false; immutable provisional history must be superseded, not overwritten or reclassified as ordinary cancellation.

First-error coverage forwards the real CAS and guarded owned call, records the actual first boundary exception before invoking real start, and distinguishes fully started versus start-event-committed-but-unstarted authority. Reopen coverage constructs fresh actual registries/stores/models; new-generation negatives reopen the exact same temporary producer files/configuration and check file digests without new science during resume. Both real SQLite claimants have separate execution threads/event loops and owners; their bounded rendezvous is immediately before forwarding the real CAS, never a mocked claim result. Fixture teardown closes all registries; newly opened sources close in `finally`.

Existing loop test: only owned initial clarification now requires a revision8 nonce/snapshot; bogus legacy nonce rejection and zero-action/model counts remain. Its 37 test function names and the complete four-case cancellation suffix are unchanged. Existing publication test: all40 functions remain, including all34 original functions and six integration additions. Waiting-status remains ownerless; an additional authenticated waiting-publication branch forwards the real CAS, retains the committed revision8 history, and verifies future rejection after invalidation. The ownerless reentrant waiting-status test still checks its original status sequence. The entire six-addition/cancellation-helper suffix is unchanged. Neither existing module was replaced with historical donor source.

Read-only preparation checks: all61 ordered section5 targets now exist, are unique, and retain the original60 order. Rechecking the76-file preservation union found only the two intentionally adapted test paths above; all74 other paths, including all production dependencies, parser/empty-journal/current-source repairs and Session/worker/store, remain identical. `git diff --check` succeeds. No Python, imports, collection, compile, runner creation/execution, network, staging, commit or push occurred; no test-pass or behavioral-RED claim is made. No sandbox worktree file was edited.

Proposed first focused RED selection, **not executed and requiring separate parent SOURCE plus sole-slot grant**, in this exact order:

1. `tests/agent/test_decision_binding_loop.py::test_initial_clarification_never_mints_legacy_nonce` — native/json; existing-API call-phase assertion that an authenticated B clarification publishes revision8, with no scientific calls.
2. `tests/agent/test_decision_binding_continuation.py::test_sqlite_revision8_no_action_smiles_then_target_keeps_original_journal` — native/json; first call must mint the missing revision8 journal nonce. Later reply/target assertions are integration goals, not evidence reached on the current baseline.
3. `tests/agent/test_decision_binding_continuation.py::test_revision8_codec_api_contract` — separate explicit absent-codec API assertion; not a fixture/import crash and not donor-defect reproduction.

The additional snapshot/first-error desired regressions are not initial defect-RED claims; their physical barriers require the later safe implementation. No known donor defect is installed to make them fail. The prior direct source-close→restore publication-scenario coverage caveat remains: closing a source at postclaim before start and asserting no restore does not close that separate caveat. Next action is parent independent SOURCE review; production, execution and publication remain held.

## 9. Accepted initial feature/API RED — actual execution evidence

After Lovelace SOURCE READY on plan800EF3 / testsACC337,5C6394,F40680, parent supplied and verified ignored runner `EA3B05E05F0F39A23D0056F9CC051B4653708F4E4ECD5335E42B7E469E7298A8` (blob `32cd1cfbec0125a8c73cc3b44e516e3446d70391`). Parent reports full forward/reverse byte comparison against admission6D490 with only REPO changed. This worker re-read its isolation settings and verified its hash; no runner edit occurred.

Parent separately granted the sole Python slot for exactly this invocation on HEAD `b4252801cbf2396957de445ec563dcfc815e953e`:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py 'tests/agent/test_decision_binding_loop.py::test_initial_clarification_never_mints_legacy_nonce' 'tests/agent/test_decision_binding_continuation.py::test_sqlite_revision8_no_action_smiles_then_target_keeps_original_journal' 'tests/agent/test_decision_binding_continuation.py::test_revision8_codec_api_contract'
```

Actual session **8244**, launch **a82028**, authoritative terminal **b05e0a**: exit **1**, `ORDINARY_PYTEST_EXIT=1`, **5 failed, 3 warnings in11.26s**. The same handle was polled, with no restart, retry or expanded selection. All five failures explicitly reported `phase=call`; there were no collection/setup/fixture failures. The three warnings were SWIG missing-`__module__` DeprecationWarnings for SwigPyPacked, SwigPyObject and swigvarlink.

- `test_initial_clarification_never_mints_legacy_nonce[native]` and `[json]`: line261 assertion failed because `result.metadata.get('continuation_id')` was None. Earlier waiting/clarification assertions were reached successfully. The right-hand zero-call conjunct at that failed line short-circuited; do not claim later assertions ran.
- `test_sqlite_revision8_no_action_smiles_then_target_keeps_original_journal[native]` and `[json]`: line962 explicit missing-nonce assertion failed after the waiting/zero-call assertion. Subsequent SMILES/target replies were not reached.
- `test_revision8_codec_api_contract`: line999 assertion `find_spec(name) is not None` failed with the explicit absent revision8 codec API message. This is API feature evidence, not an import/setup exception.

Thus four nonce feature RED cases plus one API RED; neither reported donor defect has been reproduced by this run. Before/after **87 file pairs** had identical Git blobs and SHA256s: publication76 preservation union, new continuation test, ownplan, runner, and eight additional SQLite/JSON-bound/input/history/binding-contract/factory/registry/spec dependencies. All three test/plan/runner pins remained exactly section8/above. The codec was absent before and after. HEAD stayed b4252801 and `git diff --check` succeeded. The worker explicitly RELEASED the slot immediately at terminal; parent accepted these results. This section records that historical freeze, not verification of the implementation below.

## 10. Implementation-only source freeze — awaiting independent SOURCE

Parent accepted section9 and released edits only for the approved integration, with tests frozen and another worker owning the Python slot. No Python, imports, compilation, tests, model calls, runner changes, staging, commit or push occurred in this implementation phase.

| Production path | Integrated Git blob | SHA256 / provenance |
|---|---|---|
| `src/agent/harness/decision_binding_continuation.py` | `6d500e275af2dc671cb30230b5a0665182e8ae90` | `8DBEF1367EDCE3D6B4EE526405CD243059116570C6703496EA44153B00654C05`; new codec donor-exact |
| `src/agent/harness/decision_continuation.py` | `18c6714b1dda531768203d7722ca35e9cbfd1ded` | `528E0D9ED2B9B74891CBC42686E245AFD76CB2E0EDF0827BB2A0F10F6C75F259`; one-line B fingerprint conditional, donor-exact |
| `src/agent/harness/decision_loop.py` | `e7b46ef3d0181202d34e191ae737c18bf044c577` | `1035880B33675B4F5B8BE66A191F0605C5AABD6806743425E0CE2609927AFEED`; adapted against landed publicationbeb9, not donor-exact |

Loop integration uses contextual hunks on the current publication source. It retains phase entry before finish callbacks, owned pre/post checks, commit-then-raise handling, bounded correction and actual owner drain. Added revision8 behavior includes detached replay and whole-live comparison before fresh seals; one bounded real claim; source/configuration rechecks; original no-action journal plus admitted reply; segment-start-anchored saved credit; fixed nonrefundable snapshot tail; guarded waiting publication. Legacy revision6/7 readers/CAS and their non-B restore/deadline branches remain unchanged.

Both source-reported defects are protected in the integrated change, not deliberately installed for RED. Pending snapshot fields are initialized before cancellation handlers; ordinary snapshot/pre-finish cancellation clears them, removes any assembled nonce at finish, rewrites cancelled acceptance and suppresses waiting publication without erasing consumed prior claims or refunding tail credit. Postclaim reason/boundary is frozen before start, preferring the owned first-error latch. Started Sessions use failure-only correction; unstarted commit-then-raise returns an honest detached unconfirmed failure without granting correction authority or repairing the running row.

The donor's safe detached fallback/frozen correction request/reentry guard is merged with current phases. An already latched Session failure is projected read-only from its frozen journal before attempting correction, preserving that reason even if a later callback raises; invalidation never traverses rejected restored observations for optional IDs. No Session/event/store/worker/provider/activation API was changed.

Read-only freeze audit before this documentation append found exactly the two authorized existing production changes among section9's87 pins; all85 other files, including every test, runner, parser, empty-journal and publication/source dependency, were byte-identical. The new codec is the additional approved path. `git diff --check` succeeds. Three test blobs remain `1d8e9e77`, `03149a59`, `9274535c` with their full section8 hashes; all61 regression paths/order remain unchanged. These are source/hash checks, not integrated GREEN evidence. Direct source-close→restore publication coverage remains unclaimed.

Next gate: independent SOURCE review of this exact three-production/three-test freeze and ownplan. Only a separate sole-slot grant may execute the exact three-module command in Task6, then separately authorized exact61/fresh QUALITY. No automatic run, code repair or publication follows this handoff.

## 11. SOURCE P2: unstarted fallback versus outer drain — test preparation only

Lovelace identified a remaining P2 in section10 loop `e7b46ef3`: after confirmed CAS and an actual first postclaim error, start may commit an event then raise while Session remains unstarted. The inner loop returns the detached FAILED/unconfirmed fallback without binding `finalization.session`. Outer `run` then drains the real owner; both its CancelledError and other-exception branches re-raise when that Session is None, losing the earlier returned failure. Source inspection confirms `WorkerOwner.settle` retains its physical drain but can re-raise cancellation after settlement. This is a source finding, not yet an executed behavioral RED.

Parent authorized only two new regressions plus this plan, after the separate C fresh QUALITY run reached terminal and its slot was explicitly released. No production fix, Python, retry or existing-test rewrite is authorized here. Section10 production pins stay frozen; the new source review is required before a separate two-node RED grant.

Appended helper and exactly two non-parametrized native-mode nodes in `tests/agent/test_decision_binding_continuation.py`:

1. `test_unstarted_postclaim_failure_survives_real_drain_cancellation`
2. `test_unstarted_postclaim_failure_survives_post_drain_error`

Both create a real waiting RAG observation/SQLite nonce, forward the single real claim, close the actual source and observe the owned first `invalid_dynamic_binding` before start. A real start-event write commits then raises before `_started`; the test observes the actual inner detached fallback and unbound finalization, not a supplied replacement result. That callback also registers a real WorkerOwner root with a controlled physical worker. During drain, before releasing the worker, the tests assert running/consumed row and event history unchanged, no restoration/model/tool dispatch, no Session binding, finish or invalidation. All exits release and join the same run/worker tasks in `finally`.

The cancellation node repeatedly cancels the actual run while its real owner retains the blocked worker, then requires the original FAILED reason/boundary/unconfirmed durability after physical settlement. The exception node forwards the entire real owner drain, then injects a **secondary receipt RuntimeError after proven settlement**; it does not pretend to reproduce an OS join failure. Both assert real worker exit, zero pending roots and one drain before the decisive desired-result assertion. Neither authorizes manufacturing a started Session, a failed-row repair, a terminal correction event or a new nonce. Extra secondary diagnostics are not prohibited, but may not replace the earliest reason/boundary.

Expected current-source failure is a returned/gathered CancelledError or the exact injected receipt error instead of the previously observed detached AgentResult; this remains a source expectation until the separately granted run actually reaches these barriers. The existing four cancellation parameters and every prior test body remain unchanged. New test blob: `0f79c9c6fc80818dd6ab77b8d4f294cf56d96acf`; SHA256 `73E5C0E032353A100AC74A473C777A4C97E2CD7F5EA6F98CD129467531681B7D`. No test result is claimed for this addition. Next step is SOURCE review, then separate two-node RED, then separately released minimal production reconciliation.

## 12. Accepted P2 behavioral RED and minimal source fix — GREEN not run

After Lovelace SOURCE READY and G3's terminal slot release, parent granted one invocation on HEAD `b4252801cbf2396957de445ec563dcfc815e953e`, with test73E5C0, plan5E067, loope7b46ef and runnerEA3B05 frozen:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py 'tests/agent/test_decision_binding_continuation.py::test_unstarted_postclaim_failure_survives_real_drain_cancellation' 'tests/agent/test_decision_binding_continuation.py::test_unstarted_postclaim_failure_survives_post_drain_error'
```

Actual session **85871**, launch **7d9564**, authoritative terminal **5ce251**: exit **1**, `ORDINARY_PYTEST_EXIT=1`, **2 failed, 3 SWIG warnings in11.30s**. Both failures were `phase=call` at the decisive line1635 assertion `not isinstance(result, BaseException)`: respectively `CancelledError` and the injected `RuntimeError('synthetic post-drain receipt failure')` displaced the actual inner detached FAILED. These were not barrier/setup failures. Before that assertion, both cases verified physical worker exit, one real drain, settled owner/zero roots, unstarted/unbound Session, no finish/invalidate/restore, unchanged running row/events and no additional model/scientific calls. Later desired-result assertions were not reached. All **88/88 before/after Git blob and SHA256 pairs** were unchanged. No restart/retry/expanded run occurred; the slot was explicitly RELEASED at terminal and parent accepted the evidence.

Parent then authorized edits only to loop plus this plan. The fix adds a private `detached_failure` result slot, populated solely in the confirmed-claim/unstarted-Session start-failure branch. It retains the already selected earliest reason/boundary and `correction_durability='unconfirmed'`. The mandatory outer real owner drain still executes; its separate CancelledError/Exception handlers retain that result only when Session is unbound and this explicit detached failure exists. Other unbound paths still raise; started Session handling, ordinary cancellation, correction and post-drain deadline handling are unchanged. No Session binding, finish/invalidate, persistence repair, cleanup retry or assertion weakening is introduced; an unconfirmed failure does not claim durable correction or cleanup success.

New loop blob **`ffa8883b06b359e25825ca9e0c68f302347799ac`**, SHA256 **`1B56E632BB26A2780426634AAF22000C09C89A64A3A78C064F19DB7497D0B2EF`** supersedes section10's loop pin only. The continuation test remains exactly section11's73E5C0; all other production/test/runner preservation inputs remain frozen. The direct source-close→restore publication coverage caveat remains open. This is source-only preparation for independent review: no Python, imports, GREEN, commit or push; execution requires a new explicit sole-slot grant.

## 13. Accepted exact2 GREEN and next exact61 source freeze

Parent reports Lovelace corrected SOURCE READY for loop `ffa8883b06b359e25825ca9e0c68f302347799ac` / SHA256 `1B56E632BB26A2780426634AAF22000C09C89A64A3A78C064F19DB7497D0B2EF`, with test73E5C0 frozen and pre-run plan SHA256 `6E136BEBC549B3CD17663D09C0D8A9F9AE833A0FE3D8B0D18E6F786B0E5C1206` (blob `f96010e8b43760a39de5c11a9aa307c845c1f274`). SOURCE approval is separate from runtime evidence.

Under a new sole-slot grant, the worker executed section12's exact two-node command once, in the same cancellation-then-post-drain-error order with unchanged approved isolated runner EA3B05 and MedChat `-I -S -B`. Actual session **8866**, launch **755def**, authoritative terminal **47f031**: exit **0**, `ORDINARY_PYTEST_EXIT=0`, **2 passed, 3 SWIG warnings in11.50s**. Warnings were the same SwigPyPacked, SwigPyObject and swigvarlink missing-`__module__` DeprecationWarnings. The same handle was polled without retry/restart; the slot was explicitly **RELEASED immediately at terminal**, before read-only post-run hashing.

Unlike the RED's decisive assertion failures, both complete GREEN cases reached the subsequent original-reason/boundary, FAILED/unconfirmed, no-answer/tool-results/nonce assertions. Their actual physical worker exit/owner drain and no Session correction/persistence repair checks also passed. The injected post-drain receipt error remains a controlled fault after real settlement, not proof of an OS join failure. Before/after **88/88 Git blob and SHA256 pairs were identical**. Section12's accepted **85871/5ce251: 2 failed, 3 warnings,11.30s** remains the behavioral RED evidence; nothing was overwritten or relabeled.

Parent accepted GREEN and moved the slot to Zeno. This append is documentation-only; no code/test/runner edits, Python/imports, extra tests, commit or push. Parent reports PR102 landed `18ad4f9`; this campaign is deliberately **not aligned mid-run**. HEAD remains `b4252801cbf2396957de445ec563dcfc815e953e`. No claim of full61 GREEN or direct source-close→restore publication coverage is made.

### Next exact61 ordered hash freeze (not executed)

Read-only recheck found all section5 targets present and **61 unique**, preserving its exact numbered order (original60 plus continuation61). All88 pins still matched the accepted GREEN post-run manifest before this documentation append. Only this plan's hash changes now. Each row below maps to the same numbered full path in section5; both full Git blob and file SHA256 are frozen. The next command must pass all61 paths explicitly in that order, using the same approved runner; no shortened selection or inferred passing count. Execution and fresh QUALITY each require separate parent grants.

| Section5 order | Git blob | SHA256 |
|---|---|---|
| 1 | `bd377facce7f997572fb9a51a33e46451ed77205` | `1F8092007DD9C2132435D47ABB507865F32799784835A1C537726E238A031D30` |
| 2 | `f008fd3d9295dfc37b816b7fa06ce777ce1a0fe2` | `7973D217A8E59AE4B5F1B53AB872A81ADC6E33D013324A4A71376882E236E5A3` |
| 3 | `69641054cc6c3fad08f64b54d90bc7b7fb90344d` | `1DF3190F3353BDE61BC9C2D6CCCC809A4747E69B95C97E9D5DB6998C3BAF3A3A` |
| 4 | `ba111abc8e0a7b1b042675205b420223271d82a2` | `07FD2E0129C465065D83E8F6D9D2C89AD8A88082B0FB7B8FFCC88FF87DE74B40` |
| 5 | `1d8db97dd6c27d5b965d9b96d69addfa6d24f664` | `8E9906D54CADBF42425706E17E06559F1AF93B8F2D44B38B1031B0C607B24A64` |
| 6 | `54b12b4cc2d46b93998a6d7911e4d15359f0c66f` | `793A9500D45A6E0BD7D352C1465BCC32DC1BA02A5536594D06856586B2903071` |
| 7 | `506e27c8c3de1d58ad39f278d5269f3214627351` | `80644F2846CD51B14075AD0E3375E69CDC1152C085C4B8F5407F14E8976B7E22` |
| 8 | `25683ce00eb0594988e30987dca2f2059e5ab971` | `5C3142863F6F97D5A06357488A67499AC60237E895B3DD9110E5C1FEE2A4B216` |
| 9 | `83df224daddf03bf81c5e2d7858a831c038e23a4` | `DDC529ED251A95C67AED7FF4AC4825A2F776A129B8B79E8E49B026648AB42CF6` |
| 10 | `2b0b18e01a8361d932201903fff8e7aef9dae46e` | `C0A5DC947BCB7DBE51BFF87E10117AA758AAB05061E80FD7E2388F2E2B53ED3A` |
| 11 | `69feac27d284ba67352611010020cdd5c7a21456` | `7A4BAB56DAEB4FC7E1E61EEDDC6D8D30B54940F1CAC05018AB266CB1D1ED9984` |
| 12 | `240a6acc3aa109d3f3dc4ecb517ac3f6a3ae011d` | `6E5DAF34C1B5D97A39F84A0874B8DE6CCC7B4FD9E04AA54EA7795FC2EDA68A45` |
| 13 | `2020e5701f433522c1a2e159d56066a04a52ffd1` | `25C00CC4A9474EBCF262E19F0D77742DC789D9C16A738D7EE4D94065D9181E45` |
| 14 | `be0bb0655b68221c664f365bc33c960b04b31f3c` | `3E497531AEC0BC104561901615A9F8B07822A60AFC84B42425C6F1D50F13D544` |
| 15 | `38a6491d26d917a8af0d209ef6e81a46d49cf651` | `D4B031D5F2CC79F3D4494E29C15F2AEEFB167ECB7E2F1E991F8F12EA174AA07C` |
| 16 | `544fc6668f3ab2919f8370d8b835f96052d0195e` | `680FB433AC1F30CCE971E2E3D2B9331D30F65699C1A7F4343C64668C43B0D4A0` |
| 17 | `e99a51997b26010bbde11498240a8bb994ddfae6` | `4B80F72FC374B081F8FA87F0062756ED73E8140881F502CC5C6FDB981CE4A393` |
| 18 | `34908767f4fe5883ff5d0535de3c037136690ec4` | `9409174207233BFF5D7AFE449E0D4F20FEAB650886E6C0166BC6D950CBFFFFCF` |
| 19 | `c102da0a5645201c212dbc0acf0b4d51e6623453` | `0DDDED3D6182A8FAAD73B4A886710CE5A51909CD1C482B63358C0DE2F5F83CEF` |
| 20 | `1a4d1fe1abc573771b3f1d42e1d6d13ec60e033e` | `225E74BDFBDEA36D76D9FBC5B536B51F8286A085C4C173D8C85648440DA35B95` |
| 21 | `a15a89b6f6a94781e1dacc4311a881888e019342` | `A8A2F17255631AED48E691F736D0C1E5C395B648D4B145948B31C09C5AADD632` |
| 22 | `b2cd091eb6e67bf43431619fde66f839eb0da26b` | `0CB532412BAF31228A0D4A59A223A3A1CEEE06EA13EC62DDD6A6D3C39A01FB82` |
| 23 | `97854fbd3b77f747e04d82789057c521c1d400a6` | `E8B3D27DB2FBA059A8EB1C1F217178950BE64258B88BA53E6670995AAB755F42` |
| 24 | `042da70e2b317811b2b16c3dc4bfee16ddf8d133` | `EE1B702F52C4DA126D555784FC77B2B13C5EF3E4102CA523851D2FED4DBF71CC` |
| 25 | `b4bc0a75020a825582f524b419cf0f1e8f980df4` | `7979F2306AB7EBAFE14F69FEDD35C55E3BB0EBFEA29037F6900260ED0B3D105C` |
| 26 | `75bdd8f3992931937967823628ae8012b9bc151a` | `3390E17C779BD6CB151C9F0B9383F22A192AFFAC3016BA2F27705E1A67616086` |
| 27 | `51a7c6e616268c9334a530d5b2600b546b4d3777` | `FFCF18EEE29E35437B88483E06DD9127849E157743C84180ADE87DEE22CA0AA4` |
| 28 | `5dfaa929fbf5c3764c5294517ee86576f5056bd5` | `CBDD94261245577DC559D2C11C9D44C18D31E5FE6054B516067C3EFBBD8C1759` |
| 29 | `53d12b9f299340a98b302616664dca7a70dba226` | `27C56EE9C7A90B6125E0FC5E7EA2163A4E604FA10FC8AE147430F2C40592F634` |
| 30 | `18f4e068d1eae882c1a299bae768f87676edc10c` | `018C517F72F5B3CB211346EF80614ACE19E41C7A6F0DFD5D45EF47BE174B940A` |
| 31 | `dce02d8438c217ac581f450b4a67c865e4a3b2bc` | `1983D40901DEFF7CB3595407A42AD56858C89DC5C802E259DB084EFD9D6F8F18` |
| 32 | `288cfc6d361e7918f46c54fafebb0f8a9a095f5b` | `00F8CDDFBDE821694E432968393E5F9698B0744839EBF2184F34D6777BA49F49` |
| 33 | `5204d1e34039d9e693ee5795058fbb4a5c5c98bf` | `18961BBA384091A609C6FA19FD8EF42BC67CCAAEE5D680AA1B630450B17455F9` |
| 34 | `6e0274ebe97806b133474c3aabc6c4c290ffd4a1` | `6E6573520080CBC34DA4B4F49C3C9F105A40B7F2AC260C313981C523C54474C5` |
| 35 | `833195c67b4940ada343a202f815dd6017d340eb` | `9ABBFD2A873A38224AF108860A05F71C17355F3C480DB8D388ACA5CF54776651` |
| 36 | `322d8108302ab5b34e7294cee71da242e67b8750` | `8784D3EF66A2CF02CD97C62BE6348E8BB41168F33D30E37E77B5F9B37A1C10D7` |
| 37 | `1f71d01f03a221a66336cd02759319d77baea761` | `ABEA44385A29B3DD14BA3039982C39739A56A802945E2AF1B64A5E2CEB2BE467` |
| 38 | `f67dae6889a8787cb2a48fcd3d0ca83cce379372` | `6F6E5C9D550BD433EBB215B97D4EE082F5F7DE0F3E01E12FB9B8BEAE472E8FB0` |
| 39 | `03149a596c6894e342eee483c8636a55f9361152` | `5C6394E9F5873703364D3C356A715BF6AAB83CF49AC5920CA1691184000D13D1` |
| 40 | `a30afe74ceab9d1627b89e264e81e74cf57ec2d2` | `F9B843FEE78E8F40B743EC3606AFAF101E714737142C093D1450E0C01ABB9023` |
| 41 | `84c21cade72aaa0e4343e269ae7ea1d7d1c26512` | `D717FE4DFB42FDF38DDA7993017940EAE7AF787614EFFB9A3E19FA5B36756491` |
| 42 | `550dced7bbe1e1da24ced5abbc52ce68ab0dea6f` | `81EF658183FDCB9D8F8A3B6C35A45B91EB08C75A4C86B385D0453ACB2F46794D` |
| 43 | `52b97ca230136fa424ce38871c8561faa4903068` | `D8E6EA0E6887C6184F5C8615F7AEDD69F7A0667DF58A234C6C0B6626B3448772` |
| 44 | `c149cfcaf627830d0c26718c62b2c7b8c62c7ebb` | `EC1AE90E566F27AF457BACE41125D303028119FA53DCC539028EF8CF30BC3F93` |
| 45 | `ff99b678842ae13f1a323ea21bf1e16d209b3474` | `E1AC6959EA0AEC567370954BCBF9188F2D21E6A38B218B532747D735BBD7D292` |
| 46 | `37cabbcb71ff4266462f4dcfdd623ca6545484c3` | `F0875F728BB7468E1935BA503F9908F4F0FF09BEE248F03CF96CDCEADF95E915` |
| 47 | `c987ab6f2737fa4ab68f4fe6d937577ccbedb264` | `D2CA7C0F7EED3C8853D04DD409B8A150B725E68EEC41CEED97BD151A209331CD` |
| 48 | `fad128d4baa1fd658b9b230d1a41639be869ce58` | `34584D98DFD7B7F2FCD81B76C0083E626B39081F7442F1533AE1A8972707DFA4` |
| 49 | `546579c0b5e96aae622f77e4a49c1c183c367e00` | `0419AD64FB8FA9EAD6CCB7F1CABA5F2BFD4F5240B974DFCBC9AF1E2F4B38CD20` |
| 50 | `31fc0d3f17cd5b694e852417de3d5063594a456f` | `355047EAAB40C6199CE02083D000B7187C4769061DAC90629FE8A7990BC88E91` |
| 51 | `eb6020b1be52b195d98c84fc20874af98f994d94` | `6963D6EB7A2BCE0CCA8EDC8DF607E7DBB5AFFCDFD7E0D3A01562FDF8979BF6F8` |
| 52 | `69e92c4270a5b667233ed2652043663e6be73777` | `F628B8E5DF4FCE4163E2FDE112F94BA04BCD97D8714A76C0B20ED5D60A0649D7` |
| 53 | `cf4ad8471a230efbc7804c7c262a2566939b1888` | `44DF5EB91C63B7C3AEA59FC87CF33F6AB6961168CE9D41D30EA4DB8B5FF1BBCB` |
| 54 | `464349620aa97c03079fb1f4853efaeb1a4cad38` | `997BD7194B82BB9F0A236557B2B74A75D9A95C597B1CB7468B6228B76A21D205` |
| 55 | `053af24437dcb10e80096fa09183e1739dffa468` | `A16DDD947418E371437FC933C59735A59917A4FF5E9C5CE72F5E968E6034295D` |
| 56 | `0e01612bceaabc540cce141bc2974942b46e0bc6` | `50A8F6583A2E4A18379153674F3E4FBAFED97380F11BE00FEE1CCCD41C304364` |
| 57 | `6ceed90d4072b77204bfad186d7f1fff853ca149` | `7A5E11F3616638EFE70096158F60310CC6865E22A8C58316A3B355C8AE7C1863` |
| 58 | `8033991b1f346d64f117c23cfaa46aa6eefeaa83` | `94321B9FCE3A5C43272CFC09211D122CCB32B525BD1DBE85C65DB5283EFB4A10` |
| 59 | `9274535ce828e6f1b34fa2c4cd4a85efdce16e6f` | `F406808AD9DFBFCB2A16DAF1A16227A6973595BA480BA0A52680966A3EC919B7` |
| 60 | `b3ad28806bfee5c172a528c4910f3497e66e3b69` | `669FF28845F2EEBE32936DB280328EC45C1D4846B1F52FA7831F708A639AA1C2` |
| 61 | `0f79c9c6fc80818dd6ab77b8d4f294cf56d96acf` | `73E5C0E032353A100AC74A473C777A4C97E2CD7F5EA6F98CD129467531681B7D` |

## 14. Actual author exact61 terminal — two reverse test-premise failures retained

Parent granted one full ordered section5 invocation after G3 terminal94bd9a released the slot. Preflight verified all61 explicit targets exist, are unique and match section13's ordered hashes; all88 pins matched the campaign freeze. HEAD stayed `b4252801cbf2396957de445ec563dcfc815e953e`, runner EA3B05 unchanged, with MedChat `-I -S -B`. The command used Task6's approved isolated prefix followed by **all61 section5 paths as separate positional arguments**, with no directory/`-k` substitution or omission. No PR102 alignment or real-model/service run occurred.

Actual session **41731**, launch **3e553d**. The same handle was polled throughout without restart/retry; progress reached100%. Summary chunk **633046** reported **2 failed,6370 passed,7 warnings in1418.28s (23:38)**. That chunk still had a live session, so it was not treated as runner terminal. The next poll, authoritative terminal **ca935c**, returned exit **1** and `ORDINARY_PYTEST_EXIT=1`. The worker immediately declared the sole Python slot **RELEASED**, then read-only hashing confirmed **88/88 before/after Git blob and SHA256 pairs unchanged**, including all61 targets, three production paths, preservation dependencies, plan and runner. Parent accepted the actual terminal. Warnings: three SWIG missing-`__module__` and four FastAPI on_event deprecations; captured RDKit MorganGenerator stderr messages are not additional pytest-warning counts.

Both failures were `phase=call` in `tests/agent/test_decision_binding_continuation.py::test_sqlite_reopen_new_source_generation_rejects_preclaim`, line1366, asserting `old_projection.source_sha256 == new_projection.source_sha256`:

| Parameter | Actual old source_sha256 | Actual new source_sha256 |
|---|---|---|
| reverse-native | `d7ba4b8e7da82b97b4173c8a27c9a5528266a2152fae148d13254094da2c3bb6` | `745cb64b1d04a288df8ee89b29ef71a3c24822a3b34476b04852a3aa516c8e9e` |
| reverse-json | `4b2416f839d2f65f4f8d19261e055dbad75e25853c4c23f0860ace4bf3e1ff7b` | `8d77d31f87c616cc0916d91465532f2ebe4786de387d671b1b4855e296413e43` |

These are test-premise assertion failures, not fixture-phase exceptions and not evidence of the intended new-generation preclaim rejection: the fresh registry/resume and subsequent generation/configuration/file-equality/no-work assertions were not reached. Both cases retain their existing finally cleanup. Full61 is **not GREEN**; no automatic fix or rerun followed.

Parent permits this evidence append only while another worker owns the slot. Source-only diagnosis identifies that `owned_source.py` constructs a descriptor including fresh `generation_id=uuid.uuid4().hex` and computes `source_sha256=json_sha256(descriptor)`; `capture_prediction_source` exposes that generation-bound digest, not a byte-only digest. Same fixture files therefore do not imply equal source_sha256 across independent strict initialization. The output alone does not enumerate all differing descriptor fields or prove that generation_id was the only difference. Existing file/configuration identity assertions remain required; no assertion, producer hash semantics or source validator has been altered.

The separate missing native/JSON coverage for close-before-real-restore and close-after-resumed-waiting-CAS remains open. Only read-only boundary inspection occurred; no new cases or source changes were prepared. This append changes only ownplan; no Python/imports, tests, runner modification, commit or push. Further test preparation, bounded premise correction and execution each await separate parent authorization.

## 15. Authorized test-only premise correction and four source-close cases — SOURCE pending

Parent confirmed the source diagnosis and authorized only this plan plus `tests/agent/test_decision_binding_continuation.py`; no production edits or execution. The historical section14 result remains **6370 passed/2 failed**, and section13's P2 exact2 GREEN remains unchanged evidence for its old freeze, not a pass claim for these new tests.

Reverse premise correction independently hashes **each full actual descriptor** using stdlib JSON (`ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False`, UTF-8) and SHA256, checking its own captured producer identity and generation. It compares the entire descriptor excluding **only generation_id**, reporting differing keys rather than dumping content. Both `cache_origins` entries must explicitly equal `verified_cache`: the actual fixture's default `cached=True` writes Morgan/MACCS popcounts before either strict initialization; strict loading reads/validates them and does not write caches or read legacy fingerprint metadata. Cache provenance is not dropped or normalized. Generation-bound hashes must differ; the existing generation inequality, configuration equality, independent file-hash before/after, real current-owned rejection, no writes/start/restore/model/science and finally cleanup assertions remain unchanged. No producer/receipt/hash/validator behavior was modified.

Appended a local shared helper and two native/JSON functions (four source-declared cases, **not collected/run**):

1. `tests/agent/test_decision_binding_continuation.py::test_source_close_before_real_restore_is_failure_only`: actual RAG waiting snapshot, reopened same SQLite store, real successful claim/start and completed owned precheck; the restore wrapper closes the real attached source **before** delegating actual Session.restore_observations. Require real restore return, then real owned source rejection before comparison/seals/model; sanitized failure, failed persistence, preserved consumed nonce/history and future rejection.
2. `tests/agent/test_decision_binding_continuation.py::test_source_close_after_resumed_waiting_cas_is_failure_only`: same initial snapshot/reopen; require successful actual restore, whole-live comparison and fresh seal. A single scripted clarification creates the resumed waiting snapshot without new science. Forward the real new waiting CAS, record committed status/replacement/history, then close the real source before returning to the actual owned post-publication check. Require sanitized FAILED/invalid_dynamic_binding at waiting_publication, no outward nonce/science, one invalidation terminal with provisional summaries preserved, consumed prior claim as CAS expected value, unchanged restored observations and extended input history, retained committed new snapshot, and rejection of both old and new nonces without further events/writes/model/science.

All check/restore/compare/seal/CAS/settle wrappers delegate the real implementation. No mocked validation failure, lifecycle flag mutation or manufactured success. Both variants require settled real WorkerOwner/zero roots and no duplicate RAG or synthetic HTTP requests; actual run-finally drains and the existing source fixture closes sources on failure. These tests do not block a physical worker, so add no hold/release controller. The previous blocked-worker cancellation tests remain intact.

New test blob **`96d216f15fff48a5019eb7abbe3e9888e93bd232`**, SHA256 **`BBB6BBEF9EC8749B64A9E0F884A04D7A5C3CD47372CFFF74A3241E784A8C5286`**. In-memory reverse application of only the premise hunk and removal of the appended helper/two functions reconstructs the exact former test SHA256 **73E5C0E032353A100AC74A473C777A4C97E2CD7F5EA6F98CD129467531681B7D**; no other previous test bytes changed. Section5's exact61 order/set is unchanged, but section13 row61's historical hash is superseded by this pin for any future run. Production loop ffa8883/1B56E632 and the other two production files, publication/loop tests, runner EA3B05 and other preservation inputs remain frozen.

- [ ] Independent SOURCE review of this test/plan freeze.
- [ ] Separate sole-slot grant for a focused run; candidate explicit order is the existing `test_sqlite_reopen_new_source_generation_rejects_preclaim` function (four source parameters), then the two functions above (two parameters each). This is not execution permission or an observed case count.
- [ ] Record actual reached assertions/terminal/pins; an earlier precheck/fixture failure does not close either coverage gap. A first GREEN may establish coverage without any production fix; no known bug is installed to force RED.
- [ ] Parent separately decides next exact61/fresh QUALITY gates.

No Python, imports, collection, models/services, runner creation/edit, commit, push or PR102 alignment occurred. Source-close coverage is now **prepared only**, not runtime-proven.

## 16. Parent-observed focused eight-case GREEN and local checkpoint release

Parent reports Lovelace independent SOURCE READY for test BBB6BBEF and all section15 conditions. Parent then executed the exact focused selection, in this order, using approved runner EA3B05 and MedChat `-I -S -B`:

1. `tests/agent/test_decision_binding_continuation.py::test_sqlite_reopen_new_source_generation_rejects_preclaim` — all four parameters.
2. `tests/agent/test_decision_binding_continuation.py::test_source_close_before_real_restore_is_failure_only` — native/json.
3. `tests/agent/test_decision_binding_continuation.py::test_source_close_after_resumed_waiting_cas_is_failure_only` — native/json.

**Parent execution evidence**, not a new run by this worker: session **52575**, authoritative terminal **8c1ce5**, exit **0**, **8 passed, 3 SWIG warnings in14.96s**. Parent verified test BBB6BBEF / plan886D28C2 / runnerEA3B05 beforehand and reports all pins unchanged across 656 tracked Python files plus the new codec/test, ownplan and runner. The slot was RELEASED. These passing cases establish the corrected generation/content premise and the two narrowly specified source-close boundaries with their actual rejection/history/no-duplicate-science assertions; they are not full-suite, real-model or normal-Web acceptance.

Historical evidence is preserved: section14's author exact61 **41731/ca935c remains 6370 passed, 2 failed, 7 warnings in1418.28s, exit1**. The focused eight-case result does not relabel that full61 run GREEN or establish the new freeze's full61 result. Section13's separate P2 exact2 **8866/47f031, 2 passed, 3 warnings in11.50s** also remains its own evidence.

Parent now authorizes a **local checkpoint commit only**, explicitly limited to the three production paths, three tests and this ownplan in section2. Pre-append read-only hashes matched all six code/test pins and the approved runner; production remains codec6d500e27, reader18c6714b, loopffa8883. This append alone changes the reviewed plan hash. No Python/imports/tests, runner edits, other code changes or push are authorized or performed by this worker for the checkpoint. The commit message must state the earlier full61 failure and focused-only GREEN, not claim full integration acceptance.

HEAD before checkpoint is still `b4252801cbf2396957de445ec563dcfc815e953e` on `codex/b1-continuation-integration`. Parent will align actual latest main `18ad4f9` before separately granting fresh full61; this worker does not merge/rebase/fetch or align during the checkpoint. Local commit does not grant publication or execution authority.

## 17. Actual parent alignment and one aligned author exact61 release

Parent aligned the local checkpoint to actual main and reports Lovelace alignment SOURCE READY. Read-only verification finds clean merge HEAD **`1bd76e6e4a0585443122b6b4da6c36e035a4e50e`**, parents **`efa2da335588d5c86895bf32ae13cc2ddf763974`** and **`18ad4f93732439d020d9387b1437b09c76358270`**. All seven owned files are unchanged relative to efa2da3 before this documentation append; no production/test edits or alignment operation were performed by this worker. Runner SHA256 remains **`EA3B05E05F0F39A23D0056F9CC051B4653708F4E4ECD5335E42B7E469E7298A8`**.

Parent explicitly grants the sole Python slot for **one aligned author exact61 invocation**, using `C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py` followed by all61 section5 paths as individual arguments in that exact order. Read-only selection checks found61 unique existing paths. No `-k`, directory substitution, narrowed selection, added test, retry, concurrent Python, source edit, push or merge is authorized. The actual launch handle must be reported promptly and polled unchanged through the runner's authoritative terminal; observation timeouts do not stop the run or release its slot.

The new before/after freeze covers **all658 tracked Python files plus this ownplan and the ignored approved runner:660 unique paths**. The earlier parent scope's656 tracked Python files now includes the two committed codec/continuation-test paths, explaining658; do not silently omit either. Record actual SHA256s after this append, before launch, then compare every same path at terminal and separately verify the tracked-path set. Production/test pins remain ffa8883/1B56E632, BBB6BBEF,5C6394,F40680 and the two donor-exact codec/reader pins. This plan's hash alone changes for this release record.

Historical results remain distinct: author41731/ca935c **6370 passed,2 failed,7 warnings,1418.28s, exit1** was not GREEN; parent52575/8c1ce5 **8 passed,3 SWIG warnings,14.96s** was focused validation only. Neither is evidence for this new aligned full61. This section records authorization/preflight, not a launch or passing result. At actual terminal, preserve failures/counts/warnings/time/handle and all before/after pins, explicitly release the slot and stop. Independent fresh QUALITY still requires its own subsequent parent grant; no automatic rerun follows.

## 18. Actual aligned author exact61 GREEN — authoritative terminal and pin receipt

Executed section17's single approved invocation on HEAD `1bd76e6e4a0585443122b6b4da6c36e035a4e50e`, using the exact MedChat `-I -S -B` runner prefix and all61 section5 paths explicitly in their unchanged order. Preflight established61 unique existing targets, runner EA3B05, and660 SHA256 pins (all658 tracked Python files plus plan and runner). The run-time plan was blob **`2f716adb24188aa36dce446b6a5b069cab397f42`**, SHA256 **`4A4FC3F106CA1ABDBFF4401BCBCD4DFFB03736735227B617339038A59ADF4611`**.

Actual exec session **73095**, launch chunk **af260f**; the same handle was polled throughout without restart, retries, extra tests or edits. Authoritative terminal chunk **9e510a** returned process exit **0**, `ORDINARY_PYTEST_EXIT=0`, and **6376 passed,7 warnings in816.85s (13:36)**. No failures or skips were reported. The seven warnings were three SWIG missing-`__module__` and four FastAPI on_event deprecations. This is an actual tool-returned runner terminal, not an inference from observation timeout, progress dots or parent/child PID disappearance.

The worker explicitly **RELEASED the sole Python slot immediately at authoritative terminal**, then verified **660/660 before/after SHA256s identical** and the complete658 tracked-Python path set unchanged. HEAD remained1bd76e6; only the pre-run section17 documentation append was dirty. A further read-only check before this authorized evidence append again matched all660 terminal pins. Parent's later process-absence check7f738c is supplementary only; it is not the source of the exit/result evidence.

Historical section14's 6370-pass/2-failure author run and section16's parent8-case focus remain unchanged and separately attributed. This new result is the aligned **author full61** evidence, not independent fresh QUALITY, real-model/scientific-services or normal-Web activation acceptance. No new run, source/test/runner change, commit, push or merge followed. This authorized result append changes only ownplan after final pins were confirmed; parent controls the next slot sequence and any independent fresh QUALITY grant.

## 19. Independent exact61 verification and publication checkpoint

Independent reviewer Kant completed SOURCE review without blocking findings and
one fresh exact61 run on HEAD `1bd76e6e4a0585443122b6b4da6c36e035a4e50e`.
The reviewed plan was SHA256 `6DF8CDCD72135138FDA7C4A7E07E750952A5966045D0FF42DB9D18D6BB0EF279`;
the approved isolated runner remained EA3B05. Session **37290**, launch **35d120**,
authoritative terminal **bc9113**: **6376 passed, 7 warnings in811.40s**, exit0
and ORDINARY_PYTEST_EXIT=0. No failures or skips. The warnings were three SWIG
and four FastAPI deprecations. All **660/660** before/after hashes, the658
tracked-Python path set and HEAD were unchanged. Preflight manifest SHA256 was
`CF73BB1B2ECACEA279EA47C17299405B405B11D318FA5E1D2884843D45B1A9D8`.
The reviewer explicitly released the sole Python slot after the actual terminal.
This is independently executed QUALITY evidence, not a reclassification of the
earlier failed run or the author's run. The reviewer reconfirmed this receipt
without rerunning tests when the parent resumed publication work.

Parent separately tested the two newly incoming shared modules in explicit order:
`tests/agent/test_generator_control_contracts.py` and
`tests/agent/test_json_boundary_ascii_accounting.py`, using the approved isolated
runner. Terminal **beb95e**: **462 passed in3.15s**, exit0, no warnings/skips and
five pins unchanged. This is supplementary shared-module evidence, not an extra
continuation full-suite run.

Only this evidence documentation changes after verification; the three production
and three test files remain at their reviewed hashes. Publication is restricted
to the seven section2 paths. GitHub connector access has become available and
reported no existing PR for this head branch; no credentials were extracted.
Exact-head remote CI, unresolved-review checks and reviewed/merged-tree equality
remain required before merge. No production entry, model, dataset, service or
deployment is enabled. Normal Web, B2 generation/ranking, C docking integration
and package8 live acceptance remain unfinished.
