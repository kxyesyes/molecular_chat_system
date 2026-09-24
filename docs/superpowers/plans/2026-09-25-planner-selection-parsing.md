# Planner selection/parsing implementation

Use TDD, independent SPEC then QUALITY. Only task_planner.py, two pure modules, tests/agent/test_planner_responsibilities.py and this batch's docs.

- [ ] Baseline test_task_planner.py, test_planner_step_templates.py, test_target_identity_alignment.py, test_target_selection_phrase.py, test_prompt_acceptance.py via isolated runner.
- [ ] Add explicit selector dispatch table tests and exact predicate call count; add helpers preserving metadata presence (not truthiness), invalid count propagation, target ambiguity, top-N clamping, custom facade override behavior and WorkflowPlan identity.
- [ ] Run RED before modules exist; keep existing complete plan snapshots unchanged.
- [ ] Add pure selection function and parsing functions by moving existing expressions unchanged. TaskPlanner wrappers delegate; _plan uses selection key without changing target/docking/unknown assembly logic.
- [ ] Run focused GREEN and full Agent, memory compile/diff check. Check no production parser/template/router/evidence policy changes or duplicate parser implementation.
- [ ] Append actual results, obtain independent SPEC/QUALITY, scoped commit and draft PR. Integrate latest main mechanically before current-head CI and authorized squash; no deployment/model activation.
