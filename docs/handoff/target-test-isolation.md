# Target test isolation handoff — 2026-09-24

## Scope and review state

Independent **SPEC and QUALITY approved**; local implementation is complete and rebased onto main `9eed743`, with unchanged task patches. Publication awaits authorization. The overall taskbook is not complete. No deployment, real model or API credential use was performed. The stage history below records the earlier pre-rebase evidence without rewriting it.

- Worktree: `D:/MedChat/molecular_chat_system_worktrees/target-test-isolation`.
- Branch: `codex/target-test-isolation`.
- Starting HEAD: `b71ae3bb24ea9daa22375a9c4eea82fe88f4a100`; clean at start. Approved base: `4ff859e`.
- Write set: the two existing target test modules, `tests/test_target_test_isolation.py`, this handoff, and implementation-plan checkbox updates only.
- PR57 / Web partial is separate: the coordinator reports it merged as main `9eed743`. This worktree was deliberately not rebased; integration belongs to the coordinator after reviews.

## Changes

- Both existing TestCases now use a per-case `pytest.MonkeyPatch` to delete only `TARGET_DB_PATH` and `TARGET_CACHE_DIR`. `undo` and temporary-directory cleanup are registered immediately with `addCleanup`; redundant `tearDown` cleanup was removed only after the cleanup regression passed.
- Existing temporary-directory prefixes, explicit nested environment-override tests, original fixture contents, timeouts, counts and scientific assertions are unchanged.
- The actual validation CLI call sets parent `encoding="utf-8"` and child `PYTHONIOENCODING=utf-8`, copying the already-isolated environment. Original strict exit=1 and missing-PDE JSON assertions remain.
- Both cache-hit calls guard `src.target_search.downloader.requests.get` with an immediate assertion and explicitly assert no HTTP call occurred.
- Nine additional regression cases exercise real TestCase setup/cleanup, separate seeded SQLite/cache roots, a first-case sentinel absent from the second case, restoration of present/absent overrides after an actual unittest assertion failure, and preservation of an unrelated environment mutation.
- The CLI regression calls the real CLI with a Chinese cache-path fixture and checks the unescaped Chinese path and warning in returned JSON. Ambient child output encoding is deliberately `ascii` so the UTF-8 override cannot pass by inheritance alone.
- Two forced cache misses invoke the actual cache-hit test methods. A lower HTTP-adapter trap keeps RED offline; GREEN proves the test-local guard rejects the attempted download before the transport is called. No production download behavior is changed.
- Regression imports are module imports, not TestCase aliases. Collection confirms exactly 39 existing search tests + 3 existing validation tests + 9 regressions = 51, with no duplicate collection.

## Actual RED/GREEN evidence

All runs used the specified MedChat Python and sanitized runner below, with shared absolute outer target paths intentionally retained. No parent `-X utf8` was used. Times are pytest-reported, not shell duration.

| Stage | Invocation / state | Actual result | Exit |
|---|---|---|---|
| First fixture RED | New isolation module, first six cases; original setup unchanged | **4 failed, 2 passed**, 0.39s | 1 |
| Fixture GREEN / encoding RED | Isolation + validation + search; only scoped setup/cleanup added, original CLI and cache calls unchanged | **1 failed, 47 passed, 1 warning**, 3.72s | 1 |
| CLI / guard RED | Isolation module now contains all nine cases; before encoding or guard edits | **3 failed, 6 passed**, 0.98s | 1 |
| Focused GREEN | Isolation, validation, search | **51 passed**, 4.31s; no warnings | 0 |
| Reversed-order GREEN | Search, validation, isolation; encoding probe | **51 passed**, 4.36s; no warnings | 0 |
| Related target/cache + Agent | Five related paths below | **530 passed, 2 skipped**, 22.70s; no warnings | 0 |
| Collection check | Three focused paths, `--collect-only` | **51 collected**, 0.14s | 0 |
| Combined GREEN | All eight focused + related paths in one process | **581 passed, 2 skipped**, 28.17s; no warnings | 0 |

First failure details, saved before implementation:

```text
test_cases_use_distinct_roots_and_restore_hostile_paths[TargetSearchDemoTest]
test_cases_use_distinct_roots_and_restore_hostile_paths[TargetDatabaseValidationTest]
  get_db_path(case.root) resolved to the synthetic outer outside.sqlite,
  not case.root / data/target_db/target_database.sqlite.

test_unittest_failure_restores_only_target_overrides[True-TargetSearchDemoTest]
test_unittest_failure_restores_only_target_overrides[True-TargetDatabaseValidationTest]
  observed TARGET_DB_PATH / TARGET_CACHE_DIR contained synthetic outer paths,
  expected both absent inside runTest.
```

After the fixture-only change, the sole remaining failure was the original CLI test:

```text
TypeError: the JSON object must be str, bytes or bytearray, not NoneType
PytestUnhandledThreadExceptionWarning: Exception in thread Thread-1 (_readerthread)
UnicodeDecodeError: 'gbk' codec can't decode byte 0xaa in position 5140:
illegal multibyte sequence
```

The second new-regression RED failed for exactly the missing contracts:

```text
test_real_validation_cli_uses_utf8_for_chinese_json:
  assert None == 'utf-8'  # subprocess.run had no encoding argument
both test_cache_hit_guard_rejects_missing_cache_before_transport variants:
  Expected: 'cache hit must not access HTTP'
  Actual:   'unguarded HTTP reached transport'
```

The reversed GREEN probe returned `ENCODING_PROBE=cp936 0`: the fresh pytest interpreter's default locale encoding was cp936 and `sys.flags.utf8_mode` was zero. The fixed CLI works without changing that default. No reader-thread warning appeared in GREEN.

The two related-suite skips are unchanged: `test_target_search_fallback.py:33` (real authoritative lookup not enabled) and `:1958` (Windows symlink privilege unavailable, WinError 1314). The coordinator independently reported the same 530 passed / 2 skipped baseline in 22.42s; that is separate evidence, not an implementer run.

## Reproducible commands

Run from the worktree above. This extracts the existing runner verbatim from the RAG plan and changes only its repository path. It is not a new runner file. The runner clears non-allowlisted environment variables, redirects user config and runtime databases to temporary locations, disables real/canary switches, retains shared absolute target DB/cache overrides, and copies/hash-checks the three tracked JSONL fixtures. It runs normal conftest and `python -B -m pytest` from the temporary cwd, then shuts down logging and cleans up. It never sources user environment files or prints credentials.

```powershell
$plan = Get-Content docs/superpowers/plans/2026-09-24-rag-service-extraction.md -Raw
$runner = [regex]::Match($plan, '(?s)\$runner = @''\r?\n(.*?)\r?\n''@').Groups[1].Value.Replace('D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr', 'D:/MedChat/molecular_chat_system_worktrees/target-test-isolation')
$python = 'C:/Users/xkx52/.conda/envs/MedChat/python.exe'
$focus = @('tests/test_target_test_isolation.py', 'tests/test_target_db_validation.py', 'tests/test_target_search.py')
$related = @(
    'tests/test_target_search_fallback.py',
    'tests/agent/test_target_selection_phrase.py',
    'tests/agent/test_target_selection_execution.py',
    'tests/agent/test_target_identity_alignment.py',
    'tests/agent/test_target_driven_design_workflow.py'
)

# First and second new-regression RED used this invocation at the states above:
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/test_target_test_isolation.py
# Fixture-only RED and final focused GREEN:
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" @focus
# Related regression:
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" @related
# Combined regression:
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" @focus @related

# Collection check (no change to the saved runner):
$collect = $runner.Replace('"--tb=short"', '"--collect-only", "--tb=short"')
$collect | & $python -B -c "import sys; exec(sys.stdin.read())" @focus

# Reversed run, with a fresh-interpreter encoding probe before pytest:
$probe = $runner.Replace('        result = subprocess.call(', '        print("ENCODING_PROBE=" + subprocess.check_output([sys.executable, "-B", "-c", "import locale, sys; print(locale.getpreferredencoding(False), sys.flags.utf8_mode)"], text=True, encoding="ascii").strip(), flush=True)' + [Environment]::NewLine + '        result = subprocess.call(')
$probe | & $python -B -c "import sys; exec(sys.stdin.read())" tests/test_target_search.py tests/test_target_db_validation.py tests/test_target_test_isolation.py
```

The runner appends `-q -p no:cacheprovider --tb=short -rs` to each invocation and prints `T11A_PYTEST_EXIT`; that historical label is retained from the reused runner, not a claim about the RAG batch.

## Self-review and remaining limits

- AST comparison against starting HEAD preserved all **183 search + 10 validation `self.assert*` calls**, including argument values. Every original test name is unchanged. All test methods except the two guarded calls and the CLI method are AST-identical; timeout keyword expressions are identical.
- The three changed/new Python modules pass in-memory `compile(..., "exec")` under MedChat Python `-B`; no pyc files are created. This is the plan's in-memory check, not a claim of running whole-source `compileall`.
- `git diff --check` passed. `git diff --name-only 4ff859e -- src data config .github` was empty. No shared conftest, CI, production assets, algorithm, schema or scientific behavior changed.
- Task-file/diff credential-pattern review found no credentials. Only the five approved test/document paths are staged for the delivering commit.
- Static review is limited to the task diff. No full-repository passing result is claimed; the coordinator can run broader/full verification on the stable commit. No Linux run or real scientific-tool acceptance was performed. Deployment health checks and JavaScript checks are not applicable to this test-only write set.
- Independent specification and quality review gates remain open. Commit/publication state is reported with the delivering commit; there is no push or PR for this batch.

## Coordinator final verification and integration

Both independent reviewers approved implementation `a0866ea`, each running the focused 51 tests in normal and reversed order. They independently confirmed Windows `cp936` / UTF-8 mode 0, all 193 original assertions and all 42 original test methods preserved, and no duplicate collection, broad environment resets, production changes or weakened timeouts. No review findings remain open; the preceding self-review section describes the earlier stage only.

The full repository `tests` run on **a0866ea / product base 4ff859e** completed:

**12154 passed, 252 skipped, 10 warnings, 171 subtests passed in 1307.49s (21:47), exit 0.**

Command: the same sanitized runner above, with the single argument `tests` (therefore normal `python -B -m pytest <repo>/tests -q -p no:cacheprovider --tb=short -rs`). No `-X utf8`, changed timeout or extra skip flag was used. The former target-fixture/CLI failures did not recur. This is one real whole-tests execution on the stated baseline, not a sum of focused runs, and not whole-tests evidence for the later rebased tree.

Skipped cases include Windows/POSIX/Linux capabilities, symlink privileges, explicitly disabled real-service/performance/integration gates, unavailable docker/promtool, task-store setup conditions and pre-existing superseded trust-coverage cases. They remain skipped, not passed. There is still an **unrelated reader-thread GBK UnicodeDecodeError warning** in `tests/test_reverse_target_health.py::ReverseTargetHealthTest::test_documented_fetch_cli_runs_directly_from_project_root`, whose subprocess text capture lacks an explicit encoding. That test checks return code/stderr but not help stdout. It was not changed in this two-module batch. Other warnings are existing SWIG/FastAPI/PyG deprecations and tensor-construction performance. Exit 0 must not be called warning-free validation.

After the full process terminated, `git rebase origin/main` applied all three local commits cleanly onto **9eed743**. Implementation now **4c61f75**. `git range-diff 4ff859e..a0866ea 9eed743..4c61f75` reports all three patches `=`; `git diff --exit-code a0866ea --` followed by the three task test files is empty. No test changed during the full run.

On the rebased tree, the same runner executed these 13 paths in one process:

```text
tests/test_target_test_isolation.py
tests/test_target_db_validation.py
tests/test_target_search.py
tests/test_target_search_fallback.py
tests/agent/test_target_selection_phrase.py
tests/agent/test_target_selection_execution.py
tests/agent/test_target_identity_alignment.py
tests/agent/test_target_driven_design_workflow.py
tests/agent/test_chat_handler_partial_results.py
tests/agent/test_chat_handler_agent_events.py
tests/agent/test_chat_input_budget.py
tests/agent/test_chat_local_cleanup.py
tests/test_model_request_lifecycle.py
```

Result: **862 passed, 2 skipped, 4 warnings, 48.04s, exit 0**. This specifically combines the target isolation change with newly merged Web partial/lifecycle behavior. Skips are real target lookup disabled and Windows symlink privilege; warnings are existing FastAPI lifecycle deprecations. It is not a second whole-repository run.

Final task diff relative to main has six test/docs files and no `src`, `data`, `config`, `.github` or shared conftest changes. All live test/review handles have completed. Next gate: permission to publish the independent draft PR; merging that new PR requires separate specific authorization. Taskbook T09 and remaining T11 decomposition are still unfinished. The reverse-target CLI warning is a separate follow-up, not silently declared fixed here.
