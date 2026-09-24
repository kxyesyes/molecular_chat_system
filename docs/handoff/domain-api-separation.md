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

New baseline characterization, RED/module boundary, extraction GREEN, all-operation behavior coverage, independent SPEC/QUALITY, broader regression, docs, PR/CI and merge remain in progress. No production assets, real model, deployment or original dirty-tree edits are part of this package.
