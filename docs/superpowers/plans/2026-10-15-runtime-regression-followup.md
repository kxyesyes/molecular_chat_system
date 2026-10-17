# PR #209 runtime regression follow-up

> **For agentic workers:** Use systematic-debugging and test-driven-development; apply subagent-driven-development to the independent activity cancellation slice. Do not merge.

**Goal:** Resolve the previously observed docking lease test hang and activity cancellation failure, identify their causes, rerun the related regressions, and update draft PR #209.

**Architecture:** Preserve physical command ownership, lease/capacity retention while ownership is unresolved, cancellation propagation, and child-process cleanup. Keep scientific results and API contracts unchanged.

**Stack:** Python 3.10, asyncio, pytest, multiprocessing, native Windows command ownership tests.

## Task 1 — Docking lease hang

- [x] Inspect `tests/task_runtime/test_docking_consent.py` C4a fixture and `tests/test_docking_command_cancellation.py` real-command helper against `src/task_runtime/docking_execution.py` and `src/docking/adapters/base.py`.
- [x] Reproduce `test_c4a_pending_command_retains_real_lease_and_capacity` with bounded diagnostics; verify normal/cancel/deadline variants after correction. Diagnose the first failed invariant rather than its teardown symptom.
- [x] Establish a failing regression; apply the smallest evidenced correction. Never release a lease whose physical ownership is uncertain.
- [x] Run the complete affected real-command/lease regression files and inspect child cleanup.

## Task 2 — Activity cancellation

- [x] Reproduce `tests/test_activity_route_budget.py::test_cancel_terminates_child_and_releases_capacity` using the supported Conda environment.
- [x] Inspect route/process startup/wait cancellation boundaries. Add a deterministic failing regression for the demonstrated cause; do not substitute sleeps or inflated budgets for cancellation correctness.
- [x] Make a scoped correction in activity routes/process isolation and related tests only; verify cancellation, timeout, capacity release, startup interruption, and child cleanup.

## Task 3 — Review and delivery

- [x] Independently review specification compliance, then code quality, for both slices.
- [x] Run combined focused regressions, broader relevant offline coverage, and `python -m compileall -q src scripts`; report exact outcomes and any limitations.
- [x] Update the existing browser acceptance validation report without changing unrelated blocked scientific scenarios to PASS.
- Delivery gate: explicitly stage only follow-up files on `codex/browser-agent-real-acceptance`, commit with Conventional Commit wording, push and update PR #209. Preserve draft status; do not merge. The published commit and delivery outcome are recorded on PR #209.

## Verified outcome

The two requested blockers have deterministic RED/GREEN evidence and independent specification/quality review. Combined runtime regressions: **1893 passed, 31 skipped**. Final full offline suite: **23526 passed, 270 skipped, 1 deselected, 173 subtests passed**. See the companion validation report for commands, root causes, earlier startup-timing fluctuations, warnings, and unchanged scientific limitations.
