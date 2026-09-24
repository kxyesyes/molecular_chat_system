# Reverse-target whole-input implementation

Use TDD and independent SPEC then QUALITY review. Only reverse_target_tool.py, focused tests and these documents may change. No original dirty checkout edits.

- [ ] Baseline relevant current tests using the isolated runner in the RAG extraction plan with this worktree path.
- [ ] Add tests/agent/test_reverse_target_complete_input.py. Counting predictor receives the exact submitted valid structure and kwargs; malformed `CCO)((`, `CC(C)((`, bracket/salt suffixes and mixed batches must receive zero calls and return no data.
- [ ] Run RED with the unchanged real ReverseTargetTool. Record failures rather than claiming the parser was already fixed.
- [ ] Import the existing parser and unavailable exception. In should_use catch ValueError and abstain. In execute parse after the existing RDKit gate; catch unavailable before ValueError, return error codes tool_unavailable/validation_error respectively. Keep `smiles_list[0]` and predictor options unchanged.
- [ ] Run focused GREEN, existing reverse/domain/factory tests and full Agent regression; no external dependencies enabled. Syntax/diff checks.
- [ ] Independent SPEC then QUALITY; record findings and exact test results. Scoped commit/draft PR, latest checks and review status before authorized squash merge. No deployment.
