# Reverse-target whole-input implementation

Use TDD and independent SPEC then QUALITY review. Only reverse_target_tool.py, focused tests and these documents may change. No original dirty checkout edits.

- [ ] Baseline relevant current tests using the isolated runner in the RAG extraction plan with this worktree path.
- [ ] Add tests/agent/test_reverse_target_complete_input.py. Counting predictor receives the exact submitted valid structure and kwargs; malformed `CCO)((`, `CC(C)((`, bracket/salt suffixes and mixed batches must receive zero calls and return no data.
- [ ] Run RED with the unchanged real ReverseTargetTool. Record failures rather than claiming the parser was already fixed.
- [ ] Import the existing parser and unavailable exception. In should_use catch ValueError and abstain. In execute parse after the existing RDKit gate; catch unavailable before ValueError, return error codes tool_unavailable/validation_error respectively. Keep `smiles_list[0]` and predictor options unchanged.
- [ ] Run focused GREEN, existing reverse/domain/factory tests and full Agent regression; no external dependencies enabled. Syntax/diff checks.
- [ ] Independent SPEC then QUALITY; record findings and exact test results. Scoped commit/draft PR, latest checks and review status before authorized squash merge. No deployment.

## Execution evidence

Baseline domain validators and registration66 passed7 warnings4.61s. New real-tool/RDKit tests RED20 failed11 passed1.01s: malformed CCO prefixes reached the predictor, valid bracket/isotope/percent-ring structures were omitted and unavailable validation was ignored. Minimal parser reuse GREEN: focused31 plus unchanged66 tests,97 passed7 warnings3.42s. No real prediction database or external model was used. Full Agent run and independent reviews remain release gates.

Full isolated tests/agent:5664 passed,2 skipped,7 warnings281.52s. Skips: Windows directory-symlink unavailable and explicitly disabled performance test. Independent SPEC approved, reproduced the20/11 RED baseline in memory, independently ran97 GREEN and10 extra checks. QUALITY and CI remain pending.

Independent QUALITY approved:31 passed0.34s and16 additional checks, including real RDKit exception, validation-before-prediction ordering, exact structure preservation and memory compilation. Its first isolation/encoding wrapper attempts failed before product testing, then were corrected and rerun. Parent memory-compiled both changed Python files and diff-check passed. CI remains pending; no real predictor result is claimed.

Before publication, merged reviewed main68b8538 (PR #65 CI workload partition) without conflicts. No reverse-target production changes were introduced by this merge. Same isolated runner over tests/agent/test_reverse_target_complete_input.py, tests/test_quality_workflow_contract.py and tests/test_deployment_assets.py:48 passed,7 existing warnings in4.81s, exit0. New CI requires8 checks; PR publication and exact-head merge gate remain pending.

PR #66 was published at4dd2725; its first8 checks all passed. PR #64 then merged as ecd6cca, so this branch integrated that reviewed API split without conflicts before its own merge. Same isolated runner over tests/agent/test_reverse_target_complete_input.py, tests/test_reverse_target_health.py and tests/test_api_route_boundary.py:118 passed,2 subtests passed in12.32s, exit0. The changed head needs fresh8-check CI; old passing checks do not substitute for that gate.
