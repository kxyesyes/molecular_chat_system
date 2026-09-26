# B1 Model-Selected Loop Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Independent SOURCE/SPEC and fresh QUALITY reviews are required. This draft grants no implementation, test-slot or publication authority.

**Goal:** Integrate the reviewed Task4B seven-tool, evidence-bound model decision loop into the current B1 foundation, as a dormant server-only profile.

**Architecture:** Extend the existing ModelDecisionLoop, resolver and WorkflowRunSession; retain the existing registry/adapters, immutable input journal, acceptance evaluator, ledger and WorkerOwner. Preserve default legacy/A2 behavior and all current producer/security fixes. Do not add another planner, engine, scheduler or tool sequence.

**Tech Stack:** Python 3.10, Pydantic 2, existing LangGraph decision graph, SQLite, pytest, RDKit and temporary RAG/reverse sources. Scripted model decisions and typed ADMET/activity/target fixtures are explicitly synthetic, not live scientific acceptance.

---

## 1. Status, authority and hard gates

This is a **documentation/source audit only**, dated 2026-09-27. The only current
write is this file. No Python process, tests (including collection), probes,
application imports, compilation, runner creation, environment/asset/model
discovery, network access, source/test edits, staging, commit or push is permitted
by this task. All commands in execution tasks below are future instructions.

Audited worktree: `D:/MedChat/molecular_chat_system_worktrees/b1-model-loop-integration`.
Branch: `codex/b1-model-loop-integration`.
Initial worktree/index: clean.
Reviewed PR93 head / local HEAD: `a84aa921b20de2417c830b2717bba7f77e54952c`.
Reviewed tree: `7977ae132abe215bb14643c7bb3a1059ee461aab`.
PR93 CI `36268575293` is **failed per the latest parent/user update**: core job
`108478025405` exited 124 at exactly 600 seconds; collection succeeded with
`10742 = 10518 core + 224 Web`. Parent diagnoses timeout configuration; this
source-only task has not independently established the cause or queried remote
state. Successful collection is not a passing core run. The initial pending
status is superseded; landing/alignment/release gates remain closed. A reviewed
candidate is not a landed prerequisite. Any reviewed-head change for the CI fix
requires a fresh pin/tree/dependency check before later release.

Local read-only refs at audit time were `main=3b87853066069231891bad09efefd165ecf5af26`
and `origin/main=82322c3d0098d44082e732b41084c71b4d73c230`. Neither proves current
remote state or PR93 landing. Their trees differ from the reviewed PR93 tree.
Do not fetch, align, merge or interpret a local branch name as release authority
during this documentation task.

- [ ] Parent reviews this plan, including Session-test de-duplication, behavioral
  RED staging and the exact56 regression selection.
- [ ] Parent supplies actual PR93 landing and required CI/review evidence; verify
  the **complete landed commit tree** equals the reviewed PR93 tree, not just a
  selected-path diff. A mismatch requires reconciliation/review, not a waiver.
- [ ] Parent aligns this worktree with actual landed main and inspects the full
  diff. Apart from this draft, no unexplained source/test difference may remain.
  Recheck every manifest/dependency against that aligned baseline. Any later
  main changes require a renewed overlap audit; the table below is not permission
  to overwrite them.
- [ ] Parent explicitly releases test-file preparation and separately grants the
  sole local scientific/Python test slot and approved isolation runner.
- [ ] Observe genuine behavioral RED before production edits. Collection/setup,
  missing dependencies and an unexpected constructor keyword alone do not count.
- [ ] Freeze implementation for independent SOURCE/SPEC before expanded QUALITY;
  parent reviews results and explicitly releases each later stage.

No elapsed time, historical approval, CI completion elsewhere or publication of
this draft automatically advances a gate. Do not update another handoff file:
the user's one-document scope overrides the general handoff convention.

## 2. Immutable sources and complete eight-file manifest

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
The other seven final files must equal their selected donor blobs exactly.
This exception is not permission for further test adaptation.

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

- [ ] Satisfy section 1 gates; read AGENTS/PROJECT_STANDARDS again after alignment.
  Use read-only `git status --short`, `git rev-parse HEAD 'HEAD^{tree}'`,
  `git ls-tree`, `git show` and `git diff` to recheck complete blobs and scope.
- [ ] Read the exact complete test source (do not transcribe selected assertions):

```powershell
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:tests/agent/test_decision_binding_loop.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:tests/agent/test_decision_binding_inputs.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:tests/agent/test_decision_binding_session.py
```

- [ ] Apply the full loop/input tests with apply_patch. Add only the exact
  48-line Session block, preserving current de-duplication. Pin test blobs.
  Full authoritative blob references above specify all code, fixtures and
  parameters; no historical document extraction is needed.
- [ ] Parent separately approves/prepares the isolated launcher. The prior
  admission worktree's approved launcher is documented with SHA-256
  `6D49050C00D66642F2045828AAAE927C7254B8C282ACCFC0EC1E24A7AF5623AD` in
  `docs/superpowers/plans/2026-09-27-b1-admission-acceptance-integration.md`.
  This audit did not read/copy/run that ignored file. Later verify its full bytes
  and parent approval, substitute only literal REPO using apply_patch if released,
  compare full bytes and reverse substitution, and record the new hash. A
  documented historical hash is not a substitute for checking the actual runner.
  No raw pytest, new runner logic, environment change or asset discovery.
- [ ] With the sole slot explicitly granted, first run the donor's existing
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

- [ ] Temporarily use this complete body for the donor-named test in
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

- [ ] Run exactly this focused node, before changing any production blob:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_session.py::test_reference_guard_replaces_provisional_checkpoint_before_reentry
```

Expected genuine RED: the current execution ignores the installed guard, retains
the provisional checkpoint success, and fails the unchanged `calls == [1]`/denial
assertions. Require actual call-phase assertion evidence; if setup/validation
fails first, do not claim RED and do not implement. Have the parent review the
observed failure and any necessary test-only correction. This is a missing
dispatch-guard behavior reproduction, not proof of seven-tool execution yet.

- [ ] Restore the exact donor constructor form `dynamic=True, reference_guard=guard`
  and remove the bootstrap assignment/comment using apply_patch. Recheck the
  adapted Session blob `54b12b4...`. Keep the behavioral RED output and exact
  temporary diff in the review record; never count the subsequent keyword error
  as another behavioral RED or leave a private-attribute bypass in final tests.
- [ ] If parent releases the broader pre-integration run, run the three modules
  in the following order and classify each failure (behavior/interface/setup).
  No fake GREEN, xfail/skip or assertion weakening is allowed:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_loop.py tests/agent/test_decision_binding_inputs.py tests/agent/test_decision_binding_session.py
```

## 6. Task 2 — integrate the five production files, then freeze

**Files:** The five production paths in section 2 only. The three test paths
must be at their final selected/adapted blobs before GREEN.

- [ ] Parent accepts the genuine RED and releases extraction. Verify baseline
  blobs again; read these complete immutable production bodies:

```powershell
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:src/agent/harness/decision_bindings.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:src/agent/harness/decision_policy.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:src/agent/runtime/run_session.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:src/agent/harness/decision_continuation.py
git show 0064c30dce5d2c38aa4c655a45026e0baf66dd7e:src/agent/harness/decision_loop.py
```

- [ ] Use apply_patch to install only these exact bodies, in the displayed order.
  Do not execute a partially integrated tree. Their full blobs are the exact
  implementation specification; do not reconstruct them from historical prose.
- [ ] Verify all eight final manifest blobs (seven exact donor, one adapted
  Session test), LF endings, empty index, and `git diff --check`. Preserve every
  section 3 prerequisite blob; inspect a complete diff against aligned main.
- [ ] Independently inspect the section 4 contracts against source, especially
  the Session-owned copy, denied checkpoint retry and first-reason latch. Confirm
  `INITIAL_TOOLS`, Web callers and ordinary v6/v7 remain unmodified.
- [ ] After explicit test-slot grant, rerun the identical focused checkpoint node
  and the identical ordered three-module command from Task 1. Expected GREEN is
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
to the 53 regression-only modules are authorized by selecting them.

Selection: preserve the prior admission plan's **exact38 in its original order**
(positions 1-38), then append binding-loop (39) and the 17 affected modules
(40-56). Static `Test-Path`/Git inventory verified 56 unique paths: 55 exist in the
current worktree; only binding-loop is intentionally new and exists at the donor.
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
```

- [ ] Record the fully expanded command, branch/HEAD/aligned-base identities,
  eight source/test blobs, SHA-256 of runner and all56 modules before/after,
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
  transfers the sole slot for one identical exact56 repeat. Record its real
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
- [ ] Only with separate publication release, open one draft PR against actual
  main, describing scope, dependencies, exact validation, risks and remaining
  gates. Attach the actual PR to the task when created. Do not reuse PR93 as this
  implementation's PR and do not manufacture a URL/number now.
- [ ] Obtain exact-head required CI and full collection evidence and resolve
  all review findings. Review state must be fully paginated, not a first-page
  sample; required checks/protection are revalidated at release, not inferred
  from a prior run ID or historical pass count.
- [ ] No automatic merge. Only a later explicit authorization naming this PR
  and merge method, with every required CI/review gate satisfied, may release
  merge. Recheck head/base and then actual landed tree equality. Preserve the
  dormant profile through landing; no Web activation/deployment follows.

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
characterization before production extraction; use the exact56 source-selected
union; keep all later publication/revision8/Web work excluded. These choices are
written for parent review, not implemented by delegation.

Current implementation/tests/probes/compilation/runner/network/commit/push/PR:
**NOT RUN / NOT PERFORMED**. No test-pass, CI-success, landed-PR93, runtime-readiness
or P7/P8 completion claim. Parent plan review and prerequisite landing/alignment/
explicit release are the next gates; do not offer or begin execution while held.

## 10. Independent SOURCE plan approval and documentation-only commit release

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
