# B1 Model-Selected Loop Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Hume SOURCE and fresh Halley QUALITY approve the amended freeze. Both ordered58 runs passed6107; see sections17-18. Parent owns scoped publication and exact-head CI/merge gates. No Web/model activation is authorized.

**Goal:** Integrate the reviewed Task4B seven-tool, evidence-bound model decision loop into the current B1 foundation, as a dormant server-only profile.

**Architecture:** Extend the existing ModelDecisionLoop, resolver and WorkflowRunSession; retain the existing registry/adapters, immutable input journal, acceptance evaluator, ledger and WorkerOwner. Preserve default legacy/A2 behavior and all current producer/security fixes. Do not add another planner, engine, scheduler or tool sequence.

**Tech Stack:** Python 3.10, Pydantic 2, existing LangGraph decision graph, SQLite, pytest, RDKit and temporary RAG/reverse sources. Scripted model decisions and typed ADMET/activity/target fixtures are explicitly synthetic, not live scientific acceptance.

---

## 1. Status, authority and hard gates

Current state, 2026-09-27: **SOURCE and fresh QUALITY approved; author and
independent exact58 each passed6107; sole local slot released**. Sections12-18
preserve actual RED/GREEN and expanded verification. Parent may publish only this
reviewed slice, subject to exact-head CI/review/tree guards. No further worker
source edit, test run, environment/asset/model discovery, commit or push is
released automatically. This is dormant/offline integration, not live acceptance.

Subsequent Hume P2: parent accepted focused cancellation RED (section 15), released
the narrow production fix, and its unchanged four cases passed (section 16).
Current manifest has explicit loop-production/test exceptions. Parent subsequently
reported Hume re-review SOURCE APPROVE with no P1/P2 and released the one exact58
run recorded in section 17; no further execution is inferred.

Audited worktree: `D:/MedChat/molecular_chat_system_worktrees/b1-model-loop-integration`.
Branch: `codex/b1-model-loop-integration`.
Preparation-start worktree/index: clean. Parent-aligned local HEAD:
`f99cf7efb9e92632e4f6b961363f5a5730a1a179`.
Reviewed PR93 head: `0d6f40c3030467032bdae007d1c68e61fe477f9a`.
Actual SHA-squash landing / locally verified `origin/main`:
`5d36eee2977152fe5047dc21e260500d6c965c4b`.
Read-only Git object verification confirms both complete reviewed/landed trees
equal `a3ebb7b758df36daad66197928a2700c7b79a6c6`.
Before preparation, `git diff --name-status origin/main HEAD` contains only this
own plan. The parent resolved the admission-plan add/add conflict by retaining
main's record; this task verifies that file equals landed main and does not edit it.

Parent-reported prerequisite evidence (not a local rerun or network check here):
PR93 CI `36272209579`, exact nine checks all success; core job `108488189672`
collected `10820 = 10596 core + 224 Web`, core `10595 passed, 1 skipped` in
467.02 seconds, Web `224 passed` in 180.48 seconds. Parent reports two fresh
guards at head `0d6f40c` / base `4d896b6`, reviews 0 / unresolved threads 0,
then ready/merge, session `86313` terminal `33649a`, and fetched tree equality.
The earlier 600-second CI timeout and pending-landing statements are historical;
they are superseded for the prerequisite, not evidence of next-loop test success.

- [x] Parent and Mill SOURCE approved the eight-file manifest, Session de-duplication
  and behavioral RED plan; parent released the updated 58-module selection.
- [x] Actual PR93 landing/review/CI evidence supplied; complete reviewed/landed
  tree equality verified from local Git objects.
- [x] Parent aligned this worktree; full pre-preparation diff contains only this
  plan. Manifest baseline blobs are unchanged from the original audit. Any later
  main change still requires a renewed overlap/dependency audit.
- [x] Parent explicitly released this plan re-pin and three-test preparation only.
- [x] Parent separately granted the sole slot and approved the isolated runner
  for exactly two sequential nodes. Both are terminal; slot is now released.
- [x] Observe genuine behavioral RED before production edits: checkpoint guard
  assertion failed in phase=call, not collection/setup or a missing keyword.
- [x] Parent reviewed actual RED and restored Session blob, then separately
  released only the five exact donor production files for source-only extraction.
- [ ] Freeze implementation for independent SOURCE/SPEC before expanded QUALITY;
  parent reviews results and explicitly releases each later stage.

No elapsed time, historical approval, CI completion elsewhere or publication of
this plan automatically advances another gate. Do not update another plan or
handoff file; the only additional writes released are the three manifest tests.

## 2. Immutable sources and complete eight-file manifest

The donor columns below remain immutable historical sources. The current freeze
is recorded in section 16: only `decision_loop.py` and its loop test additionally
carry the parent-approved Hume P2 cancellation correction/regression. Do not claim
those two are still donor-exact. The Session adaptation remains the same; the
other five files still equal their selected donor blobs.

Selected donor: `0064c30dce5d2c38aa4c655a45026e0baf66dd7e`.
Actual first parent: `ce47c826d8347caaf91dbc17c9211252475a0f87`.
The commit changes ten paths; select **only five production and three test
paths** below. Its historical plan and handoff changes are reading material,
not extraction targets. No cherry-pick or historical branch merge.

Read at that exact donor, including the original design's consumed-reference
dispatch seam and the plan's Task4B correction/review record:

| Authoritative document | Donor Git blob |
|---|---|
| `docs/superpowers/specs/2026-09-26-b1-decision-execution-design.md` | `6a10146ef231fc864b99735762dd5f2ddcb94a87` |
| `docs/superpowers/plans/2026-09-26-b1-decision-execution.md` | `17c7f44f34def72c752a9f27da938f4942557f91` |

The design records source review of the Session seam; the plan records corrected
Task4B SPEC/QUALITY approval. These establish historical intent and selected
source, **not current test results, parent approval or production readiness**.
No historical pass counts are adopted as this integration's evidence.

All eight donor files were read in full. All five current production blobs and
the existing input-test blob equal the donor parent. The loop-test path is absent
in the current baseline and parent. The Session-test divergence is fully bounded
below. Git blob IDs identify the complete files, not excerpt hashes.

| Path | Baseline / parent blob | Selected donor blob | Integration action |
|---|---|---|---|
| `src/agent/harness/decision_bindings.py` | `be27b66ceb72e8c9d13512800209e4fb3cf6306a` / equal | `0ebd9aa7aa046aaa7a7f9c8275d761638236cf48` | Update; issued-action dispatch authentication; +34/-2 |
| `src/agent/harness/decision_continuation.py` | `1c9b6f7f8dd506fd3cd9c7a4161f350c06b47efb` / equal | `b44a0cbcbab7d13286c2c3c4fe94a65f4b5750af` | Update; profile fingerprint, B nonce rejection; +6/-1 |
| `src/agent/harness/decision_loop.py` | `fc90509594b78e64782b0fd620f0715c412e0762` / equal | `251cd452ddfc69ad442de82dd4818bcebcb4a2ed` | Update; actual graph/owned resolver/acceptance wiring; +241/-41 |
| `src/agent/harness/decision_policy.py` | `f438c555ea2743fbb2c00a8eb9e5cb101019304e` / equal | `52d723de6901597b9b2e2478d2078ad959444089` | Update; explicit B catalog and bounded requirements prompt; +30/-4 |
| `src/agent/runtime/run_session.py` | `488994ce2784eadd16a2a5d49bf841216d185314` / equal | `94b03a668834a2a994bde8157b603ec6d28fde1c` | Update; optional dynamic reference guard and denial journal; +20/-0 |
| `tests/agent/test_decision_binding_inputs.py` | `8ffd78e8f06b9bf391914bc6ec252f4c372e6526` / equal | `bd377facce7f997572fb9a51a33e46451ed77205` | Update; replace all four gap cases with actual same-context dispatch; +25/-17 |
| `tests/agent/test_decision_binding_loop.py` | absent / absent | `bb021cb19090967f9a547823b1682e444463ebf3` | Add full 666-line actual graph suite |
| `tests/agent/test_decision_binding_session.py` | current `adc943de668bbb1c6484ce35a33fc82aa2f222d2`; parent `7d3f347fcc39a13f9d0afedfd22270cc88e3affd` | `6ec63d5c1ad041ba80f2a7992c078280afe6efd6` | Add only donor's 48-line guard-test block to current file |

The final tracked implementation allowlist is these eight files plus this plan.
The three tests come first; production comes only after RED. No new helper file,
new dependency, configuration, schema, Web code, asset or historical handoff is
included. Further behavioral findings require parent-reviewed bounded TDD.

### Session test is an explicit adapted extraction

Current main already moved the following donor definitions into
`tests/agent/test_decision_owned_call.py` (current blob
`25683ce00eb0594988e30987dca2f2059e5ab971`):

- `owned_helper`
- `test_generic_owned_call_drains_nested_reservation_after_callback_return`
- `test_generic_owned_call_returns_result_never_retries_callback`
- `test_generic_owned_call_repeated_cancel_drains_before_return`
- `test_generic_owned_call_task_creation_failure_closes_coroutine`

Retain that file unchanged and run it explicitly. Do not restore duplicate
definitions, delete other Session cases or clean up retained imports. The donor
addition is exactly the three decorated tests between `proof()` and `BASE`:
`test_reference_guard_requires_dynamic_callable`,
`test_reference_guard_requires_exact_none_and_journals_denial`, and
`test_reference_guard_replaces_provisional_checkpoint_before_reentry`.

Read-only PowerShell/.NET reconstruction checked the entire parent-minus-duplicate
text against current text, then current-plus-the-48-line-block against donor
minus those same duplicates. With the existing LF/single-final-newline file
format, the planned final Session test is 549 lines, Git blob
`54b12b4cc2d46b93998a6d7911e4d15359f0c66f`. No file was created by this check.
The initial extraction's other seven files equaled their donor blobs exactly.
Only the subsequently authorized Hume P2 exceptions in sections 15-16 supersede
that equality; no other test/production adaptation is permitted.

Re-pin at landed PR93 `5d36eee`: all seven existing manifest baseline blobs
still equal this table; `test_decision_binding_loop.py` remains absent before
preparation. Thus the reviewed donor extraction and 549-line Session adaptation
are unchanged. The five production files must remain at the baseline column
through test preparation and genuine RED; donor production blobs are future targets.

## 3. Dependency audit: preserve the current implementation

### Equal prerequisite contracts

These baseline files equal the donor parent as full Git blobs. They are reused,
not re-extracted or edited:

| Path | Current and donor-parent blob | Required seam |
|---|---|---|
| `src/agent/contracts/decision_bindings.py` | `8265f19ca5cb7c9929bc4c8c4e35d52cbcfe6f3d` | Closed B1 arguments, roles, proof and profile |
| `src/agent/contracts/binding_requirements.py` | `9d651e5e0d610195c0e62647aa136989d32fa4e2` | Explicit v2 and required-tool union |
| `src/agent/harness/decision_binding_inputs.py` | `5fd21c6f26356a4c36a9be28c07f84ff868c7826` | Frozen complete admitted context, prefix journal |
| `src/agent/harness/decision_binding_acceptance.py` | `2b1dbc06143f548300db6557282b2ea14735e198` | Deterministic acceptance/cited closure/rendering |
| `src/agent/harness/decision_bounds.py` | `079b2315ea5baf7eee43b34ed18a32db5cf56786` | Exact Boolean B content-byte option; native bounds |
| `src/agent/harness/decision_inputs.py` | `b92b0880e62fbb7474a086ff42eff59ae9e94920` | Whole-input parsing, seals, selected-reference checks |
| `src/agent/evidence/ledger.py` | `893f3cd40dbfff3fb39407c77c87dadf65a6cf8f` | Explicit proof authority, legacy binding identity |
| `src/agent/harness/decision_execution.py` | `bd06d6cebe8f0e70b0752ed8bd80dc783e675682` | Owned call settlement; request-local guard; typed-target status fix |
| `src/agent/tooling/adapters.py` | `cff17c3a3e8985ad4b87a7207670c21d959d1152` | Guard after validation, reservation and inside worker; no inner retry |
| `src/agent/tools/reverse_target_tool.py` | `e42dca591f198b4c877bda3ec602a658179df5b3` | Attached-source check, raw receipt binding, no reacquisition |

`SingleAttemptTool` still recognizes only the actual typed TargetToolAdapter for
`target_database_search`, bounds the untouched native envelope, maps recognized
lookup-success status in a detached check-view, then lets the typed adapter
validate the original raw result. Preserve this guard fix: `not_found` execution
is not a failed invocation, but never means a resolved target. Misleading tool
names and other adapters do not receive this exception.

`settle_owned_call` does not retry arbitrary callbacks. `settle_action` retains
its named `advance` journal retry. Guard denials must survive that re-entry,
adapter error normalization and nested-worker settlement. Do not change these
shared mechanisms or weaken adapter capacity/cancellation semantics.

### Intentional current differences from donor parent

| Path | Donor-parent blob | Preserve current blob | Risk and disposition |
|---|---|---|---|
| `src/agent/tooling/rag_contract.py` | `23ecbcc9cbc1289aebb9e9ff5a7e04e6ad32efb3` | `69245d341c89f1df46c22602c84d008a1a1b2679` | Proof-covered data AND evidence must survive redaction unchanged; includes configured sensitive fields |
| `src/agent/tooling/target_contract.py` | `4c02f3061e5cece8e4acbddcdb3ea518e84825d9` | `a1b401f63922d8d2f18dcef51b6deb27f43e00bf` | Reverse proof rejects security-normalized changes before seal/persistence; never repair hashes |
| `src/agent/tools/rag_search_tool.py` | `503a7dbdc421e9006c572726d09dbc45ba7c6f8e` | `2232d207e02f8bab9a6fc352661f6b916b2b9a2d` | Formatting exception returns unavailable, not successful or leaked output |
| `src/rag/service.py` | `d039f39f68e0be616f99e464fda9f41b27e69187` | `433ec6668245bb2d6410cd59028099862e1b4296` | Preserve object-cell dtype while detaching, match frame to actual source bytes before generation publication |
| `src/reverse_target/owned_source.py` | `f0d55a000304306d990531fb0795e6537394917f` | `76798257f89c95e79d562bdf74c598f38ed2b1e5` | Strict TSV logical records/header/shape/index and cancellation/cleanup before pandas/arrays |

Source checks still use attached services/predictors, original receipts and exact
resolved input; they neither re-search/re-predict nor initialize/repair a source.
RAG checks can hash full CSV bytes, so they are owned blocking work, not event-loop
accessors. Failed RAG/reverse observations cannot gain usable proof by filling a
missing receipt. Current producer output can now reject additional unsafe
envelopes; do not downgrade these checks to reproduce historical acceptance.

### Test helper/import closure

The new loop module imports `sources`, ScriptedModel/tool/finish/clarify/
last_observation, admet_tool/target_tool/requirements, Recorder/single_row,
family_row and selections. The input suite additionally reuses
build/decision/owned/execute/eid and forbid_work; Session uses CountingTool,
CommitThenFailExecutionOnceStore, signalled/pending and `_candidate_set`.

The following complete helper-provider files equal the donor parent, verified
by blob comparison: `tests/agent/test_decision_dynamic_bindings.py`,
`tests/agent/test_decision_binding_acceptance.py`,
`tests/agent/test_activity_tool_contract.py`, `tests/agent/test_analysis_contract.py`,
`tests/agent/test_family_activity_tool.py`, `tests/agent/test_target_tool_contract.py`,
`tests/agent/test_scientific_reference_store.py`, `tests/agent/test_worker_ownership.py`,
`tests/agent/test_workflow_run_session.py`, `tests/agent/test_current_source_tool_hooks.py`
and `tests/agent/test_decision_loop.py`. Input selections remain in the selected
input module. No imports/reexports are removed from the adapted Session module.

Transitive current-source fixtures intentionally differ and remain current:

- `tests/agent/test_rag_receipt_consumption.py`, blob
  `288cfc6d361e7918f46c54fafebb0f8a9a095f5b`: initialized_service adds the compatible
  keyword-only `source_name='synthetic.csv'`. Current tests hook the actual owned
  FAISS index instance, not an assumed Python subclass; preserve traced/untraced
  dispatch and native/base-flat-IP variants, plus evidence-only redaction cases.
- `tests/conftest.py`, blob `eb944857e749757550ce32e7c95ed0c363cc9d7e`: retain
  `rag_ip_loader`; do not backport the donor conftest or change isolation.
- `tests/agent/test_reverse_receipt_consumption.py`, blob
  `5204d1e34039d9e693ee5795058fbb4a5c5c98bf`: keep evidence-only redaction negatives.
- `tests/test_reverse_target_invocation_receipts.py`, blob
  `f67dae6889a8787cb2a48fcd3d0ca83cce379372`: sources still calls
  make_writer_database/close_fixture_mmaps; current strict TSV and source tests
  stay in the regression, not replaced by older fixtures.
- `tests/agent/test_rag_current_eligibility.py`, blob
  `833195c67b4940ada343a202f815dd6017d340eb`, and
  `tests/test_rag_owned_generation.py`, blob
  `1f71d01f03a221a66336cd02759319d77baea761`: retain current frame/source and native
  FAISS regressions. `tests/test_rag_retrieval_outcome.py` is also retained.

Importing a helper does not execute its provider module's test suite. This is why
the regression explicitly includes the family and current-source modules.
Static compatibility is not proof that fixtures collect or run in the future
approved environment. No environment installation/discovery is authorized now.

### Landed-baseline delta: parser, empty journal and CI capacity

Compared with the initial `a84aa921` audit, the only production-source delta in
landed PR93 is `src/agent/contracts/target_request.py`, preserved blob
`2ec6087fdf3276a97154fd436a282caf83f44713`. This is the PR94 full-clause analytical
role fix: ADMET/QED analysis items no longer become inferred unknown targets;
explicit labels, recognized targets and ambiguity guards remain authoritative.
Do not extract the historical donor parser or rewrite the user's whole query.
All prerequisite/helper blobs and current RAG/target/source/dispatch guard fixes
listed above remain unchanged from the initial audit.

Preserve the two added regression-only modules without edits:

- `tests/agent/test_binding_analysis_clause_integration.py`, blob
  `6ceed90d4072b77204bfad186d7f1fff853ca149`: seven zero-science-state journal cases,
  exact Task6 query, four whole-batch obligations and unknown/multiple-target guards.
  Parent reported genuine old-parser RED 3 failed / 4 passed (4.90 seconds), then
  aligned GREEN 7 passed (4.19 seconds), unchanged test hash. These are prerequisite
  evidence, not next-loop runs or full Web acceptance.
- `tests/agent/test_target_analysis_phrase.py`, blob
  `8033991b1f346d64f117c23cfaa46aa6eefeaa83`: 71 parser/router/planner clause and
  ambiguity cases, with bounded scanner coverage; no model/scientific readiness claim.

PR95 changed only the core CI command budget 600 -> 1200 and its workflow contract
tests, plus documentation. Preserve `.github/workflows/quality.yml` and
`tests/test_quality_workflow_contract.py`; no runtime deadline or performance fix
is implied. Other baseline deltas are parent plans/design records, not extraction
targets. The admission plan is byte-for-byte landed main after parent alignment.

## 4. Required behavior and deliberate non-goals

### Seven actual model-selected tools

The exact names are `property_calculator`, `drug_likeness_assessment`,
`activity_predictor`, `admet_predictor`, `reverse_target_predictor`,
`target_database_search`, and `rag_search`. Every next action comes from the model
proposal over actual prior observations; the required set is not an execution
order. Tests cover native and JSON decision modes using the real graph, registry,
adapters, Session, ledger and validators. Temporary RAG/reverse sources and RDKit
are real local computations; ADMET/activity and target Service fixtures are
labelled synthetic. No live-model claim follows from ScriptedModel.

Only the exact server constructor `binding_profile=B1_PROFILE_REVISION` opts in.
Default None preserves INITIAL_TOOLS/original four. Unknown/B2/non-string
profiles reject. B requires explicit v2 requirements and an actual WorkerOwner;
ordinary admission carry/exchange cannot select B. Catalog authority intersects
profile, allowed/required/forbidden sets, strict Boolean capabilities, actual
registry ownership, idempotence and no-side-effects. Scientific false permits
only explicitly authorized RAG when rag is true. Chat exposes no tools/citations.

### Owned checks, reference exception and first-error latch

Freeze the original full context before catalog callbacks. Bind every action to
the journal head/prefix and immutable original obligations. Keep legacy clock
placement and reference checks unchanged. B establishes its root deadline before
owned requirement preparation; resolver/source/acceptance work is owned, with
deadline checks before and after. Limits remain 16 model requests, 12 tool
attempts and 300 seconds; no new credits or retry framework.

The sole Session reference exception is the **server-supplied callable dynamic
reference_guard**, returning exactly None. At the existing checkpoint-reuse and
pre-dispatch locations it validates the authority actually consumed. Independent
RAG and user-target lookup may retain an expired/unavailable incidental raw
selection without consuming it. Do not clear the context, special-case a global
tool-name bypass, remove reference checks or trust arbitrary model metadata.
Consumed selected references, foreign owners and molecular ancestors behind a
target/RAG step still require freshness and complete prefix integrity.

Authenticate the actual Session-owned deepcopy, not only the append_step caller:
tool, output key, transform/binding absence, complete resolved input, registered
issued record and **all** admitted step metadata must agree. Re-resolve the
original tool/arguments against the unchanged journal and current closure.
At adapter stages the request-local guard repeats this check after validation,
reservation and worker entry; shared adapters are not mutated.

Cache denial and clear checkpoint_reused before raising, replacing provisional
checkpoint success before execute_step retry. Latch the first safe boundary
reason irreversibly through normal/exceptional settlement; a later successful
source check or generic normalized adapter error cannot erase it. Pre-Session
denial means zero Session attempts and zero physical calls. Adapter-stage denial
may consume one conservative attempt/budget reservation but must make zero
physical calls; do not refund, retry or claim it was unattempted.

### Whole batches, roles and truthful finish

Property/likeness/activity/ADMET upstream molecular bindings consume the whole
sealed batch, retaining evidence IDs, output digests, subject count and original
activity target. Reverse requires exactly one complete molecule. Reverse-to-target
uses an issued original-row record handle, not a model-supplied identifier,
CHEMBL conversion, name hash or automatically chosen best hit. Ancestors must be
prior, sealed, same-owner and currently eligible; equal SMILES is not ancestry.

The actual-loop seven-tool test includes both property-to-analysis and
reverse-to-target binding. The retained dynamic whole-batch test and acceptance
subject/missing/duplicate matrix cover multi-subject semantics; do not describe
the donor seven-tool single-subject scenario as a multi-molecule loop test.

Model-authored scientific finish prose is never rendered. Deterministic
acceptance expands cited ancestors, selects qualifying required evidence within
that closure, and reports missing_required_citation when successful required
science was not cited. Valid empty retrieval/target lookup differs from requested
hits/resolution. Sparse ADMET with actual observed values is allowed; empty or
unknown-only output is not success and unknown alerts never become low risk.
Family disagreement remains partial/review. Failed optional tools, demo/fallback,
unavailable models, malformed/foreign evidence and stale sources never become
completed science or silently repeat an action.

### Dormant scope: later publication and continuation work is mandatory

This donor explicitly refuses B continuation claims and legacy6/7 snapshots;
clarification may mark waiting but releases no B nonce. It changes the fingerprint
to bind the profile/content-byte input, **not** to implement authenticated
revision8 restore. Default v6 and ordinary v7 must remain unchanged.

Later donor identities, resolved locally solely for exclusion:

- revision8: `a5189a5f854050e59c6fc8ed4f8a397313c09067`;
- final publication barriers: `2c7fd00d16bcae0d289e926519d19f98b951b469`.

Do not extract either here. Task4B checks terminal metadata then closure, but
does **not** implement the later post-finish_dynamic/event/status/waiting/drain
publication barriers, durable invalidation correction or charged revision8 tail
credit. Existing terminal callbacks can still invalidate a source after this
slice's last check. Do not call this a safe production answer-release boundary.
Returning/clearing answer text alone is not proof that no earlier terminal
payload exposed scientific data. Later work must inspect returned AND persisted
terminal state and event contents.

Current `src/web/decision_runtime.py` and `src/web/decision_lab.py` construct the
loop without B enrollment. They, application assembly, `/ws`, frontend,
ordinary admission, model configuration and deployment remain unchanged.
Normal-Web B activation, B2 generation/ranking, C cleanup and P8 real-provider/
scientific/UI repeated acceptance are separate gated work. P7/P8 remain incomplete.

## 5. Task 1 — re-pin, prepare tests, and obtain genuine RED

**Files:** Modify only the two existing manifest test paths; create only
`tests/agent/test_decision_binding_loop.py`. This plan may record evidence.
No production changes at this stage.

- [x] Satisfy section 1 preparation gates; read AGENTS/PROJECT_STANDARDS again after alignment.
  Use read-only `git status --short`, `git rev-parse HEAD 'HEAD^{tree}'`,
  `git ls-tree`, `git show` and `git diff` to recheck complete blobs and scope.
- [x] Read the exact complete test source (do not transcribe selected assertions):

```powershell
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:tests/agent/test_decision_binding_loop.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:tests/agent/test_decision_binding_inputs.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:tests/agent/test_decision_binding_session.py
```

- [x] Apply the full loop/input tests with apply_patch. Add only the exact
  48-line Session block, preserving current de-duplication. Pin test blobs.
  Full authoritative blob references above specify all code, fixtures and
  parameters; no historical document extraction is needed.
- [x] Parent separately approves/prepares the isolated launcher. The prior
  admission worktree's approved launcher is documented with SHA-256
  `6D49050C00D66642F2045828AAAE927C7254B8C282ACCFC0EC1E24A7AF5623AD` in
  `docs/superpowers/plans/2026-09-27-b1-admission-acceptance-integration.md`.
  The initial audit did not read/copy/run that ignored file. Under the later
  explicit release, verified its full bytes and parent approval, substituted
  only literal REPO using apply_patch, compared full bytes and reverse
  substitution, and recorded the new hash in section 12. A
  documented historical hash is not a substitute for checking the actual runner.
  No raw pytest, new runner logic, environment change or asset discovery.
- [x] With the sole slot explicitly granted, first run the donor's existing
  legacy authorization characterization through that runner:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_loop.py::test_actual_legacy_graph_seven_tool_gap
```

Expected baseline: all three registered actual RAG/ADMET/reverse adapters are
denied by the real graph as tool_not_authorized, with one model request and zero
physical calls. These are **passing negative characterizations, not RED**. Retain
them unchanged in the final suite; default legacy authorization must not widen.

### Behavioral RED without pretending a missing keyword is the bug

The exact donor opt-in tests currently encounter a missing binding_profile or
reference_guard constructor keyword. Record such interface failures separately;
they alone do not satisfy the gate. Recommended test-only staging, subject to
parent plan approval: for the existing new Session checkpoint test, temporarily
install the same trusted guard into its future private slot **after using the
current constructor**. This bootstraps only the constructor seam, not the behavior
being tested. It exercises actual Session checkpoint processing and settle_action
with unchanged assertions. It neither mocks `_reject_unavailable_reference` nor
changes production code, source eligibility or legacy expectations.

- [x] Temporarily use this complete body for the donor-named test in
  `tests/agent/test_decision_binding_session.py`; no other assertions change:

```python
def test_reference_guard_replaces_provisional_checkpoint_before_reentry(build, monkeypatch):
    old, tool, *_ = build()
    calls = []
    def guard(step, input_data):
        calls.append(1)
        raise DecisionBoundaryError('invalid_dynamic_binding')
    session = WorkflowRunSession(old.orchestrator, AgentContext('fixture', 'guard-checkpoint'),
        [], {tool.name: tool}, dynamic=True)
    # RED-only constructor bootstrap; current execution does not consult this slot.
    session._reference_guard = guard
    session.start()
    session.append_step(old.steps[0])
    monkeypatch.setattr(session.orchestrator, '_compatible_checkpoint', lambda *a, **k: {'fixture': True})
    monkeypatch.setattr(session.orchestrator, '_result_from_checkpoint',
        lambda *a: ToolResult.success_result(tool.name, {'must_not_reuse': True}))
    asyncio.run(decision_execution.settle_action(session))
    assert calls == [1] and not tool.calls and session.tool_attempt_count == 0
    assert not session.results[0].success and session.results[0].data is None
    assert session.results[0].error.details['reason'] == 'invalid_dynamic_binding'
    assert not session.reused_steps and not session._step_journals[0].checkpoint_reused
```

- [x] Run exactly this focused node, before changing any production blob:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_session.py::test_reference_guard_replaces_provisional_checkpoint_before_reentry
```

Expected genuine RED: the current execution ignores the installed guard, retains
the provisional checkpoint success, and fails the unchanged `calls == [1]`/denial
assertions. Require actual call-phase assertion evidence; if setup/validation
fails first, do not claim RED and do not implement. Have the parent review the
observed failure and any necessary test-only correction. This is a missing
dispatch-guard behavior reproduction, not proof of seven-tool execution yet.

- [x] Restore the exact donor constructor form `dynamic=True, reference_guard=guard`
  and remove the bootstrap assignment/comment using apply_patch. Recheck the
  adapted Session blob `54b12b4...`. Keep the behavioral RED output and exact
  temporary diff in the review record; never count the subsequent keyword error
  as another behavioral RED or leave a private-attribute bypass in final tests.
The parent expressly declined the full three-module preflight: missing-keyword
failures add no useful behavioral evidence. The historical command below is
**NOT RUN and NOT RELEASED**; it must not be treated as the next automatic step.
No fake GREEN, xfail/skip or assertion weakening is allowed:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_loop.py tests/agent/test_decision_binding_inputs.py tests/agent/test_decision_binding_session.py
```

## 6. Task 2 — integrate the five production files, then freeze

**Files:** The five production paths in section 2 only. The three test paths
must be at their final selected/adapted blobs before GREEN.

- [x] Parent accepts the genuine RED and releases extraction. Verify baseline
  blobs again; read these complete immutable production bodies:

```powershell
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:src/agent/harness/decision_bindings.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:src/agent/harness/decision_policy.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:src/agent/runtime/run_session.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:src/agent/harness/decision_continuation.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:src/agent/harness/decision_loop.py
```

- [x] Use apply_patch to install only these exact bodies as one five-file patch.
  Do not execute a partially integrated tree. Their full blobs are the exact
  implementation specification; do not reconstruct them from historical prose.
- [x] Verify all eight final manifest blobs (seven exact donor, one adapted
  Session test), LF endings, empty index, and `git diff --check`. Preserve every
  section 3 prerequisite blob; inspect a complete diff against aligned main.
- [ ] Independently inspect the section 4 contracts against source, especially
  the Session-owned copy, denied checkpoint retry and first-reason latch. Confirm
  `INITIAL_TOOLS`, Web callers and ordinary v6/v7 remain unmodified.
- [ ] After explicit test-slot grant, first run the identical ordered three-module
  command from Task 1 (which includes the checkpoint node). Expected GREEN is
  genuine assertions passing with final constructor paths, not a promised count.
  Capture actual failures/warnings/skips/exit codes; no outcome is preclaimed.
- [ ] Freeze the eight source/test blobs and runner for independent SOURCE/SPEC.
  Any new dependency/behavior defect stops release for parent review and a
  bounded new RED; do not edit producers, fixtures, guards or obligations for green.

The input suite replaces the one parametrized historical gap test's four cases
(RAG/user-target times expired/unavailable) with same-context actual loop success.
Retain the expired reference, browser metadata, head identity, zero fabricated
roles and one actual Session attempt. Other input-suite stale-selection comments
are historical explanatory text; the changed executable test, not those comments,
defines this slice's required same-context behavior. The negative consumed/
foreign-reference and reverse-ancestor tests must continue to reject.

## 7. Task 3 — exact source-selected regression

**Files:** Read/run the following existing tests plus the new loop test. No edits
to the 55 regression-only modules are authorized by selecting them.

Selection: preserve the prior admission plan's **exact38 in its original order**
(positions 1-38), then append binding-loop (39) and the 17 affected modules
(40-56), followed by the landed empty-journal integration (57) and parser-role
regression (58). Neither addition was already selected: **58 unique paths**,
preserving the complete previous 56 order. Landed PR93 contains 57; the donor
supplies binding-loop. After this test preparation all 58 paths exist locally.
`test_decision_owned_call.py` exists only in the current integration history and
must not be replaced by the donor's duplicated definitions. This is filename
verification, not pytest collection.

Reasons for additions:

- 39: actual opt-in graph, all seven tools, both decision modes, owned boundaries.
- 40-42: modified configuration/snapshot/claim paths, SQLite nonce semantics,
  exact message/history/fingerprint and legacy replay.
- 43-44: graph clarification and bounded schema correction retain old behavior.
- 45-48: callback/seal/terminal identity, normalized envelope/native limits,
  migration/default finish behavior and unavailable parser provenance.
- 49: typed family fixture is now used by actual loop and review rendering;
  importing family_row alone is not running its contract tests.
- 50-52: same policy prompt/loop/Session feed ordinary A2 capability, budget,
  finalization and revision7 continuation paths.
- 53: new B prompt must still fit actual transport/history limits; no transport
  limit expansion or normalization bypass.
- 54-56: current Web runtime constructs default loops and consumes lifecycle/
  selected-reference behavior; run its existing contracts without enrolling B.
- 57: newly landed empty-journal closure regression imports the selected input
  helper; retain four whole-batch obligations and zero scientific dispatch.
- 58: newly landed analytical-clause parser underlies journal reduction; retain
  full-query intent, explicit unknown/multiple-target guards and bounded scanning.

- [ ] After SOURCE approval and explicit parent slot grant, invoke the approved
  launcher once with this exact ordered argument vector. The complete command
  prefix is `& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B
  scratch/ordinary_chat_offline_runner.py`; each following line is one positional
  argument in order. No `-k`, skipped modules, replacement targets or hidden probe:

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
```

- [ ] Record the fully expanded command, branch/HEAD/aligned-base identities,
  eight source/test blobs, SHA-256 of runner and all58 modules before/after,
  process handle, terminal exit, duration, actual pass/fail/error/skip counts and
  warnings. Label all fixtures appropriately. Do not infer collection from filenames
  or runtime dependencies from import text.
- [ ] Poll the same handle until actual terminal; observation timeout is not a
  test failure or permission to restart. No parallel scientific runs, hidden
  retries, assertion edits, warning suppression or substitution with older tests.
- [ ] Release the sole slot only after terminal and hash/scope checks. If the
  ordered union exposes fixture ordering/import contamination or isolation issues,
  preserve the evidence and ask the parent for a reviewed correction; do not
  silently reorder or weaken the regression.

## 8. Task 4 — independent reviews and later publication controls

- [ ] Independent SOURCE/SPEC checks all eight full files, the adapted Session
  extraction, prerequisite tree equality/alignment, current dependency preservation,
  RED classification and dormant scope. This audit is not that approval.
- [ ] Fresh independent QUALITY reviews the unchanged freeze; parent explicitly
  transfers the sole slot for one identical exact58 repeat. Record its real
  terminal evidence and before/after hashes separately from implementer results.
- [ ] After parent authorization, perform source compilation and scoped,
  filename-only credential scanning under the approved isolation rules. Full
  compileall/full-suite/CI checks required by repository policy are separate
  release gates; not performed by this source audit. If a local check is held,
  record the hold rather than calling it passed. No deployment/health/real/API
  acceptance is needed or authorized by this dormant source integration.
- [ ] Parent reviews both reviews, all actual evidence, full diff and exact
  manifest before release to commit/publish. Stage only the explicit eight
  source/test paths and this plan, never `git add -A`. Suggested single-theme
  commit: `feat: integrate dormant B1 model-selected loop`. Commit to this task
  branch, never main; no commit is made in the current task.
- [ ] Parent alone executes publication after the required gates, opening one draft PR against actual
  main, describing scope, dependencies, exact validation, risks and remaining
  gates. Attach the actual PR to the task when created. Do not reuse PR93 as this
  implementation's PR and do not manufacture a URL/number now.
- [ ] Obtain exact-head required CI and full collection evidence and resolve
  all review findings. Review state must be fully paginated, not a first-page
  sample; required checks/protection are revalidated at release, not inferred
  from a prior run ID or historical pass count.
- [ ] Within this target's scope, the latest user instruction grants default
  authorization to the parent; no repeated item-by-item or per-PR user permission
  request is needed. This does not waive exact-head CI, complete review/thread,
  fresh head/base or reviewed/landed full-tree gates. Parent alone coordinates
  push/merge; this worker has no push/merge authority. Preserve the dormant
  profile through landing; no Web activation/deployment follows.

## 9. Audit evidence and handoff

Read AGENTS.md, docs/PROJECT_STANDARDS.md and writing-plans; used the existing
approved design, not a fresh feature redesign. Read every donor line of all
eight selected files and the relevant original design/Task4B plan sections.
Used local Git object/path/diff/history inspection, PowerShell file reads/rg,
Test-Path inventory and .NET in-memory text/blob hashing only. Long tool outputs
for selected full-file reads were split into bounded ranges; no truncated read
is treated as the complete eight-file source audit.

Recommendations delegated by the user: keep exactly this source slice; preserve
current Session de-duplication; require actual behavior RED plus legacy gap
characterization before production extraction; use the originally exact56
source-selected union, now extended to exact58 by the landed-baseline audit;
keep all later revision8/Web work excluded. Production remains held.

At the initial drafting audit, implementation/tests/probes/compilation/runner/
network/commit/push/PR were **NOT RUN / NOT PERFORMED**. Section 10 records the
subsequent documentation-only release; sections 1 and 11 supersede its historic
pending-landing state. This preparation runs no tests and makes no next-loop
pass, runtime-readiness or P7/P8 completion claim.

## 10. Independent SOURCE plan approval and documentation-only commit release

Historical pre-landing record; current authorization and pins are in sections 1
and 11. Do not interpret this earlier slot/landing status as current.

2026-09-27, parent/user-reported review: independent reviewer **Mill SOURCE
APPROVES** this plan, with no findings. The review verified the exact eight-file
manifest, the adapted 549-line Session test blob
`54b12b4cc2d46b93998a6d7911e4d15359f0c66f`, and the exact56 unique regression
paths preserving the prior38 order. This is plan/source approval, not test
execution, implementation approval, QUALITY completion or scientific acceptance.

The parent authorizes appending this review and committing **only this plan** on
`codex/b1-model-loop-integration`. This supersedes the earlier documentation
commit hold only; no source/test/runner edits, Python, tests, slot acquisition,
push or PR creation are released. The parent retains the sole local science slot
for isolated workflow contracts after CI-amendment review.

Parent reports PR93's six source/test blobs remain unchanged. The independent
core-CI capacity amendment `5bee46f` has local dual review and is published in an
incoming PR; its landing is not established here. It changes only the core CI
budget from 600 to 1200 seconds: **not a performance fix and not a production
deadline change**. No remote status, successful CI rerun or merge is inferred.

Implementation remains **HOLD** until PR93 actually merges, the reviewed/landed
tree and amended prerequisite identities are re-pinned, this worktree is aligned
and re-audited, and the parent explicitly releases the next stage. Mill's plan
approval does not bypass these gates or grant a test slot. Earlier NOT PERFORMED
statements describe the drafting audit; only the separately authorized plan-only
commit follows this append.

## 11. Actual landing re-pin and three-test preparation handoff

Preparation snapshot before the subsequent explicit two-node release in section 12.

2026-09-27: parent explicitly released this plan update and only the original
three manifest tests. Local HEAD remains
`f99cf7efb9e92632e4f6b961363f5a5730a1a179`, based on actual PR93 squash
`5d36eee2977152fe5047dc21e260500d6c965c4b`; reviewed/landed full-tree equality is
`a3ebb7b758df36daad66197928a2700c7b79a6c6`. The preserved admission-plan blob is
`33cde99627627fd80591ba56fa55de02bd3c232a`, identical to landed main.

Prepared with apply_patch, checked by Git blob hashing (no Python/import/collection):

| Prepared test | Verified Git blob | Exact change |
|---|---|---|
| `tests/agent/test_decision_binding_inputs.py` | `bd377facce7f997572fb9a51a33e46451ed77205` | Full selected donor; +25/-17 |
| `tests/agent/test_decision_binding_loop.py` | `bb021cb19090967f9a547823b1682e444463ebf3` | Full selected donor; 666-line new file |
| `tests/agent/test_decision_binding_session.py` | `54b12b4cc2d46b93998a6d7911e4d15359f0c66f` | Exactly +48/-0 guard block; 549 lines; no duplicated owned-call tests |

All five production manifest files remain at their baseline blobs in section 2,
not the donor targets. Parser `2ec6087...`, empty-journal test `6ceed90...`,
parser-role test `8033991...` and owned-call test `25683ce...` remain byte-identical
to landed main. Only this plan and the three manifest tests are changed; the new
loop file is untracked, and nothing is staged, committed or pushed.

Prepared eight-file manifest SHA-256:
`765cb9976d24893302de9196a36c129265a287a4bcabff96ea48445845dac27c`.
Definition: section 2's eight paths in table order, each serialized as
`path<TAB>current Git blob<LF>`, UTF-8 without BOM, including the final LF.
This identifies five unchanged production baseline blobs plus the three prepared
test blobs, not a completed donor integration. Read-only inventory confirms
58 distinct existing module paths and byte-identical first-56 path ordering.
Git whitespace checks reported no errors; these are static checks, not collection.

The source-exact test preparation is **ready for parent review, not executed RED**.
The Session constructor remains in final donor form; the temporary constructor
bootstrap in section 5 has not been installed. After explicit parent RED/slot
authorization, use that reviewed test-only bootstrap to exercise the real
checkpoint behavior, record actual call-phase assertion failure, then restore
the exact prepared Session blob. A missing keyword or fixture/setup failure is
not behavioral RED. No production edit follows without parent review/release.

Parent reports Wegener `8486` terminal and slot released; this worker neither
claims nor uses that slot. Runner creation/edit/execution and every Python/test
process remain held pending the separate grant. The 58-module selection preserves
all previous 56 positions and appends journal integration then parser coverage.

Authorization consistency: the latest user instruction provides default parent
authorization within this target's scope, superseding the old section 8 demand
for fresh per-PR user permission. Exact-head CI, reviews/thread checks and full
tree gates remain mandatory. Parent alone executes publication/merge; this worker
has no push/merge authority, and this preparation includes no commit or push.

## 12. Authorized two-node execution: terminal evidence and slot release

Parent approved the three test diffs/blobs and the section 5 bootstrap, then
explicitly transferred the sole LOCAL SCIENCE slot for exactly these two runs.
No full three-module preflight, production modification, retry or additional
test command was performed. Both commands ran in this worktree at unchanged
HEAD `f99cf7efb9e92632e4f6b961363f5a5730a1a179`.

The ignored `scratch/ordinary_chat_offline_runner.py` was created with apply_patch
from the admission worktree's approved runner, changing only its literal REPO
from `D:/MedChat/molecular_chat_system_worktrees/b1-admission-acceptance-integration`
to `D:/MedChat/molecular_chat_system_worktrees/b1-model-loop-integration`.
Source SHA-256: `6D49050C00D66642F2045828AAAE927C7254B8C282ACCFC0EC1E24A7AF5623AD`.
Actual runner SHA-256 before/after both runs:
`1317F77937C508A2E99DBBA693A4E046672B8D5B583229B03D074323A3E1508B` (4723 bytes).
PowerShell/.NET compared all actual bytes both forward and after reverse literal
replacement: both equal. No runner logic, inherited host configuration or asset
discovery was added; the approved runner retains its synthetic environment/cwd
and socket ban. `git check-ignore` confirms the runner is ignored.

### Command 1: legacy rejection baseline, not behavioral RED

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_loop.py::test_actual_legacy_graph_seven_tool_gap
```

Started once; initial chunk `40382f`, handle `90652`, then polled that same handle
to terminal chunk `84d326`, exit **0**. Full combined pytest/runner output:

```text
...                                                                      [100%]
============================== warnings summary ===============================
<frozen importlib._bootstrap>:241
  <frozen importlib._bootstrap>:241: DeprecationWarning: builtin type SwigPyPacked has no __module__ attribute

<frozen importlib._bootstrap>:241
  <frozen importlib._bootstrap>:241: DeprecationWarning: builtin type SwigPyObject has no __module__ attribute

<frozen importlib._bootstrap>:241
  <frozen importlib._bootstrap>:241: DeprecationWarning: builtin type swigvarlink has no __module__ attribute

-- Docs: https://docs.pytest.org/en/stable/how-to/capture-warnings.html
3 passed, 3 warnings in 8.97s
ORDINARY_PYTEST_EXIT=0
```

### Command 2: genuine checkpoint guard behavioral RED

Only after command 1 terminated, apply_patch installed exactly the section 5
bootstrap in the single checkpoint test. Relative to final `54b12b4...`, the
complete temporary delta was:

```diff
     session = WorkflowRunSession(old.orchestrator, AgentContext('fixture', 'guard-checkpoint'),
-        [], {tool.name: tool}, dynamic=True, reference_guard=guard)
+        [], {tool.name: tool}, dynamic=True)
+    # RED-only constructor bootstrap; current execution does not consult this slot.
+    session._reference_guard = guard
```

Temporary Session Git blob: `455e3e89833d8b4b7ee2a9b3fe24dfbd6c14318e`;
SHA-256: `A3D53558247F0772D9B45606DD55769D0F1BC4D1F8347E2D2BA0AAD866664259`.
Assertions and real Session/settle_action behavior were not changed.

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_session.py::test_reference_guard_replaces_provisional_checkpoint_before_reentry
```

Started once; returned directly at terminal chunk `a3402a`, exit **1**, no live
handle or restart. Complete output (apart from insignificant display spacing):

```text
FORDINARY_FAILURE phase=call node_sha256=5f567d01a6a2ebe5976bb631a1d0999debe1c74fc65539087d1df43b34cbe755
                                                                        [100%]
================================== FAILURES ===================================
_____ test_reference_guard_replaces_provisional_checkpoint_before_reentry _____
D:\MedChat\molecular_chat_system_worktrees\b1-model-loop-integration\tests\agent\test_decision_binding_session.py:77: in test_reference_guard_replaces_provisional_checkpoint_before_reentry
    assert calls == [1] and not tool.calls and session.tool_attempt_count == 0
E   assert ([] == [1]
E
E     Right contains one more item: 1
E     Use -v to get more diff)
1 failed in 1.77s
ORDINARY_PYTEST_EXIT=1
```

This is actual call-phase assertion RED: the installed guard was not called
(`calls == []`, expected `[1]`). It is not collection/setup failure or a missing
constructor keyword. Later denial assertions were not reached; do not report
them as separately observed failures or claim seven-tool integration success.

Immediately after terminal, apply_patch restored the donor constructor and
removed the bootstrap. Full file hash verifies exact final Session blob
`54b12b4cc2d46b93998a6d7911e4d15359f0c66f`. All eight manifest blobs are unchanged
from the prepared manifest, whose SHA-256 remains
`765cb9976d24893302de9196a36c129265a287a4bcabff96ea48445845dac27c`.
Runner hash is unchanged. Parser `2ec6087...`, empty-journal `6ceed90...` and
parser-role `8033991...` remain unchanged; `git diff origin/main -- src .github
tests/conftest.py` is empty. Whitespace checks report no errors. Nothing staged,
committed or pushed.

**Both commands terminal; sole LOCAL SCIENCE slot explicitly RELEASED.** Ready
for parent review of genuine RED. No further tests, production changes or runner
execution are authorized by these completed two commands.

## 13. Parent-accepted RED and exact eight-file source freeze

Parent read the complete section 12 evidence, accepted `phase=call`, `calls=[]`
versus `[1]` as the missing guard behavior, retained the three legacy denials and
verified restored Session blob `54b12b4...`. Parent then explicitly released
source-only extraction of the existing five production manifest files, with
apply_patch, full-blob checks and own-plan recording only. This supersedes the
production hold for that extraction, not the test-slot or publication holds.

No baseline conflict or interface divergence was found: immediately before the
patch, all five baseline blobs matched section 2 and the donor parent. Applied
the exact donor delta, without touching dependencies or adapting production:

| Frozen production path | Verified donor-exact Git blob |
|---|---|
| `src/agent/harness/decision_bindings.py` | `0ebd9aa7aa046aaa7a7f9c8275d761638236cf48` |
| `src/agent/harness/decision_continuation.py` | `b44a0cbcbab7d13286c2c3c4fe94a65f4b5750af` |
| `src/agent/harness/decision_loop.py` | `251cd452ddfc69ad442de82dd4818bcebcb4a2ed` |
| `src/agent/harness/decision_policy.py` | `52d723de6901597b9b2e2478d2078ad959444089` |
| `src/agent/runtime/run_session.py` | `94b03a668834a2a994bde8157b603ec6d28fde1c` |

The three tests remain `bd377facce7f997572fb9a51a33e46451ed77205`,
`bb021cb19090967f9a547823b1682e444463ebf3` and
`54b12b4cc2d46b93998a6d7911e4d15359f0c66f` in section 2 order. All eight full
files now equal the reviewed targets: seven exact donor blobs and only the
approved de-duplicated Session adaptation. Frozen manifest SHA-256, using the
same eight ordered `path<TAB>Git blob<LF>` rows defined in section 11:
`bcd07dabc6f7330a6b88e73ee022bc518ceeda03fec088329dd44eeb66c7cf96`.

Thirteen explicitly preserved paths were rehashed against landed main: parser,
empty-journal test, parser-role test, RAG/target contracts, RAG tool/service,
reverse owned source/tool, owned execution helper, typed adapters, owned-call
tests and the parent admission plan. All remain equal to main. The 58-module
selection is unchanged; no Web caller, producer, schema, asset or environment
configuration was changed. Git whitespace checks report no errors; the index
is empty and only the eight-file manifest plus this own plan differ locally.

**Source freeze READY; GREEN NOT RUN.** No Python, test, runner execution or new
scientific process occurred during extraction. Sole slot is with Wegener;
wait for explicit parent transfer before the ordered three-module GREEN. Do not
start the expanded 58 selection automatically. No commit, push or merge performed.

## 14. Authorized exact three-module GREEN: terminal and hashes

After Wegener `42865` / `dfb617` terminated and released its slot, parent explicitly
transferred the sole LOCAL SCIENCE slot for this one ordered command:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_loop.py tests/agent/test_decision_binding_inputs.py tests/agent/test_decision_binding_session.py
```

Run once at unchanged HEAD `f99cf7efb9e92632e4f6b961363f5a5730a1a179`.
Initial chunk `db96a8`, actual session **31449**; polled the same session through
`020d44`, `2fd685`, `3dca83` to terminal **b0c4a3**, exit **0**, no restart.
Terminal: **327 passed, 3 warnings in 212.98s (0:03:32)**;
`ORDINARY_PYTEST_EXIT=0`. The warnings are the existing SwigPyPacked, SwigPyObject
and swigvarlink `__module__` deprecations, not suppressed.

Before and after the run, all eight Git blobs matched the frozen manifest and
all nine actual SHA-256 values (eight files plus runner) compared identical.
Manifest before = after:
`bcd07dabc6f7330a6b88e73ee022bc518ceeda03fec088329dd44eeb66c7cf96`.
Runner SHA-256 before = after:
`1317F77937C508A2E99DBBA693A4E046672B8D5B583229B03D074323A3E1508B`.
Full per-file before/after hashes are retained in the tool output; no source or
test correction occurred. Parent's separately reported 347-source/script
in-memory compilation, diff check and filename credential scan (rg exit 1,
no matches) are not this worker's runs and do not replace the scientific tests.

**Session terminal; sole LOCAL SCIENCE slot RELEASED.** Three-module GREEN ready
for parent review. Hume SOURCE was reported in progress; no approval is inferred.
No 58-module run, commit or push started.

## 15. Hume P2 amendment: post-graph cancellation RED only

Parent and this worker verified that final `await acceptance` and post-terminal-
metadata `await boundary` sit outside the graph's CancelledError handler; outer
`run` only drains. Parent releases this short amendment, new loop regression and
its focused RED run with sole LOCAL SCIENCE slot, not a production fix or 58 run.
The original section 14 **327 passed** remains evidence for the unchanged donor
freeze; it is not overwritten or represented as covering this new regression.

New node: `test_tail_cancellation_drains_owned_worker_and_persists_one_cancelled_terminal`,
parameterized by tail acceptance/boundary and native/json (four cases). Real graph
execution and real validation are forwarded intact. A threading barrier holds
the actual owned validation worker after graph return; task.cancel must keep
owner/run pending until release, then drain and persist exactly one cancelled
terminal with no additional tool/model calls. Finally cleanup always releases
the worker and awaits the run. No mocked CancelledError, timeout change, disabled
validation or producer edit. Run only this node through runner `1317F779...1508B`;
report actual RED for parent review before any production change. Only the loop
test may diverge from the earlier test manifest during this amendment.

Focused command, run once (no original-327 or full-58 rerun):

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_loop.py::test_tail_cancellation_drains_owned_worker_and_persists_one_cancelled_terminal
```

Actual session **77285**, initial chunk `e576fa`, same-handle terminal `3ef772`,
exit **1**: **4 failed, 3 warnings in 9.85s**, `ORDINARY_PYTEST_EXIT=1`.
All four parameter cases (`tail_acceptance-native`, `tail_acceptance-json`,
`tail_boundary-native`, `tail_boundary-json`) report `ORDINARY_FAILURE phase=call`
at loop-test line 770:

```text
assert status == 'cancelled', (status, type(result).__name__, memory_terminal, stored_terminal)
AssertionError: ('running', 'CancelledError', [], [])
assert 'running' == 'cancelled'
```

Each reached the post-drain status assertion: pre-release owner/run-pending and
no-terminal checks passed; after release the real worker exited, root finished,
owner settled with zero pending roots, exactly one property call and two scripted
model requests remained. Cancellation escaped while SQLite status stayed running
and both in-memory/persisted terminal-event lists were empty. Subsequent desired
cancelled-result assertions were not reached and are not claimed as observed.
Warnings are the same three SWIG `__module__` deprecations. No restart or fixture
repair occurred; finally cleanup awaited every run after releasing its worker.

New loop-test blob before = after: `d298090d240f441a9739c9da03bb88c99b1c97b1`;
SHA-256 `B91F60FF053D4315DB07DD9960116A4AA4AD37462217553E86274EA23839D490`.
All other seven manifest blobs remain the prior freeze, unchanged during the run;
runner remains `1317F77937C508A2E99DBBA693A4E046672B8D5B583229B03D074323A3E1508B`.
No production modification. **Sole LOCAL SCIENCE slot RELEASED; RED ready for
parent review and separate production-fix release.**

## 16. Hume P2 narrow fix, four-case GREEN and amended manifest

Parent accepted the four real cancellation RED cases and separately released
only `decision_loop.py`'s two tail waits. Added 16 lines versus donor: explicit
`except asyncio.CancelledError` projects cancelled outcome/error, clears waiting
and successful answer, and sets acceptance `satisfied=False`,
`finish_eligible=False`, `reason_codes=['cancelled']`. The first catch re-raises
on the legacy path. The second refreshes already-written acceptance metadata
through existing `persist('terminal')`; existing `finish_dynamic` alone handles
the terminal event/run status. No validation, owner draining, real tool_results,
noncancel handler, timeout or publication barrier was removed or added.

Ran the exact section 15 node once, with its four cases unchanged:
session **79946**, initial chunk `fef6e5`, same-handle terminal **785efb**, exit
**0**: **4 passed, 3 warnings in 8.03s**, `ORDINARY_PYTEST_EXIT=0`.
Warnings are the same three SWIG deprecations. No extra node, original-327 rerun
or expanded58 run occurred. Original 327-pass and P2 four-failure evidence remain
separate; do not combine them into a full amended-tree regression result.

Current eight-file manifest (supersedes the source-exact freeze in section 13):

| Path | Current Git blob | Basis |
|---|---|---|
| `src/agent/harness/decision_bindings.py` | `0ebd9aa7aa046aaa7a7f9c8275d761638236cf48` | Donor exact |
| `src/agent/harness/decision_continuation.py` | `b44a0cbcbab7d13286c2c3c4fe94a65f4b5750af` | Donor exact |
| `src/agent/harness/decision_loop.py` | `963682752927ff6e52ce235158699526691129e2` | Donor + two cancellation catches, +16/-0 |
| `src/agent/harness/decision_policy.py` | `52d723de6901597b9b2e2478d2078ad959444089` | Donor exact |
| `src/agent/runtime/run_session.py` | `94b03a668834a2a994bde8157b603ec6d28fde1c` | Donor exact |
| `tests/agent/test_decision_binding_inputs.py` | `bd377facce7f997572fb9a51a33e46451ed77205` | Donor exact |
| `tests/agent/test_decision_binding_loop.py` | `d298090d240f441a9739c9da03bb88c99b1c97b1` | Donor + four-case P2 regression |
| `tests/agent/test_decision_binding_session.py` | `54b12b4cc2d46b93998a6d7911e4d15359f0c66f` | Original approved 549-line adaptation |

Manifest SHA-256 (same ordered path/TAB/blob/LF convention):
`a676a81cc3096e09dc5dbd5d8836ad02909fa5be416a4c077d5a50db1e7b8be0`.
Run-before/after SHA-256 matched for production loop
`881C5EBBABB07BB176BC65C6D068A63A37896F71E161E078D691507EC854F794`,
loop test `B91F60FF053D4315DB07DD9960116A4AA4AD37462217553E86274EA23839D490`
and runner `1317F77937C508A2E99DBBA693A4E046672B8D5B583229B03D074323A3E1508B`.
Remaining six manifest blobs stay at the prior freeze; no additional source/API/
model/asset or fixture edit. Whitespace checks pass; index remains empty.

**Terminal; sole LOCAL SCIENCE slot RELEASED.** Ready for parent/Hume source
re-review. Expanded58 requires separate release after that review; no automatic
run, staging, commit or push.

## 17. Hume SOURCE approval and exact58 terminal evidence

Parent reports Hume overall SOURCE APPROVE, no P1/P2 remaining, for manifest
`a676a81c...` / loop `9636827...` / loop test `d298090...`. After the parent's
other runs terminated, parent explicitly transferred the sole LOCAL SCIENCE slot
for one exact58 run. Executed the approved Conda `-I -S -B` runner once with all
58 section 7 paths as explicit arguments in unchanged order (original56, then
empty journal and parser). The fully expanded command is retained in tool output;
there was no path filter, reduction, restart or second run.

Actual session **89924**, initial chunk `86ce96`; parent independently identified
runner-wrapper PID **56480**, creation **06:04:08**. Polled only that session;
reported progress 9/36/62/68/74/78/95/97 percent before completion. Parent's
interrupt stopped a waiting tool call only, not the scientific process. Resumed
observation of the same session and received real terminal chunk **58e614**,
exit **0**:

```text
6107 passed, 7 warnings in 916.84s (0:15:16)
ORDINARY_PYTEST_EXIT=0
```

Warnings: three SWIG `__module__` deprecations and four FastAPI `on_event`
deprecations from the Web readiness fixture. No suppression, failure or skip was
reported. This is the amended freeze's actual expanded result, not an inference
from the historical 327-pass run or parent's compilation/credential checks.

Before/after captures include all eight manifest files, all58 targets, 14 explicit
preservation paths and runner, deduplicated to **75 files**. Every Git blob and
actual SHA-256 is unchanged; full per-file hashes are retained in tool output.
Hash-set digest before = after:
`732e45ff843a103e861d77823da7a0cc2a9ded37d87c0210d3bbe76c0100e011`.
Definition: UTF-8 `path<TAB>lowercase file SHA-256<LF>`, final LF included, first
occurrence order of manifest + section7 exact58 + captured preservation14 + runner.
Manifest remains `a676a81cc3096e09dc5dbd5d8836ad02909fa5be416a4c077d5a50db1e7b8be0`;
runner remains `1317F77937C508A2E99DBBA693A4E046672B8D5B583229B03D074323A3E1508B`.

**Run terminal and hashes verified; ready for parent review.** No live run remains
owned by this session. Per the parent's latest explicit no-release instruction,
this worker does not unilaterally transfer the slot; parent controls reassignment.
No further test, source edit, staging, commit or push performed.

## 18. Fresh QUALITY and parent publication release

After the author's terminal, parent explicitly transferred the sole local
scientific slot to independent Halley. Same approved runner and ordered58,
no restart or narrowed selection: session **77863**, wrapper PID40244 and child
PID56340; real terminal **bd9896**, exit0, `ORDINARY_PYTEST_EXIT=0`:
**6107 passed, 7 warnings in 971.91s**, no failures/skips. All frozen75 file
SHA-256/Git blobs, including the section17 plan, matched before/after. Processes
were observed exited before release. QUALITY APPROVE, no blocking P1/P2, limited
to this dormant/offline scope. Hume SOURCE approval remains the prior independent
source gate; neither reviewer claims real-provider or full-Web acceptance.

Parent additionally compiled all347 src/scripts Python sources in memory with
stdlib only (no project imports or bytecode writes), checked whitespace and ran
a filename-only scoped credential-pattern scan with no matches. The scan is a
limited check, not proof of absence of every possible secret. Parent's remote
read-only check found main still5d36eee and no open PR at that time.

Parent now releases exact staging/commit and a unique draft PR for these eight
implementation files and this plan only. No worktree-wide add or production
activation. Final head-specific CI, full collection, paginated review/thread
checks, fresh main/head guards and reviewed/landed tree equality remain required
before reporting merge. Later publication/revision8/Web/B2/C/P8 remain incomplete.
