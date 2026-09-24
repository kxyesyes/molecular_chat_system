# Snapshot directory identity implementation plan

Use TDD and independent SPEC then QUALITY. Scope src/task_runtime/secure_io.py, tests/task_runtime/test_secure_snapshot_boundary.py and these two documents. Do not modify Planner or weaken docking tests.

- [ ] Run existing tests/agent/test_docking_tool_contract.py plus bounded snapshot consumer tests through the isolated runner from docs/superpowers/plans/2026-09-24-rag-service-extraction.md with this worktree.
- [ ] Add a real temp-file test: wrap _bounded_read, create an unrelated sibling in parent or ancestor, force only that directory mtime to change, then expect original bytes/hash. Record RED on current implementation.
- [ ] Add negative controls for same-size target mutation/replacement, ancestor identity/permissions/reparse changes and POSIX named ancestor replacement. Verify all opened descriptors close on success/error.
- [ ] Implement directory-only identity/type/permission helper and replace only ancestor version comparisons. For POSIX recheck named components against pinned descriptors with nofollow; keep file version/hash and Windows handle checks intact.
- [ ] Run focused GREEN, original snapshot/docking consumers and full Agent. Preserve natural intermittent failure records separately from deterministic regression. Memory compile and git diff --check.
- [ ] Independent SPEC then QUALITY, scoped commit, integrate latest reviewed main, fresh CI8/8 and unresolved-review check before authorized merge. No server/model activation.

No tests have been run for this new branch at plan creation. The source diagnostic is retained in the ignored planner worktree scratch/package5-docking-diagnostic.md; it is not a committed test or proof of a fix.

## Execution evidence

All commands use the documented isolated MedChat Python runner with only its worktree replaced; no live services or production assets. Baseline: tests/agent/test_docking_tool_contract.py plus tests/test_temporal_operator_scripts.py::test_bounded_asset_snapshot_rejects_same_size_replacement and ::test_windows_snapshot_allows_handle_path_ctime_difference:249 passed in4.28s, exit0.

New tests/task_runtime/test_secure_snapshot_boundary.py on unchanged production:3 failed,7 passed,3 skipped in0.45s, exit1. Both sibling-only directory changes wrongly failed; an ancestor mode change wrongly succeeded. POSIX named-path cases skipped on Windows, not counted as passed.

Minimal production change: directory identity/type/reparse/mode/uid/gid checks omit mutable child-driven timestamps/size; POSIX additionally verifies still-named components against pinned parent descriptors. File identity/version/size/hash and descriptor cleanup remain intact; atomic writer unchanged.

Focused GREEN: new boundary + existing docking contract + whole tests/test_temporal_operator_scripts.py:506 passed,47 skipped in67.35s, exit0. Skips are POSIX-only semantics and unavailable Windows symlink privileges. Linux CI is still required. Full Agent and independent SPEC/QUALITY remain pending; this does not retroactively resolve every historical intermittent failure.

Independent SPEC approved, separately ran boundary/docking/temporal acceptance/operator tests:552 passed49 skipped75.85s, exit0. Parent full Agent plus new snapshot boundary:5643 passed5 skipped7 existing warnings281.22s, exit0. Two existing Agent skips plus three native-POSIX skips on Windows. Two changed/new Python files memory-compiled; diff check passed. QUALITY and Linux exact-head CI remain required.

Independent QUALITY approved with305 passed5 skipped7.24s and diff-check, no unresolved findings; confirmed O(path-depth) added POSIX checks and no atomic-write/config change. Its report was written before the parent's full run was collected; that full result is recorded above, not independently repeated. Native Linux CI remains the publication gate.

Implementation9a19106 then integrated reviewed main3a68264 without conflicts:288 passed3 POSIX-skipped3.17s (new boundary+docking+reverse complete-input). Subsequently integrated reviewed analysis PR #67 main1bba0256409a06317486530e5c1cfa6598b8e381 without conflicts:692 passed3 POSIX-skipped6.84s (new boundary+docking+analysis contract). Secure snapshot implementation/tests did not change during either merge. Linux exact-head CI8/8 remains required.
