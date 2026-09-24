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

Parent accepted this bounded design with the two corrections above. Commit only design/plan, then merge origin/main before TDD. Package 4A is approved at a80df26 and merged by PR64 at ecd6cca; mirror its worker containment without importing its adapter. Implementation remains uncommitted for parent review/release. Minimum Pydantic profile is supplied separately by the parent; use the read-only 4A plan's isolated profile setup.
