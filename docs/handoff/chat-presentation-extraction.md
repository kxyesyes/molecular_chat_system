# ChatHandler presentation and prompt extraction

Date: 2026-09-25. Branch: `codex/chat-presentation-extraction`, baseline main `6af7292` (PR #62 merged). This is work package 2 of [remaining-through-step8.md](remaining-through-step8.md), not completion of packages 3–8 or deployment.

## Scope and behavior

- `src/web/agent_result_presentation.py`: the existing failure envelope, text/warning sanitization, status projection, partial result and failure content algorithms now have one implementation.
- `src/web/chat_prompt_builder.py`: the existing bounded RAG record formatting, ordinary prompt and Agent evidence prompt algorithms receive explicit data and a lazy input-limit callback.
- `src/web/chat_handler.py`: original helper signatures and class overrides remain compatible through thin delegates. The handler retains network/model ownership, WebSocket sends, history, candidate/event projection and cleanup.
- `tests/agent/test_chat_presentation_boundary.py`: fixed expected output characterization, direct module behavior, delegation, override hooks, lazy evaluation/exception order, input non-mutation and runtime-independent imports.

No scientific result, status, model setting, API frame or cleanup algorithm is intentionally changed. Partial results retain exact scientific body text, precision and SMILES; failure summaries retain their existing bounded safety projection. Malformed-step diagnostics remain caller-owned and occur at the original failure point. The existing `chat_handler.format_rag_context` injection remains effective.

The new modules reuse `prompt_budget`, `rag_presentation` and the existing redaction functions. They do not import the handler/app/model/transport or create service instances. There is no new Agent framework, parallel formatter or routing behavior.

## TDD evidence

All tests used the MedChat Python environment with the existing isolated runner documented in `docs/superpowers/plans/2026-09-24-rag-service-extraction.md`, replacing only the repository path. It clears unrelated environment, uses temporary runtime/configuration/storage and checked synthetic evaluation fixtures; no real model APIs or user runtime assets are used.

| Stage | Actual result |
|---|---|
| Six-file pre-change baseline | 271 passed, 4 existing warnings |
| New characterization against the original methods | 45 passed |
| Initial combined RED | 13 failed, 41 errors, 316 passed; absent new modules |
| RED after moving direct imports into test execution | 54 failed, 45 passed; absent new modules |
| New boundary GREEN | 99 passed |
| New boundary plus the six baseline files | 370 passed, 0 skipped, 4 existing warnings |

An earlier six-file selection accidentally included legacy-cleanup instead of the intended file and produced 269 passed. It is not the formal baseline and was not substituted for the corrected 271 result. Initial RED fixture errors remain recorded rather than being presented as a clean first run.

The six baseline paths (under `tests/agent/`) are `test_chat_handler_partial_results.py`, `test_chat_handler_agent_events.py`, `test_chat_input_budget.py`, `test_agent_audit_regressions.py`, `test_chat_local_cleanup.py`, and `test_scientific_reference_web.py`.

## Parent verification

Joint regression command dispatched through the same isolated runner:

```text
python -B -m pytest tests/agent tests/test_web_app_lifecycle.py tests/test_model_request_lifecycle.py tests/test_design_model_switch.py tests/test_phase2_phase3_routes.py tests/test_static_placeholder_cleanup.py tests/test_main_routes_template_compat.py -q -p no:cacheprovider --tb=short -rs
```

Result: **5728 passed, 2 skipped, 7 warnings in 272.71s**, exit 0. This is the stated Agent plus six-root-file scope, not the whole repository. The two skips are unavailable directory symlinks on this Windows host and the explicitly disabled performance test; warnings are existing SWIG/FastAPI deprecations.

The following parent-run checks passed:

```text
node tests/home_scientific_references_test.js
node tests/home_structured_molecule_render_test.js
node tests/home_agent_task_panel_test.js
node tests/home_workflow_completion_behavior_test.js
node tests/frontend_safe_render_test.js
node --check src/web/static/js/home/main.js
git diff --check
```

314 tracked source/script plus new-module/test paths passed in-memory `compile()` using MedChat Python. This creates no bytecode; it is not a claim that `compileall` was run locally in this batch. CI has a separate `compileall` gate.

Independent SPEC review: APPROVED; the reviewer replayed original baseline characterization (45 passed) and final boundary tests (99 passed), and verified nine old method signatures/decorators and the other 27 method ASTs unchanged. It did not claim an independent rerun of the historical RED evidence.

Independent QUALITY review: APPROVED, no unresolved findings. It was read-only static review and did not claim another heavy test run. The two import-isolation subprocesses now have a 30-second timeout (2 passed, 3.05s after that edit). The joint run had already loaded the earlier test version, so the parent reran the final boundary file separately: **99 passed in 4.20s**, exit 0; production files are unchanged. This is test-harness hardening only.

Local implementation and reviews are complete; PR/CI and merge remain pending at this commit. The original checkout's nine tracked modifications and four untracked entries remain unchanged. No local model service was started or production configuration edited.

## Remaining boundaries

No external model, local generation service or trained activity model was enabled or tested by this refactor. Offline fixtures do not establish scientific readiness. Original dirty checkout remains untouched. The next package is domain API separation after publication gates; initial read-only route-count estimate was corrected by AST/registration inspection from 31 to **30** actual operations (29 OpenAPI paths). That separate package must preserve runtime/getter/threadpool semantics and separately track the existing properties endpoint's heuristic ADMET issue.
