# Domain API separation checkpoint

Date: 2026-09-25. Work package 3; branch `codex/domain-api-separation`. Code extraction and review are in progress, not complete. The branch started from PR #63's reviewed head `d4860d7`; PR #63 subsequently squash-merged as `aa86377c60ff4c8a0457dc28ab6a9d6b4856c194` after CI7/7 and exact-head/review checks. Merged and reviewed trees both equal `b52c7c751a855621a996bdbb077f9ec909bca166`. Synchronize that main ancestry before publication; no deployment occurred.

## Baseline evidence

Read-only AST and isolated registration inspection corrected the initial route estimate: **30 operations**, 14 POST / 13 GET / 3 DELETE, 29 OpenAPI paths. Existing independent registrars are outside this count. All 30 must retain order, names, parameters, responses and per-app injected ownership.

Parent ran the existing isolated runner (documented in `docs/superpowers/plans/2026-09-24-rag-service-extraction.md`, repository path substituted) with MedChat Python before any source edits:

```text
python -B -m pytest tests/test_docking_agent_architecture.py tests/test_docking_configuration.py tests/test_docking_history_index.py tests/test_temporal_docking_routes.py tests/test_reverse_target_health.py tests/test_reverse_target_pharmacophore.py tests/test_activity_prediction_contract.py tests/test_activity_family_api.py tests/test_admin_auth_routes.py -q -p no:cacheprovider --tb=short -rs
```

Result: **169 passed, 1 skipped, 7 warnings, 11 subtests passed in 46.90s**, exit 0. Skip: POSIX process-group regression on Windows. Existing SWIG/FastAPI deprecation warnings. This is a baseline, not post-extraction proof or real scientific acceptance.

## Known separate issue

The current properties endpoint checks for an ADMET private method absent from the current tool. Its heuristic branch includes a fixed low hepatotoxicity label without a method/provenance marker. It must not be described as genuine model inference. This move will characterize the old response without endorsing it; a separate bounded scientific fix is required before the final acceptance package. Do not silently alter this behavior or HTTP failure status while moving routes.

## Pending

Task1–3 initial implementation completed: old registration fixture1 passed; boundary RED9 failed/1 passed (8 missing modules plus missing delegation); full original behavior characterization59 passed/9 deselected; extraction GREEN68 passed. New tests plus original nine files237 passed/1 skipped/7 warnings/11 subtests. All30 operations have controlled endpoint calls, not just FastAPI pre-handler validation.

Parent joint command: the isolated runner over `tests/agent`, `tests/test_api_route_boundary.py`, the original nine files above, plus `tests/test_web_app_lifecycle.py`, `tests/test_model_request_lifecycle.py`, `tests/test_design_model_switch.py`, `tests/test_phase2_phase3_routes.py`, `tests/test_static_placeholder_cleanup.py`, `tests/test_main_routes_template_compat.py`. Result: **5965 passed, 3 skipped, 7 warnings, 11 subtests passed in 316.82s**, exit0. Skips: Windows directory symlink unavailable, disabled performance test, POSIX process-group test. This is the named joint scope, not all repository tests.

Parent also ran the five homepage Node contracts (scientific references, structured molecules, task panel, workflow completion, safe rendering), `git diff --check`, and in-memory compilation of322 source/script/test files: all passed. No bytecode was generated; CI separately runs compileall.

Compatibility finding: isolated original/new comparison is equal within both local FastAPI0.135.3/Pydantic2.12.5 and CI0.104.1/2.5.0. Cross-profile schema snapshots differ at24 locations: nine binary-upload nodes, ValidationError input/ctx declarations and four dictionary additionalProperties descriptions. Actual validation error bodies can also differ by framework-provided URL fields. The single local fixture therefore cannot serve as the CI baseline. Test-only profile snapshots from the original registrar are being added; production files are unchanged. Deployment0.115.6/2.10.4 still needs its own verification. The earlier joint run precedes this fixture correction and is not presented as its validation.

SPEC review is in progress pending profile correction; QUALITY, final test-only reruns, docs, PR/CI and merge remain pending. No production assets, real model, deployment or original dirty-tree edits are part of this package.
