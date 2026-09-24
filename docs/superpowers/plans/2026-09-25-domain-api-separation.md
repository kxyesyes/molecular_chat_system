# Domain API separation implementation plan

> **For agentic workers:** Use subagent-driven-development and TDD. Independent SPEC then QUALITY review is required. Do not change main or publish before the parent verifies the prerequisite PR #63 merge.

**Goal:** Move all 30 operations from the mixed registrar into focused domain modules while retaining runtime HTTP and lifecycle behavior.

**Architecture:** Eight domain setup functions in original registration order. `setup_api_routes` remains the facade; its existing helpers/logger/executor/semaphore remain a compatibility support boundary dynamically injected into domain registrars. No APIRouter, model/service factory or extra state is introduced.

**Tech Stack:** Existing FastAPI, Python 3.10, pytest/TestClient, existing threadpool and temporary-file fixtures.

Design: `docs/superpowers/specs/2026-09-25-domain-api-separation-design.md`. Recommended choices and quality-gated merges are delegated by the user. Prerequisite branch was created from PR #63's reviewed head; synchronize the squash-merged main before publishing this package, preserving only this package's changes.

## Task 1 — Freeze observable registration and behavior

Create `tests/test_api_route_boundary.py` and, if needed, a checked text-only `tests/fixtures/api_route_contract.json` captured from the original registrar. Do not modify old tests or generate expected values from the new implementation.

- [ ] Inspect all original 30 decorated endpoints; count methods (POST14/GET13/DELETE3), names/order, exact parameters and schema. Reconfirm actual parameter counts from signatures, not old narrative estimates.
- [ ] Freeze an explicit expected route manifest and normalized OpenAPI contract. A compact runtime assertion has this shape:

```python
def route_manifest(app):
    from fastapi.routing import APIRoute
    return [(sorted(route.methods), route.path, route.name, route.status_code)
            for route in app.routes if isinstance(route, APIRoute)]

def test_registration_contract():
    from fastapi import FastAPI
    from src.web.routes.api_routes import setup_api_routes
    app = FastAPI()
    assert setup_api_routes(app) is None
    assert len(route_manifest(app)) == 30
    assert route_manifest(app)[0] == (
        ["POST"], "/api/docking/tasks", "submit_durable_docking_task", 202)
    assert route_manifest(app)[-1] == (
        ["GET"], "/api/agent/metrics", "get_agent_metrics", None)
```

The complete test must compare all 30 manifest entries, not only endpoints shown here. Keep names, OpenAPI request/response schema refs, defaults, aliases, ranges and required fields fixed; exclude only Python module/qualname, which necessarily move.

Profile follow-up: capture full original-registrar fixtures for the verified CI, deployment and local FastAPI/Pydantic pairs listed in the design; select explicitly by installed versions and fail unknown profiles. Preserve the local snapshot, share genuinely identical profile snapshots, and document regeneration from the old revision. First reproduce the existing fixed-fixture failure under the CI profile, then rerun boundary tests under all three isolated profiles. Do not normalize away upload or ValidationError descriptions or change production code for this test-only compatibility issue.

- [ ] Run characterization against the baseline before moving. Use the existing isolated runner from `docs/superpowers/plans/2026-09-24-rag-service-extraction.md` with this worktree substituted. Never import app from a user-configured cwd or load runtime assets.
- [ ] Add boundary tests requiring all eight modules and their registration ownership; prove RED due to absent domain modules/delegation. Imports should occur inside tests to distinguish intentional failures from fixture-collection errors.
- [ ] Preserve existing runtime behavior assertions; add fixed assertions for any uncovered operation using fake services/temporary inputs. Do not bless an ADMET heuristic as genuine inference.

Focused baseline command (through the isolated runner):

```text
python -B -m pytest tests/test_api_route_boundary.py tests/test_docking_agent_architecture.py tests/test_docking_configuration.py tests/test_docking_history_index.py tests/test_temporal_docking_routes.py tests/test_reverse_target_health.py tests/test_reverse_target_pharmacophore.py tests/test_activity_prediction_contract.py tests/test_activity_family_api.py tests/test_admin_auth_routes.py -q -p no:cacheprovider --tb=short -rs
```

## Task 2 — Mechanical move with a thin compatibility facade

Modify only `src/web/routes/api_routes.py` and add:

- `src/web/routes/docking_routes.py`
- `src/web/routes/molecule_utility_routes.py`
- `src/web/routes/docking_report_routes.py`
- `src/web/routes/reverse_target_routes.py`
- `src/web/routes/activity_prediction_routes.py`
- `src/web/routes/activity_model_routes.py`
- `src/web/routes/molecule_properties_routes.py`
- `src/web/routes/agent_metrics_routes.py`

- [ ] Move endpoint blocks unchanged from the original source by named function boundaries. Preserve decorators, signatures, docstrings, deferred imports, catches/finally and internal helpers. Names/ranges at baseline: docking `submit_durable_docking_task` through `delete_docking_job` (including pose helpers); utilities `smiles_to_3d` through `get_mcs`; report `get_docking_report`; reverse `reverse_target_predict` through `get_pharmacophore`; inference `activity_predict`/`activity_batch_predict`; models `start_activity_training` through `remove_activity_model`; properties `calculate_molecule_properties`; metrics `get_agent_metrics`.
- [ ] Qualify original shared helper/logger accesses through `_support` rather than copying their implementations or importing the facade. Keep annotations bound to imported FastAPI/typing objects as before. For example, the inference closure still does:

```python
def setup_activity_prediction_routes(app, *, _support):
    @app.post("/api/activity/predict")
    async def activity_predict(smiles: str = Form(...), target: Optional[str] = Form(None)):
        """活性预测 API"""
        try:
            def run_activity_prediction():
                from src.activity.prediction_service import predict_activity
                return predict_activity(smiles, target=target)
            return await _support._invoke_in_threadpool(run_activity_prediction)
        except HTTPException:
            raise
        except Exception:
            _support.logger.error("活性预测请求失败")
            raise HTTPException(status_code=500, detail="活性预测服务不可用")
```

This is a relocation example, not permission to omit the batch endpoint. Each original body remains authoritative, including known historical quirks.

- [ ] Keep all 11 facade helper algorithms and module-level resource identities unchanged. Add explicit registrar imports and use this delegation order (each imported from its same-named module):

```python
def setup_api_routes(app, docking_service=None, task_runtime=None):
    support = sys.modules[__name__]
    setup_docking_routes(app, docking_service=docking_service,
                         task_runtime=task_runtime, _support=support)
    setup_molecule_utility_routes(app, _support=support)
    setup_docking_report_routes(app, docking_service=docking_service, _support=support)
    setup_reverse_target_routes(app, _support=support)
    setup_activity_prediction_routes(app, _support=support)
    setup_activity_model_routes(app, _support=support)
    setup_molecule_properties_routes(app, _support=support)
    setup_agent_metrics_routes(app, _support=support)
```

The three utility endpoints use their current local RDKit imports and do not consume the injected docking service; keep their setup signature as shown. Keep `current_task_runtime` a per-registration closure inside docking, resolving only on durable requests.

- [ ] Run boundary and existing focused suites to GREEN. Compare original/new endpoint ASTs accounting only for lexical relocation/support qualification, but do not use AST equality as a substitute for behavior tests.

## Task 3 — Integration edge cases and ownership

Extend only `tests/test_api_route_boundary.py` unless a new separate test file materially improves clarity.

- [ ] Two apps with distinct fake docking services/runtime getters: setup calls getters zero times, repeated durable requests resolve again, no cross-app service leak, getter failure/None returns503, synchronous submit never touches getter.
- [ ] Old `api_routes._invoke_in_threadpool`, `run_in_threadpool` and `logger` patches before and after setup remain observable. Do not replace old behavioral tests with static import checks.
- [ ] Executor/semaphore identity does not change across setup calls. Preserve original timeout/queue behavior and no new shutdown obligations.
- [ ] Preserve threadpool execution of awaitable predictors, upload/response order, warnings redaction, image/report media types and request budgets, pose helper behavior, 3D timeout/fallback and training file handoff/cleanup. Existing tests can supply coverage; list which assertions cover each behavior instead of duplicating them.
- [ ] Verify all 30 operations have at least one controlled runtime success/failure test, adding missing branches without accessing real databases/models. Record any untestable behavior explicitly rather than inventing coverage.

## Task 4 — Parent verification, documentation and release

- [ ] Parent runs focused routes plus existing app/lifecycle/root integration and Agent regressions in isolation. Report actual scope, counts, skipped reasons and warnings. Compile `src/scripts` and run existing Node contracts as required by CI; never claim skipped dependencies passed.
- [ ] Independent SPEC then QUALITY review. Fix valid findings and rerun affected tests; no unresolved findings before publication.
- [ ] Update `docs/AGENT_MAINTENANCE.md`, `docs/handoff/latest.md`, new `docs/handoff/domain-api-separation.md`, and the package3 row in `docs/handoff/remaining-through-step8.md`. Record the separate heuristic ADMET issue for bounded follow-up before final scientific acceptance.
- [ ] Confirm PR #63 merged and synchronize main without deleting user changes or rewriting a shared branch. Explicitly stage task files, commit, create/attach draft PR. Recheck exact head, required CI and review threads before authorized squash merge. No direct main commit, deployment or real model activation.

No work package4–8 is marked done by this refactor. A smaller facade is not sufficient completion evidence without route, failure and resource-lifetime compatibility.
