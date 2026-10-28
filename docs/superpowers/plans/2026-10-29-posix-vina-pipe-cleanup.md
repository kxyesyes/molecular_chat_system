# POSIX Vina Pipe Cleanup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prevent a completed POSIX Vina command from being reported as failed solely because an external descendant still holds the inherited stdout/stderr pipe open.

**Architecture:** Keep the existing reader-thread and ownership-proof path for commands with `CommandOwnershipScope`. For unowned POSIX commands, capture output through nonblocking parent-side reads during process supervision, drain bytes that are immediately available, and close the parent pipe once the Vina process group has stopped instead of waiting for EOF from an unrelated descendant. Preserve the existing output limit, timeout, cancellation, decoding, and failure semantics.

**Tech Stack:** Python 3.10+, `subprocess`, `os.set_blocking`, `threading`, pytest, existing `CommandAdapter` tests.

---

## File map

- Modify `src/docking/adapters/base.py`: add the nonblocking capture state and use it only for unowned POSIX commands.
- Modify `tests/test_docking_command_cancellation.py`: add a POSIX regression test for a detached descendant holding inherited pipes, plus assertions for output and cleanup behavior.
- Do not modify `src/docking/molecular_docking_service.py`: the Vina service should continue receiving a normal `CompletedProcess` or an existing command failure.
- Do not modify the ownership-scoped receipt path: its reader and process-tree proof remains fail-closed.

## Task 1: Add a failing POSIX regression test

**Files:**
- Modify: `tests/test_docking_command_cancellation.py` near the existing normal-completion tests.

- [ ] **Step 1: Write the failing test**

Add a POSIX-only test that launches a short Python parent command. The parent prints a marker, starts a child with `start_new_session=True` so the child is outside the parent process group, and exits immediately. The child inherits stdout/stderr and sleeps briefly, keeping the parent pipe open after the command process group has stopped.

```python
@pytest.mark.skipif(os.name != "posix", reason="POSIX pipe ownership regression")
def test_unowned_posix_command_does_not_wait_for_external_pipe_holder(tmp_path):
    child = (
        "import time; "
        "time.sleep(0.6)"
    )
    parent = (
        "import subprocess, sys; "
        "subprocess.Popen([sys.executable, '-c', %r], start_new_session=True); "
        "print('parent-finished', flush=True)"
    ) % child

    started = time.monotonic()
    result = CommandAdapter(sys.executable).run(
        [sys.executable, "-c", parent],
        cwd=str(tmp_path),
        timeout=5,
    )

    assert result.returncode == 0
    assert "parent-finished" in result.stdout
    assert time.monotonic() - started < 2.0
```

- [ ] **Step 2: Run the test and verify the expected failure**

Run on a POSIX host:

```bash
pytest -q tests/test_docking_command_cancellation.py::test_unowned_posix_command_does_not_wait_for_external_pipe_holder -vv
```

Expected before the production change: failure from `RuntimeError("Command output readers did not stop")` after the existing two-second reader join timeout. On Windows, the test is explicitly skipped because the target behavior is POSIX-only.

## Task 2: Implement nonblocking capture for unowned POSIX commands

**Files:**
- Modify: `src/docking/adapters/base.py` in `CommandAdapter._run_posix`, `_supervise_posix`, `_take_captured_output`, and adjacent capture helpers.

- [ ] **Step 1: Add a nonblocking capture state helper**

Add `_attach_nonblocking_capture(process)` beside `_attach_capture_readers`. It must create the same bounded `buffers`, `total`, `exceeded`, and `errors` state, mark it with `mode="nonblocking"`, retain `process.stdout` and `process.stderr`, and call `os.set_blocking(stream.fileno(), False)` for both streams. It must not start reader threads.

- [ ] **Step 2: Add a bounded drain helper**

Add `_drain_nonblocking_capture(process)` that repeatedly calls `os.read(fd, 64 * 1024)` until `BlockingIOError`, EOF, or the existing 8 MiB total limit is reached. Append only bytes within the limit, set `state["exceeded"]` when the total exceeds the limit, and record read errors in `state["errors"]`. Treat EOF as closure of that stream but do not require both streams to reach EOF before the process group is considered finished.

- [ ] **Step 3: Route only unowned POSIX commands through the new capture mode**

In `_run_posix`, retain `_attach_capture_readers(process)` when `receipt is not None`. When `receipt is None`, call `_attach_nonblocking_capture(process)` before `_supervise_posix`. Do not change `_run_windows` or the receipt branch.

- [ ] **Step 4: Drain during supervision and finalize without waiting for inherited EOF**

In `_supervise_posix`, call `_drain_nonblocking_capture(process)` on every supervision iteration for the unowned path and once more after the process group is no longer active. Let the existing timeout, cancellation, and output-limit branches terminate the process group as before. In `_take_captured_output`, detect `mode="nonblocking"`, perform one final nonblocking drain, close the parent streams, reject recorded read errors, enforce the existing output limit, and decode the buffered bytes with `errors="replace"`. The reader-thread logic and its `reader_join_failed` / `CommandOwnershipUncertainError` behavior remain unchanged for receipt-backed commands.

- [ ] **Step 5: Run the regression test and verify it passes**

Run:

```bash
pytest -q tests/test_docking_command_cancellation.py::test_unowned_posix_command_does_not_wait_for_external_pipe_holder -vv
```

Expected: PASS on POSIX; SKIPPED on Windows.

## Task 3: Run focused regressions

**Files:**
- No additional production files.

- [ ] **Step 1: Run command-adapter tests in the MedChat environment**

```bash
python -m pytest -q tests/test_docking_command_cancellation.py --disable-warnings
```

Expected: all applicable tests pass; platform-specific tests may be skipped.

- [ ] **Step 2: Run docking service and adapter compile checks**

```bash
python -m pytest -q tests/test_docking_execution.py tests/test_docking_demo_route.py --disable-warnings
python -m compileall -q src scripts
```

Expected: focused Python tests pass and `compileall` exits 0. If a test requires unavailable external scientific binaries, report it as skipped/unavailable rather than treating it as a pass.

- [ ] **Step 3: Re-run existing frontend checks**

```bash
node --check src/web/static/js/docking/main.js
node --check src/web/static/js/docking/ui_manager.js
node tests/docking_demo_ui_test.js
node tests/docking_task_ui_flow_test.js
```

Expected: all commands pass; frontend behavior is unchanged by this backend adapter fix.

## Task 4: Review and commit

- [ ] **Step 1: Inspect the final diff and verify scope**

```bash
git diff --check
git diff --stat HEAD~1
git status --short
```

Confirm that only the adapter, its regression test, and the already committed design/plan documents are part of this task; do not stage generated runtime artifacts.

- [ ] **Step 2: Commit the implementation**

```bash
git add src/docking/adapters/base.py tests/test_docking_command_cancellation.py
git commit -m "fix: drain unowned POSIX docking output safely"
```

- [ ] **Step 3: Report deployment and validation limits**

Record that the local Windows environment validates the adapter's Windows path and existing successful Vina runs, while the new regression is POSIX-only and must be run on Linux or CI. The server should be updated to the implementation commit and the original docking scenario retried with its job manifest and logs retained.
