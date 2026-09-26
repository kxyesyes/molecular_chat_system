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

- [ ] Add failing parser tests for version2/profile-required, strict native types,
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

- [ ] Run `tests/agent/test_decision_binding_requirements.py` and record RED.
- [ ] Implement the exact design's frozen/strict/extra-forbid types, use existing
  MolecularRequirement unchanged. Parser `parse_binding_requirements(value)`
  returns BindingRequirements; absent value rejects. Validate native dict before
  JSON serialization; only exact known constructed models may be projected with
  primitive fields preserved and then revalidated. No bytes/tuple/Enum coercion
  from raw JSON callers. Persisted tuples become arrays via validated model dump.
- [ ] Add `prepare_binding_requirements(value, *, context, request_kind,
  allowed_tools, required_tools)` in the resolver: whole-SMILES validation and
  canonical-equivalent duplicate rejection occur here, never import-time.
  Retrieval query equals original context query; target query must equal original
  query or a field extracted by the existing user-input target parser. Compare
  activity target to original user target, not the model proposal. Inconsistent
  obligations fail before a model, tool or trace creation.
- [ ] Re-run new module plus existing `test_decision_requirements.py` and
  `test_decision_binding_arguments.py`; preserve all old tests unchanged.

## Task 2: Owned-call settlement and pre-ledger proof hook

- [ ] Add real Session tests with a prepare callback that observes domain-validated
  results, modifies the copied step metadata, and deliberately raises or encounters
  a persistence retry. Count tool calls and callback calls. Assert no provisional
  scientific data is registered or persisted on preparation failure.
- [ ] Extract `settle_owned_call(callable, *, worker_owner=None)` from existing
  settle_action, preserving Task creation failure, repeated cancellation, root
  abort_unstarted/settle and worker.result semantics. `settle_action` retains its
  existing execute_step journal retry inside the callable and delegates settlement.
- [ ] Add optional `observation_prepare` to Session constructor, allowed only for
  dynamic sessions and a callable. Invoke after domain validation/alignment but
  before final provenance/output digest and ledger registration. Pass the actual
  copied WorkflowStep, not the append_step caller's object. The hook returns no
  replacement result; it mutates the accepted result/step under server authority.
  Convert failure into a safe invalid-output result with no scientific data,
  artifacts or source evidence; never store the unsafe intermediate.
- [ ] Conditional proof propagation adds binding_proof to ledger input_binding and
  the corresponding integrity comparison only when present. Preserve the three
  legacy fields and old evidence hashes when absent. The existing capture hook
  remains once-after-ledger; normalized/captured journal flags prevent tool replay
  and resealing on commit-then-error.
- [ ] Run targeted new Session cases then `test_decision_loop.py`,
  `test_decision_inputs.py`, `test_decision_adapter_retry.py` and existing receipt
  consumer Session cases. Record each result, not only the final green run.

## Task 3: Inputs, action identity and live closure

- [ ] Add negative role/identity tests before resolver code: unknown/foreign IDs,
  wrong producer, wrong whole subject set, duplicate/forward/cyclic parents,
  modified selected reverse row, sealed nested tamper and valid-equal SMILES from
  a different source. Reject with zero downstream tool calls.
- [ ] Implement pure binding resolution over current session observations and
  explicit requirements. User input is whole parsed current input or authorized
  selected structure; reverse enforces exactly one. Upstream molecules preserve
  the full batch, activity target stays in the structured query wrapper. Use
  existing BindingResolver rendering/whole parsing instead of regex fragments.
- [ ] Issue deterministic reverse record handles beside encoded observations.
  Hash the original raw row, its position and observation evidence ID; lookup
  reconstructs all values. Project only source-backed TargetSearchInput fields,
  never normalized name hashes/CHEMBL molecule IDs as gene identifiers. Empty or
  contradictory fields fail; a protein name is only a lookup hypothesis.
- [ ] Resolve logical action hash from the design's closed payload and adapter
  version. Pre-ledger preparation validates attached source through the new hooks,
  records exact own_source and finalizes operation_key. Failed source has null
  own_source and cannot satisfy requirements. Strip tool-authored reserved fields.
- [ ] `verify_binding_closure` reconstructs and checks all prior ancestors,
  whole-observation seals, ledger proof, original obligation hash and current
  source. Caller executes this via owned-call settlement with the same deadline
  checked before/after. Reuse searches logical action identity then checks final
  operation key/source; stale evidence is not rerun or relabelled.

## Task 4: Actual seven-tool dynamic execution and acceptance

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
