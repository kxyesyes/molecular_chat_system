# B1 actual decision execution implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Parent owns this plan. Do not start this implementation before source-hook code review and written-plan review pass.

**Goal:** Execute all seven B1 tools through the existing model-decides-next-action loop with whole-input evidence binding, current-source revalidation, immutable obligations and bounded continuation.

**Architecture:** Extend ModelDecisionLoop and WorkflowRunSession behind the explicit server B1 profile. Keep legacy four-tool behavior and revision6/7 unchanged. One resolver handles B1 role/input/proof policy; existing adapters, validators, ledger, storage and WorkerOwner remain the execution/authority mechanisms.

**Tech Stack:** Python, Pydantic2, LangGraph's existing loop, SQLite, RDKit, temporary FAISS/reverse sources, pytest; synthetic decision transport only in this batch.

## Release boundary

This plan is grounded in source `66039eb` and design commit `c6d150f`; the
attached-source hooks are a separately reviewed prerequisite, now implemented
and independently approved at `435ae32`. Written core review approval is recorded
below. Normal Web B admission, B2 generation/ranking,
C cleanup and final real-model/UI testing remain required separate increments.
No scientific producer changes, arbitrary model input, static tool sequence,
parallel execution framework, new schemas in browser messages or default switch.

Read the actual design `docs/superpowers/specs/2026-09-26-b1-decision-execution-design.md`.
All tests run serially through the approved offline wrapper:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py <explicit test paths or node IDs>
```

Do not use raw pytest, application probe imports, live service configuration or
host model/data discovery. No credentials, real APIs/models, deployment or push.

## File responsibilities

| Files | Responsibility |
|---|---|
| new `src/agent/contracts/binding_requirements.py` | Frozen strict v2 obligation types/parser; no scientific I/O |
| `src/agent/contracts/decision_bindings.py` | Closed source, role and proof types beside existing closed arguments |
| new `src/agent/harness/decision_bindings.py` | Prepared obligations, exact input/role resolution, record handles, action identity, current-source closure and acceptance |
| `src/agent/harness/decision_policy.py` | Explicit profile-aware authorization/catalog; no global INITIAL_TOOLS expansion |
| `src/agent/harness/decision_execution.py` | Reusable owned-call settlement, same cancellation/drain rules |
| `src/agent/runtime/run_session.py` | Optional trusted pre-ledger prepare seam, idempotent journal, terminal invalidation boundary if required by reviewed design |
| `src/agent/evidence/ledger.py`, `src/agent/harness/decision_inputs.py` | Conditional binding-proof inclusion and matching integrity checks; legacy exact serialization unchanged |
| `src/agent/harness/decision_loop.py` | B1 selection of resolver/acceptance, deadline accounting, dispatch/reuse/terminal checks |
| `src/agent/harness/decision_continuation.py` | Revision8 configuration, history reconstruction, source validation before CAS, no budget reset |
| new `tests/agent/test_decision_binding_requirements.py` | Closed contract/parser and immutable obligation preparation |
| new `tests/agent/test_decision_dynamic_bindings.py` | Actual loop, registry, Session, seven-tool/dataflow/terminal evidence |
| new `tests/agent/test_decision_binding_continuation.py` | Actual SQLite restore, replay, pre-CAS/state/callback/deadline checks |

No other production files without an explicit parent dependency review. Avoid a
generic rule language or JSONPath evaluator; B1 supports only the named roles.

## Task 1: Closed requirements and proof inputs

- [x] Add failing parser tests for version2/profile-required, strict native types,
  forbid-required conflicts, duplicate tools/subjects, invalid constructed models,
  bounds before serialization/default expansion, and exact source/role fields.
  Assert v1 parser still rejects v2 and the old argument parser stays closed.

```python
payload = {
    'version': '2', 'profile': B1_PROFILE_REVISION,
    'retrieval_result': {'query': 'synthetic query', 'k': 3, 'require_hits': False},
}
parsed = parse_binding_requirements(payload)
assert parsed.model_dump(mode='json')['retrieval_result'] == payload['retrieval_result']
payload['retrieval_result']['k'] = True
with pytest.raises(ValueError, match='^invalid_binding_requirements$'):
    parse_binding_requirements(payload)
```

- [x] Run `tests/agent/test_decision_binding_requirements.py` and record RED.
- [x] Implement the exact design's frozen/strict/extra-forbid types, use existing
  MolecularRequirement unchanged. Parser `parse_binding_requirements(value)`
  returns BindingRequirements; absent value rejects. Validate native dict before
  JSON serialization; only exact known constructed models may be projected with
  primitive fields preserved and then revalidated. No bytes/tuple/Enum coercion
  from raw JSON callers. Persisted tuples become arrays via validated model dump.
- [x] Add `prepare_binding_requirements(value, *, context, request_kind,
  allowed_tools, required_tools)` in the resolver: whole-SMILES validation and
  canonical-equivalent duplicate rejection occur here, never import-time.
  Retrieval query equals original context query; target query must equal original
  query or a field extracted by the existing user-input target parser. Compare
  activity target to original user target, not the model proposal. Inconsistent
  obligations fail before a model, tool or trace creation.
- [x] Re-run new module plus existing `test_decision_requirements.py` and
  `test_decision_binding_arguments.py`; preserve all old tests unchanged.

## Task 2: Owned-call settlement and pre-ledger proof hook

- [x] Add real Session tests with a prepare callback that observes domain-validated
  results, modifies the copied step metadata, and deliberately raises or encounters
  a persistence retry. Count tool calls and callback calls. Assert no provisional
  scientific data is registered or persisted on preparation failure.
- [x] Extract `settle_owned_call(callable, *, worker_owner=None)` from existing
  settle_action, preserving Task creation failure, repeated cancellation, root
  abort_unstarted/settle and worker.result semantics. `settle_action` retains its
  existing execute_step journal retry inside the callable and delegates settlement.
- [x] Add optional `observation_prepare` to Session constructor, allowed only for
  dynamic sessions and a callable. Invoke after domain validation/alignment but
  before final provenance/output digest and ledger registration. Pass the actual
  copied WorkflowStep, not the append_step caller's object. The hook returns no
  replacement result; it mutates the accepted result/step under server authority.
  Convert failure into a safe invalid-output result with no scientific data,
  artifacts or source evidence; never store the unsafe intermediate.
- [x] Conditional proof propagation adds binding_proof to ledger input_binding and
  the corresponding integrity comparison only when present. Preserve the three
  legacy fields and old evidence hashes when absent. The existing capture hook
  remains once-after-ledger; normalized/captured journal flags prevent tool replay
  and resealing on commit-then-error.
- [x] Run targeted new Session cases then `test_decision_loop.py`,
  `test_decision_inputs.py`, `test_decision_adapter_retry.py` and existing receipt
  consumer Session cases. Record each result, not only the final green run.

### Task2 source-inspection clarification: explicit ledger authority

The existing ledger copies three quality fields when request_input_digest is
present; arbitrary legacy quality extensions are not authority. Do not infer B
enrollment just because a raw tool includes a key named binding_proof. Add an
optional keyword-only `binding_proof=None` to `register_tool_result`, defaulting
to the unchanged three-field legacy behavior. A non-None proof must be the
revalidated, closed native projection returned by the server's B preparation,
match result.quality and be bounded before copy/hash; reject missing base input
binding when proof is supplied. The dynamic Session passes this argument only
after its explicit observation_prepare callback completed and the copied step
metadata/proof agree. A raw binding_proof quality extension with no callback
does not change legacy ledger identity or enroll a result in B.

The integrity verifier reconstructs the fourth binding field only when the
authoritative ledger record contains it, then requires exact equality with the
sealed result quality; it must not add a fourth field to old records based on
untrusted extras. Validate both proof projections before comparison (Python
equality alone accepts True == 1). Revision8 decode and the separate
Session.restore_observations ledger rebuild pass validated proof through an
explicit B authority parameter; neither discovers authority from quality keys.
Revision6/7 remain unchanged. Add three boundary tests:
legacy raw proof-shaped extension does not alter ledger binding, explicit proof
appears in both ledger and sealed quality, and dropping/replacing a recorded
proof causes integrity failure.

Preparation has its own journal boundary before registration. Cache the accepted
prepared result, detached validated proof and completion flag before calling
register_tool_result. A failure after preparation must retry registration without
repeating domain validation or the preparation callback against mutated state.
On callback failure or proof mismatch, cache a fresh sanitized INVALID_OUTPUT,
discard provisional proof/source metadata and never resurrect the provisional
result; diagnostics contain neither callback exception text nor mutated payload.
Existing normalized/captured flags continue to govern subsequent persistence,
but are not sufficient for this earlier boundary. Add injected registration
failure/retry and callback-mutation/failure tests with exact tool, validator and
prepare-call counts. Generic owned-call settlement never retries arbitrary
callbacks; the execute_step retry stays inside settle_action.

Godel's source review at756c26a approves the explicit-proof authority interface
and identified the separate preparation-journal requirement recorded above.
The independent follow-up review approves the amended clarification with no
remaining bounded design defect. This is an implementation requirement, not
evidence of completed code or tests. Task2 source review also confirms existing
dispatch-failure tests inspect settle_action.<locals>.advance: keep that closure
inside settle_action when extracting settlement, rather than weakening those
tests. Session restoration and continuation decoding must both retain their
legacy calling convention when no explicit proof authority is supplied.

## Task 3: Inputs, action identity and live closure

Dependency reproduced during Task3: the actual TargetDatabaseTool returns
success=True/status=not_found for a completed empty lookup. TargetToolAdapter
validates/maps domain lookup statuses, but SingleAttemptTool's earlier generic
raw guard rejects this before the typed validator runs. The actual reverse-to-
target Session test remains failing; do not replace it with a fake success.

Allow a narrow prerequisite in decision_execution.py plus a separate new
test_decision_target_status.py: for the actual typed target-search adapter only,
bound the untouched native raw envelope first; apply the existing LOOKUP_STATUS
mapping to a detached status-check view for recognized successful lookup states.
Run the existing generic guard on that view. The adapter must still receive and
validate the original raw status/data/evidence/quality, including contradictory
resolved/not_found counts. All other tools/adapters retain the existing strict
guard, and no raw envelope is modified. Ambiguous/error/partial states must not
be promoted. Cover real empty lookup and resolved rows, non-bool success, errors,
unknown/conflicting statuses, misleading tool names and preserved metadata.
This is a bounded execution-wrapper compatibility fix, not broad status
normalization or a relaxation of typed scientific checks. Record independent
review and test evidence before publishing it as a prerequisite change.

- [x] Add negative role/identity tests before resolver code: unknown/foreign IDs,
  wrong producer, wrong whole subject set, duplicate/forward/cyclic parents,
  modified selected reverse row, sealed nested tamper and valid-equal SMILES from
  a different source. Reject with zero downstream tool calls.
- [x] Implement pure binding resolution over current session observations and
  explicit requirements. User input is whole parsed current input or authorized
  selected structure; reverse enforces exactly one. Upstream molecules preserve
  the full batch, activity target stays in the structured query wrapper. Use
  existing BindingResolver rendering/whole parsing instead of regex fragments.
- [x] Issue deterministic reverse record handles beside encoded observations.
  Hash the original raw row, its position and observation evidence ID; lookup
  reconstructs all values. Project only source-backed TargetSearchInput fields,
  never normalized name hashes/CHEMBL molecule IDs as gene identifiers. Empty or
  contradictory fields fail; a protein name is only a lookup hypothesis.
- [x] Resolve logical action hash from the design's closed payload and adapter
  version. Pre-ledger preparation validates attached source through the new hooks,
  records exact own_source and finalizes operation_key. Failed source has null
  own_source and cannot satisfy requirements. Strip tool-authored reserved fields.
- [x] `verify_binding_closure` reconstructs and checks all prior ancestors,
  whole-observation seals, ledger proof, original obligation hash and current
  source. Caller executes this via owned-call settlement with the same deadline
  checked before/after. Reuse searches logical action identity then checks final
  operation key/source; stale evidence is not rerun or relabelled.

### Task3 interim source review (not approval)

James's independent read-only SPEC review inspected an unchanged four-file
snapshot at HEAD b907e8c: resolver SHA256
136D8AE00A2F0057A23D8EE68959FF2F6DD67A56E8CE832892C1AD157F98BADF,
dynamic test 44ACE8A45379A4F3A6328F4C0046B8828A0AE8BE4616C0021369A22133727879,
execution wrapper E1A648747E69EF58818CF23B5217F0D76E320B06D45FE10423440036A0E199AA,
target-status test DDC529ED251A95C67AED7FF4AC4825A2F776A129B8B79E8E49B026648AB42CF6.
No tests were run by the reviewer. Three findings require reproduction/correction:

- Original input without SMILES, followed by an admitted SMILES clarification
  and then a target-only clarification, must retain the prior independent
  property result. Original-only fallback is insufficient. Any retained input
  authority must come from explicit bounded server-admitted records, not model
  history or an unvalidated query list; reference identity remains mandatory.
- Original action records duplicate context and input. Use their appropriate
  bounded record/snapshot budget consistently through issuance/reconstruction/
  restore, without relaxing the separate64KiB scientific-observation boundary.
  The long escaped-query test was present but is not presumed passing.
- Non-activity molecular tools must not inherit activity-family target rejection
  merely to extract complete SMILES. Property-only EGFR plus a valid SMILES must
  remain executable; activity authorization and invalid whole-input rejection
  remain strict.

Parent source inspection confirmed these are relevant boundaries and requested
TDD from the existing implementer. No SPEC approval, final regression result or
Task3 completion is claimed. The narrow typed-target status prerequisite had no
source defect identified in this review; it still requires final test/quality
evidence. Task4/5/6 were explicitly outside this interim review.

## Task 4: Actual seven-tool dynamic execution and acceptance

### Task4A/4B implementation order after Task3 review

Read the source-grounded input-admission clarification appended to the core
spec. Independent written review must approve it before implementation. It adds
only harness/decision_binding_inputs.py (server human-input boundary) and
harness/decision_binding_acceptance.py (pure deterministic acceptance using the
existing resolver/validators), with focused new input/acceptance/loop test files.
No new conversation database, model inference, Web protocol or legacy rewrite.
The input clarification review resolution also allows only the keyword-only
context_value(query_content_bytes=False) extension in decision_bounds.py; exact
bool True selects B content bytes while preserving every other existing bound.
Default/legacy behavior remains unchanged. Follow the full-context freeze,
one-based proposal/zero-based journal mapping, exclusive journal authority and
trusted-store/pre-CAS candidate/post-CAS installation rules in that resolution.

- [ ] 4A-input RED: original missing both inputs, admit SMILES, then target,
  without executing a tool between replies. Resolve correct molecule/target;
  model/session history alone still cannot supply them. Verify native turn index,
  owner/fixed metadata, exact retry vs rewrite, gaps, prefix digest, detached
  projections,16-turn/16KiB-query/64KiB-context/512KiB-total boundaries.
- [ ] Implement BindingInputJournal original freeze, explicit admit_context,
  bounded prefix export/restore and context projection. Restore requires explicit
  caller validation; do not infer authority from a raw dict or checksum. Actual
  revision8 claim integration remains Task5 and must validate every saved turn.
- [ ] Extend resolver only with explicit optional journal injection. Normal B
  loop will require it; direct Task3 API retains its current behavior. Commit each
  action's input_turn/prefix digest in the full original record, not logical
  action hash. Resolve historical actions from their own prefixes and current
  reuse from current constraints. Preserve malformed-input rejection, original
  requirements, target changes/new subjects, and selected-reference identity.
- [ ] Cover sequential-prefix negatives: valid -> malformed -> omitted input,
  conflict -> continue, omitted reference retention, equal-SMILES foreign
  reference and sticky explicit-SMILES selection clearing. No later reply erases
  a prior conflict; unknown activity families do not block property parsing.
- [ ] Cover all frozen AgentContext fields and journal-head mismatch at resolve,
  prepare, reuse and closure. Restore trial resolver must retain explicit journal
  authority; historical actions use their own prefix, not the latest context.
- [ ] Keep proposal input_turn one-based and record/journal turn zero-based;
  test 0/1/2 journal against 1/2/3 proposals and unchanged earlier reuse records.
  Test B UTF-8 content boundary plus unchanged legacy JSON limit and64KiB context.
- [ ] Run new input module plus Task3/requirements/Session/receipt regression;
  independent SPEC then fresh QUALITY before the 4A-input checkpoint.
- [ ] 4A-acceptance RED/GREEN: implement the existing seven-tool/group checks and
  citation closure specified below in the focused acceptance module. Use actual
  sealed Session observations, not dictionaries standing in for evidence. Domain
  failures/demo/fallback/family disagreement remain unusable or partial/review;
  no-hit differs from requested hits. Required tools include all explicit groups.
- [ ] 4B actual loop: implement the catalog and execution tests below. Readonly
  source evidence shows legacy preparation only includes molecular groups and
  legacy context limits; do not reuse those paths accidentally for explicit B.
  Until revision8 is implemented, B must not mint or claim a revision6/7 nonce.
  Task4 alone cannot be published as complete B1 or enabled in normal Web; Task5/6
  and full integration gates remain required in this same core effort.

Source inspection after Task1: context_value still enforces the legacy16KiB
JSON-encoded query limit, and the loop calls it before requirements preparation.
Task1's UTF-8 retrieval-content regression alone therefore does not establish the
same behavior through B's actual loop. Add an actual B-loop quoted/backslash and
non-ASCII near-limit retrieval case before wiring. Preserve legacy context bounds
and the whole64KiB context boundary; only the explicit B path may use the approved
16KiB UTF-8 query-content limit. This is an integration test requirement, not a
claim that the existing loop has already been repaired or exercised.

- [ ] Baseline actual-loop test must demonstrate that registered RAG/ADMET/reverse
  are currently unauthorized, not merely that a new constructor keyword is absent.
  Keep a failing new opt-in test and a passing unchanged legacy negative.
- [ ] Add server constructor binding_profile; None retains old behavior, exact B1
  selects the reviewed implementation, B2/unknown reject. B requires a WorkerOwner
  and explicit v2 requirements. Catalog intersects B1, allowed/required permissions,
  actual registry ownership, idempotence and side-effect policy. Scientific and
  RAG flags are independent strict bools. Chat has no tools even if both flags true.
- [ ] Use existing decide/tool graph. The model chooses every next tool from actual
  observations. Add B-only resolver, action hash index, source preparation and
  acceptance branches; no ordered list of tools or keyword workflow templates.
  Every post-callback boundary before model/tool/answer validates source closure.
- [ ] Require actual temporary-source RAG and reverse, actual RDKit properties/
  likeness, typed ADMET/activity fixtures where dependencies are synthetic and
  explicitly labelled, and test-owned target database. Both native and JSON
  modes must exercise next-decision selection from prior result, reverse→target,
  property→ADMET/activity, RAG-only with scientific_tools=False, and no-tool chat.

```python
def consume_actual_reverse(messages):
    observation = last_observation(messages)
    descriptor = observation['record_references'][0]
    return tool('target_database_search', arguments={
        'input_ref': observation['quality']['evidence_id'],
        'record_ref': descriptor['record_ref'],
    })
```

- [ ] Acceptance reuses existing molecular metric checks and domain validators.
  Check exact input subjects/count, ADMET method availability vs unknown, real
  activity provenance/status (family disagreement stays partial), reverse proof,
  RAG valid no-hit vs discarded/unavailable, target status/evidence and required
  tools. Use no LLM self-evaluation. Final scientific text is deterministic tool
  evidence with visible limitations; never hide failed optional steps.
- [ ] Add mandatory citation closure: expand each explicitly cited sealed
  observation's ancestors in observation order; for every obligation/required
  tool select the earliest satisfying observation within that closure. Return
  cited_closure_ids and required_evidence_ids. A fulfilled-but-uncited group gives
  missing_required_citation and cannot complete. Add unrelated-only citation
  negative and downstream-only citation positive with ancestor expansion. Render
  successful scientific prose only from that verified closure; uncited failure
  diagnostics remain visible without adding ungrounded numeric claims.

## Task 5: Revision8 restore and charged verification time

- [ ] Add actual SQLite tests: clarify→new loop instance→restore/reuse; count zero
  repeated scientific calls. Assert old revision6/7 snapshots retain old readers.
  Mutate every proof, role/input, producer version and obligation independently.
- [ ] Extend configuration/snapshot/history readers only for B revision8. Persist
  profile/closed proofs/requirements and original inputs; never source objects or
  callables. Rebuild handles and every action/key from historical decisions and
  validated original source observations before CAS; model messages are not proof.
- [ ] B segment_start precedes continuation read. After saved credit validation,
  set deadline=min(initial_deadline,segment_start+saved_credit,admission_deadline
  if present). Charge read/decode/hash time. State restores this exact deadline.
  Test clock advancement inside source validation and callbacks; known stale or
  exhausted pre-CAS retains waiting nonce. Post-CAS drift/exhaustion may consume
  nonce but must block all new calls/results with honest failed state.
- [ ] Debit outgoing post-snapshot time via a fixed conservative reservation:
  at t with remaining R>0 save R-F with F=min(30.0,R/4), plus exact native finite
  pre_finalization_remaining_seconds=R and finalization_reserve_seconds=F.
  Revision8 validates the policy and arithmetic; revision6/7 are unchanged.
  Bound the finish/publish/check/drain tail by t+F. Never refund unused reserve
  or update snapshots in a loop. Two-segment controlled-clock test: R100/F25
  with20s tail resumes with at most75; a30s tail exposes no continuation.

## Task 6: Final publication and worker lifetime

- [ ] Keep B WorkerOwner open through all needed terminal checks; settle each
  action root as before, but close owner only in outer finally after callbacks.
  Tests must prove no reopening, final source checks run off event loop, repeated
  cancellation drains and no answer is released from stale source data.
- [ ] Move B outward checkpoint/result release to outer run after owner drain and
  final clock check. A private request-local finalization record carries the
  candidate and correction authority from _run; never persist it. No checkpoint
  is set on admission_exchange before drain. Tail/root exhaustion after drain
  invokes failure-only correction without opening new worker/source work.
- [ ] Add B-only DecisionEvents terminal projection retaining event identity but
  carrying only terminal_attempt_id, observed_outcome,
  publication_stage='pending_source_check', answer_released=false. No scientific
  payload or continuation ID. The later normal-Web B integration must recognize
  pending publication; unchanged legacy consumers cannot receive it as completion.
- [ ] Add opt-in Session `invalidate_dynamic_publication` with a frozen correction
  request and per-write journal. It is legal after terminal finalization starts
  or finishes, but cannot reopen execute/ordinary finish or mutate historical
  events. Replace cached candidate with a new sanitized FAILED result. Persist
  invalidation marker then failed status then distinct stable TASK_FAILED event
  identifying superseded terminal attempt, failed boundary and invalidated IDs.
  Never copy tool data, evidence/ledger/outputs, artifacts/claims, acceptance
  success or continuation IDs into failed result metadata. Each failure-only
  write is attempted even if another fails; retry only identical payload once.
- [ ] Fixed barriers are terminal metadata, finish_dynamic/event/status, then
  waiting publication/fallback status. Check source/deadline after each, even
  exceptional callback returns. Latch invalidation permanently; corrections do
  not trigger source verification or positive retries. Leave checkpoints unset
  until last successful check. Reject stored revision8 invalidation markers even
  if the failed-status write was unavailable. Report durable correction failure
  truthfully; do not delete committed waiting snapshots or undo uncertain claims.
- [ ] Regress callbacks mutating source during metadata persistence, finish event,
  status update and waiting publication. Verify returned result, stored status,
  emitted terminal sequence and absence of usable scientific payload after the
  invalidation boundary. Healthy B returns the verified result without correction;
  old event payloads and revision6/7 paths remain unchanged. The source may change
  after the final check; do not claim an atomic file/database transaction.

## Task 7: Integration review and commit gate

- [ ] Run new modules and all existing decision*, ordinary semantic and Web decision
  tests relevant to modified call paths through the isolated runner, one heavy
  process. Existing tests remain unchanged unless independently reproduced test
  fixture incompatibility is approved; no deadline/scientific assertion relaxation.
- [ ] Independent SPEC then fresh QUALITY inspect actual loop execution, source
  handling, callback/replay budgets and same-result preservation. Record commands,
  full counts, skips/warnings, intermediate failures and exact source hashes.
- [ ] Parent diff/compile check, original worktree unchanged confirmation, exact
  staging and commit; update eight-package ledger with precise offline scope.
  This gate proves B1 core only. Immediately following normal-Web work must use
  actual /ws admission and same-socket continuation, not a lab endpoint substitute.

## Written review and implementation release

Tasks1–5 map the contract, proof and replay requirements. Source inspection showed
ordinary finish retries cannot correct already published results. Herschel's
read-only design sidecar recommended the B-only provisional summary plus
append-only failure correction now specified in Task6. Parent adopts that
recommendation, with a mandatory normal-Web flag-consumption gate.

Zeno's independent written review initially rejected two bounded gaps: outgoing
snapshot finalization time could be regained, and fulfilled-but-uncited mandatory
evidence could pass completion. Parent added the fixed reserved-credit protocol
and deterministic mandatory citation closure to both spec and plan. Zeno's
second read-only review APPROVES the written B1 core spec/plan and found no direct
contradiction introduced. No tests were run by the written reviewer.

Implementation release is OPEN for this plan's allowlist/TDD, not normal Web
activation or a scientific acceptance claim. Source-hook independent SPEC and
QUALITY plus repeat1217 passed are recorded at435ae32. Tasks1–7 here remain
unchecked until actual implementation/test/review evidence exists. Preserve the
original eight-package objective and pending B2/C/P8 boundaries.

## Task1 implementation evidence (review pending)

Task1 implementer changed only the four contract/preparation/test files in this
plan. No resolver, execution, continuation or Web activation is implemented by
this slice. Parent verified the four file hashes against the implementer freeze.
Existing argument/v1/profile tests remain unchanged. No scientific source access,
external models, credentials or services were used.

All commands used the isolated runner prefix documented above. Argument sets:

```text
A: tests/agent/test_decision_binding_requirements.py
B: tests/agent/test_decision_binding_requirements.py::test_explicit_v2_and_detached_frozen_roundtrip tests/agent/test_decision_binding_requirements.py::test_valid_whole_batch_and_missing_input_preserve_obligations tests/agent/test_decision_binding_requirements.py::test_closed_proof_sources_roles_and_detachment
C: tests/agent/test_decision_binding_requirements.py tests/agent/test_decision_requirements.py tests/agent/test_decision_binding_arguments.py tests/agent/test_decision_binding_profiles.py
D: tests/agent/test_decision_binding_requirements.py::test_preparation_rag_query_uses_utf8_content_not_json_escape_budget
```

| Run | Arguments | Observed result | Pytest duration |
|---|---|---|---|
| 1 | A | Missing-module/API RED:98 failed,1 passed | 6.07s |
| 2 | B | Callable-entry behavioral RED:3 failed | 2.72s |
| 3 | A | 98 passed,1 failed | 3.00s |
| 4 | A | 111 passed | 10.67s |
| 5 | C | 245 passed | 26.05s |
| 6 | D | 2 failed,2 setup/teardown errors | 1.64s |
| 7 | D | Clean behavioral RED:3 failed | 3.37s |
| 8 | D | 3 passed | 1.72s |
| 9 | C | 249 passed | 24.19s |

Run3 corrected an undersized default-expansion test fixture to a verified
32,760-byte input. Runs6–8 distinguish UTF-8 query content size from JSON escape
size; run6 also hit oversized parameter IDs, shortened before clean RED. Runs1
and6 had truncated output but visible final summaries. Final union reported no
skips/warnings, exit0. Sessions37719/51545/63443 were polled to completion, not
restarted; no test process remains live at this checkpoint. Runner SHA256:
F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183.

Frozen SHA256 (before independent review):

- contracts/binding_requirements.py: 7C27697B3158461729774AB1E36A697D834505BA272A4BC0D74F2D75B2ECBC29
- contracts/decision_bindings.py: 4683C6CFA60156AD6D2576086F2E4ABAE1FC71862402B42A2DCB40E65D2A3A56
- harness/decision_bindings.py: 013B88E9826CDBA49D230ABEE9D017F999647F49196505A1CB0D77339182862E
- tests/agent/test_decision_binding_requirements.py: 6FFF3F7181904A213A23CBC1C6329F3A982D1E4179D8B9D8778C59C0388E0D62

Independent SPEC then fresh QUALITY review/repeat are pending. Checkboxes remain
open until those gates close; passing these contracts is not seven-tool execution
or current real scientific acceptance.

### Task1 SPEC correction: exact activity target authorship

Fermat's first SPEC review rejected family-only comparison: an obligation such
as `BuChE KRAS` matched the original BuChE family despite the unauthored extra
identifier. Source inspection confirmed the family helper intentionally ignores
unrelated identifiers. The fix reuses canonical_target_identifier for the whole
field, preserving genuine aliases and distinct PDE subtypes; no router changes.

The same runner prefix executed these exact focused targets twice:

```text
tests/agent/test_decision_binding_requirements.py::test_activity_obligation_rejects_nonwhole_target_identifier
tests/agent/test_decision_binding_requirements.py::test_activity_obligation_preserves_exact_buche_aliases
tests/agent/test_decision_binding_requirements.py::test_activity_obligation_does_not_collapse_distinct_pde_subtypes
```

RED7 failed/12 passed8.71s (47076,exit1), GREEN19 passed6.05s (8405,exit0), then
the unchanged four-module C command268 passed51.69s (68963,exit0). No restarts,
truncation or reported skips/warnings. All handles completed. Corrected hashes:
harness/decision_bindings.py D5DBF020EE5B95DA2FDF164479B2B8CE57FB99230D19D7A78AFF74DEAA05EAB7;
test_decision_binding_requirements.py 8E9906D54CADBF42425706E17E06559F1AF93B8F2D44B38B1031B0C607B24A64.
Other two hashes unchanged. SPEC re-review and fresh QUALITY remain gates.

### Task1 release

Fermat's re-review APPROVES the corrected Task1; fresh QUALITY reviewer Volta
APPROVES the same four hashes and independently ran command C:268 passed12.05s,
exit0, no reported skips/warnings or truncated test output. Handle56149 finished,
no restart. Runner and code hashes matched before/after. Parent's four-file
in-memory compile (stdlib compile, no application imports or bytecode) and diff
check passed; original checkout still has the same13 status paths. Task1 is
complete locally; Tasks2–7, normal Web B, B2/C and P8 remain pending. The
preparation callback retry clarification is committed separately as e10df12.

## Task2 implementation evidence (independent review pending)

Implementation is limited to decision_execution.py, run_session.py, ledger.py,
decision_inputs.py and new test_decision_binding_session.py. No loop/continuation
activation; revision8 readers remain Task5. Parent reconciled the old spec's
normalized-only sentence with e10df12's approved preparation journal and recorded
the actual-loop UTF-8 integration gate for Task4. Neither documentation change is
a claim of full B execution.

All tests used the isolated runner prefix in this plan, from this worktree.

```text
M: tests/agent/test_decision_binding_session.py
N: tests/agent/test_decision_binding_session.py::test_preparation_journal_precedes_registration_and_capture[none]
D: tests/agent/test_decision_binding_session.py::test_cached_proof_detaches_callback_projection_before_register_retry
P: tests/agent/test_decision_binding_session.py::test_no_throw_malformed_preparation_is_cached_safe_before_copy_hash
U: tests/agent/test_decision_binding_session.py tests/agent/test_decision_loop.py tests/agent/test_decision_inputs.py tests/agent/test_decision_adapter_retry.py tests/agent/test_workflow_run_session.py tests/agent/test_rag_receipt_consumption.py tests/agent/test_reverse_receipt_consumption.py
```

| Run | Targets | Passed | Failed | Errors | Warnings | Seconds | Exit |
|---|---|---:|---:|---:|---:|---:|---:|
|1|M initial RED|2|41|27|0|4.51|1|
|2|N fixture diagnosis|0|1|1|0|1.33|1|
|3|M corrected RED|2|41|0|0|8.82|1|
|4|M initial GREEN|43|0|0|0|10.58|0|
|5|M resurrection RED|48|1|0|0|3.62|1|
|6|M GREEN|49|0|0|0|3.55|0|
|7|U union|487|0|0|3|42.64|0|
|8|D equality probe|1|0|0|0|1.14|0|
|9|D strict-type RED|0|1|0|0|1.26|1|
|10|M GREEN|49|0|0|0|6.75|0|
|11|U first freeze|487|0|0|3|41.47|0|
|12|P post-callback RED|0|8|0|0|2.38|1|
|13|P correction GREEN|8|0|0|0|1.66|0|
|14|M module GREEN|57|0|0|0|4.24|0|
|15|U final union|495|0|0|3|42.01|0|

All runs had zero skips. Run1 output was truncated; all other test output was
not truncated. Initial fixture teardown called nonexistent store.close(), fixed
before production implementation. Run8 equality missed True==1; run9's stricter
type assertion exposed the proof-alias defect before repair. Parent identified
the post-callback malformed-data/digest exception gap before independent review;
eight unchanged RED/GREEN cases now prove safe caching and no unsafe copy hook.
Warnings are the three SWIG __module__ deprecations. Handles45330/28050/89789/
68696/43363 all completed by polling, no restart or live test process remaining.
Runner hash remains F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183.

Parent verified frozen hashes before independent SPEC:

- decision_execution.py: 0649A3ED41305A435FA5EECDD8DB984BD0FE95846D74A52423C435ACAFCD905B
- run_session.py: 9D10601DE9FADC33979FFA8C51BD4842308F7FEFF2A07E77F41812D92E1D48B2
- ledger.py: 43EBACE472B2BDAEA00B721E922057770EA93CA1BD34450B9592640BD2C3C1C6
- decision_inputs.py: 8BEF78D5AF6AFBE9932241383B9C40E975CDF51ED4C8478122D20890BF82E56C
- test_decision_binding_session.py: 87FAD197678AA44AEC19FB2C3C93A93DE5E90BBA9F9C49FA79CC245C4412E9C2

### Task2 SPEC correction: replaced metadata container

Poincare's SPEC review found cleanup invoked clear/update on callback-mutated
step.metadata. Replacement with None made cleanup raise before caching the safe
rejection. Source inspection confirmed the gap. The fix restores the saved slot
directly via object.__setattr__, without invoking replacement-container methods.
Six tests cover None/list/hostile-dict replacements, each returning or raising;
they count one tool/validator/prepare/capture and verify safe SQLite persistence
under commit-then-error retry.

Using the same isolated runner prefix, the focused target was:
tests/agent/test_decision_binding_session.py::test_replaced_metadata_cleanup_never_invokes_mutated_container.
RED6 failed3.71s(exit1), GREEN6 passed4.30s(exit0), followed by command U:
501 passed68.02s,3 SWIG warnings,exit0. All runs zero errors/skips, no output
truncation. Handle93850 reached terminal by polling; no restart/live handles.
Corrected run_session.py SHA256 EB307774A3EB48CE03E5DF07DA7EB4D00FBF1E1FB1CB56151E3432E067BAED72;
test SHA256 CDBC26F3105C64DF41E2340AE19A7DAE6EE4B1CD57A0F05FC1A2ED1C46895018.
Other three source hashes and runner unchanged. Independent re-review remains.

### Task2 release and cumulative review

Poincare's corrected SPEC re-review APPROVES; fresh QUALITY reviewer Bernoulli
APPROVES the frozen five files and independently executed command U plus all
four Task1 command-C modules in the same invocation:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_session.py tests/agent/test_decision_loop.py tests/agent/test_decision_inputs.py tests/agent/test_decision_adapter_retry.py tests/agent/test_workflow_run_session.py tests/agent/test_rag_receipt_consumption.py tests/agent/test_reverse_receipt_consumption.py tests/agent/test_decision_binding_requirements.py tests/agent/test_decision_requirements.py tests/agent/test_decision_binding_arguments.py tests/agent/test_decision_binding_profiles.py
```

Result769 passed82.45s, zero failed/errors/skips, three SWIG deprecation warnings,
runner/process exit0. Handle81182 polled to terminal, no restart/live tests.
Test output was not truncated; truncated source reads were reread in targeted
ranges. Runner/five-source hashes matched before and after. Parent compiled five
files in memory (stdlib only, no imports/bytecode) and checked the diff. Original
checkout still has the same13 status paths; no production assets were touched.

Public seams for next tasks: settle_owned_call(callable,worker_owner=...),
Session observation_prepare(result,copied_step), explicit ledger binding_proof,
and restore_observations(...,binding_proofs={evidence_id: native_proof}). The last
is caller-PREVALIDATED authority, not revision8 decoding or source authentication.
The generic Session proof-map cap128 does not supersede B's12-action/512KiB limits;
Task5 must enforce those existing bounds before invoking restoration. No default
profile change, seven-tool loop integration, v8 activation or normal-Web release
is claimed. Task3 is the next implementation increment.

### Task3 implementation chronology (awaiting corrected independent reviews)

Worker Banach reports the following serial runs, all through the exact isolated
runner prefix above, cwd dynamic-bindings-b1. Labels below define the complete
ordered target list; prefix plus each list is the executed command. All targets
are under tests/agent/. No existing test file or contract was edited.

- M: test_decision_dynamic_bindings.py.
- C: M::test_target_only_clarification_preserves_property_closure_and_reuse,
  M::test_clarification_cannot_change_subject_or_original_activity_target.
- C+: C then M::test_current_authority_change_cannot_reuse_equal_structure,
  M::test_equal_smiles_different_confirmed_reference_never_substitutes.
- T: test_decision_target_status.py.
- R: M::test_tampered_reuse_index_never_becomes_a_cache_miss,
  M::test_failed_recorded_action_is_not_a_new_dispatch_cache_miss.
- MT: M then T.
- Q: M::test_long_escaped_rag_query_original_input_record_uses_snapshot_not_observation_ceiling.
- S: M::test_intermediate_admitted_molecule_survives_target_clarification_and_restore,
  M::test_near_limit_escaped_action_record_exceeds_observation_ceiling,
  M::test_property_molecular_parsing_does_not_require_supported_activity_target.
- N: M::test_intermediate_subject_cannot_be_replaced_by_later_explicit_input,
  M::test_intermediate_subject_restore_requires_same_owner_sealed_original_input,
  M::test_input_history_without_sealed_molecular_action_is_not_subject_authority,
  M::test_unknown_activity_target_does_not_block_properties_or_authorize_activity,
  M::test_unknown_activity_context_never_drops_malformed_molecular_fields,
  M::test_large_record_allowance_does_not_widen_scientific_payload_hashes.
- A: M::test_prior_admitted_action_target_cannot_be_retargeted,
  M::test_prior_admitted_target_is_retained_when_current_reply_omits_it,
  M::test_restore_cannot_rewrite_prior_property_target_into_new_admitted_authority.
- U: M, T, test_decision_binding_requirements.py,
  test_decision_binding_session.py, test_current_source_tool_hooks.py,
  test_decision_inputs.py, test_rag_receipt_consumption.py,
  test_reverse_receipt_consumption.py.

| Run | Targets | Passed | Failed | pytest seconds | Handle, terminal |
|---|---|---:|---:|---:|---|
| 1 | M | 0 | 20 | 2.54 | direct |
| 2 | M | 0 | 20 | 2.85 | direct |
| 3 | M | 0 | 20 | 8.92 | 18695 |
| 4 | M | 20 | 0 | 11.69 | 27949 |
| 5 | M | 45 | 8 | 25.86 | 10258 |
| 6 | M | 52 | 1 | 21.69 | 10067 |
| 7 | M | 63 | 3 | 40.53 | 86721 |
| 8 | M | 66 | 1 | 36.71 | 70711 |
| 9 | C | 3 | 1 | 4.92 | direct |
| 10 | C+ | 9 | 0 | 11.09 | 74139 |
| 11 | M | 77 | 1 | 17.19 | 89808 |
| 12 | T | 27 | 3 | 1.97 | direct |
| 13 | T | 30 | 0 | 3.46 | direct |
| 14 | M | 77 | 1 | 57.98 | 69027 |
| 15 | M | 78 | 0 | 46.26 | 9138 |
| 16 | U | 819 | 0 | 91.63 | 35406 |
| 17 | R | 0 | 4 | 5.21 | direct |
| 18 | MT | 112 | 0 | 19.22 | 10742 |
| 19 | Q | 1 | 0 | 2.33 | direct |
| 20 | MT | 113 | 0 | 15.58 | 85294 |
| 21 | U | 824 | 0 | 294.31 | 28042 |
| 22 | S | 0 | 3 | 2.87 | direct |
| 23 | S | 3 | 0 | 2.75 | direct |
| 24 | M | 84 | 2 | 13.95 | 33361 |
| 25 | M | 86 | 0 | 13.08 | 16129 |
| 26 | N | 11 | 3 | 3.27 | direct |
| 27 | N | 14 | 0 | 2.99 | direct |
| 28 | MT | 130 | 0 | 14.99 | 46939 |
| 29 | A | 0 | 6 | 2.78 | direct |
| 30 | A | 6 | 0 | 2.57 | direct |
| 31 | MT | 138 | 0 | 15.94 | 58703 |
| 32 | U | 849 | 0 | 52.41 | 59757 |

All runs: zero skips/collection errors. T has zero warnings; others have three
SWIG DeprecationWarnings. Failing runs exit1, passing runs exit0. Run1 missing-API
RED output was3898 tokens truncated at3500; summary retained. Other test output
was not truncated. Runs2/3 lacked fixture trace/store and are not functional RED.
Run12 includes the actual wrapper RED and a subsequently corrected partial-state
test expectation. Run14 was an incorrect positive expectation that target inputs
would include schema None defaults; the real adapter deliberately preserves the
non-projected dictionary. Negative tests were unchanged. Run24 overchecked source
diagnostics before authority was needed; correction narrowed that precheck, not
receipt validation. Run26 reproduced restore owner/trace omissions. Some failed
source tests captured MorganGenerator deprecation stderr separately from pytest
warnings. All handles reached terminal by polling; no restarted/live test remains.

Corrected source also rejects tampered reuse indexes and failed-cache reruns.
Complete original action records have a separate sealed binding_record_sha256
quality field; this prevents rewriting prior property-action target prose while
retaining a logical action hash based on consumed inputs. Intermediate subjects,
targets and selected-reference identity derive only from reconstructed sealed
records, never from a model transcript or arbitrary session.input_queries.
Pure clarification turns with no recorded action still need an explicit server
admission interface in Task4/5; do not silently count them as authenticated input.

Final worker and parent-read hashes before corrected SPEC:

| File | SHA256 |
|---|---|
| harness/decision_bindings.py | E27F46738EF3AED46635C5C324D348AC2377CFEA0F620CD62095695B91DD25CF |
| test_decision_dynamic_bindings.py | B9D42B0E9F3ECEEFA0C5E7CB280C2354DD1667A8B330372756CFC8D4A693E7D0 |
| harness/decision_execution.py | E1A648747E69EF58818CF23B5217F0D76E320B06D45FE10423440036A0E199AA |
| test_decision_target_status.py | DDC529ED251A95C67AED7FF4AC4825A2F776A129B8B79E8E49B026648AB42CF6 |

contracts/decision_bindings.py remains unchanged at
4683C6CFA60156AD6D2576086F2E4ABAE1FC71862402B42A2DCB40E65D2A3A56;
runner remains F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183.
Corrected SPEC and fresh QUALITY are required before commit/release. No actual
ModelDecisionLoop B1 integration, revision8 or final live acceptance is claimed.

Corrected SPEC at the four hashes above confirmed the original three fixes but
requested two more bounded reproductions before approval: find_reusable must
validate the stored record against its sealed record digest before using its
action hash even to skip an index entry; and authority reconstruction must
verify observation integrity before filtering on mutable usability flags.
Counterexamples are a changed private record action hash yielding a cache miss,
and a changed published demo flag hiding a previously admitted PDE5A constraint
before a user-bound BuChE activity action. These are source findings, not executed
results yet. The existing implementation worker owns the sole test slot for
RED/GREEN and the unchanged eight-module union; no approval/commit is claimed.

Both counterexamples were reproduced and corrected by the same worker. The
following targets also use the same isolated prefix and tests/agent/M expansion:
P2 = M::test_server_record_action_hash_tamper_cannot_become_reuse_cache_miss,
M::test_target_authority_is_verified_before_usability_filtering,
M::test_subject_authority_is_verified_before_usability_filtering,
M::test_intact_missing_receipt_diagnostic_is_not_required_as_target_authority.
Bounds = M::test_reuse_record_is_bounded_native_before_index_skip.

| Run | Targets | Passed | Failed | pytest seconds | Handle, terminal |
|---|---|---:|---:|---:|---|
| 33 | P2 | 2 | 6 | 31.56 | 94401 |
| 34 | Bounds | 0 | 4 | 3.84 | direct |
| 35 | P2 then Bounds | 12 | 0 | 3.19 | direct |
| 36 | U | 861 | 0 | 60.18 | 69868 |

All four runs: three SWIG warnings, zero skips/collection errors, no truncation;
failed runs exit1, passing runs exit0. All handles terminal, no restart/parallel
test. The reuse index now checks bounded native records and their sealed digest
before any match/skip; subject/target authority checks seals before usability
filtering. Intact failed diagnostics are still excluded without requiring a
successful receipt. API/proof fields and target prerequisite files unchanged.
Resolver SHA256 now548AABD2E6B8F631661CCA726B4D30BE0260BB78F1D87419587490D2EC52ED61;
dynamic test BE50370572D0301E71246C48659AEEDB9F71C0DF45E7D68D48B302BD7C7A4675.
Independent corrected SPEC remains the next gate, followed by fresh QUALITY.

Next SPEC re-review confirmed both corrections but found a legitimate diagnostic
case: source validation may throw during preparation, so Session intentionally
seals a sanitized failure with only the original three input-binding fields and
no prepared proof/record digest. Such a failure must prevent repeating that same
action, without blocking an unrelated permissible action or later restoration.
The old diagnostic test stopped after resolve; extend it through reuse lookup,
actual unrelated execution, closure and restoration before claiming success.

Parent inspected Session's existing rollback and base-binding path. A narrow
resolver-only solution is authorized for TDD: initial operation_key commits the
bounded original action record; successful preparation still replaces it with
the unchanged hash(action_sha256, own_source). On a precisely identified sealed
sanitized preparation failure, validate that initial record identity plus the
request/input/provenance/owner before treating it as non-scientific diagnostics.
No invented scientific proof/receipt, source retry or success promotion; same
failed-action rerun and modified record/seal/owner/input must still reject.
No Session or legacy ledger change is authorized by this clarification. The
independent reviewer is checking this bounded dependency, and the implementer
must reproduce and verify it. Task3 is still not approved or committed.

James's independent source/design review approves this bounded mechanism, not
implementation. The initial commitment must use a distinct deterministic domain
tag and full bounded record digest; success retains its existing final-key codec.
Recognize the exact Session failure shape by status/error/empty scientific fields
and absent proof, not message text alone. Seal/ledger, owner/tool/request/input IDs
and provenance remain mandatory before index use. No scientific-source refresh
is needed merely to preserve this unusable diagnostic, and it contributes no
subject/target/reference authority. No Session/ledger code expansion is needed.

The worker implemented that resolver-only boundary; exact new targets, using
the same prefix and M expansion:

- P: M::test_sanitized_preparation_failure_allows_unrelated_execution_closure_and_restore,
  M::test_preparation_failure_retains_domain_tagged_original_record_commitment,
  M::test_preparation_failure_original_record_or_seal_tamper_is_not_skippable.
- Diagnostic-N: M::test_same_message_non_sanitized_sealed_failure_cannot_use_diagnostic_exception,
  M::test_sanitized_failed_action_supplies_neither_subject_nor_scientific_role,
  M::test_sanitized_failed_action_does_not_supply_target_authority_after_restore.
- J: M::test_preparation_diagnostic_json_roundtrip_actual_session_restore_and_continue.

| Run | Targets | Passed | Failed | pytest seconds | Handle, terminal |
|---|---|---:|---:|---:|---|
| 37 | P | 8 | 6 | 25.66 | 37546 |
| 38 | P | 14 | 0 | 25.25 | 13338 |
| 39 | Diagnostic-N | 9 | 0 | 17.64 | 66620 |
| 40 | J | 0 | 2 | 6.90 | 37344 |
| 41 | J | 2 | 0 | 6.05 | 68207 |
| 42 | U | 886 | 0 | 59.87 | 92095 |

All runs: three SWIG warnings, zero skips/collection errors, no truncation;
failure exit1 and success exit0, all handles polled terminal. Initial key is
EvidenceLedger.output_digest({'kind': 'b1-preparation-input',
'record_sha256': EvidenceLedger.output_digest(full_bounded_record)}).
Successful preparation's final key is unchanged. Exact sealed sanitized failures
authenticate their original input but supply no scientific or user-subject/target
authority; unrelated execution and restoration work without retrying failed source
hooks. J performs a real JSON roundtrip and new Session.restore_observations with
explicit proof authority, then actual activity execution. The JSON failure fixed
compatibility of empty error details None versus the existing serialized {} only.

Latest frozen resolver SHA256
7CEAF74DAB637B43453E4A6EB0599E9CD2EBD6889B471B33C36F79F765A385AC;
dynamic test80644F2846CD51B14075AD0E3375E69CDC1152C085C4B8F5407F14E8976B7E22.
Wrapper, target-status test, contracts and runner remain at their recorded hashes.
Corrected implementation SPEC and fresh QUALITY remain required before commit.

James's corrected implementation SPEC APPROVES the final four-file freeze above.
This is source review, not an independent test run. No remaining actionable SPEC
finding; no Task4/5/6 approval is implied. Fresh QUALITY reviewer Kierkegaard now
owns the sole heavy test slot for U plus test_target_tool_contract.py,
test_decision_adapter_retry.py and test_decision_loop.py in one serial invocation.
Parent in-memory compilation of the four final files passed with no application
imports or bytecode writes, and git diff --check passed. No commit/publication
until the fresh review and terminal test evidence are returned.

### Task3 release and independent cumulative verification

Fresh QUALITY reviewer Kierkegaard APPROVES the final four-file freeze after
independent source inspection and this exact serial command:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_dynamic_bindings.py tests/agent/test_decision_target_status.py tests/agent/test_decision_binding_requirements.py tests/agent/test_decision_binding_session.py tests/agent/test_current_source_tool_hooks.py tests/agent/test_decision_inputs.py tests/agent/test_rag_receipt_consumption.py tests/agent/test_reverse_receipt_consumption.py tests/agent/test_target_tool_contract.py tests/agent/test_decision_adapter_retry.py tests/agent/test_decision_loop.py
```

Result1201 passed85.21s, zero failed/errors/skips, three SWIG __module__ warnings
(SwigPyPacked, SwigPyObject, swigvarlink), runner/process exit0. Handle63640 was
polled terminal without restart, no live handles. Test output untruncated;
truncated source/document reads were followed by targeted ranges. Four final
file hashes and the isolated-runner hash matched before/after; scoped diff check
passed. Parent reconfirmed the same hashes, compiled all four files in memory
without application imports/bytecode, and retained the original checkout's13
status paths. A filename-only credential-pattern scan of the six task files
returned no matches; this is a limited static check, not a whole-history audit.

The target-status prerequisite was explicitly staged alone and committed locally
as5bf3344. Resolver, new dynamic tests and parent-owned evidence documents form
the following Task3 checkpoint. No push/PR/deployment/provider activation here.

Public integration contract: instantiate B1BindingResolver with actual dynamic
Session, prepared v2 requirements, original admitted context and real adapters;
resolve gives an immutable ResolvedBindingAction; register_action commits initial
record identity; prepare_observation supplies accepted proof/final key before
ledger capture; verify_binding_closure and find_reusable check source/authority;
record_descriptors exposes original-row handles; export_records/restore_records
use bounded original server records plus actual restored Session seals/ledger.
Task4 must call scientific resolution/source checks through owned settlement and
root deadlines; Task5 must preserve full sealed quality and enforce whole512KiB
snapshot/12-action budgets. Explicit admission of pure human clarification turns
without prior actions still needs a reviewed Task4/5 interface. Task3 does not
read model history or arbitrary session.input_queries as authority. Full P7/P8
and actual seven-tool loop/normal-Web/live end states remain unfulfilled.
