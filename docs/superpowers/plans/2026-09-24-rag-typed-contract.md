# RAG Typed Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Preserve RED results and request SPEC then QUALITY review.

**Goal:** Add a non-lossy, all-terminal RAG tool contract without changing other scientific tools.

**Architecture:** Reuse ToolSpec, LegacyPythonToolAdapter, execute_tool_compat and the existing raw-validator invocation boundary. Select a narrow typed RAG adapter in the existing registry factory. Validate views without projecting validated model dumps into scientific data.

**Tech Stack:** Existing Python 3.10, Pydantic 2, pytest; no new dependencies.

## 1. Baseline and RED

- [ ] Read the sibling approved design, repository rules, RAG service/retrieval, ToolResult and adapters.
- [ ] Create `tests/agent/test_rag_tool_contract.py` exercising actual build_tool_registry and injected RAGSearchTool. Use synthetic provenance only; real manifest verification remains covered by existing tests.
- [ ] First failure must show invalid input executes the tool or malformed success/partial/failed output is accepted, not a missing import. Example test body:

```python
adapter = build_tool_registry([tool]).resolve("rag_search", require_available=False)
result = adapter.execute({"query": 123})
assert result.success is False
assert result.error.code is AgentErrorCode.INVALID_INPUT
assert tool.calls == 0
```

- [ ] Add baseline-preservation cases before implementation: exact query/default k, aliases, arbitrary record fields/provenance, warnings/evidence/artifacts, all legal statuses, parent raw_validator and non-RAG behavior.
- [ ] Run `python -B -m pytest tests/agent/test_rag_tool_contract.py -q -p no:cacheprovider --tb=short`; record actual RED counts/reasons.

## 2. Minimal GREEN

Files: new `src/agent/tooling/rag_contract.py`; modify `src/agent/tooling/factory.py`; if required, narrowly adjust return hooks in `src/agent/tooling/adapters.py`. No service or scientific algorithm changes.

- [ ] Add text-only input validation at the RAG boundary. Preserve text exactly and accept existing query dict plus raw string.
- [ ] Add record/provenance validation views that allow extension fields and finite similarity values outside 0..1. Source indices/labels are nonnegative integers, not bool/string-coerced.
- [ ] Validate raw output before normalization and every final ToolResult without reimplementing normalization or replacing the caller raw_validator. Reject malformed raw failures even if normalization would discard their data.
- [ ] Reject INVALID_OUTPUT with safe fixed diagnostics; retain existing genuine execution errors. Never return unvalidated science as success.
- [ ] Preserve all fields by returning the original normalized object after validation, not the Pydantic serialization:

```python
self.spec.output_schema.model_validate(observation_view)
return result
```

- [ ] Wire factory RAG-only selection. Preserve legacy names/aliases and all other specs/owners/timeouts/retry policies.
- [ ] Run the focused matrix below and investigate every failure without weakening scientific protection.

```powershell
python -B -m pytest tests/agent/test_rag_tool_contract.py tests/agent/test_tool_adapters.py tests/agent/test_tool_adapter_compat.py tests/agent/test_tool_registry.py tests/agent/test_registration_consistency.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py -q -p no:cacheprovider --tb=short -rs
```

## 3. Integration, review and delivery

- [ ] Run `python -B -m pytest tests/agent -q -p no:cacheprovider --tb=short -rs` and RAG/Web integration tests in a temporary configuration/database/cwd with external-service switches disabled and no inherited credentials. Use the existing documented RAG extraction test wrapper; Windows multiprocessing must enter normal `-m pytest`, not stdin pytest.main.
- [ ] Run offline `scripts/run_agent_acceptance.py --mode contract`, explicitly blocking remote connections. Reuse temporary fixture copies and prove hashes equal; never run real/all.
- [ ] Compile changed Python files in memory (no production initialization), run `git diff --check`, exact file-scope and credential-pattern checks.
- [ ] Record RED/GREEN/failed attempts/skips and precise commits in this plan. No invented totals or full-repository claim for a focused suite.
- [ ] Independent SPEC then QUALITY review; fix findings with RED regression where applicable.
- [ ] Stage only this spec/plan, new contract/tests, factory and necessary adapter edits. Follow explicit authorization and latest CI before any merge; never modify main directly.

## Execution evidence

Pre-implementation baseline on the equivalent merged-main tree: existing adapter/registry/manifest four-file suite 84 passed, four existing FastAPI deprecation warnings, 14.33s. This is not the new contract's test result. New RED/GREEN results have not yet run.
