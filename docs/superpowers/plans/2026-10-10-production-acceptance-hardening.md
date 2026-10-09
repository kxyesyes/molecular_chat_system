# MedChat Production Acceptance Hardening Implementation Plan

> **For agentic workers:** Use the test-driven-development and systematic-debugging skills while executing this plan.

**Goal:** Close the remaining production-readiness gaps that can hide unsafe runtime behavior: fragile target-search SQL binding, health checks that report an in-process task backend as production-ready despite lacking restart recovery, and health checks that count unselected activity-model artifacts as ready for inference.

**Architecture:** Keep the existing FastAPI, SQLite target database, activity model registry, and task-runtime backend selector. Make SQL parameters derive from the same placeholder groups as the query, and keep local execution and isolated model-family stores available for development while exposing their production limitations in strict health checks.

**Tech Stack:** Python 3.10+, FastAPI, SQLite, pytest, PowerShell.

---

## Task 1: Reproduce and lock down target-search fuzzy query binding

**Files:** `tests/test_target_search.py`, `src/target_search/service.py`

1. Add a regression test that exercises a fuzzy query with every supported SQL filter.
2. Run the focused test and confirm the pre-change behavior or at least establish the query contract.
3. Refactor `_search_local()` to build ordered parameter groups explicitly from the SQL placeholder groups, avoiding hand-counted tuples.
4. Run the target-search test module.

## Task 2: Make production health checks fail closed for non-durable task execution

**Files:** `tests/test_agent_platform_health_check.py`, `scripts/health_check.py`

1. Add a failing test for an explicit `MEDCHAT_REQUIRE_DURABLE_TASKS=1` deployment requirement when the selected backend is local.
2. Implement the smallest environment-gated check. Preserve the current non-blocking local-development behavior when the requirement is unset.
3. Verify the strict and development paths, ensuring no secrets or absolute deployment details are included in the message.

## Task 2b: Make production activity readiness require active family bundles

**Files:** `tests/test_agent_platform_health_check.py`, `scripts/health_check.py`, `deployment/README.md`

1. Add a failing test showing that registered child-family artifacts alone do not satisfy a strict production readiness requirement.
2. Implement the environment-gated `MEDCHAT_REQUIRE_ACTIVE_ACTIVITY_MODELS=1` check against the root registry's verified PDE and BuChE family bundles.
3. Keep default development behavior unchanged and never auto-select a bundle.

## Task 3: Regression and production-readiness verification

1. Run focused Python tests for target search and health checks.
2. Run the existing docking, pharmacophore, ADMET, activity-budget, and task-runtime regression tests.
3. Run `compileall` and the relevant Node static tests.
4. Run the strict health check if the local environment supports it; record missing external services as unavailable rather than successful.
5. Review `git diff` and stage only task-scoped files. Never stage the pre-existing untracked FAISS manifest.
