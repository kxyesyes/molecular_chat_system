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

### Task4B implementation checkpoint — independent review pending

At parent HEAD ce47c82, the eight-file uncommitted Task4B implementation wires
explicit B1 decisions into the existing loop and Session. This is not a release:
independent SPEC and fresh QUALITY are still required. Default legacy behavior
remains covered; B continuation is explicitly rejected until Task5 revision8.
Task6 final-publication barriers, normal Web/B2/C and P8 remain incomplete.

Implementer final serial command (no live models or host assets):

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_loop.py tests/agent/test_decision_binding_inputs.py tests/agent/test_decision_binding_acceptance.py tests/agent/test_decision_dynamic_bindings.py tests/agent/test_decision_binding_requirements.py tests/agent/test_decision_binding_session.py tests/agent/test_current_source_tool_hooks.py tests/agent/test_decision_loop.py tests/agent/test_decision_continuation.py tests/agent/test_decision_requirements.py tests/agent/test_decision_inputs.py tests/agent/test_decision_adapter_retry.py tests/agent/test_worker_ownership.py
```

Result:1105 passed, zero failed/errors/skipped, three SWIG deprecation warnings,
259.34s, handle68582 terminal exit0. Wrapper SHA256 before/after:
F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183.
This is implementer evidence, not independent review or live scientific acceptance.

The implementer retained these RED runs; hexadecimal IDs are command chunks,
numeric IDs are terminal process handles. Intermediate timings were not supplied
in the handoff and are not inferred. The final union covers the corrected cases.

| Run | Original result | Reproduced issue and correction |
|---|---|---|
| 379b50 | 5 failed | Three legacy unauthorized tools and two missing B constructor cases; explicit profile added, legacy denials retained. |
| 77c4b5 | 10 failed,63 passed | Session guard contracts and checkpoint-denial journaling missing. |
| 67139 | 5 failed,23 passed,4 errors | Observation-name/metadata gaps; oversized escaped fixture and overly long parameter IDs corrected. |
| 79017 | 7 failed,27 passed | Legacy history query ceiling, incomplete metadata comparison and missing observation name. |
| 39145 | 2 failed,28 passed | Activity fixture lacked target evidence; replaced with existing labelled synthetic family fixture, not weaker acceptance. |
| 49406 | 2 failed,28 passed | Consumed expired/unavailable selection raised outside protected graph handling. |
| 81220 | 3 failed,10 passed | Native admission errors, invalid-owner cleanup and late admission freeze. |
| 7a8c57 | 1 failed,7 passed | Legacy continuation helpers did not explicitly reject B. |
| 86469 | 6 failed,2 passed | Unexpected guard return not latched, extra legacy clock call and lost preparation deadline reason. |
| 83f05b | 1 failed | Pydantic normalized non-native proposal arguments before native bounds. |

Frozen eight-file SHA256 values supplied for independent comparison:

| Repository-relative path | SHA256 |
|---|---|
| src/agent/harness/decision_loop.py | E023E8C403D5AD3D3086A3EA91217F79F7DD46D66485A8251009383BE5CA81DE |
| src/agent/harness/decision_policy.py | 939FEF5C3F7EFD797EC7C52F16D56C2AE63347E839BFF8D3BEA51D41A82B638B |
| src/agent/harness/decision_bindings.py | D9490F70E8389163668852E2A65F59FE57A42872B84E94D88D96C8EF0A3C9A15 |
| src/agent/harness/decision_continuation.py | CA2506DFC83D8294A51255907C34A9D525B826B60A9D8AED50C3745171B1DC8C |
| src/agent/runtime/run_session.py | 7324C9BEB5240F0AB75A866705642F5671C18FA8E55B8B21907A804535DBA734 |
| tests/agent/test_decision_binding_loop.py | 66EA586D45331D7334778437EA4125DBEB5CB109BBA8A5A4FCA7F44FAC733197 |
| tests/agent/test_decision_binding_inputs.py | 1F8092007DD9C2132435D47ABB507865F32799784835A1C537726E238A031D30 |
| tests/agent/test_decision_binding_session.py | E78E88C41BF9D8E0C274A60EB178ABC1AA83F2454566FF7CC75A0659DBEF1CFD |

### Planned ownership

Independent SPEC Hilbert requested one correction to this freeze: a valid
16KiB retrieval query made entirely of `<` passes B input bounds, but the
requirements prompt uses the HTML-safe scientific-observation serializer and
expands beyond its64KiB limit. Prompt construction is outside graph handling.
This was initially source-only evidence, not an executed failure. Parent
confirmed the policy/loop call chain and assigned native/JSON actual-loop RED
tests and a B-only prompt-serialization correction, without changing legacy
serialization or enlarging scientific-observation limits. Release remains open.

P2 correction evidence: actual-RAG RED handle90809 returned2 failed/4 passed,
both modes raising observation_too_large. Boundary RED chunke55efe returned
7 failed/1 passed before the explicit prompt profile parameter existed. Focused
GREEN handle62248 returned14 passed54.73s. The unchanged13-module command above
then returned1115 passed284.83s,0 failed/errors/skipped,3 SWIG warnings,
handle71234 terminal exit0. Each process ended before starting the next.
The tests preserve exact admitted-query roundtrip and validate actual messages
using the existing transport snapshot validator. Additional cases retain native
size/type/cycle checks before redaction, repeated alias values, unknown-profile
rejection and unchanged legacy/scientific-observation serialization limits.

Corrected SPEC Hilbert APPROVES source and test coverage, without executing
tests. Fresh QUALITY Linnaeus owns the next independent serial union; no release
is implied before that result. Only three hashes changed from the table above:

- decision_loop.py:D36A80B28C8F95F78787C971F6FDCBC91125CC3CB314AE3AFD4A525694A60DA8
- decision_policy.py:ADD42F61EB883554D846BAC84A26EE4D6297E17E7C5E2D7050C83CBEB6CC3B78
- test_decision_binding_loop.py:1721C3D2E6B72C844017E0D6959347D98E08D3652F4C41A646AE51DE018B3923

Task4B local release verification: fresh QUALITY Linnaeus APPROVES the corrected
eight-file freeze after independent inspection and exactly one invocation of
the13-module command above. Result1115 passed303.10s,0 failed/errors/skipped,
3 SWIG __module__ warnings, handle10417 terminal exit0 and
ORDINARY_PYTEST_EXIT=0. All eight hashes and wrapper hash matched before review,
immediately before testing and after testing. No additional probe/import/live
calls were used. Parent in-memory compilation of all eight Python files and
git diff --check passed; original checkout retains its13 unrelated status paths.
This closes Task4B only. Task5 revision8, Task6 final publication and the later
normal Web/B2/C/focused-PR/P8 gates remain open; production is not activated.

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

### Task4A-acceptance first worker checkpoint (not released)

Worker Nietzsche added only decision_binding_acceptance.py and its focused test
module. These files are not yet committed or independently approved. The first
source hash was675DF636633CC69F90E1D74DA7B848E9F3EFA27FAEEBE6C2BE056FEA484C9C5D;
the test hash was9EE2DD541AAAA2A7B13A4FA72EE1E6E4AB9458A2D4CC39B05C783B44B8D5D553.

All runs below used MedChat Python with `-I -S -B
scratch/ordinary_chat_offline_runner.py`; wrapper SHA256 remained
F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183.
M means tests/agent/test_decision_binding_acceptance.py.

| Run | Observed result | Terminal handle |
|---|---|---|
| M::test_independent_rag_citation_does_not_complete_successful_property |1 failed4.72s, missing API after both actual tools succeeded|immediate|
| M initial RED |56 failed24.40s:54 missing API,2 new target-not-found fixture mistakes (`[]` vs actual None)|8730|
| M::test_target_not_found_execution_only corrected fixture |2 failed4.07s, missing API|immediate|
| M initial implementation |55 passed1 failed16.59s, family fixture used legacy rather than canonical envelope|90529|
| M::test_family_disagreement_cited_raw_data_is_partial_never_success corrected canonical fixture |1 passed3.52s|immediate|
| M expanded |71 passed1 failed20.34s, an earlier not_found incorrectly blocked a later qualifying resolved lookup|40811|
| M corrected |72 passed19.60s|56367|
| Ten-module union below |1908 passed122.41s|31960|

Every run reported3 SWIG warnings, no skips; failures exit1 and passes exit0.
All handles reached terminal without restart. Union targets, under tests/agent:
test_decision_binding_acceptance.py, test_decision_binding_inputs.py,
test_decision_dynamic_bindings.py, test_decision_binding_requirements.py,
test_decision_binding_session.py, test_analysis_contract.py,
test_activity_tool_contract.py, test_target_tool_contract.py,
test_rag_receipt_consumption.py, test_reverse_receipt_consumption.py.

Before SPEC review the worker explicitly raised the legacy single-model target
proof gap: model_provenance.target_id is currently only an opaque extra field.
Parent selected fail-closed insufficient_target_evidence even when that extra
is supplied, pending a formal target-proof contract; no expansion of legacy
producer schemas is authorized here. Validated family model targets remain
supported. The worker is adding RED/GREEN for this and checking preservation of
safe provenance/artifacts/warnings in deterministic rendering. The1908 pass
count precedes those changes and is not final acceptance or independent review.
Actual seven-tool model decisions, revision8, publication, normal Web/B2/C and
P8 real/UI repeat gates remain required. No real API/model/service was used.

Worker follow-up freeze (independent SPEC pending): source now
203F3341A3F080C4F612B2D724EB5D1C63CF6FF0AEB9A64F70173DDB8F83EB9D,
test ADDD49DD35EE7675C19F83B7D28B11E5F6D1FD50F263FD0AE2B6BDC86B6ADFB1.
Existing load-free model_registry.validate_endpoint_metadata supplies a full
endpoint-ready target contract; parent approved reusing it without assets or
schema expansion. Opaque target_id/readiness markers alone still fail closed.
The renderer now projects scoped provenance/artifacts and safe warnings.

Follow-up RED/GREEN with the same wrapper, each3 SWIG warnings/no skips:

- Five new node groups (legacy_extra_target_id, complete_endpoint_target_contract,
  cited_typed_provenance_artifacts_and_warnings, safe_uncited_failure_warnings,
  cited_family_review; exact names in M):15 failed8.93s, handle29387, exit1.
- M:87 passed37.68s, handle13970, exit0.
- M::test_single_model_untyped_family_extras_do_not_become_display_diagnostics
  and M::test_optional_untyped_family_source_metadata_never_copies_debug_containers:
  2 failed6.09s, immediate chunk50b75c, exit1. An opaque row error caused an
  AttributeError; optional nested metadata leaked into display. Neither was
  accepted as a successful scientific result; projection was corrected.
- M:89 passed23.44s, handle59528, exit0.
- Same ten-module union:1925 passed130.29s, handle39628, exit0.

All handles are terminal, no restarts. Source is frozen for independent Peirce
SPEC review, followed by a fresh independent QUALITY review/repeat if approved.
These counts do not satisfy Task4B, recovery/publication or final live gates.

Independent Peirce SPEC returned one concrete blocker at this freeze: renderer
unconditionally reads family provenance.models for every row. A valid review
batch can include a disagreement plus an unsupported-structure failed sibling
with provenance={}; the existing predictor and family_review_observation permit
that diagnostic shape. Source inspection confirms it, but the review itself ran
no tests. Nietzsche is reproducing the exact canonical mixed batch before a
minimal projection correction; final SPEC and fresh QUALITY remain open.

SPEC correction: the exact focused node
M::test_cited_family_conflict_with_unsupported_sibling_preserves_empty_provenance
failed with KeyError('models') in4.18s (chunk09b1cc, exit1). Minimal conditional
models projection preserves the authentic empty provenance; no synthetic model
evidence is filled in. M then90 passed22.48s (handle41583), and the unchanged
ten-module union1926 passed127.38s (handle27865), exit0,3 SWIG warnings/no skips.
The fixture traverses actual family canonicalization/service/tool/Session with
explicitly synthetic stages, not post-seal mutation. Peirce re-reviewed and
APPROVED this two-file SPEC slice; source-only, no independent test execution.

Corrected source hash E95A6B53AC99364F3EBC8B1C644AFA482A1B192F548AC91CD7E04D516AD09B84;
test hash7973D217A8E59AE4B5F1B53AB872A81ADC6E33D013324A4A71376882E236E5A3.
Fresh Laplace QUALITY owns the only test slot for independent repeat of the ten
modules plus test_family_activity_tool.py, test_decision_requirements.py,
test_decision_loop.py and test_decision_continuation.py. Release remains pending.

Final local release: Laplace independently APPROVED source quality and repeated
exactly those14 modules once:2224 passed161.87s,0 failed/errors/skipped,3 SWIG
deprecation warnings, ORDINARY_PYTEST_EXIT=0 and process exit0. Handle49059 reached
terminal chunkfe3e85 without restart. No independent failures; earlier REDs above
are retained. Corrected source/test and wrapper hashes matched before and after.
This closes Task4A-acceptance, not Task4B/5/6 or P7/P8. Parent two-file in-memory
compile and diff checks passed on the same frozen hashes; no real API,
production assets, deployment or push was part of this release.

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

- [x] 4A-input RED: original missing both inputs, admit SMILES, then target,
  without executing a tool between replies. Resolve correct molecule/target;
  model/session history alone still cannot supply them. Verify native turn index,
  owner/fixed metadata, exact retry vs rewrite, gaps, prefix digest, detached
  projections,16-turn/16KiB-query/64KiB-context/512KiB-total boundaries.
- [x] Implement BindingInputJournal original freeze, explicit admit_context,
  bounded prefix export/restore and context projection. Restore requires explicit
  caller validation; do not infer authority from a raw dict or checksum. Actual
  revision8 claim integration remains Task5 and must validate every saved turn.
- [x] Extend resolver only with explicit optional journal injection. Normal B
  loop will require it; direct Task3 API retains its current behavior. Commit each
  action's input_turn/prefix digest in the full original record, not logical
  action hash. Resolve historical actions from their own prefixes and current
  reuse from current constraints. Preserve malformed-input rejection, original
  requirements, target changes/new subjects, and selected-reference identity.
- [x] Cover sequential-prefix negatives: valid -> malformed -> omitted input,
  conflict -> continue, omitted reference retention, equal-SMILES foreign
  reference and sticky explicit-SMILES selection clearing. No later reply erases
  a prior conflict; unknown activity families do not block property parsing.
- [x] Cover all frozen AgentContext fields and journal-head mismatch at resolve,
  prepare, reuse and closure. Restore trial resolver must retain explicit journal
  authority; historical actions use their own prefix, not the latest context.
- [x] Keep proposal input_turn one-based and record/journal turn zero-based;
  test 0/1/2 journal against 1/2/3 proposals and unchanged earlier reuse records.
  Test B UTF-8 content boundary plus unchanged legacy JSON limit and64KiB context.
- [x] Run new input module plus Task3/requirements/Session/receipt regression;
  independent SPEC then fresh QUALITY before the 4A-input checkpoint.
- [x] 4A-acceptance RED/GREEN: implement the existing seven-tool/group checks and
  citation closure specified below in the focused acceptance module. Use actual
  sealed Session observations, not dictionaries standing in for evidence. Domain
  failures/demo/fallback/family disagreement remain unusable or partial/review;
  no-hit differs from requested hits. Required tools include all explicit groups.

Source-only acceptance audit at9360c7b (Parfit, no execution) pins the following
reuse points for that implementation; these are not passing test results:
`decision_requirements._check_batch` for properties/likeness; typed
`analysis_contract.ADMETOutput` plus `ADMETPredictor._has_observations` for
available methods (an empty sparse adme_py payload is typed-valid but does not
meet acceptance); `ActivityPredictOutput` and `ActivityResultValidator` for
whole-batch activity; resolver current-source closure plus receipt validators
for reverse/RAG; `TargetSearchOutput` and quality.lookup_status for target lookup.
`usable` alone is insufficient; family_review_observation never supplies success.
Do not mutate sealed results with ResultValidator.validate_tool_result or reuse
the legacy active_results-based evaluate_requirements/verify_finish wholesale.
Required RED vectors: independent property success omitted by RAG-only citation;
downstream target citation including reverse/property ancestors; verified empty
RAG with require_hits false/true; empty sparse ADMET; family disagreement;
not_found target with require_resolved false/true. Required tools without an
explicit structured group still undergo the corresponding tool-specific checks.

- [x] 4B actual loop: implement the catalog and execution tests below. Readonly
  source evidence shows legacy preparation only includes molecular groups and
  legacy context limits; do not reuse those paths accidentally for explicit B.
  Until revision8 is implemented, B must not mint or claim a revision6/7 nonce.
  Task4 alone cannot be published as complete B1 or enabled in normal Web; Task5/6
  and full integration gates remain required in this same core effort.
- [x] Preserve the actual incidental-selection integration gate: existing
  WorkflowRunSession._reject_unavailable_reference and initial loop checks apply
  to every tool, even pure RAG/user-target lookup. Task4A resolver acceptance does
  not remove that dispatch restriction. Test an expired, scientifically irrelevant
  raw selection through actual B loop/Session without a user clearing it first;
  apply reference checks to the authority actually consumed by each B action,
  with legacy defaults and molecular/ancestor freshness protections unchanged.
  A separate cleared-selection follow-up test is not proof of this original case.

  Implementation detail is pinned in the spec's Task4B consumed-reference
  dispatch seam (independent source review Sartre,2026-09-26). Use the optional
  trusted dynamic Session guard with unchanged legacy default; validate the
  Session-owned step, registered full action/input/metadata and admitted head.
  Journal denial before settlement re-entry (including provisional checkpoint
  replacement), latch the first safe guard error across adapter normalization,
  and distinguish zero physical calls from conservatively consumed attempts.
  Required new regressions include checkpoint-success -> guard-denial ->
  settlement re-entry, Session-copy tampering, and descendant guard failure
  followed by an otherwise valid source check. These now pass in the reviewed
  Task4B union recorded above; this does not complete the later publication gate.

Source inspection after Task1: context_value still enforces the legacy16KiB
JSON-encoded query limit, and the loop calls it before requirements preparation.
Task1's UTF-8 retrieval-content regression alone therefore does not establish the
same behavior through B's actual loop. Add an actual B-loop quoted/backslash and
non-ASCII near-limit retrieval case before wiring. Preserve legacy context bounds
and the whole64KiB context boundary; only the explicit B path may use the approved
16KiB UTF-8 query-content limit. This is an integration test requirement, not a
claim that the existing loop has already been repaired or exercised.

- [x] Baseline actual-loop test must demonstrate that registered RAG/ADMET/reverse
  are currently unauthorized, not merely that a new constructor keyword is absent.
  Keep a failing new opt-in test and a passing unchanged legacy negative.
- [x] Add server constructor binding_profile; None retains old behavior, exact B1
  selects the reviewed implementation, B2/unknown reject. B requires a WorkerOwner
  and explicit v2 requirements. Catalog intersects B1, allowed/required permissions,
  actual registry ownership, idempotence and side-effect policy. Scientific and
  RAG flags are independent strict bools. Chat has no tools even if both flags true.
- [x] Use existing decide/tool graph. The model chooses every next tool from actual
  observations. Add B-only resolver, action hash index, source preparation and
  acceptance branches; no ordered list of tools or keyword workflow templates.
  Every post-callback boundary before model/tool/answer validates source closure.
- [x] Require actual temporary-source RAG and reverse, actual RDKit properties/
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

- [x] Acceptance reuses existing molecular metric checks and domain validators.
  Check exact input subjects/count, ADMET method availability vs unknown, real
  activity provenance/status (family disagreement stays partial), reverse proof,
  RAG valid no-hit vs discarded/unavailable, target status/evidence and required
  tools. Use no LLM self-evaluation. Final scientific text is deterministic tool
  evidence with visible limitations; never hide failed optional steps.
- [x] Add mandatory citation closure: expand each explicitly cited sealed
  observation's ancestors in observation order; for every obligation/required
  tool select the earliest satisfying observation within that closure. Return
  cited_closure_ids and required_evidence_ids. A fulfilled-but-uncited group gives
  missing_required_citation and cannot complete. Add unrelated-only citation
  negative and downstream-only citation positive with ancestor expansion. Render
  successful scientific prose only from that verified closure; uncited failure
  diagnostics remain visible without adding ungrounded numeric claims.

### Task4A input implementation review checkpoint (not released)

Written clarification approved by Parfit and committed9360c7b. First implementation
RED:47 failed/4 setup errors8.47s, corrected missing-implementation RED49 failed
10.84s; expanded RED80 passed/2 failed14.29s found empty-closure validation and
an over-narrow test exception assertion. Subsequent focused96 passed23.77s and
eight-module union952 passed127.88s (terminal94145/exit0,3 SWIG warnings).
These runs used the isolated runner below, not real services or model assets.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_inputs.py
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_inputs.py tests/agent/test_decision_dynamic_bindings.py tests/agent/test_decision_binding_requirements.py tests/agent/test_decision_binding_session.py tests/agent/test_current_source_tool_hooks.py tests/agent/test_decision_inputs.py tests/agent/test_rag_receipt_consumption.py tests/agent/test_reverse_receipt_consumption.py
```

Independent SPEC Pasteur withheld approval: unconditional scientific prefix
reduction incorrectly gates pure RAG comparison text and irrelevant selected
molecules on RAG/user-target lookup. Reproduce and scope reduction to applicable
molecular actions/obligations/ancestors, retaining universal journal-head/owner
checks and scientific conflict retention. This is original-query compatibility,
not new multi-target inference. The952-pass run does not close that finding.
Parent in-memory compilation/diff check passed and original13-path dirty checkout
remains untouched; no code commit, publication or full Task4 completion yet.

Corrective scope RED10 failed/31 passed26.12s; expanded RED18 failed/31 passed
62.70s; first implementation run4 failed/140 passed142.74s. The four failures
were actual Session dispatch rejection for expired/unavailable raw selections
on RAG/user-target lookup after successful resolver admission. This existing
Session gate is not fixed in4A. Intermediate145 passed102.16s used a separately
cleared selection and did NOT establish the original same-context success.
Final tests remove that ambiguity: resolver admission is tested separately from
valid initial execution/post-execution expiry, and four named Task4B gap tests
preserve actual unchanged rejection without xfail/skip. Task4B must replace those
gap assertions with the required same-context successful execution tests.

Revised focused20 passed9.46s; final eight-module command above1005 passed89.94s,
exit0,3 SWIG warnings, no skips (149 input tests plus856 unchanged baseline).
Terminal handle60163/chunk99486c. Corrected SPEC Pasteur approves the four-file
helper checkpoint; independent QUALITY/expanded regression is still pending.
Final implementation SHA256 values at this checkpoint:

```text
decision_binding_inputs.py D4D3EA40F0A6C5B02859704D3350B02E271645BFD90B848E2B0CC07562B7C5FF
decision_bindings.py C96CD40064B87CD3647932A833D1941182FCBCE10A134BFA2AFE2DFAD815C296
decision_bounds.py 2B4BAC3EA35187C887312EA4A37EF3270A41A08FCB5246922EB3A92269BB142C
test_decision_binding_inputs.py 258CA39A3B949B5B4E2A532DA2BEB4B6EF22DAA8815A9A7F7DA3FE5A58EA6467
```

Fresh QUALITY Dalton repeated the eight-module command plus
test_decision_target_status.py, test_target_tool_contract.py,
test_decision_adapter_retry.py, test_decision_loop.py and
test_decision_continuation.py (all under tests/agent):1373 passed185.63s,
exit0,3 SWIG warnings,0 skips, terminal handle85612. Four hashes above match
before/after. Approval withheld for a source-derived grammar regression:
`predict activity for CCO` then `target: PDE5A` freezes CCO as a target via
the generic target parser, unlike the existing activity parser's valid-molecule
exception in the for position. Reproduce and repair without weakening explicit
target labels or unknown/conflicting-target checks. The1373 pass run is retained
as regression evidence, not proof this finding is closed.

The grammar reproduction used the same isolated runner with three node IDs:
test_decision_binding_inputs.py::test_for_molecule_no_action_then_target_and_sealed_activity,
::test_for_molecule_target_grammar_rejections_persist and
::test_for_molecule_property_target_is_not_activity_family_validation (all under
tests/agent; repeat the full module filename for each node argument). RED5 failed/
8 passed32.59s; initial fix1 failed/12 passed5.69s exposed `for CCO invalid`
fragment acceptance. That negative remains and now passes with whole-field parsing.
Focused13 passed6.17s; module162 passed33.57s; eight-module union1018 passed89.34s,
exit0,3 SWIG warnings,0 skips, terminal54639/chunk0e3730.
Only the target-analysis projection masks whole valid molecular `for` occurrences,
using existing activity positional grammar; original input/commitments remain
unchanged. Explicit target labels, unknown identifiers, malformed structures,
conflicts and non-activity property context retain their checks. New resolver
hash BD0DF638874D56EAB15DC4BBCED764AC55F8EB63D32D145883A1EDB1F7BB42C2;
input-test hash46593180D244A2C5448CFBC48447112B2AAB0AA1C40B29C68F39998C50F5BB04.
Input helper/bounds hashes unchanged. Final independent rereviews remain pending.

Final review supersedes that pending status: SPEC Pasteur and QUALITY Dalton
approve the final four-file freeze. Independent13-module repeat1386 passed157.41s,
exit0,0 failures/errors/skips,3 SWIG warnings; terminal53969; hashes unchanged
before/after. Command is the eight-module union above with the five additional
test modules named in the1373-run paragraph. Parent in-memory compilation of
all four changed Python files and diff checks pass. Filename-only credential
pattern scan over this code/test/plan slice had no matches (limited pattern scan,
not a repository security audit). Original checkout still has its same13 paths.
Task4A-input is locally implemented/reviewed; Task4A-acceptance and Task4B remain
unchecked. No real API/model, remote publication, production activation or
deployment was performed. The four Task4B gap tests are characterization of
existing rejection, not satisfaction of the original successful-dispatch goal.

## Task 5: Revision8 restore and charged verification time

Source-grounded pre-claim staging choice is documented in the spec's Task5
pre-claim replay isolation section. Legacy SimpleNamespace replay cannot be
passed to the exact-type B resolver, while calling start/restore on the live
Session before CAS would publish events and confer lifecycle authority early.
Use an unstarted private Session as a bounded in-memory evidence projection,
without toggling its lifecycle flags or calling execution/persistence methods.
Preserve proofless sanitized preparation diagnostics. After confirmed CAS,
actual restore requires fresh write-once live seals before record restoration;
restore_observations does not invoke the capture hook. Independent source review
requested those last two clarifications; they are now explicit, not implemented.
Beauvoir independently re-reviewed and approved the corrected written choice;
this is source-only evidence. Task4B implementation proceeds first.

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
