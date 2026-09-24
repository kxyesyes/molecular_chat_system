# Snapshot directory identity implementation plan

Use TDD and independent SPEC then QUALITY. Scope src/task_runtime/secure_io.py, tests/task_runtime/test_secure_snapshot_boundary.py and these two documents. Do not modify Planner or weaken docking tests.

- [ ] Run existing tests/agent/test_docking_tool_contract.py plus bounded snapshot consumer tests through the isolated runner from docs/superpowers/plans/2026-09-24-rag-service-extraction.md with this worktree.
- [ ] Add a real temp-file test: wrap _bounded_read, create an unrelated sibling in parent or ancestor, force only that directory mtime to change, then expect original bytes/hash. Record RED on current implementation.
- [ ] Add negative controls for same-size target mutation/replacement, ancestor identity/permissions/reparse changes and POSIX named ancestor replacement. Verify all opened descriptors close on success/error.
- [ ] Implement directory-only identity/type/permission helper and replace only ancestor version comparisons. For POSIX recheck named components against pinned descriptors with nofollow; keep file version/hash and Windows handle checks intact.
- [ ] Run focused GREEN, original snapshot/docking consumers and full Agent. Preserve natural intermittent failure records separately from deterministic regression. Memory compile and git diff --check.
- [ ] Independent SPEC then QUALITY, scoped commit, integrate latest reviewed main, fresh CI8/8 and unresolved-review check before authorized merge. No server/model activation.

No tests have been run for this new branch at plan creation. The source diagnostic is retained in the ignored planner worktree scratch/package5-docking-diagnostic.md; it is not a committed test or proof of a fix.
