# Analysis tool contracts implementation plan

> **For agentic workers:** use subagent-driven-development and test-driven-development. Parent owns integration, reviews, commit and PR; implementation worker does not push, merge or deploy.

**Goal:** strict non-projecting boundaries for three existing analysis tools, without changing scientific computation or execution lifecycle.
**Architecture:** one analysis-only adapter, per-tool row/envelope views, existing compat invocation and domain validation.
**Tech stack:** Python, Pydantic 2.5+, RDKit, pytest.

## Write set

- Add src/agent/tooling/analysis_contract.py.
- Modify src/agent/tooling/factory.py only to wire three schemas/adapter.
- Add tests/agent/test_analysis_contract.py.
- Existing tests may be adjusted only when a fixture falsely impersonates one of these tools; preserve intended assertion, document and review each change.
- Append evidence to this plan; parent maintains handoff/ledger. Do not touch production tools or generic adapters.

## 1. Characterization and RED

- [ ] Read producer execute/calculation methods, compat, ToolResult, ADMETResultValidator, current activity/docking contracts and factory tests.
- [ ] Establish actual PropertyCalculator and DrugLikenessAssessment output from CCO and aspirin, and forced real RDKit ADMET (ADME_PY_AVAILABLE false). Store test fixtures in memory, not real asset files.
- [ ] Add malformed-output tests using a counting producer and build_tool_registry. Core cases:
```python
raw = PropertyCalculator().execute("CCO")
raw["data"][0]["properties"]["qed"] = float("nan")
tool = CountingTool("property_calculator", raw)
registry = build_tool_registry([tool])
try:
    result = registry.get("property_calculator").execute({"query": "CCO"})
    assert result.error.code is AgentErrorCode.INVALID_OUTPUT
    assert tool.calls == 1
finally:
    registry.close()
```
Use the registry's actual public lookup/cleanup methods if names differ; inspect before writing. Parametrize other numeric leaves, missing descriptors, contradictory states, failed snapshots and malformed envelope containers. Fail because unsafe observations pass, not only missing import.
- [ ] Input tests reject wrong query types before producer call; direct string/wrapper forward exact query; no implicit smiles extraction added.
- [ ] Preservation tests compare fields and nested extension values against execute_tool_compat of the same raw result, ignoring only elapsed timing; typed views must not project.
- [ ] Record baseline and RED commands/counts.

## 2. Minimal GREEN

- [ ] Define strict extra-allow validation views for exact producer shapes above, reject nonfinite known numeric fields and boolean/numeric-string coercion. No output projection.
- [ ] Implement one AnalysisToolAdapter mirroring the reviewed activity raw/normalized validation lifecycle with tool-specific schema selection; bound failed snapshot traversal.
- [ ] Use payload-free error results; preserve caller validator ordering, worker deadlines and single execution.
- [ ] Wire only the three canonical names through factory with strict input and per-tool output schemas; keep existing policy fields untouched.
- [ ] Run the new tests and existing actual-science/adapter/factory regressions; investigate failures before changing tests.

## 3. Regression matrix

- [ ] New contract tests plus existing explicit_molecular_input, admet_whole_input, property_report_boundaries, drug_likeness_evidence, candidate_alignment and adapter/registry tests found with rg --files tests/agent. Root test_agent_anti_hallucination_fallbacks and existing ADMET fallback tests.
- [ ] Full tests/agent with the isolated MedChat Python runner, -B, -q -p no:cacheprovider --tb=short -rs. Do not run from an unisolated real config cwd.
- [ ] Minimum Pydantic profile tests in isolated dependency target when available; don't change installed dependencies.
- [ ] In-memory compile changed Python and git diff --check. Node tests only if frontend changes (not planned).

## 4. Review and release

- [ ] Parent independently reviews diff and runs joint tests.
- [ ] Fresh independent SPEC review; fix/re-review every blocker; then fresh QUALITY review.
- [ ] Exact scoped staging, conventional commit, independent draft PR.
- [ ] Required CI7/7, current head, main base, no unresolved reviews; delegated squash merge only then. Update remaining-work ledger honestly. No production activation.

## Evidence

Not yet run. Implementation worker will append actual baseline/RED/GREEN commands and outcomes, including failures; no prewritten success claims.

