# Domain API route separation design

Date: 2026-09-25. Work package 3 of the remaining-through-step8 objective. User delegated recommended implementation choices and quality-gated PR merges. No deployment or real model activation.

## Verified baseline and scope

The mixed registrar contains **30** operations, not the initial 31 estimate: 14 POST, 13 GET, 3 DELETE, 29 OpenAPI paths. Only durable docking explicitly declares success status 202. Existing separate task/design/target/workflow/scientific-reference/system registrars are outside this move.

Retain setup_api_routes(app, docking_service=None, task_runtime=None), its imports/exports and return None. Use domain setup functions, not a new router framework/service pool. Preserve exact endpoint names, method/path/order/signatures/defaults/aliases/docstrings and OpenAPI semantics. Do not repair unrelated response inconsistencies as part of the move.

## Modules in registration order

1. docking_routes.setup_docking_routes: original first 10 operations, durable tasks, submit, env_check, batch_submit, status, result, pose_sdf, GET history, DELETE history, DELETE history/job.
2. molecule_utility_routes.setup_molecule_utility_routes: docking/smiles_to_3d, utils/smiles_to_image, utils/mcs.
3. docking_report_routes.setup_docking_report_routes: docking/report/job.
4. reverse_target_routes.setup_reverse_target_routes: predict, batch_predict, stats, health, similar_molecules, predict_3d, pharmacophore.
5. activity_prediction_routes.setup_activity_prediction_routes: predict, batch_predict.
6. activity_model_routes.setup_activity_model_routes: train, train/status/job, models, models/switch, DELETE models/id.
7. molecule_properties_routes.setup_molecule_properties_routes: molecule/properties.
8. agent_metrics_routes.setup_agent_metrics_routes: agent/metrics.

All in src/web/routes. Local pose extraction helpers move with docking. Internal MCS helpers stay inside their endpoint.

## Compatibility support and lifetime

The original api_routes module retains existing 11 top-level helpers, logger and pharm3d executor/semaphore as a deliberate compatibility support boundary. Domain registrars receive a private keyword-only _support module reference; call its helper attributes at execution time. No child module imports the facade, no duplicated algorithms, no early snapshots of monkeypatchable helpers. This is registration dependency injection, not an HTTP request parameter or a new global registry.

Preserve registration-before/after monkeypatch behavior for _invoke_in_threadpool, run_in_threadpool, logger; retain existing deferred imports of predictor/refiner/trainer so lazy initialization and module injection still work. Preserve tempfile module attribute calls, environment timing and exception scopes.

The pharm3d executor/semaphore remain one set per existing module lifetime, not per app. Queue waiting remains outside the current execution timeout. Do not add shutdown or force-cancel semantics. Shared helpers remain dynamically resolved as before.

The runtime getter closure moves with docking and resolves once per durable request, never during registration. None and getter errors remain 503; no implicit runtime construction or caching; synchronous submit never resolves it. App/TaskRuntimeBinding retain ownership. Two app instances retain distinct docking/runtime closures.

## Scientific and error boundaries

No formulas, fields, status mappings, fallback behavior, upload budgets, provenance or cleanup ownership change. Preserve existing warnings redaction, candidate ordering, report media headers, temporary-file transfer to training, threadpool execution of synchronous/awaitable predictors, durable parameter limits, and 3D timeout/explicit fallback behavior.

Known separate issue: properties currently tests for an absent ADMET private method and therefore emits heuristic/fixed ADMET labels without provenance. Characterization of this old behavior is NOT scientific validation. Register a separate fix after extraction rather than hiding it in a move. Missing SMILES/metrics HTTP200 failure shapes also remain unchanged in this refactor.

## Tests and completion

Before moving, create explicit fixed 30-route manifest and normalized OpenAPI/parameter characterization independent of the post-move implementation. Record RED for module/delegation boundary tests. Preserve existing behavior tests; no need to relocate source-text assertions (none directly lock these endpoint locations). Extend tests for per-app service isolation, lazy runtime resolution, dynamic old-path patches, exact registration order and one controlled branch of each operation. No external model/service calls or production assets.

Version-profile clarification after isolated probing: FastAPI/Pydantic change upload/schema descriptions across versions. Compare full fixed baseline snapshots captured from the original registrar for each verified profile (CI 0.104.1/2.5.0, deployment 0.115.6/2.10.4, local 0.135.3/2.12.5). Identical snapshots may share a file. Do not erase upload/ValidationError descriptions to force cross-version equality. Unknown profiles fail explicitly with baseline-update guidance; never derive expected schema from the refactored implementation or skip the check. Same-profile before/after behavior is the equivalence target, not equality between different framework versions.

Parent runs focused route suites, broader integration and Agent regressions, compile/Node checks as relevant, independent SPEC then QUALITY review and CI. Update docs and ledger. Only then publish/merge exact reviewed head under default authorization. Package 3 is done only when all 30 operations are attributed and compatible, not merely when api_routes.py is shorter.
