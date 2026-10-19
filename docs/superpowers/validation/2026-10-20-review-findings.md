# Independent review fixes — validation

Base: `73d75a7f` (main after PR #212). One isolated branch, one draft PR; no merge authorized.

## Scientific goal integrity

- Initial regression tests reproduced 24 assertion failures plus missing raw SA evidence; 8 additional adversarial cases caught quantified/unsupported goals and contradictory provenance flags.
- Independent specification review found two remaining false claims: dual-head probability improving while pIC50 worsens, and negated absolute bounds bypassing negation. Added regressions produced 8 failures / 1 control before repair. Potency now requires pIC50; real dual-head activity comparisons prefer pIC50 and include endpoint/units. Negation is checked before absolute bounds, preserving supported Chinese operators.
- Specification re-review: PASS, 126 focused tests plus independent counterexample probes.
- Subsequent independent quality review: PASS, 126 focused tests; actual producer/provenance compatibility verified.
- Expanded regression: `python -m pytest tests/agent/test_lead_optimization_verifier.py tests/test_molecular_design_integrity.py tests/agent/test_review_absolute_bounds.py tests/agent/test_browser_property_constraints.py tests/agent/test_review_bound_language_units.py tests/agent/test_planner_template_execution.py tests/agent/test_molecular_agent_adapter_science.py tests/agent/test_supervisor_agent.py tests/agent/test_delegated_session_parity.py -q -p no:cacheprovider --tb=short` — 407 passed, one existing pandas warning. Counts overlap other batches and must not be summed.
- Compatibility: negated, shared, conflicting or unsupported objectives remain unverified. Flat legacy provenance remains supported. Display rounding is unchanged; `raw_properties.sa_score` and activity comparison endpoint/units are additive evidence fields.

## Task cancellation and resource lifetime

- RED: three real ASGI/Supervisor/adapter regressions reproduced downstream calls after cancellation during initialization/running, and early lease release on adapter timeout. An additional real application shutdown regression reproduced closing the shared reverse-target tool while its worker remained alive.
- GREEN: `python -m pytest -p no:cacheprovider tests/agent/test_browser_session_integration.py tests/test_task_runtime.py tests/test_model_request_lifecycle.py tests/agent/test_worker_ownership.py tests/agent/test_delegated_session_lifecycle.py tests/agent/test_delegated_session_parity.py tests/agent/test_supervisor_delegation.py tests/agent/test_tool_adapter_compat.py tests/agent/test_tool_adapters.py tests/agent/test_workflow_run_session.py tests/agent/test_ordinary_web_lifecycle.py tests/agent/test_admet_worker_lifecycle.py -q` — 292 passed, 7 existing deprecation warnings.
- Coverage includes cleanup failure retaining leases, duplicate task IDs, queued cancellation, submit/claim failures, ownership authorization and replay. Adapter signature transparency preserves compatibility with cancellation-aware dispatch.
- Independent specification review: PASS, all 9 browser integration cases passed (7 existing dependency/FastAPI warnings).
- Final quality review found an additional lease gap in workflow routing arbitration: without an explicit skill, `routing_model_call` could time out while its shared-pool model invocation remained alive. A real ASGI reproduction observed a failed task and an available exclusive gate before model exit. This finding is tracked as part of the resource-lifetime fix, not accepted as a limitation.
- Closed that finding after RED ASGI and executor-construction/submission rollback tests: workflow routing now reserves its own tracked executor before submission, retains capacity until physical completion, and never attaches or shuts down the shared standalone pool.
- Final independent specification re-review: PASS, 36 browser/ownership tests. Final independent overall quality/integration re-review: PASS, no remaining actionable findings; `python -m pytest tests/agent/test_browser_session_integration.py tests/agent/test_worker_ownership.py tests/agent/test_allocation_deadline.py tests/agent/test_allocation_handoff.py tests/agent/test_allocation_config.py tests/test_task_runtime.py tests/test_model_request_lifecycle.py -q -p no:cacheprovider` — 150 passed, 7 existing deprecation warnings. The implementation agent independently ran the same final batch with the same result.
- Cancellation is cooperative. An already running native worker may continue until exit; its resource lease intentionally blocks replacement/shutdown until physical cleanup is proven. Existing instrumented executor paths are covered; detached threads created internally by external tools are not newly instrumented.

## Web boundaries

- RED: new route regressions produced 4 failures / 16 controls; old polling regressions produced 5 failures / 9 controls.
- GREEN: `python -m pytest tests/test_molecule_properties_unknown.py tests/test_target_search_route_responsiveness.py tests/test_route_compatibility.py tests/test_target_search.py -q -p no:cacheprovider` — 77 passed.
- `node --check src/web/static/js/docking/ui_manager.js` passed; both docking UI contract files passed (behavior suite: 14 cases).
- All 44 root `tests/*_test.js` files plus `tests/activity_family_acceptance_dom.js` passed (45 files), matching the static-quality workflow selection.
- Independent specification review: PASS, 20 route tests and 14 UI cases passed.
- Subsequent independent quality review: PASS, same focused suite passed; additional read-only probes covered stale cancel success/HTTP/network/JSON failures, delayed JSON decoding and obsolete polling entry.

## Boundaries

Tests use temporary stores and synthetic observations. These checks do not establish real model accuracy, real Vina execution, or external API availability. No dependencies, deployment configuration, schema, or production datasets are changed. Existing HTTP response envelopes and rounded display properties remain compatible.

The original checkout's staged diff, unstaged diff and full untracked-file status are checked separately; unrelated local work is excluded from this branch.

`python -m compileall -q src scripts`, changed-JavaScript `node --check`, `git diff --check` and the CI tracked-file credential-pattern scan passed. Deployment/data health checks and real scientific acceptance are not run because this change does not modify deployment or datasets and uses no real service credentials.

The full offline local run (`python -m pytest tests -q -p no:cacheprovider -m "not real_external" --tb=short`, with temporary databases/configuration and no inherited provider credentials) started before the final routing repair. It was deliberately stopped at approximately 11% after that code changed; no failures had appeared, but this is **not** a full-suite pass. Final affected tests are green as above; complete final-commit coverage remains for the PR's partitioned CI. No CI result is claimed in this pre-publication record.
