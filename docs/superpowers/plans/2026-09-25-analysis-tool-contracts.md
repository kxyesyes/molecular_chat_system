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

### Implementation worker evidence (2026-09-25; uncommitted)

Worktree `analysis-tool-contracts`, branch `codex/analysis-tool-contracts`, starting
HEAD `01de16b83a919a0a42da9ee2318d2de19a7f4cb1`. Read AGENTS, project standards,
the three producers, compat, registry, ToolResult/provenance, ADMET domain validator,
and activity/docking contracts. Used test-driven-development and the delegated
subagent-driven-development split (parent owns independent SPEC/QUALITY/release).
No changes to production scientific tools, routes, generic adapters, assets, or
the original dirty worktree; no commit, push, merge, model activation or key access.

All pytest commands below used the supplied isolation runner extracted from
`2026-09-24-rag-service-extraction.md`, replacing its repo with this worktree:

```powershell
$python='C:/Users/xkx52/.conda/envs/MedChat/python.exe'
$plan=Get-Content docs/superpowers/plans/2026-09-24-rag-service-extraction.md -Raw
$m=[regex]::Match($plan,'(?s)\$runner = @''\r?\n(.*?)\r?\n''@')
if(-not $m.Success){throw 'Isolation runner missing'}
$runner=$m.Groups[1].Value.Replace('D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr','D:/MedChat/molecular_chat_system_worktrees/analysis-tool-contracts')
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" @paths
```

The runner clears inherited secret/config environment, uses a temporary cwd and
isolated runtime paths with real-service switches off, and runs pytest with
`-q -p no:cacheprovider --tb=short -rs`. No scientific assets were read; RDKit
fixtures are calculated in memory. The three checked-in evaluation case files
copied by the existing runner are test fixtures, not model/index assets.

Path sets (all relative paths are resolved to this worktree by the runner):

- **BASE**: `tests/agent/test_explicit_molecular_input.py`,
  `test_admet_whole_input.py`, `test_property_report_boundaries.py`,
  `test_drug_likeness_evidence.py`, `test_candidate_alignment.py`,
  `test_tool_adapter_compat.py`, `test_tool_adapters.py`, `test_tool_registry.py`,
  `test_registration_consistency.py` (all in `tests/agent/`), plus
  `tests/test_agent_anti_hallucination_fallbacks.py`.
- **NEW**: `tests/agent/test_analysis_contract.py`.
- **FOCUS**: BASE + NEW + `tests/test_admet_predictor_fallback.py` +
  `tests/agent/test_activity_tool_contract.py`.
- **CONSUMERS**: NEW plus `test_decision_requirements.py`,
  `test_rag_tool_contract.py`, `test_decision_loop.py`, `test_dynamic_run_session.py`,
  `test_delegated_session_parity.py`, `test_supervisor_harness_integration.py`,
  `test_supervisor_runtime_integration.py`, `test_delegated_baseline_integration.py`
  (all in `tests/agent/`).

Observed TDD/regression results, including intermediate failures:

| Stage / path set | Result |
| --- | --- |
| Baseline BASE, before changes | 414 passed, 7 warnings, 7.42s |
| Initial NEW RED (before production implementation) | 237 failed, 72 passed, 5.38s |
| Initial NEW GREEN | 309 passed, 2.52s |
| Expanded NEW RED, malformed elapsed metadata | 18 failed, 382 passed, 3.33s |
| FOCUS before registration fixture corrections | 2 failed, 1095 passed, 7 warnings, 12.28s |
| FOCUS after the two registration fixture corrections | 1097 passed, 7 warnings, 11.56s |
| Minimum Pydantic initial focus (NEW + BASE minus registration + ADMET fallback) | 778 passed, 1 warning, 7.52s |
| NEW expanded real-producer and domain-gate coverage | 411 passed, 3.24s |
| Registry consumer discovery | Legacy stub shapes caused failures in harness, runtime, delegated parity, decision loop and dynamic session; no production relaxation made |
| Six affected consumer files after initial fixture fixes | 3 failed, 166 passed, 21.81s (two now-satisfied QED requirements and one old dict index) |
| NEW normalized worker-deadline RED (single test, three tools) | 3 failed, 1.05s |
| NEW + decision requirements + RAG contract | 14 failed, 744 passed, 14.39s (downstream legacy gate fixtures and non-RAG legacy sentinel) |
| Final CONSUMERS, host profile | 927 passed, 28.67s |

The initial RED failures include actual nonfinite observations accepted as success
and unchecked malformed failure snapshots, not just absent schemas/imports. Real
CCO/aspirin outputs, rounded property MW (46.07/180.16), unrounded ADMET MW,
QED/component precision, formatted output, and real invalid-whole-input failures
were characterized before implementation. Preservation tests compare complete
ToolResults against execute_tool_compat, excluding elapsed time only.

The implementation uses compact shared strict leaf views, three per-tool output
views and one analysis-only adapter. No scores, thresholds, weighted formulas or
cross-field scientific algorithms are recomputed. Both raw and post-compat
validation occur in the existing invocation worker; the existing adapter final
hook additionally rechecks the normalized result. Caller raw validators run first
outside the contract exception catch. Tests cover deadline/slot release, one-call
execution, closure, 16 embedded snapshots/cycles, strict transport, metadata,
ADMET sparse sections, domain-gate reuse and non-projecting extensions.

#### Existing fixture corrections requiring parent review

- `test_registration_consistency.py`: two availability tests now use the actual
  PropertyCalculator instead of a query-echo result; availability assertions unchanged.
- `test_supervisor_harness_integration.py`: counting fixture returns complete RDKit
  rows; harness/call-count assertions unchanged.
- `test_supervisor_runtime_integration.py`: property/ADMET three-candidate fixtures
  contain complete real RDKit descriptor/rule rows; run/events/reuse assertions unchanged.
- `test_delegated_session_parity.py`: complete property/likeness rows plus the prior
  `input`/`fixture_value` extensions; equality assertions now include complete row
  lists. Order, evidence, IDs, concurrency, recovery and call counts unchanged.
- `test_dynamic_run_session.py`: complete property rows for default/binding/alignment
  fixtures, prior input extension moved into its row, mutation/index assertions
  follow that shape. The deliberate success-plus-error session test uses the
  untyped candidate_ranker fixture so the original downstream contradiction/error
  assertions remain reachable. Actual typed analysis rejection has separate tests.
- `test_decision_loop.py`: complete property/likeness shape, preserving the prior
  explicit synthetic MW 46.069 and all associated numeric assertions. Because real
  property rows must contain QED, the review-plus-unmet-requirement fixture now
  requests a different subject (CCN) for its one-molecule case; all original outcome,
  evidence, review and unsatisfied-requirement assertions remain unchanged.
- `test_delegated_baseline_integration.py` and `test_rag_tool_contract.py`: generic
  legacy-adapter sentinels use candidate_ranker instead of impersonating an analysis
  tool; original raw passthrough, telemetry, and legacy-schema assertions unchanged.
- `test_decision_requirements.py`: its RowsTool intentionally supplies missing,
  split and malformed metrics to exercise the *independent downstream acceptance
  gate*. A file-local fixture selects LegacyPythonToolAdapter for **only RowsTool**;
  real producers still use the typed factory adapter. All original scientific
  rejection/value assertions are unchanged, not weakened to a generic upstream
  invalid-output result. This test-only isolation is an explicit review focus.

#### Boundaries and deferred validation

Current analysis producers and ADMETResultValidator define no named scientific
evidence slots outside data rows. Evidence/quality and unknown scientific extension
contents remain opaque; only known container/identity fields and error snapshot
links are validated. `backend_version="unknown"` is permitted for adme_py; neither
that label nor successful structural validation proves an installed or real model.

As parent noted, current RDKit ADMET `pains`/`brenk`/`zinc` are constant false,
not actual alert-screen calculations. This increment only validates their current
boolean shape and does not endorse their scientific interpretation. That issue,
the legacy properties HTTP endpoint's unsupported ADMET labels, and the target
resolved/not_found status incompatibility are **not fixed here**.

Full `tests/agent` was intentionally **not run** at parent's request while the
PR64 root profile was running; parent will run it after that workload completes.
No all-suite pass or CI7/7 is claimed. Independent SPEC then QUALITY, full Agent,
exact-head CI, commit and PR remain parent-owned. Package4 is still incomplete
pending target/reverse, generation/ranking and helper inventory.

#### Later verification updates

- Final CONSUMERS under retained CI target / Pydantic **2.5.0**:
  **927 passed, 1893 warnings in 28.50s**. The subprocess asserts
  `pydantic.__version__ == '2.5.0'` before invoking pytest. Warnings are the
  pre-existing protected `model_` namespace and Pydantic v1/v2 `__fields__`
  compatibility warnings; they were not suppressed or fixed in production.
  Read the domain-api-separation worktree's `tests/fixtures/api_route_contract_profiles.md`
  first. Used the existing target without installing, changing or deleting packages.
  Exact modifications to the isolation runner for both minimum-profile runs:

  ```powershell
  $runner=$runner.Replace('"PYTHONPATH": str(repo)', '"PYTHONPATH": r"C:/Users/xkx52/AppData/Local/Temp/medchat-domain-api-profiles-20260925-b831/ci" + os.pathsep + str(repo)')
  $runner=$runner.Replace('[sys.executable, "-B", "-m", "pytest"]','[sys.executable, "-B", "-c", "import sys, pydantic; assert pydantic.__version__ == ''2.5.0''; print(''PYDANTIC_PROFILE='' + pydantic.__version__); import pytest; sys.exit(pytest.main(sys.argv[1:]))"]')
  $runner | & $python -B -c "import sys; exec(sys.stdin.read())" @paths
  ```

- In-memory compile of all 12 changed/new Python files passed, as did
  `git diff --check`. An initial Get-Content-to-stdin compile attempt failed with
  PowerShell/Conda UnicodeEncodeError on a Chinese-containing existing test;
  byte-preserving compile corrected the *invocation*, not the files:

  ```powershell
  $files=@(git diff --name-only -- '*.py') + @(git ls-files --others --exclude-standard -- '*.py')
  & $python -B -c "from pathlib import Path; import sys; [compile(Path(p).read_bytes(), p, 'exec') for p in sys.argv[1:]]; print('COMPILED_FILES=' + str(len(sys.argv)-1))" @files
  git diff --check
  ```

- Parent then reported its root profile finished and explicitly released the
  long-test slot. Full isolated `tests/agent` was launched using the unchanged
  host runner above. This supersedes the earlier deferment; final result follows.

- First full Agent run: **16 failed, 6031 passed, 2 skipped, 7 warnings in 276.33s**.
  Skips: unavailable Windows directory symlinks and disabled harness performance
  test. Failures identified remaining shared-fixture indexing and deliberately
  contradictory legacy observations in independent downstream integrity tests.
  No production relaxation followed. Additional narrowly scoped fixture changes:
  - `test_decision_inputs.py`: SMILES and MW tampering now targets the real row /
    properties paths, retaining all downstream rejection and no-exposure assertions.
  - `test_decision_merge_blockers.py`: source-extension/NaN tampering uses row paths,
    so tests mutate the observation rather than accidentally raise TypeError.
  - `test_decision_spec_findings.py`: the oversized-extension fixture now has a
    complete property row; history tampering targets its nested MW. Deliberate
    success-plus-error seal/publication tests use an untyped counted
    `target_database_search` fixture, retaining provider-error, one-call,
    immutable-seal and no-scientific-rendering assertions.
  - `test_decision_migration_boundaries.py`: the same generic counted target-name
    fixture preserves the intentional contradictory observation reaching the
    independent downstream integrity gate. No real target tool is invoked.
  - Shared `CountingTool` retains its old arbitrary shape only for **non-analysis**
    fixture names. The test `run` helper permits explicit allowed-tool names;
    the real decision policy still owns and enforces its unchanged allowlist.
  An initial attempt used candidate_ranker for these decision-loop-only fixtures:
  **11 failed, 625 passed in 41.92s**, because the existing INITIAL_TOOLS policy
  excludes that name. Source inspection identified that policy boundary; choosing
  its already-allowed generic target name (not changing policy) yielded
  **11 passed in 3.92s** for the exact failing seal nodes.

- The preceding 636-test adversarial command was NEW plus
  `tests/agent/test_decision_inputs.py`, `test_decision_merge_blockers.py`,
  `test_decision_spec_findings.py`, `test_decision_migration_boundaries.py`.
  The exact 11-node-group retest used:
  `test_decision_spec_findings.py::test_publication_retry_cannot_replace_original_full_seal`,
  `::test_full_seal_precedes_every_publication`,
  `::test_error_cannot_be_erased_before_first_observation_seal`, and
  `test_decision_migration_boundaries.py::test_retained_tool_cannot_clear_error_to_promote_science`
  (each file under `tests/agent/`).

- Implementation/fixture code frozen for parent SPEC. Byte-preserving in-memory
  compile now passes **16 Python files**; `git diff --check` passes. Second full
  isolated Agent run started after the fixture corrections; no pass claimed yet.

- Parent has started independent SPEC review of the frozen implementation.
  Parent also flagged that target-name generic seal fixtures will hit the earlier
  typed boundary once 4B adds target contracts. The 4B worker is to replace these
  with a **test-double-only generic-adapter fixture** preserving the independent
  downstream tests, not repeatedly switch scientific tool names. No 4B production
  or test infrastructure is implemented in this increment; this is an explicit
  fixture-maintenance handoff, not an unresolved production contract exemption.

- **Second full Agent GREEN:** unchanged isolated host runner with only
  `tests/agent`: **6047 passed, 2 skipped, 7 warnings in 270.98s (4:30), exit 0**.
  Same two skips (Windows directory symlinks unavailable; optional harness
  performance test disabled). All first-run failure nodes passed. No model,
  external service, asset or secret activation was performed.

- Final **pre-SPEC-fix** minimum-profile command used the retained Pydantic 2.5.0
  runner above with NEW, `tests/agent/test_decision_inputs.py`,
  `test_decision_merge_blockers.py`, `test_decision_spec_findings.py`,
  `test_decision_migration_boundaries.py` (all four under tests/agent),
  `tests/test_agent_anti_hallucination_fallbacks.py`, and
  `tests/test_admet_predictor_fallback.py`: **646 passed, 4137 warnings in
  42.41s, exit 0**. Version assertion passed; existing protected-namespace and
  `__fields__` compatibility warnings remained visible.

- Independent SPEC identified P1: the worker checked the normalized result,
  but inherited execute called the same scientific validator again on the caller
  after slot release. The previous thread test checked only the first normalized
  check. The 6047/646 results above describe this **pre-fix version only**, not
  acceptance of the forthcoming deadline/slot fix. New RED must inspect every
  scientific validation and prove deadline/slot ownership before a minimal fix.

#### SPEC P1 deadline/slot correction

- RED used the unchanged isolated runner and these exact nodes under
  `tests/agent/test_analysis_contract.py`:
  `::test_every_output_validation_owns_worker_and_slot`,
  `::test_caller_only_slow_validation_cannot_escape_deadline`,
  `::test_each_validation_stage_is_deadline_bounded_and_holds_slot`.
  **12 failed, 6 passed in 1.84s**. All three tools' success/partial/failure
  paths showed a third check on the caller with a free invocation slot. The
  caller-only 80ms delay produced successes at 83.68/93.99/90.74ms despite a
  30ms deadline. An initial `-k` invocation collected nothing (exit 4) because
  this runner accepts paths/node IDs only; it was replaced with the exact nodes.
- Minimal implementation changes are confined to AnalysisToolAdapter. Both
  raw checking and complete normalized checking run in `_invoke_guarded`.
  The existing superclass data normalization/redaction also runs there, **before**
  the normalized check. The outer `_normalize` only fills a missing elapsed_ms
  with the framework-generated integer. The outer `_validate_output` returns
  either this checked result or an existing framework-generated no-data error.
  No mutable validated flag, identity cache, generic adapter change, new worker,
  retry change, or resource-ownership change was introduced.
- This does **not** assume redaction preserves domain fields: custom sensitivity
  policies can replace known numeric fields with strings, and tests confirm that
  is rejected by the worker's final check. Existing malformed-normalization tests
  now patch ToolAdapter's actual data-transforming `_normalize`, not the outer
  timing-only hook. Their invalid warnings/elapsed assertions remain unchanged.
  Separate redaction blocking tests verify its thread, deadline and retained slot.
- Exact RED nodes after fix: **18 passed in 1.49s**. Focused command with NEW,
  `tests/agent/test_tool_adapter_compat.py`, `test_tool_adapters.py`,
  `test_tool_registry.py`, `test_registration_consistency.py` (all tests/agent):
  **503 passed, 7 existing warnings in 7.06s**. Earlier full/minimum counts are
  not used as evidence for this correction; fresh runs follow.

- **Post-P1 full Agent GREEN:** unchanged isolated host runner with
  `tests/agent`: **6068 passed, 2 skipped, 7 warnings in 291.87s (4:51), exit 0**.
  Same Windows symlink and disabled-performance skips; same SWIG/FastAPI
  deprecation warnings. This run includes the P1 implementation and all its new
  regression tests. Byte-preserving compile of 16 Python files and
  `git diff --check` also passed after the correction.

- **Post-P1 Pydantic 2.5.0 GREEN:** retained minimum-profile runner, same seven
  paths as the pre-fix 646-test run above: **667 passed, 4137 warnings in
  45.28s, exit 0**. Pydantic version assertion passed. Warnings remain the same
  pre-existing namespace and `__fields__` compatibility categories. No dependency
  changes or target cleanup. Implementation/tests remain frozen for independent
  SPEC re-review then QUALITY; no approval, commit, PR, CI or merge is claimed.

## Parent verification and independent SPEC checkpoint

Independent post-P1 SPEC approved the frozen implementation, with its own503 passing focused tests and135 malformed-output injections. It did not repeat the worker's long run. Independent QUALITY is in progress; no publication approval is implied.

Parent first attempted the isolated runner with an incorrect test filename `tests/agent/test_domain_validators.py`: exit4, no tests ran. Corrected parent scope `tests/agent/test_analysis_contract.py tests/agent/test_registration_consistency.py`: **472 passed,7 existing warnings in8.04s**, exit0. The actual existing domain file is `tests/agent/test_domain_result_validators.py`; the wrong-path command is not a product-test failure or a passing run.

CI infrastructure PR #65 has since merged independently; publication must integrate current main and require all8 checks, not the earlier7. No production analysis code changed for that documentation correction.

Independent QUALITY approved the same implementation:11 focused files895 passed in49.13s, exit0;16 Python files memory-compiled and diff check passed. It independently checked deadline/slot ownership, raw-validator ordering, retries, non-projecting semantics, mutable result handling and the consumer-fixture changes. No additional full run, live service or publication was claimed by the reviewer.

Reviewed implementation committed as a80df26, then main ecd6cca (reviewed PR #64 plus CI PR #65) merged without conflicts. Parent post-integration scope: tests/agent/test_analysis_contract.py, test_registration_consistency.py, test_domain_result_validators.py plus tests/test_api_route_boundary.py, test_quality_workflow_contract.py and test_deployment_assets.py:586 passed,7 existing warnings in12.52s, exit0. No analysis production change occurred during the integration. Full8-check CI is still required before publication merge.
