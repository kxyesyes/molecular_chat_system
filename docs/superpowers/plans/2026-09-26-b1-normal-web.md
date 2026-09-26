# B1 Normal Web Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans after parent releases implementation. Execute task-by-task; this document is not release authority.

**Goal:** Make the normal protected `/ws` use the existing seven-tool B1 decision loop with truthful science, semantic ordinary chat and authenticated same-socket continuation.

**Architecture:** Compose existing async app assembly, semantic admission carry, shared ActiveSegment, one model reader/WorkerOwner, B8 journal/source replay and post-drain publication. Keep v6/v7 and all startup defaults unchanged; no second engine or static workflow.

**Tech Stack:** FastAPI/ASGI WebSocket, native JavaScript, actual OpenAICompatibleModel with synthetic HTTP transport, Pydantic2, SQLite, RDKit and temporary actual FAISS/RAG sources.

## Authority, source and execution rules

Written design and plan passed corrected independent Euclid review after three
P2 amendments. Parent accepts them under delegated recommended choices. Task1
is released for preparation and actual behavioral RED only; later tasks require
their own review checkpoint. No production activation, source-wide edits or
relaxed acceptance is authorized. The original drafting restrictions below
describe the completed document-preparation phase, not a blanket code release.

Read [design](../specs/2026-09-26-b1-normal-web-design.md), `AGENTS.md`,
`docs/PROJECT_STANDARDS.md`, the 2026-09-25 dynamic bindings design and
2026-09-26 core plan Task5 latest freeze/Task7. Pin
reviewed source `a5189a5f854050e59c6fc8ed4f8a397313c09067`; current HEAD
`f3e5f0ae3562e5b82ba126d3edf66d6be235153d` differs only in parent ledger text,
verified by local diff. The reported independent2078/592.33s
is prior core evidence only. No new tests/imports/compilation/CI/network are run
by this drafting task. Only the two new documents are writable now, via apply_patch;
no staging/commit/PR. Parent handles PR84 RAG-focused CI/class diagnostics
separately; pending investigation is neither an assumed fix nor a dependency
on hypothetical code.

The implementation steps below are future work, unchecked. Recommended choices
are delegated and selected in the design; do not ask the user to choose again.
Parent and an independent reviewer must approve the **written** design + plan,
including proposed seams, before any source/test edit. Then use TDD: add assertion,
run focused RED, identify the intended failure rather than a fixture/import error,
make minimum change, rerun GREEN and the named existing regressions. Never relax
an existing scientific, timeout, ownership or snapshot assertion to make B pass.

One heavy test process at a time, coordinated by parent. The existing approved
isolated runner is `scratch/ordinary_chat_offline_runner.py` (expected SHA256
`F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183`). Future commands
below use `python -I -S -B scratch/ordinary_chat_offline_runner.py ...` **only with
parent's approved MedChat interpreter**, not an arbitrary PATH interpreter. Verify
the launcher hash first; do not edit/recreate it or use raw app probe imports.
No live provider, host model/science assets or credentials in the offline phase.

## Exact file ownership

Production files are exactly design §3's eleven existing files plus new
`src/web/decision_binding_request.py` (12 total). No changes to auth/store schemas,
scientific producers, RAG service/core, model clients, Session, ledger, binding
resolver/requirements, lab entry, launcher or defaults. Any need outside that
allowlist returns a source-grounded amendment before implementation.

Future test files:

- Create `tests/agent/test_b1_normal_web_admission.py` — whole request/profile/permissions.
- Create `tests/agent/test_b1_normal_web_runtime.py` — actual `/ws` dataflow/chat/RAG.
- Create `tests/agent/test_b1_normal_web_continuation.py` — shared credit and B8 restore.
- Create `tests/agent/test_b1_normal_web_lifecycle.py` — assembly/publication/drain faults.
- Create `tests/agent/b1_normal_web_fixtures.py` — actual temporary source ownership only.
- Modify `tests/agent/test_web_decision_runtime.py` only to add opt-in construction
  injections to its existing `actual_app` fixture; keep all old defaults/assertions.
- Modify `tests/home_decision_runtime_test.js` additively for B pending events/count
  override and actual-frame input mode; use existing home code, not a copied handler.

Existing reference/ordinary/core tests are regression targets, not permission to
rewrite them. No new shared test framework or runner. Temporary generated test
data/frames belong under the test's temporary directory, never committed.

## Task 1 — establish the normal-route RED and fixture boundary

Files: new runtime test/fixture; narrow `actual_app` fixture additions only.

- [ ] Add this baseline probe using existing helpers, before production changes.
  It intentionally exercises today's mounted normal route, not a B constructor
  that does not exist yet. Run native and JSON.

```python
@pytest.mark.parametrize('wire', ['native', 'json'])
def test_normal_entry_b1_retrieval_gap(actual_app, wire):
    async def scenario():
        async with actual_app(mode='decision_a2', wire=wire,
                              ordinary_policy='semantic_v1') as b:
            async with ActualSocket(b.app, await cookie_for(b.app)) as socket:
                await socket.ready()
                result = result_of(await socket.turn({
                    'message': '使用 RAG 检索本地分子库中与乙醇相关的分子',
                    'enable_tools': False, 'enable_rag': True, 'rag_count': 3}))
                assert result['metadata'].get('stop_reason') != 'unsupported_scientific_request'
    asyncio.run(scenario())
```

  Imports are existing `pytest`, `asyncio`, `ordinary_chat_fixtures.actual_app`,
  `ActualSocket`, `cookie_for`, `result_of`. Expected initial RED is the explicit
  unsupported-science assertion; missing dependency/timeout/fixture error is not
  acceptable RED. After B assembly exists, pass its explicit server profile and
  replace this weak gap assertion with receipt/dispatch/SQLite assertions in Task6;
  add an unchanged-original-profile rejection control. Do not loosen old profiles.
  The fixture is defined in test_web_decision_runtime and re-exported by
  ordinary_chat_fixtures; edit only the defining fixture, never a duplicate.
- [ ] Extend `actual_app`'s builder with test-only optional `binding_profile=None`,
  `tool_factory=None`, `rag_service=None`. Defaults must reproduce old fixtures.
  Wire inputs at existing external constructor boundaries, never replace runtime,
  admission, model.decide, Session, store/CAS or result creation. Add fixture
  rollback tests before injecting actual B resources.
- [ ] Build `b1_normal_web_fixtures.py` from the actual setup in
  `test_rag_receipt_consumption.initialized_service` and
  `test_reverse_target_invocation_receipts.make_writer_database`. Use synthetic
  temporary CSV/manifest/FAISS and reverse arrays; initialize outside ASGI loop.
  Do not use `test_current_source_tool_hooks.sources` unchanged: it executes once
  before yielding and would contaminate first-call counts/evidence.
- [ ] Use actual RAGSearchTool/ReverseTargetTool and actual RDKit tools. Inject only
  HTTP replies for main decisions/embedding/external lookup as needed. No Recorder,
  fabricated ToolResult/family row or scripted model object in the Web evidence
  path. Missing activity weights must return actual unavailable/partial; seven-tool
  dispatch coverage is not seven successful predictions. All source owners close
  on setup failure, assertion failure and cancellation; no fake transcript seeds.
- [ ] Run `python -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_normal_web_runtime.py::test_normal_entry_b1_retrieval_gap`.
  Record the actual RED and exact source hashes; no success claim from its failure.

## Task 2 — B-only whole-request admission, no execution authority from intent

### Task1 checkpoint and Task2 release (2026-09-26)

Task1 is complete as a RED/fixture checkpoint, not B functionality. Worker
original native/JSON mounted `/ws` probes failed2 at the exact unsupported-science
assertion before any intent/model request. Final new-file run retained those2
ordinary failing tests and passed13 fixture controls. Existing/new union passed
207 in111.07s. Meitner SOURCE/SPEC and fresh Ampere QUALITY found no actionable
issues. Ampere independently ran the complete new file:2 intended failures and13
passes7.74s, then the exact existing/fixture union207 passes111.49s (terminal73367).
Each final run reports7 SWIG/FastAPI deprecation warnings. No skipped/xfail
conversion, fake scientific evidence or production edit occurred.

Frozen tests: runtimeA8305EDA63C8815F7206F9E3D123E750D82CD3ED41E047CECF1639A55769DCC8;
sourcesC6B6D0CC3FE3B7015787FE7019DB5E7636403429AA20D41BB098160385497BBE;
shared fixture889B4DE1EA751335B7E52A81E680F9DD8C89163CBF67A860BD57B695687ACEBE.
Approved runnerF2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183
matches before/after. Intermediate fixture wiring/registration/JSON role failures
were repaired only in tests and are not counted as behavioral RED.

Parent accepts this reviewed checkpoint and releases Task2 only, using the
existing written design and exact three-file allowlist below. Retain Task1's
two REDs until Task4/6 assembly; do not claim Task2 alone fixes the normal route.
Task3+ production work and default activation are not released. Test execution
still requires an explicit sole-slot grant after the reverse-source regression.

Files: `src/web/decision_request.py`, new `src/web/decision_binding_request.py`,
`tests/agent/test_b1_normal_web_admission.py`.

- [ ] Write profile-parametrized tests before adding optional profile arguments.
  Once the seam exists, require the following assertions; initial behavioral RED
  is unsupported B obligations/original-only capability rejection, not a mock
  returning a pre-authorized PreparedDecision.

| Probe name | Exact input / required assertion |
|---|---|
| `test_b1_available_method_admet` | `评估 ADMET；SMILES: CCO`: scientific, required/allowed exactly ADMET, v2 available_methods, expected CCO/count1 |
| `test_b1_reverse_target_linkage` | `反向寻靶并查询返回的一个靶点；SMILES: CCO`: reverse + target, target.input=reverse; no fixed action order |
| `test_b1_local_rag_permission` | Retrieval query from Task1, toolsFalse/RAGTrue: only rag_search, exact original query/k3; RAGFalse rejects with zero calls |
| `test_b1_rag_count_not_silently_reduced` | Omitted count→3; explicit3 accepted; explicit5/10 or prose “返回五个” blocks retrieval with supported-count notice; never normalize explicit5; same option must not block non-science chat |
| `test_b1_full_clause_coverage` | Supported analysis plus melting point, docking, generation/ranking, web literature, custom reverse threshold/top-K or highest-score selection: whole reject/clarify, never subset/chat |
| `test_b1_same_subject_obligations` | Property/likeness/ADMET/activity conjunction with authored PDE5A and CCO: whole shared batch; divergent clause subjects/counts reject |
| `test_b1_ordinary_empty_authority` | REAL-010/DIVERSE-015 and safe follow-up: semantic intent + empty v2/sets; no reference resolve/tool/RAG merely for preference |
| `test_b1_missing_input_keeps_original_obligation` | Activity without molecule/target, and reverse without molecule: required tools retained, no invented subjects; genuine later clarification possible |
| `test_b1_browser_cannot_select_profile` | profile/carry/budget/owner/requirements/history injected into JSON rejects before any model/store action |

- [ ] Implement only design §§3–4: full-consumption B request reducer, existing
  subject/target/reference parsers, explicit v2 groups and whole-risk guard.
  Preserve v1 branch byte-for-byte where possible. `_validated_envelope` must
  revalidate B omission/defaults/query bounds under the same server profile.
  Do not let a semantic scientific/retrieval proposal invent admitted obligations.
- [ ] Add exact16KiB UTF-8 content and one-byte-over query tests, plus escaped/
  Unicode queries whose JSON size exceeds16KiB but whose actual envelope fits24KiB.
  Preserve whole-context64KiB and historical-pair16KiB limits. In Task4 these must
  reach actual native/JSON intent HTTP unchanged; over-limit cases dispatch zero.
- [ ] Run `python -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_normal_web_admission.py tests/agent/test_ordinary_semantic_admission.py tests/agent/test_web_decision_admission.py tests/agent/test_decision_binding_requirements.py`.
  Expected future GREEN for all; retain all four A1 counterexamples from the spec.

## Task 3 — compose B carry/root budget and B8 before exposing Web assembly

### Task2 implementation checkpoint (2026-09-26; QUALITY pending)

Worker Kuhn implemented only the three authorized Task2 files. Initial behavioral
RED3 failed/1 passed1.04s established missing B admission; fixture parameter-ID
errors and a browser/server profile helper confusion were corrected separately,
not counted as behavioral RED. First union744 passed42.85s was followed by
additional whole-suffix/count/description regressions, not treated as final.

Independent Carson SOURCE/SPEC identified two P2s: same-family distinct authored
PDE5A/PDE4A targets collapsed to the first identity, and fully descriptive reverse
target questions remained blocked. Actual focused RED4 failed/6 passed1.63s;
bounded canonical-identity rejection and complete-description grammar passed10
in1.38s. Unknown/appended scientific clauses remain blocked, not reclassified chat.
Carson corrected SOURCE/SPEC approves; final worker new-file123 passed2.33s and
exact four-file union775 passed42.40s (session18868 terminal), normal exit0, no
reported warnings/skips. Frozen files:

- decision_request.py332C18250B45F73E36D1FE88472E571CA82A59160EFC850994F738F5CF56FA99
- decision_binding_request.pyB080B428F8DCF2F91253223FFE8B7995C401D68935B694BE02AF46FDB052CD9F
- test_b1_normal_web_admission.pyCE30444A69AC157DBE5A2801664EA79DB8E286CDF1E8138D13119187B83EC0F8

Task1 fixture/test and approved runner hashes remain unchanged; its two mounted
route REDs are retained. Fresh Carver QUALITY review/repeat is pending. Task3 is
NOT released by this checkpoint, nor is normal B Web execution or activation
claimed. Parent owns documentation and commits; original dirty tree is untouched.

Fresh Carver QUALITY now approves Task2 after an independent exact four-module
repeat775 passed41.71s, session97543 terminal, process/wrapper exit0, no reported
warnings/skips. All three frozen hashes, Task1 files and approved runner matched
before/after. Parent in-memory compile/diff checks pass. This is a reviewed local
admission checkpoint; Task1 route gaps and normal-Web assembly are still pending.

Parent accepts Task2 local checkpoint954e4f4 and releases Task3 only against the
already approved design and four-production-file/new-test allowlist below. Keep
Task1's two route REDs and all old snapshots/defaults intact. Preparation/source
edits may begin; local tests require a separate sole-slot grant after the active
RAG test-isolation regression/review. No Task4 assembly or production activation
is released. Parent owns documentation, commits and subsequent publication gates.

Files: `src/agent/contracts/ordinary_admission.py`,
`src/agent/harness/decision_loop.py`, `decision_continuation.py`,
`decision_binding_continuation.py`; new continuation tests.

- [ ] Add `test_b1_carry_reaches_actual_core` using real B snapshot/binding,
  AdmissionCarryIn/Exchange, actual WorkerOwner, SQLite and actual HTTP model.
  Current source must RED at `invalid_binding_admission`; separately demonstrate
  original-only `loop_admission` rejection. No bypass of either guard in fixtures.
- [ ] Add `test_b1_intent_repair_and_finish_share_root`: one real intent HTTP reply,
  malformed decision then valid repair/finish, max_model_requests3; counters are
  intent1 + decision2 = total3, no fourth HTTP. At limit2 stop before the repair;
  at limit1 semantic candidate dispatches no intent. Known chat can use one slot.
- [ ] Add `test_b1_preprocessing_uses_web_segment`: controlled clock advanced during
  refresh, queued reader, preparation/source hashing and post-proposal callback.
  A30s segment spent by preprocessing cannot dispatch under a new300s loop deadline.
- [ ] Implement closed profile/catalog/assessment triple in `loop_admission`;
  allow B carry only with matching constructor profile and empty concrete exchange.
  Web never supplies only one. Preserve explicitly trusted no-carry B8 and v6/v7.
  Normalize trusted intent turn metadata before journal freeze. Initial/rechecked
  fingerprints both include identical admission binding and B-semantic budget policy.
- [ ] Extend semantic B8 exact snapshot fields by `ordinary_admission`,
  `intent_requests`, `total_model_requests`; keep core-only8's key set unchanged.
  Reconcile sums against actual model_calls, original intent receipt and durable
  decision_loop metadata. Add ordinary historical display checks; no fake metadata
  can authenticate itself merely by recomputing checksum. TTL/credit before CAS
  and after claim, from the original segment start. Never rerun intent.
- [ ] Add `test_b1_exchange_released_after_drain`: real waiting snapshot with
  R100/F25;20s terminal tail gives saved75,30s tail fails. Observe exchange remains
  empty during callbacks/drain and on failure. Only successful outer run assigns
  WaitingCheckpoint with original binding digest, original intent count and total
  decision count; no post-check positive persistence. Failures expose no nonce.
- [ ] Run `python -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_normal_web_continuation.py tests/agent/test_ordinary_admission_budget.py tests/agent/test_ordinary_continuation.py tests/agent/test_decision_binding_continuation.py tests/agent/test_decision_binding_publication.py`.
  Do not open normal B entry until this composition passes source review.

### Task3 first union and late-source TTL review correction

Tesla's first frozen five-module union reached terminal13606:376 passed,
3 SWIG warnings340.25s, exit0, with all five hashes and approved launcher unchanged.
This is PRE-CORRECTION evidence, not final Task3 approval. Earlier parameter-ID,
checksum-fixture and callback-keyword mistakes are not behavioral RED evidence.

Independent Hooke SOURCE/SPEC found a P2 after that freeze: a resume can pass
post-CAS validation while later live source restoration consumes its TTL, with
remaining active credit still allowing a model dispatch. Full-B section11.3
requires TTL again before first dispatch. Actual RAG/SQLite native/JSON controls
reproduced two failures in8.06s: CAS at400, TTL401, live source completion402,
active deadline475, and four model HTTP calls instead of the expected two.

Bounded correction: the existing owned-call guard invokes existing check_deadline
before/after callbacks while keeping the separate terminal-tail cap. No new
engine, snapshot write, Session change or production activation. The late-source
regression asserts no extra model/tool/embedding dispatch, settled owner, no
checkpoint/nonce, and truthful failed state with the claim still consumed.
Corrected focus plus adjacent controls passed20/3 warnings24.99s, terminal30495.
Updated hashes:

- ordinary_admission.py0068B42F9377465EAD4AB2261F9D335431E27DA181EF64659B38479EE358A88B
- decision_loop.pyCEB3E0011E3C36A2A78FB032285829B8DFC0C468F636D0EE6E0B29E05F6EEBC1
- decision_continuation.py4E945678CE35F5409A67FE3DC9956C0FC25FE9CA642EEE7F3BF6E8F162DDC520
- decision_binding_continuation.py5ADF48EA1514D029EB8539D35BF33685AB3F5D8B11C78C97B5CFE79FD54EBE56
- test_b1_normal_web_continuation.py027C589A3F2A67391A83D9DC556E153C3CC553126270FC4B04FF5DFB0DAD3A86

Corrected SOURCE re-review and the updated full union are pending. Tesla explicitly
released the sole test slot after terminal runs; parent uses it for R4 regression.
Fresh independent QUALITY follows corrected SOURCE and worker final union.
Task4, mounted refresh/queued-reader integration and end-to-end display remain
unreleased/unproven. Task1's two original normal-route REDs remain unchanged.

### Task3 worker-to-coroutine dispatch boundary correction

The updated five-module union10310 completed378 passed/3 SWIG warnings337.23s,
exit0, frozen hashes unchanged. Fresh Nietzsche QUALITY then found a separate
handoff gap: a worker can settle at400 while the event-loop coroutine resumes
at402, past TTL401 but within active deadline475. Its prior worker-side check
cannot certify this later dispatch boundary. Actual native/JSON regressions
failed2 in2.52s: model HTTP count increased from2 to3. This is distinct from
expiry inside the live source callback, and the378-pass union is pre-correction.

The existing pre-dispatch block now synchronously rechecks B deadline/TTL, then
rolls back only the current never-dispatched decision reservation on rejection.
Original-profile carry/no-carry behavior is retained. Focus plus adjacent tests
passed22/3 warnings11.57s, session11872 terminal. No further long union started
until both corrected source reviews are complete. Updated loop/test hashes:

- decision_loop.py2FC675ACFCAA8E8925959A1C9E5312FDE63B82B1050C1231C1D44BA23BD572A8
- test_b1_normal_web_continuation.py24B5CE6799EF0CD33DC61F78F41BD3B84F5011EC0AE52E0689393033C2F9392E

Other three code hashes and approved launcher remain unchanged. Nietzsche
corrected QUALITY source review approves this freeze; fresh Huygens SOURCE
checks the amendment before worker final union and independent repeat. No Task4
assembly/activation, normal-route success or P7/P8 completion is claimed.

### Task3 final independent gate and Task4 release

Corrected Huygens SOURCE and Nietzsche QUALITY approve the five-file freeze.
Worker final union42625/f68ddc passed380 with3 SWIG warnings334.58s. Independent
exact same five-module union91646/f03c4c passed380 with3 SWIG warnings338.97s;
pytest/Python/shell exits0, five file hashes and approved launcher unchanged.
Parent rechecked all hashes and exact dirty-file scope before this checkpoint.
Earlier376/378-pass results and both reproduced TTL defects remain historical,
not substituted for these corrected final runs. Independent test slot is released.

Task4 is now released ONLY for its three production files and runtime/lifecycle
tests below. Reuse existing Task1 actual-app fixture seams; no Task5 continuation
Web changes, frontend flag work, settings selectors or default activation. Parent
owns documentation/commits; the fresh worker receives the sole local test slot.
This completes Task3 locally, not P7 integration or P8 live acceptance.

## Task 4 — atomic server assembly and actual normal entry

Files: `src/web/app.py`, `ordinary_capabilities.py`, `decision_runtime.py`;
new runtime/lifecycle tests.

### Task4 scoped display-context amendment (design review pending)

Actual assembly TDD has initial RED5/4.34s and RED8/5.96s, then focused GREEN15/
2.55s. These worker-reported terminal results establish assembly only; prefix
RED and the full union remain pending. Rawls explicitly released the local slot.

Source inspection exposes a second independent query limit after intent/decision:
decision_loop.validate_ordinary_display -> ordinary_chat_policy._session_facts
calls context_value twice with its legacy serialized-query16KiB default, although
B ingress/loop already admit16KiB UTF-8 query content within bounded envelopes.
That can reject an otherwise admitted B ordinary answer as chat_output_unsafe.
Do not weaken the real native/JSON positive or replace it with a stubbed answer.

Recommended minimal amendment: additionally allow ONLY
src/agent/harness/ordinary_chat_policy.py and
tests/agent/test_ordinary_chat_policy.py. After strict snapshot validation, exact
profile ordinary-semantic-b1-v1 plus catalog ordinary-product-b-v1 selects the
existing context_value(query_content_bytes=True) for BOTH compared live/replay
contexts. No new public API/caller parameter; keep private helper default false.
Preserve the original-profile query/context/error guards, all text risk checks,
32768-byte _view JSON bound,64KiB whole-context bound, empty scientific ledger,
and context identity/equality. Unknown/mismatched profiles must not gain the B
content policy. Normal-Web24KiB envelope remains stricter than _view's32KiB;
no global default, output limit, historical v6/v7 or decoder change.

TDD before correction: actual normal-Web native/JSON escaped/Unicode query
positives must reach both transports unchanged and the actual display boundary;
direct display tests cover B content16KiB positive, one-byte-over rejection,
original-profile serialized-limit rejection and mismatched session context.
Run unchanged original display tests plus the Task4 five-module union. Independent
source/design approval is required before this extra production-file edit; no
change has been granted yet. This fulfills the existing positive requirement,
not additional model/scientific capability or default activation.

- [ ] RED `test_b1_server_profile_assembly_is_closed`: B requires async
  decision_a2/semantic_v1, exact seven reviewed canonical adapters, distinct feature
  inverse mapping. B2/unknown/missing wiring rejects before publication; constructor
  failure does not leak any acquired owner. Registration/readiness are not consent.
- [ ] Add the optional server assembly/profile projection seams in design §3.
  `_prepare_semantic` captures one base/history under existing reader; project
  tools/RAG flags independently. Thread this exact view through assessment,
  prepare_with_intent, carry, loop constructor and display/fingerprint. No settings
  endpoint/CLI/env selector or default activation. Keep direct legacy dispatch closed.
- [ ] B intent-prefix RED must expose current history_prefix serialized-query
  rejection, not a stubbed transport success. After actual B content/envelope
  validation, obtain unchanged bounded system/history from history_prefix with an
  empty current-query slot and replace only that slot with the exact validated
  query before actual transport validation. Assert no empty placeholder escapes.
  Original-profile helper calls and all historical limits remain unchanged.
- [ ] RED then GREEN `test_b1_partial_assembly_and_writer_rollback`: fail after each
  acquired owner, fail/cancel close, deduplicate tool pool, fail after partial main
  consumer setter. Assert exactly-once attempted owned cleanup, unchanged old
  model/capability pair on failed publication, untouched gmm and no nested writer.
  Old model closes only after physical consumers settle.
- [ ] Ready frame remains normal_chat_mode=decision_a2; B advertises installed
  seven-tool wiring, local retrieval and fixed rag_count3, not runtime readiness.
  Source unavailable must not prevent ordinary capability/general conversation.
- [ ] Run `python -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_normal_web_runtime.py tests/agent/test_b1_normal_web_lifecycle.py tests/agent/test_ordinary_app_assembly.py tests/agent/test_decision_binding_profiles.py tests/agent/test_ordinary_capabilities.py`.

## Task 5 — same-socket original-journal resume and pre-CAS rejection

Files: `src/web/decision_runtime.py`, `decision_binding_request.py`,
`src/agent/harness/decision_binding_continuation.py`; new continuation tests.

- [ ] RED `test_b1_same_socket_original_smiles_target_new_loops`: request activity,
  clarify, reply `SMILES: CCO`, clarify, reply `target: PDE5A`. Different actual
  ModelDecisionLoop instances per segment, same original trace/obligations, original
  query journal turns0/1/2 and proposal turns1/2/3; no new admission/intent/reset.
  Existing A1 reply classification must be demonstrated as the initial blocker.
  Do not require a fake positive activity prediction: prove original subject/target
  reaches real action boundary and actual unavailable remains unsatisfied.
- [ ] Replace only B `_validate_resume` branch with immutable waiting/view/whole-
  reply-scope checks. In decoder, trial-copy authenticated journal/projection and
  use existing resolver reduction before CAS. No live journal admission or scientific
  calls on failed claim. Then retain core's full live compare→seal→records ordering.
- [ ] Add `test_b1_rag_then_clarify_reuses_sqlite_observation`: actual first RAG,
  clarify, same-socket resume into new Loop; original receipt/proof/ledger survives,
  duplicate model action reuses once, embedding/index/tool call count stays1.
  No tool execution during decode/replay. Reopen SQLite via its actual path for
  inspection; never fabricate a waiting snapshot to obtain the positive case.
- [ ] Parameterize pre-CAS negatives: changed profile/model/capability/permission,
  TTL/zero credit, forged/foreign/used nonce, new socket same cookie, disconnected
  socket, original journal/history/intent/observation warning/native-type tamper,
  stale full CSV bytes, source generation/config, same-SMILES different selected
  identity, added action, changed count/target/subject, invalid input then omission.
  Assert zero CAS/model/tool and exact unchanged durable waiting record.
- [ ] Parameterize committed/uncertain CAS, source mutation in claim/start/restore
  callback, deadline expiry after claim, full-observation mutation before seal.
  Assert no new model/tool, no old answer/new nonce, no false unconsumed claim;
  invalidate safely where live Session authority exists. Web retry-authority read
  must inspect invalidation marker/status/claim and decrease elapsed credit.
- [ ] Add chat clarify replay: forged intermediate unsafe question must fail the
  actual display gate before CAS, even with recomputed history checksum. Browser
  supplied transcripts never enter the decoder as authority.
- [ ] Run `python -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_normal_web_continuation.py tests/agent/test_ordinary_continuation.py tests/agent/test_decision_binding_inputs.py tests/agent/test_web_decision_runtime_lifecycle.py`.

## Task 6 — actual Web dataflow, publication flags and home behavior

Files: `src/web/decision_chat.py`, `decision_runtime.py`,
`src/web/static/js/home/main.js`, `advanced_options.js`; new runtime tests and
additive `tests/home_decision_runtime_test.js` cases.

- [ ] Replace Task1 gap assertion with `test_b1_real_rag_ws_receipt` (native/JSON):
  actual query→HTTP embedding→real FAISS→RAGSearchTool→adapter→Session/SQLite→next
  model request. The HTTP reply callback reads the actual preceding observation
  and chooses next action/citations from its IDs; do not preload a transcript or
  hard-code evidence IDs. Assert unchanged query/k3, one dispatch, stored receipt,
  strict own_source, final cited closure, retrieval_performed true. Valid-empty
  remains true with index_search_executed false; unavailable/discard never succeeds.
- [ ] `test_b1_scientific_dataflow_is_observation_driven`: property→ADMET/activity
  whole batch; reverse→target exact returned row handle. Use actual producers and
  temporary sources; count adapter and physical entries independently. Change a
  real prior result and require a different scripted HTTP next decision, proving
  branching rather than a static sequence. All seven tools get actual Web dispatch
  coverage across cases; unavailable activity is honestly failed/partial, not a
  synthetic positive. Only tool-accepted numbers appear in scientific final text.
- [ ] `test_b1_chat_without_science_assets`: REAL-010, DIVERSE-015, general knowledge
  and follow-up use actual intent/decision transport and display gate, zero tool/
  RAG calls, empty SQLite executions. Scientific mixed clauses, tool proposals
  from chat, invented numeric claims/citations/readiness stay denied. Preserve
  exact safe same-socket history and no cross-socket history.
- [ ] RED `test_b1_pending_terminal_not_home_completion`: take exact actual ASGI
  `agent_event` frames during blocked final source check/drain; feed their bounded
  JSON via stdin to a proposed `--b1-frames` mode of the existing Node harness.
  Assert no completed/green task UI, answer, nonce or enabled continuation until
  verified result/complete. This is protocol/DOM evidence, not a live browser run.
- [ ] Keep pending flags through `_event_frame` and gate home presentation.
  Derive B retrieval metadata from released validated evidence rather than the
  current unconditional false. Do not change original-four metadata semantics.
- [ ] Add `test_b1_attempted_unverified_retrieval_is_not_no_attempt`: actual RAG
  discard, tool failure and source-invalidated publication frames go through the
  real home handler. B false/no accepted receipt shows no verified result, never
  "本轮未执行检索". Distinguish actual zero-dispatch, verified-empty and unknown
  dispatch history with a bounded B presentation state; only known zero dispatch
  supports not_attempted. Provider/model/browser text cannot set this fact.
- [ ] Add `test_b1_count_policy_preserves_explicit_choice` to the Node harness:
  untouched default5/no saved count becomes a visible default3, outbound3 with
  no localStorage write. Saved5/custom or newly edited5 stays visible/outbound5,
  with an explicit supported3 choice and unsupported-count notice; only clicking
  that choice authorizes3. A crafted explicit5 request is rejected by the server,
  never repaired to3. Reset/disconnect/reconnect cannot erase explicit intent;
  old-profile defaults/preferences remain intact. Reject malformed ready count.
- [ ] Load actual advanced_options.js in the Node harness (not its current stub)
  for count-policy probes. Keep persisted preference separate from effective B
  default. In B with no explicit RAG choice, edit temperature and molecule count:
  saved config must omit an implicit RAG count, reload still uses visible B3 and
  legacy transition defaults5. With preexisting explicit5, unrelated saves/reload
  must retain5, not3. Only a genuine RAG edit/use3 choice persists a new count.
  Verify reset/disconnect/reconnect and unchanged non-B save/default behavior.
- [ ] Preserve same-result reference projection exactly once. Test actual owner-
  confirmed reference→property calculation and wrong-owner/revision/order/revoked
  negatives. Historical reference fixtures must be explicitly labeled existing
  confirmed-source controls, never represented as B-generated candidates. No new
  dynamic report success: unsupported_binding remains honest pending B2/G1 work.
- [ ] Run `python -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_normal_web_runtime.py tests/agent/test_web_decision_runtime_references.py tests/agent/test_scientific_reference_web.py tests/agent/test_ordinary_web_runtime.py`.
  Run `node --check src/web/static/js/home/main.js`,
  `node --check src/web/static/js/home/advanced_options.js`,
  `node tests/home_decision_runtime_test.js`,
  `node tests/home_agent_task_panel_test.js`,
  `node tests/home_scientific_references_test.js` after authorized implementation.

## Task 7 — resource/correction and delivery fault matrix

Files: new lifecycle/continuation tests; corrections only in listed production seams.

- [ ] `test_b1_terminal_callback_source_swap`: mutate actual temporary source during
  terminal metadata, finish/event/status, waiting-publication callbacks, including
  commit-then-raise. Irreversible failure correction, no science/nonce, exchange
  empty, historical records retained; restoring source in correction cannot heal it.
- [ ] `test_b1_worker_and_projection_drain_retains_reader`: threading events block
  actual owned validation/tool worker; separately block existing reference projection.
  Cancel/timeout/disconnect/shutdown/repeated-cancel and queue model writer. Until
  actual exit, no lease release/model close/positive complete/new turn; failed join
  is retained-unconfirmed, not artificial settlement. Finally unblock and assert
  closed ownership exactly once with truthful result/delivery status.
- [ ] `test_b1_complete_send_only_grants_remaining_credit`: clock-controlled20s tail,
  slow projection and complete send; checkpoint remains75 but socket cap may be
  lower, never higher. TTL origin stays bridge return. Send failure/expiry/shutdown
  callback clears waiting/history; same cookie reconnect cannot resurrect them.
- [ ] `test_b1_storage_failure_does_not_publish_or_replay`: reserve/metadata/snapshot/
  CAS/correction failures, including persistent failure. Counters reflect known
  attempts, durability unconfirmed where appropriate, no refunded unknown call,
  no second intent/tool, no stale success frame.
- [ ] Run `python -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_b1_normal_web_lifecycle.py tests/agent/test_b1_normal_web_continuation.py tests/agent/test_ordinary_web_lifecycle.py tests/agent/test_web_decision_runtime_lifecycle.py tests/agent/test_worker_ownership.py tests/test_model_request_lifecycle.py tests/test_web_app_lifecycle.py`.

## Task 8 — independent evidence and handoff, not P8 real acceptance

- [ ] Re-pin source against parent PR84 diagnostic/CI disposition before release;
  inspect only actual relevant source diff, never plan against an assumed fix.
  If scientific/source or admission contracts changed, amend written plan and review
  before continuing. No competing CI ownership or assumed green result.
- [ ] Run the **exact ordered25-module union** recorded in core plan Task5 (do not
  replace it with a count), then the four new B Web modules and these existing
  source-selected modules, serially under the approved runner:

```text
tests/agent/test_web_decision_runtime.py
tests/agent/test_web_decision_runtime_lifecycle.py
tests/agent/test_web_decision_runtime_references.py
tests/agent/test_web_decision_references.py
tests/agent/test_web_decision_fingerprint.py
tests/agent/test_web_decision_admission.py
tests/agent/test_ordinary_web_runtime.py
tests/agent/test_ordinary_web_lifecycle.py
tests/agent/test_ordinary_app_assembly.py
tests/agent/test_ordinary_semantic_admission.py
tests/agent/test_ordinary_capabilities.py
tests/agent/test_ordinary_chat_policy.py
tests/agent/test_ordinary_intent_transport.py
tests/agent/test_decision_chat_transport.py
tests/agent/test_scientific_reference_web.py
tests/agent/test_scientific_reference_resilience.py
tests/test_model_request_lifecycle.py
tests/test_web_app_lifecycle.py
```

  Existing v6/v7 and B8 core regressions remain mandatory. No claimed totals in
  advance. Missing dependencies/skips/fixture faults are reported, never counted
  as scientific success. Parent coordinates full-suite/compile/CI separately.
- [ ] Independent SPEC then fresh QUALITY reviews exact file hashes, actual source
  boundaries and terminal test results (including REDs/intermediate failures,
  commands, duration, exit, warnings/skips). Parent rechecks diff/allowlist/clean
  unrelated work. No commit until explicitly released; explicit paths only,
  no git add -A, no main commit. Any later draft PR is parent-coordinated, not
  created or merged by this drafting task.
- [ ] Report normal `/ws` offline scope separately from synthetic HTTP/science
  fixtures and actual unavailable models. P7 still owes ordinary normal chat and
  actual known12/20 case sets with unchanged positive expectations. B2 generation/
  ranking/dynamic reports, C and repeated P8 real browser/provider/tool acceptance
  are committed next work, not optional deferrals. Do not claim full capabilities
  from B1. Keep ledger32 GOLD/DIVERSE ×3, REAL-010 UI positive and16 legacy
  accounting intact. Deployment remains out of scope.

## Draft self-review / unresolved source questions

All design sections map to Tasks1–8; proposed names are labeled, existing symbols
were source-inspected, and no run/result is claimed. Initial RED assertions target
actual B admission/carry/resume/presentation gaps. No scientific evidence is granted
by fixtures, checksum, browser transcript or semantic intent.

Before code release, written reviewers must resolve the design's explicit
compatibility amendment (trusted core-only8 versus strict Web8), conditional B8
ordinary fields, pre-CAS trial reply validation and non-coercing supported3 UI contract. These are
selected recommendations, not unanswered user choices. Source proof still needed:
zero-pre-execution actual-source fixture composition without nested asyncio.run;
actual activity unavailable isolation without host weights; any changes caused
by parent's independent RAG CI. If these need producer/framework changes, stop
for an exact allowlist amendment rather than broadening this plan silently.
