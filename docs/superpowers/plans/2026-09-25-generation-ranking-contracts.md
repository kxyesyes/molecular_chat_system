# Generation and ranking contracts implementation plan

> **For agentic workers:** after parent design approval, use subagent-driven-development or executing-plans with test-driven-development, task by task. This document is not authorization to execute. Parent owns integration/review/release; no commit, push or PR in the present design task.

**Goal:** add strict, non-projecting type boundaries for generation and candidate ranking without changing candidate canonicalization or scoring.

**Architecture:** two schema pairs/adapters in one domain-scoped module; reuse LegacyPythonToolAdapter's lifecycle and compatibility executor. Raw, normalized and failed-snapshot checks stay in its worker. Existing CandidateSet/domain owners retain scientific normalization.

**Tech Stack:** Python 3.10-compatible code, Pydantic 2 (including repository minimum profile), RDKit, pytest; no new dependency or frontend work.

**Design:** `../specs/2026-09-25-generation-ranking-contracts-design.md`, proposal at `68b8538` on `codex/generation-ranking-contracts`.

## File responsibilities and limits

| Action | Exact repository path | Responsibility |
| --- | --- | --- |
| Create | `src/agent/tooling/generation_ranking_contract.py` | GenerationInput/Output/ToolAdapter, RankingInput/Output/ToolAdapter, strict leaf/raw envelope views and a private two-tool worker-bound adapter |
| Modify | `src/agent/tooling/factory.py` | Imports and schema/adapter selection for exactly two tool names; no policy changes |
| Create | `tests/agent/test_generation_ranking_contract.py` | Input/output/metadata/snapshot preservation and lifecycle RED/GREEN tests |
| Create | `tests/agent/test_generation_ranking_contract_integration.py` | Offline real-producer shape, CandidateSet and workflow/registry transport tests |

No writes to producers, generic adapters, domain validators, workflow/Planner, other worktrees or runtime assets. Existing test fixture changes need an explicitly identified failing assertion and parent review; no broad fixture rewrite. Existing test paths below are regression inputs, not an assumed write allowance.

## Task 1 — characterize and establish meaningful RED

- [ ] Confirm branch/base/status, reread the design and upstream lifecycle changes after approval. Verify exact methods with `rg`; this baseline has no `CandidateSetValidator` symbol. No implementation against a presumed future API.
- [ ] Establish isolated offline baseline with the existing paths in Task 5. Do not run Web imports from the real-config cwd. Record passed/failed/skipped and dependency limitations, not only exit status.
- [ ] In the new contract test file add a counting tool and the two baseline-red tests below. They use current public registry methods, without requiring new symbols to exist. The outputs are deliberately malformed synthetic contract fixtures, not scientific results.

```python
from copy import deepcopy
import pytest
from src.agent.contracts import AgentErrorCode
from src.agent.tooling.factory import build_tool_registry

class CountingTool:
    def __init__(self, name, raw):
        self.name, self.raw, self.calls, self.inputs = name, raw, 0, []
    def execute(self, payload):
        self.calls += 1
        self.inputs.append(payload)
        return deepcopy(self.raw)

@pytest.mark.parametrize("name,raw", [
    ("llm_molecular_generator", {
        "success": True, "data": [{"smiles": "CCO", "source": 17}],
        "quality": {"requested_count": 1, "actual_count": 1},
    }),
    ("candidate_ranker", {"success": True, "data": {
        "requested_top_n": True, "ranked_candidate_count": 1,
        "top_candidates": [], "ranked_candidates": [],
        "unrankable_candidates": [],
    }}),
])
def test_rejects_malformed_observation_through_current_registry(name, raw):
    tool = CountingTool(name, raw)
    registry = build_tool_registry([tool])
    payload = ("generate 1 molecule" if name == "llm_molecular_generator"
               else {"metadata": {"docking_top_n": 1}, "outputs": {
                   "molecules": [{"smiles": "CCO"}], "properties": []}})
    try:
        result = registry.resolve(name).execute({"query": payload})
        assert result.error is not None
        assert result.error.code is AgentErrorCode.INVALID_OUTPUT
        assert tool.calls == 1
    finally:
        registry.close()
```

- [ ] Run `python -B -m pytest tests/agent/test_generation_ranking_contract.py -q -p no:cacheprovider` in the isolated environment. Expected baseline failure: unchecked observations are accepted, not a missing import. Capture this RED before creating the production module.
- [ ] Add parametrized input cases from the design routing table, recording exact producer input and call count. Cover direct string, query-only wrapper, direct structured generation, outer structured wrapper, full ranker input with text query, and ambiguous sibling/deeper wrappers. Test explicit null vs absent fields and preconstructed/mutated Pydantic inputs after schemas exist.

## Task 2 — strict validation views (minimal GREEN)

- [ ] Add the module's strict extra-allow validation base (`strict=True`, `extra="allow"`, `revalidate_instances="always"`); use supplied-field inspection, never serialize views into output. Define the six public names listed above. Keep optional fields nullable only where the design permits.
- [ ] Implement input routing exactly as the design table: recognize a single outer mapping query only when it has no siblings; distinguish domain mappings from text wrappers; preserve original containers, extras, immutable trust mappings and absent fields. Reuse validate_generation_count, finite-temperature checks and the producer's existing request normalization; do not reimplement grammar/evidence allowlisting.
- [ ] Add input RED cases: count 0/11/bool/string/fraction, nonfinite/bool/string temperature, malformed metadata/outputs, invalid top_n, string evidence instead of sequence and wrong row containers. Assert INVALID_INPUT before producer execution for type/count errors. For text grammar, request length and trust errors assert the existing producer classification and no model execution, not necessarily zero producer calls.
- [ ] Implement all supplied-field checks in the scientific map, raw/ToolResult envelope views and raw_result traversal. Exact raw/normalized states are separate views; map enum values without broadly enabling numeric/string coercion. Keep raw optional null defaults compatible and normalized containers strict.
- [ ] For serialized CandidateSets reuse `CandidateSet.from_dict` and the existing read-only `_revalidate_candidate_set(..., trusted_checkpoint=False)` under the worker. Distinguish CandidateValidationUnavailable from malformed output. Do not call mutating validate_tool_result on the returned observation; raw-list filtering remains downstream.
- [ ] Extend RED mutations to every named numeric/bool/count/identity field and every status (success, partial, failure). Cover both top_candidates and ranked_candidates, evidence-only records and the failure snapshot chain. Add success/error contradictions, malformed provenance/artifact fields, invalid elapsed_ms, cyclic and depth-17 snapshots. Unknown extension dictionaries must survive without being recursively interpreted.
- [ ] Add relational assertions for ranking counts/rank order/top prefix, uniqueness, missing/null consistency and named evidence count. No weighted-score recomputation or model authenticity checks. Run the new tests to GREEN for views only; adapter/factory failures remain expected until Tasks 3–4.

## Task 3 — worker-bound, non-projecting lifecycle

- [ ] Write event-controlled RED tests before adapter integration: block normalized observation validation and separately inherited data redaction; short deadline returns TOOL_TIMEOUT while a second call reports capacity exhaustion. Release the event in finally, wait for completion, then verify a later call succeeds. Assert validation/redaction thread identity is not the caller. Do not rely on sleep-only timing.
- [ ] Use this control-flow contract for the private shared adapter; domain subclasses select input routing and output views. This is a sequence specification, not replacement lifecycle code:

```text
_invoke_guarded(payload, caller_check):
  inherited guarded invocation invokes producer once
  callback: caller_check(raw) first, outside our exception translation
            validate raw + known snapshot chain
  existing compat normalization returns ToolResult
  inherited _normalize(result, None) performs data redaction in worker
  validate normalized ToolResult + known snapshot chain
  return original normalized ToolResult, not a view dump
_normalize(result, elapsed_ms): attach elapsed only if absent
_validate_output(result): return result (worker-checked or framework error)
```

- [ ] Translate only this module's own validation exceptions to fixed payload-free errors. Catch bounded domain parsing/type failures, not arbitrary caller exceptions. Keep unavailable canonical validation separate. Do not mutate schemas/specs with per-call payload, cache last-request proof, or call the producer twice.
- [ ] Add tests that caller raw_validator sees the untouched object first and its exceptions retain inherited classification; allow_retry=False and timeout never replay; unavailable/over-capacity execution invokes no producer; registration does no domain/model/file work; aliases/readiness/owner/close remain inherited.
- [ ] Compare accepted results to `execute_tool_compat` for the same raw fixture, excluding only elapsed time. Include dicts and ToolResult, partial-with-error/data, failed raw_result snapshots, explicit failure details, warnings, artifacts, evidence, quality, provenance and nested extensions. Redaction differences must match inherited behavior, not custom serialization. Contract failures contain no rejected payload.
- [ ] Re-run contract tests to GREEN. Verify generic adapters.py and all three existing contract modules remain byte-for-byte unchanged.

## Task 4 — factory wiring and actual-shape integration

- [ ] First add registry assertions that only the two names use new schema pairs/adapters; preserve tool version, aliases, molecular_design owner, timeout, retries=1, idempotence, side_effects and generator lazy readiness. Expected RED against baseline factory.
- [ ] Add imports and exact-name branches in factory input_schema/output_schema/adapter_class selection. Do not add a generic selector framework. Keep all unrelated selections intact; use the registry's resolve/as_mapping/close interfaces.
- [ ] In integration tests use real LLMMolecularGenerator with `_generate_with_retry` replaced by an in-memory row-returning spy, not an actual model/API. A local sentinel supplies model_name; no `.generate` call is allowed. Exercise request normalization/count/temperature/target helper errors and actual execute envelope. For valid chemistry use in-memory RDKit only. Test no-LLM and RDKit-unavailable failures without probing installed scientific assets.
- [ ] Exercise real CandidateRanker on this minimal in-memory fixture through direct and outer-wrapped registered inputs; assert equivalence to the producer and absence of new energy claims:

```python
payload = {
    "query": "prioritize these candidates",
    "metadata": {"docking_top_n": 2},
    "outputs": {
        "molecules": [{"candidate_id": "legacy-ethanol", "smiles": "OCC"}],
        "properties": [{"smiles": "CCO", "properties": {"qed": 0.72, "logp": 0.1}}],
    },
}
```

These descriptor values are explicitly synthetic scoring-test inputs, not a claim they are the computed descriptors of ethanol. Add separate real PropertyCalculator output for CCO to prove its full actual row is accepted; do not load ADMET/activity models.

- [ ] Parametrize missing/null/empty optional evidence; sparse ADMET backend sections; complete summary inputs; demo/fallback/no-identity activity; normalized_activity priority vs probability; well-typed but unusable risk counts; raw pIC50/value/family scores that must not become activity_score. Assert null optional scores/weights and missing_evidence unchanged. Malformed supplied optional types reject at the registered boundary but do not alter direct-producer tests.
- [ ] Test unknown/duplicate candidate evidence, invalid/duplicate canonical molecules and top_n larger than population retain producer error/results. Do not require hashed IDs for legacy ranker rows.
- [ ] Adapter plus existing AgentResultValidator integration: raw list/alias/string rows, duplicate OCC/CCO, invalid SMILES, excess rows, missing request count, shortfall, complete CandidateSet round trip, forged rejected records, zero-count failed sets and unavailable validation. Compare candidate IDs/source_index/metadata/source/provenance/quality/status and formatted output to the same existing validator path without the new adapter. A raw shortfall may be succeeded before this gate and partial afterward; no new status rewrite in the adapter.
- [ ] Exercise WorkflowOrchestrator and SpecialistDispatch input nesting, request-local temperature, and ranking's $.workflow bindings. Preserve input digest sensitivity and no shared-request mutation. Run the exact existing transport/workflow tests in Task 5.

## Task 5 — exact offline regression set and verification

Verified existing baseline paths (all are present, not guessed):

```text
tests/test_llm_molecular_generator.py
tests/agent/test_candidate_ranker.py
tests/agent/test_generation_temperature_transport.py
tests/agent/test_generated_candidate_validation.py
tests/agent/test_candidate_contracts.py
tests/agent/test_candidate_alignment.py
tests/agent/test_target_driven_design_workflow.py
tests/agent/test_binding_resolver.py
tests/agent/test_plan_compiler.py
tests/agent/test_planner_template_execution.py
tests/agent/test_tool_adapter_compat.py
tests/agent/test_tool_adapters.py
tests/agent/test_tool_registry.py
tests/agent/test_registration_consistency.py
tests/agent/test_activity_tool_contract.py
tests/agent/test_docking_tool_contract.py
tests/agent/test_rag_tool_contract.py
tests/agent/test_result_validator.py
tests/agent/test_delegated_session_parity.py
tests/agent/test_workflow_resume.py
```

- [ ] Run focused command below inside an isolated test process. Use temporary cwd/runtime/config paths set before import, clear inherited provider credentials without printing them, disable real services and prevent network access. `tests/conftest.py` isolates user config/session DB but is not by itself a complete asset/network sandbox. The existing `docs/superpowers/plans/2026-09-24-rag-service-extraction.md` is present in this worktree (verified with Test-Path). Extract its `$runner` here-string with the 4A regex, replace its repository path with this worktree, and pipe it to Conda MedChat Python using `-B -c "import sys; exec(sys.stdin.read())"`; this supports the runner's normal `-m pytest` child. Do not run against real config/assets.

```powershell
python -B -m pytest tests/agent/test_generation_ranking_contract.py tests/agent/test_generation_ranking_contract_integration.py tests/test_llm_molecular_generator.py tests/agent/test_candidate_ranker.py tests/agent/test_generation_temperature_transport.py tests/agent/test_generated_candidate_validation.py tests/agent/test_candidate_contracts.py tests/agent/test_candidate_alignment.py tests/agent/test_tool_adapter_compat.py tests/agent/test_tool_adapters.py tests/agent/test_tool_registry.py -q -p no:cacheprovider --tb=short -rs
```

- [ ] Add the remaining explicit paths above, then run `python -B -m pytest tests/agent -q -p no:cacheprovider --tb=short -rs` under the same isolation. Record exact commands and all skips/failures. Existing tests use local FakeLLM/RecordingModel responses; these are offline contract checks, not real model calls or quality evidence.
- [ ] Run the new contract/integration tests with the minimum supported Pydantic 2.5 profile if the parent's isolated profile is available. Do not alter installed dependencies to obtain it; report unavailable coverage honestly.
- [ ] Run `python -m compileall -q src scripts` only during authorized implementation in the isolated workspace; use in-memory compile when artifact-free verification is required and state the substitution. Run `git diff --check`. No Node check is required unless scope is separately expanded to JavaScript. Health/real acceptance are not applicable to this bounded contract-only change and must not be invoked as a disguised model/asset probe.
- [ ] Inspect every regression failure before altering fixtures. For a consumer fixture intentionally impersonating a scientific tool with contradictory data, use an explicit generic LegacyPythonToolAdapter in that test only; retain its downstream assertions and record why. Never rename it to another scientific tool that will become typed, or weaken a contract. If a valid producer shape conflicts with this design, report it before changing product behavior.

## Parent review and stop conditions

- [ ] Parent approves design before implementation; independent SPEC then QUALITY review before integration. Combine 4A/4C factory changes explicitly without importing unreviewed sibling code.
- [ ] Confirm changes stay within the agreed write set, no science formula/normalizer/generic lifecycle rewrite, no output projection or missing evidence promotion. Report branch, exact files, executed test commands/counts, limitations and unresolved questions. Parent owns any later commit/draft PR; none is authorized by this design task.
- [ ] Return any genuine need to change source/partial semantics, wrapper precedence, ADMET summary production, activity normalization or failure snapshot persistence. Do not resolve such conflicts by guessing, altering scores or erasing evidence.

## Evidence for this design task

Static source/test-path inspection only; no pytest, compileall, model execution or scientific acceptance performed. Proposed RED/GREEN outcomes above are expectations, not recorded passes. Only the two requested documents were written. Parent design review remains pending.

### Parent approval and implementation authorization

Parent accepted this bounded design with the two corrections above. Commit only design/plan, then merge origin/main before TDD. Package 4A is approved at a80df26; current main includes the separate domain-API PR64 at ecd6cca. Mirror approved 4A worker containment without importing its adapter. Implementation remains uncommitted for parent review/release. Minimum Pydantic profile is supplied separately by the parent; use the read-only 4A plan's isolated profile setup.

## Implementation evidence (2026-09-25)

Design/plan-only commit: `a93a7d7`. Authorized merge of `origin/main` (`ecd6cca`):
`6fb78dc`. Implementation baseline is that merge; no implementation commit/PR.
Approved 4A was read using `git show a80df26:...`, not merged or edited. The
existing local runner document was verified present; the earlier contrary design
claim was corrected before the design commit.

All pytest commands use this existing isolation runner, normal child pytest,
cleared inherited credentials/config environment, temporary cwd/runtime paths and
disabled real-service switches. The runner copies only the three checked-in
evaluation case fixtures, never model weights, production indexes or secrets.
The tests' fixed local responses/descriptor values are synthetic contract inputs,
not model-quality or experimental evidence.

```powershell
$plan=Get-Content docs/superpowers/plans/2026-09-24-rag-service-extraction.md -Raw
$m=[regex]::Match($plan,'(?s)\$runner = @''\r?\n(.*?)\r?\n''@')
if(-not $m.Success){throw 'Isolation runner missing'}
$runner=$m.Groups[1].Value.Replace('D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr','D:/MedChat/molecular_chat_system_worktrees/generation-ranking-contracts')
$runner | & 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -B -c "import sys; exec(sys.stdin.read())" @paths
```

The child adds `-q -p no:cacheprovider --tb=short -rs`. Path sets:

- **BASE**: the Task 5 focused command minus the two new test paths; nine files.
- **NEW**: `tests/agent/test_generation_ranking_contract.py` and
  `tests/agent/test_generation_ranking_contract_integration.py`.
- **FOCUS**: NEW plus all twenty existing paths in Task 5.
- **MIN**: NEW + BASE, through the retained Pydantic 2.5.0 profile.
- **FULL**: `tests/agent`, one serial heavy run after FOCUS and MIN complete.

| Stage | Actual outcome |
| --- | --- |
| BASE before implementation | 246 passed, 162 subtests passed, 2.31s |
| Initial contract RED, first new file only | 87 failed, 31 passed, 2.99s |
| Initial contract GREEN | 118 passed, 1.36s |
| Expanded NEW first collection | 1 collection error: pytest reserves parametrize name `request`; changed only the test parameter name |
| Expanded NEW regression RED | 2 failed, 308 passed, 4.18s |
| FOCUS after caller-exception correction | 1521 passed, 7 existing warnings, 162 subtests passed, 17.24s |
| MIN (asserted Pydantic 2.5.0) | 556 passed, 1 existing warning, 162 subtests passed, 4.57s |

Initial RED reproduced actual direct-domain-wrapper metadata/outputs loss,
accepted malformed observations, wrong input types, unchecked failure snapshots,
and redaction running on the caller after slot release. Event-controlled deadline
tests timed out waiting for the caller's redaction, demonstrating that omission
before production code was added. Subsequent raw/normalized stage tests cover
success/partial/failure, capacity exhaustion during late validation, and slot
reuse only after complete worker postprocessing.

The expanded RED isolated a genuine implementation defect: catching
CandidateValidationUnavailable around the inherited guarded call also intercepted
the caller validator's exception. The fix introduces a private marker only around
our own domain check; caller exceptions retain the generic adapter classification.
The two failing assertions were retained and now pass. No generic adapter change
or scientific-rule relaxation was used.

Minimum profile setup, after reading `tests/fixtures/api_route_contract_profiles.md`
and the approved 4A plan read-only:

```powershell
$runner=$runner.Replace('"PYTHONPATH": str(repo)', '"PYTHONPATH": r"C:/Users/xkx52/AppData/Local/Temp/medchat-domain-api-profiles-20260925-b831/ci" + os.pathsep + str(repo)')
$runner=$runner.Replace('[sys.executable, "-B", "-m", "pytest"]','[sys.executable, "-B", "-c", "import sys, pydantic; assert pydantic.__version__ == ''2.5.0''; print(''PYDANTIC_PROFILE='' + pydantic.__version__); import pytest; sys.exit(pytest.main(sys.argv[1:]))"]')
```

No dependencies installed or changed. Host warnings are existing SWIG/FastAPI
deprecations; minimum-profile warning is the existing model_provenance protected
namespace. Four implementation/test Python files compile in memory from source
bytes (no pyc; explicit substitution for compileall), and `git diff --check`
passes. Generic adapters, existing activity/docking/RAG contracts, producers,
candidate/domain validators and scoring code remain unchanged.

### Full-run findings, timing lesson and final verification

The one serial heavy FULL run completed with **2 failed, 5941 passed, 2 skipped,
7 warnings in 290.35s**, exit 1. Skips were Windows directory symlinks unavailable
(`test_decision_chat_acceptance.py:149`) and the disabled performance test
(`test_harness_shadow.py:277`). The two failures were:

- `tests/agent/test_supervisor_runtime_integration.py::test_delegated_supervisor_persists_run_events_and_tool_results`
- `tests/agent/test_supervisor_runtime_integration.py::test_delegated_supervisor_reuses_idempotent_completed_steps`

Root cause: these persistence/idempotency fixtures deliberately return a
`{"query": ...}` sentinel for candidate_ranker, not a CandidateRanking observation.
The new output boundary correctly rejected it, so the overall run became partial.
Reported the failures before altering anything. Applied the parent's explicit
consumer-fixture rule: a local `runtime_fixture_registry` uses
LegacyPythonToolAdapter/LegacyQueryInput/no output schema **only for that sentinel
ranker in these two tests**. Kept the scientific name, all downstream assertions,
the factory ownership test and every production contract unchanged. No other
existing test needed a fix. This is the sole additional existing-file write.

The parent's 4B timing lesson prompted twelve controlled-clock cases, parameterized
over both tools, canonical ToolResult vs raw dict, and elapsed_ms None/0/123.
They observe the worker's validation stages and set only the framework clock to
10.0 -> 10.875. Canonical None remains None through both worker checks and becomes
875 only at the outer framework handoff; supplied 0/123 survive. Raw dict retains
the generic adapter's historical compat timing (normalized elapsed None, then
framework duration). Here the inherited guarded invocation already bypasses the
completed proxy for canonical ToolResult, so **no production timing change was
needed**. The test demonstrates that fact rather than assuming it from 4A/4B.

| Final verification | Actual outcome |
| --- | --- |
| NEW + supervisor_runtime_integration + generation_temperature_transport + tool_adapter_compat + tool_adapters + delegated_session_parity (all under tests/agent) | 363 passed, 7.11s |
| MIN + tests/agent/test_supervisor_runtime_integration.py | 571 passed, 1 existing warning, 162 subtests passed, 6.75s |
| FOCUS + tests/agent/test_supervisor_runtime_integration.py | 1536 passed, 7 existing warnings, 162 subtests passed, 17.84s |
| In-memory source-byte compile | 325 files: all tracked Python under src/scripts plus new module and three changed/new test files |
| git diff --check | Passed |

Final FOCUS and MIN include the timing tests and repaired runtime fixture. The
heavy full run preceded those test-only changes; it was not repeated, so there is
**no post-fixture full-suite GREEN claim**. No production code changed after that
full run. Parent can repeat the full suite on its final combined integration.

### Frozen handoff

Branch remains `codex/generation-ranking-contracts`, HEAD `6fb78dc`. The parent
later reported main `3a68264` and 4A PR67 at `9f3ce84` with CI pending; neither was
automatically merged into this implementation. Independent SPEC/QUALITY review,
latest-main/4A integration, implementation commit and PR remain parent-owned.

Uncommitted write set is exactly the four implementation/test files in the table,
`tests/agent/test_supervisor_runtime_integration.py`, and this plan. No other
worktree edits, production model calls, secret access, scientific asset reads,
dependency changes, deployment, push or PR. No generic adapter, producer,
CandidateSet/domain validator or scoring formula changes. Design-only commit and
authorized main merge are the only new commits.

Frozen code/test file SHA-256 (plan excluded to avoid self-reference):

```text
src/agent/tooling/factory.py dcc1a38d5296bcc6283f1105ffe8be0fcfca3df0b6bbc72f2b1ee22179d9f0cc
src/agent/tooling/generation_ranking_contract.py d02e10fde7f0224f585ca2046a65a3bf90e44b74a547aa03368ca4c6bb94786d
tests/agent/test_generation_ranking_contract.py 7c60de25aff6ae55798307c8c1924c1b204db7f7c64a867287941e97ce4630a4
tests/agent/test_generation_ranking_contract_integration.py f932a2df092c77077dff75eac04780e6d2466b5d970ec85a193646f6e83f3a78
tests/agent/test_supervisor_runtime_integration.py 895ee22e04b77ccc52049998335d9ba347877f003ff83ef5382b46acf410e74e
```

Sorted `path + space + sha256 + LF` aggregate SHA-256:
`d381ecbb8113d0707733254a4d31a4f8b6aa2c5240f9ebb5b47a76f381e72fa4`.
This is a worker handoff, not independent review approval or scientific validation.

### Parent correction: pending 4A consumer-fixture integration

Read-only inspection of actual `9f3ce84` diffs confirmed two additional 4C
integration cases. They are not present under those ranker names at this worktree's
current HEAD, so no speculative merge or fixture edit was performed:

1. `tests/agent/test_dynamic_run_session.py::test_dynamic_completion_rejects_preserved_structured_error`
   (both outcome parameters): 4A renamed the intentionally contradictory
   property_calculator observation/source to candidate_ranker to reach the
   downstream session guard. When integrating 4A, keep the name and all error,
   terminal-event and outcome assertions; add an explicit test-local generic
   registry option to `make_session`, enabled only for this contradiction test.
   Use LegacyPythonToolAdapter with the registry-derived spec replaced to
   LegacyQueryInput/output_schema=None. The default fixture path remains typed.
2. `tests/agent/test_rag_tool_contract.py::test_non_rag_factory_retains_legacy_schema_and_payload`:
   4A lines 292–296 renamed its arbitrary-output legacy regression to
   candidate_ranker. When integrating, preserve its name and `query=123` accepted
   payload assertion, but explicitly construct the generic adapter rather than
   expecting the now-typed factory entry to be legacy:

   ```python
   spec = build_tool_registry([tool]).resolve(tool.name, require_available=False).spec
   adapter = LegacyPythonToolAdapter(
       replace(spec, input_schema=LegacyQueryInput, output_schema=None), tool)
   ```

   Import replace/LegacyQueryInput locally or in this test module as appropriate;
   do not introduce a production generic escape hatch or rename another tool.

Mandatory post-integration FOCUS adds **both complete files** above. In particular,
the existing successful RAG FOCUS result covered the baseline property_calculator
sentinel, not 4A's later candidate_ranker version; it does not prove that combined
fixture passes. Record combined RED and focused GREEN before claiming integration
complete. No new full run while the parent owns the heavy-test slot.

The analogous target-name changes in `test_decision_migration_boundaries.py` and
`test_decision_spec_findings.py` belong to parent 4B's explicit test-local generic
registry fix, not this worker. Parent reported 4B FULL RED running after main
`1bba025`; no 4B helper code was available at this checkpoint. These integration
fixes remain pending until the authorized 4A/main combination is present. Frozen
4C implementation/test hashes above are unchanged; only this plan was appended.

### Actual 4A integration checkpoint (supersedes pending integration above)

Parent authorized the bounded implementation's local unreviewed commit
`a458ade`, followed by main integration. Merge `c088f65` incorporates exact main
`1bba0256409a06317486530e5c1cfa6598b8e381` without conflicts. Factory changes
remain additive: only the two generation/ranking contracts differ from main;
4A's analysis selections remain intact. No unmerged 4B code was consumed.

Before editing either merged consumer fixture, the two exact node IDs documented
above produced **3 failed, 1.38s** (both dynamic outcomes rejected generic input
as INVALID_INPUT before the downstream guard; RAG received the typed ranker).
The fixes explicitly construct a test-local LegacyPythonToolAdapter using the
registry-derived spec with LegacyQueryInput/output_schema=None. Dynamic fixture
opt-in is enabled only for its contradictory observation test. Tool names,
downstream assertions and all default typed paths are unchanged. No production
escape hatch, adapter change, or scientific contract weakening was introduced.

| Integrated verification at c088f65 plus fixture fixes | Actual outcome |
| --- | --- |
| Complete test_dynamic_run_session.py + test_rag_tool_contract.py | 366 passed, 6.60s |
| FOCUS: prior complete FOCUS + supervisor_runtime_integration + dynamic_run_session + analysis_contract (25 files total) | 2029 passed, 7 warnings, 162 subtests passed, 27.83s |
| Same 25 files under existing minimum dependency profile, asserted Pydantic 2.5.0 | 2029 passed, 8 warnings, 162 subtests passed, 29.43s |
| In-memory source-byte compile, tracked src/scripts plus five changed/new tests | 328 files passed |
| git diff --check | Passed |

Both profiles use the existing runner extracted from
`docs/superpowers/plans/2026-09-24-rag-service-extraction.md`, with only its cwd
replacement and the documented minimum-profile substitution. Existing warnings
were not suppressed. These are focused results, **not a full Agent GREEN**.

Parent subsequently requested secure_io sibling-mtime fix PR68, exact fetched
main `e173d432f767fe76e1c1f101d9cd8824ffee612d`, before any new full Agent run.
The current combined fixture work and this record will be committed locally as
unreviewed, then that main merged. Full Agent remains deferred until parent
releases the heavy-test slot (4B GREEN active session 88542); this worker is next,
with planner queued afterward. Repeat focus/min on the new integration before
requesting the full slot. Independent SPEC/QUALITY and external publication
remain parent-owned; no push or PR from this worker.

### PR68 integrated; ready for the full Agent slot

Committed the two fixture fixes and preceding checkpoint locally as unreviewed
`24d7e9c`. Authorized merge `c5791f6` now contains exact main
`e173d432f767fe76e1c1f101d9cd8824ffee612d`; ancestry verified. Merge was clean,
with no factory changes or local edits to secure_io. The 4C production module,
factory and two new test files are unchanged from the preceding checkpoint.

Reran the same complete 25-file FOCUS serially with the same isolated runner:

| Post-PR68 profile | Actual outcome |
| --- | --- |
| Host MedChat | 2029 passed, 7 warnings, 162 subtests passed, 29.50s; exit 0 |
| Existing minimum profile, asserted Pydantic 2.5.0 | 2029 passed, 8 warnings, 162 subtests passed, 28.70s; exit 0 |

No warning suppression or contract weakening. `git diff --check` is clean. Only
this results record is uncommitted after the authorized fixture commit/merge.
**Ready for full Agent, not started:** wait for parent's explicit heavy-slot
release after 4B session 88542. There is no full-suite GREEN claim for this
snapshot. No push, PR, independent review approval or scientific-quality claim.

### Released-slot full Agent result: one remaining generic fixture failure

Parent released the heavy slot after 4B completed. Ran exactly one full Agent
invocation on frozen HEAD `c5791f641262ecc0ebf17b4e716bb4f0993b7854`, using the
same extracted isolation runner with sole path argument `tests/agent` (underlying
pytest: `-q -p no:cacheprovider --tb=short -rs`). Session 30511 completed with
exit 1: **6420 passed, 1 failed, 2 skipped, 7 warnings in 280.29s**. No other heavy
run was launched; slot released back to parent for queued Planner.

Failure:
`tests/agent/test_delegated_baseline_integration.py::test_health_telemetry_does_not_raise_or_publish_invalid_raw_status`,
line 87 (`assert result is observation`). This existing generic telemetry probe
uses candidate_ranker, query='CCO', and an intentionally invalid raw status. The
typed factory adapter rejects its non-ranking input before returning that raw
observation. Read-only inspection confirms the test explicitly describes generic
adapter telemetry; its health/redaction assertions must remain intact. A scoped
test-local LegacyPythonToolAdapter construction, as used for the other generic
probes, is the proposed correction. **Not implemented or rerun at this frozen
checkpoint; reporting the failure before any change.** No contract weakening.

Exact skips:

- `tests/agent/test_decision_chat_acceptance.py:149`: directory symlinks unavailable.
- `tests/agent/test_harness_shadow.py:277`: performance test disabled.

Frozen code/test SHA-256 values (plan excluded):

```text
src/agent/tooling/factory.py 3cee838682590d3b0e9c486231aa2d3ebf5c595e28e13b13cb0befc5fd833215
src/agent/tooling/generation_ranking_contract.py d02e10fde7f0224f585ca2046a65a3bf90e44b74a547aa03368ca4c6bb94786d
tests/agent/test_dynamic_run_session.py a8a2f17255631aed48e691f736d0c1e5c395b648d4b145948b31c09c5aadd632
tests/agent/test_generation_ranking_contract.py 7c60de25aff6ae55798307c8c1924c1b204db7f7c64a867287941e97ce4630a4
tests/agent/test_generation_ranking_contract_integration.py f932a2df092c77077dff75eac04780e6d2466b5d970ec85a193646f6e83f3a78
tests/agent/test_rag_tool_contract.py 3923a00d98da876af4a7f5f8bd590715d392fb597b6bdbb733ef9319424cc173
tests/agent/test_supervisor_runtime_integration.py bcd880c29f6a83853c04ea8f54484b568d0d1b407f9fed5e1847f331207b4464
```

Ordinal path-sorted `path + space + sha256 + LF`, UTF-8 aggregate SHA-256:
`c86ec686597a2b998fb57169abe1ee447a59d24361a20b01bade936e7c7e7c4d`.
Only this plan is dirty; production/tests remain frozen. Prior post-PR68 FOCUS
and minimum-profile evidence (each 2029 passed + 162 subtests) is retained, but
does not include the newly identified generic telemetry fixture.

### PR69 integration and authorized telemetry fixture correction

Merged reviewed target-contract main `16b91575229be987b0c5cf3d8d9039135d8ade29`
as `bcb7d541b44b8d609fda7c77232b3fe5fe0c6730`. Conflicts occurred only in
factory imports and input/output/adapter selectors; resolution retains both 4B
and 4C branches additively. Diff against that main contains only the 4C import
and its two exact-name selections. Verified byte-identical 4B target_contract,
target tests, registry tests, and three decision fixture files against reviewed
main. Existing 4C production module and tests were unchanged by this merge.

Before the telemetry correction, merged host FOCUS reported **2497 passed,
1 failed, 7 warnings, 162 subtests passed, 65.75s**; minimum Pydantic 2.5.0 profile
reported **2497 passed, 1 failed, 3968 warnings, 162 subtests passed, 71.48s**.
The sole failure in both was the previously reported health telemetry fixture.

Parent explicitly authorized only a test-local generic adapter replacement.
First reran its exact node on the merged baseline: **1 failed, 1.02s, exit 1**.
Then changed only that function in
`tests/agent/test_delegated_baseline_integration.py`: derive ToolSpec from the
actual factory for the same candidate_ranker object, replace only input_schema
with LegacyQueryInput and output_schema with None, and register an explicit
LegacyPythonToolAdapter in a local registry. No invocation/resources start in
the temporary factory construction; the executing registry retains closure
ownership. All tool names, inputs, raw status and downstream assertions remain
unchanged. AST comparison of every existing assertion in the file passed.
No production changes were made to fix this failure.

Post-fix verification, same isolation runner and unchanged profile setup:

| Scope/profile | Actual outcome |
| --- | --- |
| Complete test_delegated_baseline_integration.py | 11 passed, 2.36s, exit 0 |
| Merged 30-file host FOCUS | 2498 passed, 7 warnings, 162 subtests passed, 74.67s, exit 0 |
| Same 30 files, asserted Pydantic 2.5.0 | 2498 passed, 3968 warnings, 162 subtests passed, 78.63s, exit 0 |
| In-memory compilation, tracked src/scripts plus six changed/new tests | 330 files passed |
| git diff --check | Passed |

The minimum-profile warning increase is from the three decision fixture files'
Pydantic v1/v2 `__fields__` compatibility paths, in addition to previously
reported warnings; identical counts before/after the fixture fix. No warning
filters or skips added. Initial ad-hoc AST check hit Windows GBK decoding of
git-show UTF-8 output; rerunning with explicit UTF-8 passed. No repository code
was changed for that check.

Exact merged FOCUS path arguments (30 complete files, no deselection):

```text
tests/agent/test_generation_ranking_contract.py
tests/agent/test_generation_ranking_contract_integration.py
tests/test_llm_molecular_generator.py
tests/agent/test_candidate_ranker.py
tests/agent/test_generation_temperature_transport.py
tests/agent/test_generated_candidate_validation.py
tests/agent/test_candidate_contracts.py
tests/agent/test_candidate_alignment.py
tests/agent/test_tool_adapter_compat.py
tests/agent/test_tool_adapters.py
tests/agent/test_tool_registry.py
tests/agent/test_target_driven_design_workflow.py
tests/agent/test_binding_resolver.py
tests/agent/test_plan_compiler.py
tests/agent/test_planner_template_execution.py
tests/agent/test_registration_consistency.py
tests/agent/test_activity_tool_contract.py
tests/agent/test_docking_tool_contract.py
tests/agent/test_rag_tool_contract.py
tests/agent/test_result_validator.py
tests/agent/test_delegated_session_parity.py
tests/agent/test_workflow_resume.py
tests/agent/test_supervisor_runtime_integration.py
tests/agent/test_dynamic_run_session.py
tests/agent/test_analysis_contract.py
tests/agent/test_target_tool_contract.py
tests/agent/test_decision_loop.py
tests/agent/test_decision_migration_boundaries.py
tests/agent/test_decision_spec_findings.py
tests/agent/test_delegated_baseline_integration.py
```

### Current frozen SPEC handoff (supersedes earlier snapshots)

HEAD `bcb7d541b44b8d609fda7c77232b3fe5fe0c6730`, branch
`codex/generation-ranking-contracts`. Only the authorized telemetry fixture and
this accumulated plan record are uncommitted. No push/PR, no second full Agent
run; the full slot belongs to parent Planner. A later full run requires a new
slot release. The historical full RED remains recorded; focused GREEN must not
be described as full-suite GREEN. Independent SPEC then QUALITY remain pending
with the parent.

Frozen code/test SHA-256 (eight 4C files; plan excluded):

```text
src/agent/tooling/factory.py 51da6427192db5c546ad731af260d415d3a8f0130d6a4c077dd4df66a1238a3f
src/agent/tooling/generation_ranking_contract.py d02e10fde7f0224f585ca2046a65a3bf90e44b74a547aa03368ca4c6bb94786d
tests/agent/test_delegated_baseline_integration.py 089f06ad3318eb8bca27e066b1a126cb8bd34a148d9465cde759f6053b61c5fb
tests/agent/test_dynamic_run_session.py a8a2f17255631aed48e691f736d0c1e5c395b648d4b145948b31c09c5aadd632
tests/agent/test_generation_ranking_contract.py 7c60de25aff6ae55798307c8c1924c1b204db7f7c64a867287941e97ce4630a4
tests/agent/test_generation_ranking_contract_integration.py f932a2df092c77077dff75eac04780e6d2466b5d970ec85a193646f6e83f3a78
tests/agent/test_rag_tool_contract.py 3923a00d98da876af4a7f5f8bd590715d392fb597b6bdbb733ef9319424cc173
tests/agent/test_supervisor_runtime_integration.py bcd880c29f6a83853c04ea8f54484b568d0d1b407f9fed5e1847f331207b4464
```

Ordinal path-sorted UTF-8 `path + space + sha256 + LF` aggregate:
`68bd4d2fb88f2f3e76f0dcdc9e88bdeb0042581b00070498d4117893d1d904d7`.
The exact change set against reviewed main16b is those eight code/test files
plus this plan and the existing generation-ranking design document. No generic
adapter, target/analysis contract, producer, scoring or scientific asset changes.

### Authorized latest frozen full Agent: GREEN

Parent released the heavy slot after Planner's full completed and explicitly
authorized one full Agent run on `bcb7d54` plus the telemetry fixture GREEN diff,
without waiting for independent SPEC. Ran the same isolated runner with sole
path argument `tests/agent`, session 73002: **6640 passed, 2 skipped, 7 warnings
in 287.79s**, exit 0. No code/test edits or merges occurred during this run.

Exact skips remain unchanged:

- `tests/agent/test_decision_chat_acceptance.py:149`: directory symlinks unavailable.
- `tests/agent/test_harness_shadow.py:277`: performance test disabled.

Post-run HEAD verified as `bcb7d541b44b8d609fda7c77232b3fe5fe0c6730`; the eight-file
aggregate rechecked equal to
`68bd4d2fb88f2f3e76f0dcdc9e88bdeb0042581b00070498d4117893d1d904d7`.
No new full run follows this result. Heavy slot released to parent G2 immediately
on completion. Host/min FOCUS evidence above remains valid for the same code/test
snapshot. Independent SPEC/QUALITY remain pending; full GREEN is not review
approval or a scientific/model-quality claim.

Parent reported PR70's two added recovery-test files merged while this full was
running. PR70 was deliberately not integrated mid-run. This full result applies
only to the exact frozen snapshot above; later PR70 integration may use separately
recorded light focused verification and must not be relabelled as this full run.
Only this accumulated plan record and the authorized telemetry fixture remain
uncommitted. Frozen for parent review; no push or external publication.

### SPEC Anscombe P2: restore typed supervisor success-path fixtures

Independent SPEC did **not approve** the preceding snapshot: the normal
persistence/idempotency success tests in test_supervisor_runtime_integration.py
were incorrectly treated as generic adversarial probes. Their runtime_fixture_registry
replaced candidate_ranker with LegacyPythonToolAdapter/output_schema=None, hiding
an invalid query-sentinel ranking result. The earlier rationale for this override
is rejected and superseded here. Historical full **6640 passed** remains accurate
for its earlier snapshot, but neither resolves this finding nor verifies this fix.

Parent authorized a minimal test-only TDD correction. First removed the helper
and its Legacy imports and changed both success tests back to build_tool_registry,
leaving the sentinel unchanged. Exact RED path arguments:

```text
tests/agent/test_supervisor_runtime_integration.py::test_delegated_supervisor_persists_run_events_and_tool_results
tests/agent/test_supervisor_runtime_integration.py::test_delegated_supervisor_reuses_idempotent_completed_steps
```

Both failed with `partial != succeeded`: **2 failed, 2.66s, exit 1**, matching
SPEC's independent memory probe. An initial shell attempt did not reach pytest
because the session-local runner string was unavailable; re-extracted the same
existing documented runner before the actual RED above. No test failure is
inferred from that shell error.

Minimal GREEN: FakeTool.execute still records every call, but for its exact
candidate_ranker fixture now returns actual `CandidateRanker().execute(query)`.
Both success paths use the default typed registry, with no schema overrides.
Other fixture payloads and every pre-existing assertion remain unchanged (AST
comparison verified). This calls only the existing deterministic ranker, not a
model/provider; no production implementation, scoring, evidence rule, or asset
changed. Truly adversarial generic probes in other test files retain their
separately authorized local adapters.

| Post-P2 verification | Actual outcome |
| --- | --- |
| Complete test_supervisor_runtime_integration.py | 3 passed, 2.34s, exit 0 |
| Same exact 30-file merged host FOCUS listed above | 2498 passed, 7 warnings, 162 subtests passed, 74.38s, exit 0 |
| Same FOCUS, asserted Pydantic 2.5.0 minimum profile | 2498 passed, 3968 warnings, 162 subtests passed, 69.86s, exit 0 |
| Existing assertions unchanged; no Legacy success override (AST) | Passed |
| In-memory compilation, same 330 Python files | Passed |
| git diff --check | Passed |

No warning suppression, assertion weakening, added skip, or full Agent run in
this follow-up. G2 owns the heavy slot. No merge/commit/push occurred. This
follow-up changed only test_supervisor_runtime_integration.py and this plan;
earlier uncommitted telemetry fixture remains unchanged.

Current frozen HEAD remains `bcb7d541b44b8d609fda7c77232b3fe5fe0c6730`. Of the
eight code/test hashes above, only supervisor_runtime_integration changes to:

```text
tests/agent/test_supervisor_runtime_integration.py 4f33d6e6c1e9cbab3b7677b765f24ab47c9316197ce178749a49fea0ac34b596
```

New eight-file aggregate (same ordinal sorting, UTF-8 and LF algorithm):
`c9d5cd9a50b0e4463d7ce664ed81de37073b610712d7e4770d462ba7a4d40993`.
The other seven file hashes are unchanged. Freeze for independent SPEC re-review
first, then QUALITY; neither approval is claimed. No post-P2 full GREEN claim.

### Parent-confirmed dual review and post-P2 integration gate

Parent confirmed independent SPEC/Anscombe approval of the P2 correction and
QUALITY/Helmholtz APPROVED for exact HEAD bcb7d54 plus eight-file aggregate
`c9d5cd9a50b0e4463d7ce664ed81de37073b610712d7e4770d462ba7a4d40993`.
QUALITY reported its own 352 + 3 focused tests. These are parent-reported
independent reviews, distinct from this worker's verification above.

After G2 full completed, parent released the local heavy slot and authorized:
commit only the three currently dirty paths (this plan, delegated_baseline
telemetry fixture, supervisor_runtime success fixture); merge fetched main
`5db56b0c79ac30da1ba4646c2c567e7d2dd71cc5` (PR70/71); verify integration differences
and collision-focused tests; run exactly one full tests/agent via the existing
isolated runner. Stop and diagnose any failure without weakening assertions or
contracts. No push or PR. The historical pre-P2 6640-pass full is not evidence
for this required post-P2 gate. Actual merge/focus/full results follow below.

### Post-P2 latest-main gate completed: full GREEN at 5e600a6

Committed exactly the three authorized dirty files as `d8ccc76` (typed supervisor
success fixture, adversarial telemetry fixture, this plan including dual-review
record). Merged main `5db56b0c79ac30da1ba4646c2c567e7d2dd71cc5` without conflicts
as **`5e600a6b9e371f1fcc537368b1bf8cfe4d17f6cb`**. No local production adjustment
was needed. Planner, target and analysis implementations match reviewed main;
factory diff against main remains only the two 4C contract selections/import.
The reviewed eight-file 4C aggregate remains unchanged.

Collision FOCUS included the previous 30 complete files plus these seven actual
PR70/71-related files (37 complete files total; no deselection):

```text
tests/agent/test_delegated_session_lifecycle.py
tests/agent/test_planner_responsibilities.py
tests/agent/test_task_planner.py
tests/agent/test_planner_step_templates.py
tests/agent/test_target_identity_alignment.py
tests/agent/test_target_selection_phrase.py
tests/agent/test_prompt_acceptance.py
```

The already-included test_workflow_resume.py covers PR70's other modified file.
Same existing isolated runner, host MedChat profile: **3134 passed, 7 warnings,
162 subtests passed in 79.95s**, exit 0. All 326 tracked src/scripts Python files
compiled in memory. `git diff --check` passed; worktree was clean before full.
Minimum-profile evidence remains the prior post-P2 2498-case run, not a new
post-PR70/71 minimum-profile run.

Then ran exactly one authorized post-P2 full Agent with sole path `tests/agent`
through the same isolated runner (child options `-q -p no:cacheprovider --tb=short
-rs`), session 51531. Result: **6676 passed, 2 skipped, 7 warnings in 300.25s**,
exit 0. Full-run HEAD was and remains
`5e600a6b9e371f1fcc537368b1bf8cfe4d17f6cb`. No edits, merges, second full or other
heavy runs occurred while it ran. Exact skips:

- `tests/agent/test_decision_chat_acceptance.py:149`: directory symlinks unavailable.
- `tests/agent/test_harness_shadow.py:277`: performance test disabled.

Post-run eight-file aggregate was rechecked equal to the dual-reviewed snapshot:
`c9d5cd9a50b0e4463d7ce664ed81de37073b610712d7e4770d462ba7a4d40993`.
This full is the current post-P2 gate; historical pre-P2 6640 remains separate.
No scientific model/provider activation, asset reads or model-quality claims.

Parent reported G2 PR72 at `548baca8f1c59acb6e9dbe59a7c8787b722129cd` with CI
in progress during this run. It was not integrated. Parent will coordinate G2
integration and focused verification before 4C publication; this full must not
be presented as covering that later snapshot. Heavy slot released on completion.

Ready and frozen: code/tests are committed at the exact full HEAD; only this
new results record is uncommitted. No push or PR created. Dual review applies to
unchanged 4C code/tests, and current integration/full evidence is recorded here;
publication and subsequent G2 integration remain parent-owned.
