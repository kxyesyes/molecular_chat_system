# RAG Typed Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Preserve RED results and request SPEC then QUALITY review.

**Goal:** Add a non-lossy, all-terminal RAG tool contract without changing other scientific tools.

**Architecture:** Reuse ToolSpec, LegacyPythonToolAdapter, execute_tool_compat and the existing raw-validator invocation boundary. Select a narrow typed RAG adapter in the existing registry factory. Validate views without projecting validated model dumps into scientific data.

**Tech Stack:** Existing Python 3.10, Pydantic 2, pytest; no new dependencies.

## 1. Baseline and RED

- [x] Read the sibling approved design, repository rules, RAG service/retrieval, ToolResult and adapters.
- [x] Create `tests/agent/test_rag_tool_contract.py` exercising actual build_tool_registry and injected RAGSearchTool. Use synthetic provenance only; real manifest verification remains covered by existing tests.
- [x] First failure must show invalid input executes the tool or malformed success/partial/failed output is accepted, not a missing import. Example test body:

```python
adapter = build_tool_registry([tool]).resolve("rag_search", require_available=False)
result = adapter.execute({"query": 123})
assert result.success is False
assert result.error.code is AgentErrorCode.INVALID_INPUT
assert tool.calls == 0
```

- [x] Add baseline-preservation cases before implementation: exact query/default k, aliases, arbitrary record fields/provenance, warnings/evidence/artifacts, all legal statuses, parent raw_validator and non-RAG behavior.
- [x] Run `python -B -m pytest tests/agent/test_rag_tool_contract.py -q -p no:cacheprovider --tb=short`; record actual RED counts/reasons.

## 2. Minimal GREEN

Files: new `src/agent/tooling/rag_contract.py`; modify `src/agent/tooling/factory.py`; if required, narrowly adjust return hooks in `src/agent/tooling/adapters.py`. No service or scientific algorithm changes.

- [x] Add text-only input validation at the RAG boundary. Preserve text exactly and accept existing query dict plus raw string.
- [x] Add record/provenance validation views that allow extension fields and finite similarity values outside 0..1. Source indices/labels are nonnegative integers, not bool/string-coerced.
- [x] Validate raw output before normalization and every tool-returned ToolResult without reimplementing normalization or replacing the caller raw_validator. Reject malformed raw failures even if normalization would discard their data. Adapter-generated early invalid-input/unavailable errors retain their existing canonical constructors.
- [x] Reject INVALID_OUTPUT with safe fixed diagnostics; retain existing genuine execution errors. Never return unvalidated science as success.
- [x] Preserve all fields by returning the original normalized object after validation, not the Pydantic serialization:

```python
self.spec.output_schema.model_validate(observation_view)
return result
```

- [x] Wire factory RAG-only selection. Preserve legacy names/aliases and all other specs/owners/timeouts/retry policies.
- [x] Run the focused matrix below and investigate every failure without weakening scientific protection.

```powershell
python -B -m pytest tests/agent/test_rag_tool_contract.py tests/agent/test_tool_adapters.py tests/agent/test_tool_adapter_compat.py tests/agent/test_tool_registry.py tests/agent/test_registration_consistency.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py -q -p no:cacheprovider --tb=short -rs
```

## 3. Integration, review and delivery

- [x] Run `python -B -m pytest tests/agent -q -p no:cacheprovider --tb=short -rs` and RAG/Web integration tests in a temporary configuration/database/cwd with external-service switches disabled and no inherited credentials. Use the existing documented RAG extraction test wrapper; Windows multiprocessing must enter normal `-m pytest`, not stdin pytest.main.
- [x] Run offline `scripts/run_agent_acceptance.py --mode contract`, explicitly blocking remote connections. Reuse temporary fixture copies and prove hashes equal; never run real/all.
- [x] Compile changed Python files in memory (no production initialization), run `git diff --check`, exact file-scope and credential-pattern checks.
- [x] Record RED/GREEN/failed attempts/skips in this plan. No invented totals or full-repository claim for a focused suite. Commit/publication status follows below.
- [x] Independent SPEC then QUALITY review; fix findings with RED regression where applicable.
- [ ] Stage only this spec/plan, new contract/tests, factory and necessary adapter edits. Follow explicit authorization and latest CI before any merge; never modify main directly.

## Execution evidence

Pre-implementation baseline on the equivalent merged-main tree: existing adapter/registry/manifest four-file suite 84 passed, four existing FastAPI deprecation warnings, 14.33s. This is not the new contract's test result. New RED/GREEN results have not yet run.

### Call-site audit and isolated baseline

- Current implementation branch: `codex/rag-typed-contract-pr`, based on merged main `3974f966e7adfc89f807af4b59da18ea2b60cf6c`; design/plan commit `2542d1cb4ad9ca4599c8bc4e0f81e67f53d023e1`. The original dirty worktree is not the implementation directory.
- Actual app/factory/registry/API tests in `test_registration_consistency.py` and `test_rag_index_manifest.py` already use source-mapped records from the real shared retrieval module with synthetic CSV/FAISS fixtures. Keep these evidence and row-mapping assertions unchanged.
- The alias-owner unit test in `test_registration_consistency.py` uses a generic non-RAG-shaped `{"query": ...}` result. Before code changes, its two variants pass (2 passed, 7 existing deprecation warnings, 2.21s, exit 0). If the typed boundary rejects it, update only that test's fixture to valid RAG records, keeping the specialist execution, alias, query-forwarding and result-retention assertions. This is a test compatibility adaptation, not an exemption in the production schema.
- Directly constructed legacy adapters and evaluation truth-check examples do not go through the registry's new RAG selection. Do not migrate them incidentally.
- Pre-change offline `run_agent_acceptance.py --mode contract` passed, exit 0, using a temporary cwd/config/database environment and socket connections explicitly blocked. The ignored `scratch/t10a-contract.json` report is not real-model acceptance.

Tests use the isolated normal-subprocess pytest runner documented in `2026-09-24-rag-service-extraction.md`, replacing its worktree root with this batch's worktree root. It clears inherited configuration/credentials, copies only three tracked evaluation fixtures after SHA-256 equality checks, and cleans its temporary directory. No real-mode or production-lifespan testing is enabled.

### TDD and compatibility evidence (in progress)

| Stage | Observed result |
|---|---|
| Initial actual-factory behavioral RED | 99 failed / 30 passed / 12 skipped; non-text execution and malformed observations accepted |
| Input/model construction and dataclass RED | 5 failed / 153 passed |
| Raw artifact coercion RED | 4 failed / 208 passed |
| First implementation contract matrix | 212 passed, 1.55s; zero skips |
| First seven-file focused matrix | 344 passed, 7 existing warnings, 8.16s |
| Parent alias-owner compatibility RED | 2 failed, 7 existing warnings, 2.16s; old generic dict fixture rejected |
| Parent RAG/Web/lifecycle matrix (19 root test files) | 457 passed / 6 skipped / 7 existing warnings, 57.89s |
| Parent delegated-entry fixture RED | 4 failed / 7 passed, 1.35s; old synthetic rows omitted source metadata |
| Delegated fixture corrected, all original assertions retained | 11 passed, 1.22s |
| First full Agent + 19 root-file combination | 4 failed / 4439 passed / 8 skipped / 7 warnings, 212.55s; only the four delegated fixture failures above |
| Independent parent constructed-record probe | Invalid `RAGRecord.model_construct` with negative index, NaN score and no provenance still returned success; blocker reproduced, not accepted as complete |

The initial twelve artificial skips were replaced by assertions for legal error `data=None`; the final matrix must not skip these supported states. The delegated fixture correction only adds synthetic row metadata; original success/partial, evidence/artifact, execution-count and SQLite status assertions are unchanged. Independent review and the constructed-record correction remain in progress at this checkpoint.

Root-matrix skips: Windows symlink privilege (1), POSIX directory permissions (2), and opt-in task-store/runtime ownership integration prerequisites (3). Existing warnings concern FAISS SWIG types and FastAPI `on_event`. No skips are represented as scientific success.

### Complete implementer test chronology

All rows used the documented isolated normal `-B -m pytest` subprocess. This includes intermediate failures rather than reporting only the last green run.

| Attempt | Passed | Failed | Skipped | Warnings | Seconds | Exit |
|---|---:|---:|---:|---:|---:|---:|
| Initial behavioral contract RED | 30 | 99 | 12 | 0 | 1.78 | 1 |
| First contract GREEN | 129 | 0 | 12 | 0 | 1.10 | 0 |
| Seven-file: old alias fixture | 259 | 2 | 12 | 7 | 8.06 | 1 |
| Constructed input/canonical error-member RED | 153 | 5 | 0 | 1 | 1.32 | 1 |
| Second contract GREEN | 158 | 0 | 0 | 0 | 1.17 | 0 |
| Seven-file: alias failures and wrong timeout expectation | 323 | 3 | 0 | 7 | 8.19 | 1 |
| Seven-file: corrected expectation and alias fixture | 326 | 0 | 0 | 7 | 8.02 | 0 |
| Raw artifact-member coercion RED | 208 | 4 | 0 | 0 | 1.81 | 1 |
| Seven-file GREEN | 344 | 0 | 0 | 7 | 8.16 | 0 |
| Contract confirmation | 212 | 0 | 0 | 0 | 1.55 | 0 |
| Constructed nested-view RED | 216 | 92 | 0 | 0 | 3.09 | 1 |
| Constructed nested-view GREEN | 308 | 0 | 0 | 0 | 1.76 | 0 |
| Frozen-patch seven-file GREEN | 440 | 0 | 0 | 7 | 8.68 | 0 |

The incorrect timeout expectation was corrected against the existing legacy adapter under Python 3.10 (builtin/futures TimeoutError differ), without changing production timeout mapping. Nested record/provenance/error/artifact Pydantic views are now rejected as transport values before compat normalization; original dictionaries and supported canonical dataclasses remain supported. The schema does not serialize/project models back into scientific results.

Independent SPEC review approved the frozen patch: 387 passed, no skips, 7 existing warnings across six focused suites, plus 11 independent constructed-view probes. Review was read-only and did not claim to rerun the parent's full combination. QUALITY review and final combination are still pending at this record point.

Frozen implementation/test SHA-256:

```text
src/agent/tooling/rag_contract.py dd59d3db6aec25e13ecc534cc22c8d410c27f2d725d80403b72f60be7c37c088
tests/agent/test_rag_tool_contract.py eeb1f5eed6e3197266785b9a997e8e5b12da17eff5909bc54860b4304dc72b61
```

### Final local verification and review

- Final frozen-code combination: **4539 passed, 8 skipped, 7 existing warnings, 206.19s, exit 0**. This supersedes the first failed combination, not its recorded failure evidence. It covers all `tests/agent` plus the 19 root test files listed below; it is not a whole-repository or real-model run.
- Final offline `scripts/run_agent_acceptance.py --mode contract --output scratch/t10a-contract.json`: **34/34 passed**, pass_rate 1.0, exit 0, with socket connections blocked. Report SHA-256: `25025bf9c6b1faec4a5674e4c7b36ba5e078420daff578f1252f17f78296c9fc`. Report is ignored, not committed. Invalid-SMILES RDKit diagnostics are expected negative cases.
- Independent QUALITY review approved: **342 passed**, no skips, 7 existing warnings, plus **24 independent probes**. Reviewed all six production/test files, preserving non-RAG behavior, normalization, field retention, caller validation, single execution, timeout and concurrency. SPEC had already approved separately.
- Local interpreter: Python 3.10.20, Pydantic 2.12.5. Locked CI Pydantic 2.5.0 still requires CI runtime verification; do not equate documentation/API review with that runtime result.
- Full source/scripts in-memory compile, changed-test compile, diff whitespace checks and credential-pattern checks passed. No bytecode, runtime asset, environment file or generated report is staged.
- Eight skips: two Windows symlink privilege cases, two POSIX permissions cases, one opt-in performance case, three configured task-store/runtime prerequisites. The new 308-case RAG matrix has no skips.

Reproduce the final combination with the isolated `$runner` described above (normal subprocess pytest, `-q -p no:cacheprovider --tb=short -rs`):

```powershell
$targets = @(
  'tests/agent',
  'tests/test_model_request_lifecycle.py', 'tests/test_design_model_switch.py',
  'tests/test_molecular_design_architecture.py', 'tests/test_llm_runtime_config.py',
  'tests/test_user_llm_routes.py', 'tests/test_agent_llm_wiring.py',
  'tests/test_task_runtime.py', 'tests/test_phase2_phase3_routes.py',
  'tests/test_agent_anti_hallucination_fallbacks.py',
  'tests/test_agent_platform_health_check.py', 'tests/test_agent_session.py',
  'tests/test_agent_session_entrypoints.py', 'tests/test_agent_task_ownership.py',
  'tests/test_rag_index_manifest.py', 'tests/test_web_app_lifecycle.py',
  'tests/test_openai_compatible_model.py', 'tests/test_rag_service_boundary.py',
  'tests/test_main_routes_template_compat.py', 'tests/test_static_placeholder_cleanup.py'
)
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" @targets
```

The seven-file focused command is listed in section 2; the standalone new-test command is in section 1. Both use the same isolation. Contract uses the extraction plan's documented `runpy`/socket-blocked replacement, with output changed to `scratch/t10a-contract.json`.

Only RAG's factory-registered adapter is migrated. Direct legacy execution, other tools and Planner are unchanged. Remaining T10 activity/docking contracts, T10-B templates, T09 cross-turn scientific objects and T11 remaining splits need their own bounded design/batches. This is not taskbook completion or a production rollout.
