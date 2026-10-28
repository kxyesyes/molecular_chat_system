# Comparison boundary implementation plan

1. Reproduce the retained nine failures in the comparison regression module.
2. Add a shared comparison admission view; cover complex operands and unknown
   metric rejection, and integrate early admission with full-Web coverage.
3. Independently test/fix English prose punctuation in activity input parsing,
   preserving explicit-field and whole-structure negative cases.
4. Test/fix requested-tool-driven comparison table columns without raw mutation.
5. Run focused and affected regressions, syntax checks and compileall. Conduct
   independent specification review followed by quality review.
6. Restart only the owned test server, using frontend-configured Agent settings;
   exercise successful and rejected requests through the actual page and inspect
   result details. Record exact outcomes and any remaining limitations.

Implementation uses TDD. The parent owns admission/coverage and table projection;
a bounded implementation subagent owns activity punctuation and its tests.
No implementation commit or PR until the combined changes are verified.

## Verification checkpoint

- Retained baseline: 91 passed / 9 failed. New direct-coverage and column tests
  demonstrated six additional failures; both operand-masking review findings
  were reproduced with failing tests and fixed with a closed list grammar.
- Initial combined regression: **513 passed**, seven existing SWIG/FastAPI
  deprecation warnings. Activity-focused expanded regression: **1,154 passed**,
  three SWIG warnings. These runs overlap and must not be summed.
- Node detail, comparison-table and decision-runtime contracts passed; relevant
  JS syntax checks, targeted `compileall` and `git diff --check` passed. Git
  reports the existing controller CRLF normalization warning only.
- Specification review passed after both masking findings were fixed. Final
  code quality review passed, including 55 focused tests and 26 vocabulary
  probes. This focused acceptance is not approval of the entire dirty checkout.
- Quality review additionally exposed duplicate-input exponential backtracking,
  multiline obligation loss, and RDKit's acceptance of a valid prefix before a
  Unicode suffix. Each was reproduced and fixed: deterministic list splitting,
  strict single-field checks, and printable-ASCII whole-structure validation.
  Final expanded shared-parser regression passed **1,684 tests** with seven
  existing warnings in 156.31 seconds, after mixed-language/tool-vocabulary
  alignment. Earlier overlapping test counts must not be summed.
- Real frontend: English `Compare properties and BuChE activity for CCO and CCN.`
  completed requested execution, retained the actual inconsistent CCN result as
  partial, and showed no unrequested ADMET column. Expanded details listed
  exactly two steps: property calculation and activity prediction.
- Real frontend: ADMET + activity with explicit property exclusion displayed
  only the ADMET descriptive table and activity results. The immediate ranking
  follow-up reused prior evidence and retained the exact CCO prediction and
  CCN exclusion reason. The established named aspirin/paracetamol comparison
  completed all three tools and displayed both ranked candidates again.
- Real frontend: short IC50, FooMetric and LogD embedded in the operand clause
  rejected as whole requests. Offline tests additionally assert zero endpoint
  execution for unsupported metrics.
- Final-code server restart acceptance: the established named complex-SMILES
  comparison completed at 13:59 with both ranked candidates and the unchanged
  actual predictions. An attached Unicode obligation (`CCN预测量子轨道能量`)
  rejected at 14:00. A pasted newline request also rejected, but the single-line
  HTML input normalized its newline away; literal LF/CR/CRLF validation is
  covered by offline tests, not claimed as a literal-newline browser test.
- Agent stayed on the existing frontend-configured `deepseek-v4-pro`; generator
  configuration was not changed. No scientific values or raw provenance were
  rewritten. Runtime screenshots remain ignored output artifacts.
- Free-form unquoted labels remain deliberately bounded; names outside the
  documented compatibility labels require explicit SMILES lists. This patch
  does not introduce a general molecular-name resolver.
- Branch: `codex/feat/pi-baseline-migrate-worktree`; existing HEAD `95c29f8b`.
  No implementation staging/commit, PR or merge. Unrelated dirty files remain.
- Subsequent [responsive and Stop acceptance](2026-10-26-home-chat-responsive.md)
  completed 390/768/1280 geometry and cooperative cancellation/recovery. It
  records a new comparison-prose false rejection and task-panel-position issue;
  reverse-target startup data prewarm remains unavailable. No
  deployment/data changes were made, so strict deployment health checks were
  not rerun for this input-boundary patch.

Final Python command (existing scientific Python environment):

```powershell
python -m pytest tests/agent/test_browser_comparison_without_ranking.py tests/agent/test_browser_evidence_followup.py tests/agent/test_web_profile_requirements.py tests/agent/test_hybrid_router.py tests/agent/test_scientific_obligations.py tests/agent/test_web_scientific_execution.py tests/agent/test_result_followup_delivery.py tests/agent/test_compiled_admet_summary.py tests/agent/test_optional_failure_summary.py tests/agent/test_activity_prose_punctuation.py tests/agent/test_explicit_molecular_input.py tests/agent/test_admet_whole_input.py tests/agent/test_generator_optimization_input.py tests/agent/test_reverse_target_complete_input.py tests/agent/test_activity_input_boundaries.py tests/agent/test_activity_named_molecule_input.py tests/agent/test_activity_design_role_input.py tests/agent/test_family_activity_tool.py tests/agent/test_activity_tool_contract.py tests/agent/test_activity_contract_integration.py tests/agent/test_activity_checkpoint_identity.py tests/agent/test_partial_activity_workflow.py -q --tb=short
```

Final syntax and frontend contract commands (all passed):

```powershell
node tests/home_result_details_test.js
node tests/home_comparison_table_test.js
node tests/home_decision_runtime_test.js
node --check src/web/static/js/home/result_details.js
node --check src/web/static/js/home/chat_renderer.js
node --check src/web/static/js/home/formatters.js
node --check src/web/static/js/home/main.js
python -m compileall -q src/agent/planning/comparison_request.py src/agent/tools/activity_input.py src/agent/evidence_followup.py src/agent/scientific_session/web_requirements.py src/web/scientific_obligations.py src/system/molecular_input_parser.py tests/agent/test_browser_comparison_without_ranking.py tests/agent/test_activity_prose_punctuation.py tests/agent/test_explicit_molecular_input.py
git diff --check
```

Changed production files for this boundary follow-up:

- `src/agent/planning/comparison_request.py`
- `src/agent/tools/activity_input.py`
- `src/agent/evidence_followup.py`
- `src/agent/scientific_session/web_requirements.py`
- `src/web/scientific_obligations.py`
- `src/system/molecular_input_parser.py`
