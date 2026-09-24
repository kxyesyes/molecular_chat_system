# Planner selection/parsing implementation

Use TDD, independent SPEC then QUALITY. Only task_planner.py, two pure modules, tests/agent/test_planner_responsibilities.py and this batch's docs.

- [ ] Baseline test_task_planner.py, test_planner_step_templates.py, test_target_identity_alignment.py, test_target_selection_phrase.py, test_prompt_acceptance.py via isolated runner.
- [ ] Add explicit selector dispatch table tests and exact predicate call count; add helpers preserving metadata presence (not truthiness), invalid count propagation, target ambiguity, top-N clamping, custom facade override behavior and WorkflowPlan identity.
- [ ] Run RED before modules exist; keep existing complete plan snapshots unchanged.
- [ ] Add pure selection function and parsing functions by moving existing expressions unchanged. TaskPlanner wrappers delegate; _plan uses selection key without changing target/docking/unknown assembly logic.
- [ ] Run focused GREEN and full Agent, memory compile/diff check. Check no production parser/template/router/evidence policy changes or duplicate parser implementation.
- [ ] Append actual results, obtain independent SPEC/QUALITY, scoped commit and draft PR. Integrate latest main mechanically before current-head CI and authorized squash; no deployment/model activation.

## Evidence

Existing planner/template/target/prompt baseline562 passed3.97s. New boundary tests RED29 failed4 passed1.19s (missing pure modules/delegation). Extraction GREEN595 passed3.69s, including unchanged full-plan characterization. Public WorkflowPlan identity, private helper signatures and count override hooks retained; no grammar/template changes. Full Agent and independent reviews remain pending.

## Independent reviews and retained release blocker

Independent SPEC approved:5880 selection differentials,192 override cases and104 focused tests. Independent QUALITY approved:321 tests in2.52s,520 differentials and AST comparison of six helpers/WorkflowPlan/plan/signatures. Neither changes the following failed full-run evidence.

Parent full Agent:1 failed,5665 passed,2 skipped,7 warnings in294.49s, exit1; existing test_docking_tool_contract.py::test_validated_snapshot_backend_applies_to_every_outer_artifact[False-quality]. Focused four parameters subsequently passed1.15s, which is not closure.

Independent diagnostic whole docking module247 passed4.43s; normal-order full Agent1 failed,5665 passed,2 skipped274.66s, moved to test_snapshot_sandbox_requirement_is_monotonic[False-False-outer_data3]. A subsequent passive-instrumented full run5666 passed2 skipped267.59s does not erase either failure. Controlled sibling-only churn reproduced the original unchanged test twice (each1 failed,0.97s/1.10s); file identity/content/hash stayed unchanged and only an ancestor mtime changed. The first paced attempt1 passed1.02s and no-churn control4 passed1.07s are also retained. Detailed source diagnostic lives only in ignored scratch/package5-docking-diagnostic.md, not a release artifact.

Confirmed separate defect: secure_io.read_file_snapshot compares child-driven directory timestamps as though they were file content versions. Historical full failures did not retain low-level causes, so unique attribution is not claimed. Fix is isolated on codex/snapshot-directory-identity with its own TDD/review/CI; no Planner assertion or timeout was weakened. This batch remains unpublished until that dependency is independently accepted and an integrated full regression passes.
