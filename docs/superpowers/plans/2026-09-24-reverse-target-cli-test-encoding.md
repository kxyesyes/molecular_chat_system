# Reverse-target CLI test encoding implementation plan

> **For agentic workers:** Use executing-plans for local critical-path TDD; use an independent code-review subagent alongside broader verification. Track the following checkpoints.

**Goal:** Verify real CLI help without locale-dependent decoding or missing-output false positives.

**Architecture:** Test-only explicit UTF-8 transport for one subprocess. Retain production console behavior and the separate GBK fingerprint CLI integration test.

**Tech Stack:** Python 3.10, unittest, pytest, subprocess; existing MedChat Conda environment and isolated test runner.

## Files and authority

Approved design: `docs/superpowers/specs/2026-09-24-reverse-target-cli-test-encoding-design.md`; user confirmed written design. Base `3567096`, branch `codex/reverse-target-cli-test-encoding`. Only test write: `tests/test_reverse_target_health.py`. This plan and a short handoff may also change. No source, conftest, CI, data, dependency or production configuration changes.

## Task 1 — regression first

- [x] Add these assertions after the original help return-code/stderr assertions, leaving the original subprocess arguments unchanged:

```python
self.assertIsInstance(result.stdout, str)
self.assertIn("--limit", result.stdout)
self.assertIn("获取记录数", result.stdout)
```

- [x] Add one regression method in the same TestCase. It calls the original test method (which invokes the actual CLI), not a copied subprocess implementation:

```python
def test_fetch_cli_help_preserves_parent_output_encoding(self):
    previous_encoding = os.environ.get("PYTHONIOENCODING")
    for encoding in ("ascii", "gbk:strict"):
        with self.subTest(encoding=encoding):
            with patch.dict(os.environ, {"PYTHONIOENCODING": encoding}):
                self.test_documented_fetch_cli_runs_directly_from_project_root()
                self.assertEqual(os.environ["PYTHONIOENCODING"], encoding)
    self.assertEqual(os.environ.get("PYTHONIOENCODING"), previous_encoding)
```

- [x] Run both nodes using the runner below with strict reader-thread warnings. Record actual RED: missing/garbled Chinese stdout or decoding failure, not dependency/collection failure. Existing test and new encoding regression must both be exercised before fixing.

## Task 2 — minimal GREEN

- [x] In the original test's subprocess call only, add:

```python
encoding="utf-8",
env={**os.environ, "PYTHONIOENCODING": "utf-8"},
```

- [x] Re-run the two nodes under the same runner and strict warning policy. No errors=ignore/replace, no pytest warning suppression. If a different failure occurs, diagnose before any further edit.

## Task 3 — regression and review

- [x] Run full `tests/test_reverse_target_health.py`, then combine it with `tests/test_target_test_isolation.py`, `tests/test_target_db_validation.py`, `tests/test_target_search.py`; reverse file order and re-run. Existing GBK fingerprint CLI must actually run where RDKit is available; skipped is not passed.
- [x] Compile changed test in memory with `python -B`; `git diff --check`; verify `git diff 3567096 -- src data config .github tests/conftest.py` empty and unchanged AST of the existing GBK test.
- [x] Independent read-only review of final diff against design, preserving original assertions and checking encoding isolation. Perform broader verification locally while review runs; address findings before final commit.
- [ ] Update handoff with exact RED/GREEN, scopes/skips/warnings, no broad success claim; explicitly stage task paths and commit. Publishing/new PR merge follow repository authorization gates. No deployment or real service invocation.

## Reproducible runner

Reuse the existing sanitized runner documented in the RAG plan, changing only worktree path. It clears non-allowlisted environment, redirects runtime/config/database assets to temporary directories, disables real/canary switches, and cleans temporary storage in finally. It does not read credentials or user env files. The following adds a strict warning flag only to this execution; it does not modify shared pytest/CI configuration.

```powershell
$plan = Get-Content docs/superpowers/plans/2026-09-24-rag-service-extraction.md -Raw
$runner = [regex]::Match($plan, '(?s)\$runner = @''\r?\n(.*?)\r?\n''@').Groups[1].Value.Replace('D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr', 'D:/MedChat/molecular_chat_system_worktrees/reverse-target-cli-test-encoding')
$runner = $runner.Replace('"--tb=short"', '"-W", "error::pytest.PytestUnhandledThreadExceptionWarning", "--tb=short"')
$python = 'C:/Users/xkx52/.conda/envs/MedChat/python.exe'
# RED/GREEN nodes:
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/test_reverse_target_health.py::ReverseTargetHealthTest::test_documented_fetch_cli_runs_directly_from_project_root tests/test_reverse_target_health.py::ReverseTargetHealthTest::test_fetch_cli_help_preserves_parent_output_encoding
# Module and combined regression:
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/test_reverse_target_health.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/test_reverse_target_health.py tests/test_target_test_isolation.py tests/test_target_db_validation.py tests/test_target_search.py
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/test_target_search.py tests/test_target_db_validation.py tests/test_target_test_isolation.py tests/test_reverse_target_health.py
```

Self-review: every design acceptance item maps to these checkpoints. No production behavior or timeout changes; narrow output assertions avoid coupling to full argparse formatting. Full taskbook T09/T11 completion remains outside this small batch.
