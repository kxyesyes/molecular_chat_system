# Pi Baseline Worktree Migration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a branch from `origin/main` and reapply the preserved source, test, and documentation changes without importing forbidden runtime artifacts or weakening the Pi baseline.

**Architecture:** Keep Pi runtime files and provider contracts from `origin/main` authoritative. Reapply only the backed-up Python/JavaScript/template/test/documentation files, resolve conflicts by preserving Pi entry points and current scientific-result safety contracts, then validate affected tests and source compilation.

**Tech Stack:** Git, Python 3.10, pytest, Node.js contract tests, FastAPI/Jinja2, native JavaScript.

---

### Task 1: Establish the migration branch and inventory the preserved changes

**Files:**
- Base branch: `origin/main`
- Backup: `C:/Users/xkx52/.codex/visualizations/2026/10/24/01a16f5b-8971-7d03-85f1-394c3b04c317/worktree-preserve-20261025`

- [x] **Step 1: Create the branch from `origin/main`**

Run: `git switch -c codex/feat/pi-baseline-migrate-worktree origin/main`

- [x] **Step 2: Confirm Pi baseline files are present**

Run: `Test-Path runtime/pi/session.mjs; Test-Path src/agent/pi_runtime; Test-Path docs/PI_RUNTIME.md`

Expected: all three return `True`.

- [x] **Step 3: Compare each backed-up file with its Pi-baseline counterpart**

Use `git diff --no-index` for every source, test, and documentation file before copying. Record conflicts rather than overwriting Pi-specific files. The migration kept the Pi baseline authoritative; the only import conflict was changed from the removed legacy path `src.agent.harness.decision_bounds` to `src.agent.scientific_session.decision_bounds`.

### Task 2: Migrate backend and frontend source changes

**Files:**
- Modify: `src/agent/tools/reverse_target_tool.py`
- Modify: `src/reverse_target/source_runtime.py`
- Modify: `src/web/app.py`
- Modify: `src/web/static/js/docking/ui_manager.js`
- Modify: `src/web/static/js/reverse_target/results_renderer.js`
- Modify: `src/web/static/js/shared/safe_render.js`
- Modify: `src/web/templates/molecular_docking.html`
- Modify: `src/web/templates/reverse_target.html`

- [x] **Step 1: Apply the backed-up source changes with three-way comparison**

Copy only the backed-up files listed above into the new branch, then inspect `git diff --stat` and `git diff --check`. Do not copy `runtime/pi`, `src/agent/pi_runtime`, `requirements-agent-harness.txt`, generated data, videos, indices, or temporary files.

- [x] **Step 2: Resolve any Pi integration conflicts**

Preserve `origin/main` Pi routing, `src/agent/pi_runtime` bridge/provider/runtime behavior, and explicit scientific evidence/error boundaries. Retain source changes only where they apply cleanly to the current file structure.

- [x] **Step 3: Run Python compilation**

Run: `python -m compileall -q src scripts tests`

Expected: exit code 0.

### Task 3: Migrate regression tests and documentation

**Files:**
- Modify: `tests/docking_task_ui_behavior_test.js`
- Modify: `tests/docking_task_ui_flow_test.js`
- Modify: `tests/frontend_safe_render_test.js`
- Modify: `tests/reverse_target_broad_recall_test.js`
- Modify: `tests/test_reverse_target_source_runtime.py`
- Add: `tests/test_llm_config_responsiveness.py`
- Add: `docs/superpowers/plans/2026-10-13-qi-ppt-demo.md`
- Add: `docs/superpowers/plans/2026-10-18-documentation-deduplication.md`
- Add: `docs/superpowers/specs/2026-10-13-qi-ppt-demo-design.md`

- [x] **Step 1: Restore tests and docs from the backup**

Copy only the listed files and inspect the diff for Pi-specific imports, route names, and provider assumptions.

- [ ] **Step 2: Run focused Python tests**

Run: `python -B -m pytest tests/test_reverse_target_source_runtime.py tests/agent -q -p no:cacheprovider`

Expected: all selected tests pass; blocked in this workstation because Python has no `rdkit` installation and the repository requires `rdkit==2023.9.2`.

- [x] **Step 3: Run focused Node contract tests and syntax checks**

Run: `node --check src/web/static/js/docking/ui_manager.js; node --check src/web/static/js/reverse_target/results_renderer.js; node --check src/web/static/js/shared/safe_render.js; node --test tests/docking_task_ui_behavior_test.js tests/docking_task_ui_flow_test.js tests/frontend_safe_render_test.js tests/reverse_target_broad_recall_test.js`

Expected: syntax checks and all selected Node tests pass. The four migrated frontend contract files passed. Pi session syntax passed, but its six runtime tests remain blocked until `npm ci --prefix runtime/pi --ignore-scripts --no-audit --no-fund` can install `@earendil-works/pi-coding-agent@1.4.0`; the local npm attempt ended with `Exit handler never called`.

### Task 4: Review, commit, and report

**Files:** only the migrated source, tests, docs, and the migration plan.

- [ ] **Step 1: Check repository boundaries**

Run: `git status --short; git diff --check; git diff --stat`

Expected: no generated artifacts, credentials, large binaries, `tmp/`, `deliverables/`, or runtime data are staged or present as migrated changes. Generated artifacts remain outside the repository in the previously recorded preservation backup. A pre-existing CRLF/LF-only working-tree difference in `src/agent/scientific_session/controller.py` is semantically empty (`git diff --ignore-space-at-eol` is clean).

- [ ] **Step 2: Commit the migration**

Run: `git add <explicit migrated paths>` followed by `git commit -m "chore: migrate preserved changes onto Pi baseline"`. Not executed in this managed workspace because `.git/index.lock` cannot be created (permission denied); the migration remains inspectable as an uncommitted working-tree change.

- [ ] **Step 3: Report branch, commit, tests, and unresolved items**

Include the exact branch and commit, every validation command and result, any skipped real-service checks, and any remaining compatibility risks.
