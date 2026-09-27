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
