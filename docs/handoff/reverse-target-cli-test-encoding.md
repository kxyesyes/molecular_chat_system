# Reverse-target CLI test encoding — local verification

## Scope

Base `3567096` (merged PR #58); branch `codex/reverse-target-cli-test-encoding`. User approved the written design. Implementation commit `cf7936d` changes only `tests/test_reverse_target_health.py` (14 added lines); design, plan and this handoff are the only other task files.

The real help subprocess now explicitly decodes UTF-8 and overrides only its copied child environment's output encoding. Original return-code and stderr assertions remain; new checks require string stdout, `--limit`, and Chinese option help. One regression invokes that same test with temporary outer ASCII/GBK output settings and checks parent settings remain unchanged/restored. No parallel CLI helper or production change was introduced.

## Actual TDD and regression

All pytest runs used the existing sanitized runner and MedChat Python documented in the implementation plan, from a temporary runtime/config/data root. Parent UTF-8 mode was not enabled by adding `-X utf8`; strict reader-thread warnings were enabled with `-W error::pytest.PytestUnhandledThreadExceptionWarning` for these runs only. No external models, credentials or ChEMBL downloads were used.

| Run | Actual pytest result | Exit |
|---|---|---|
| Added output assertions + encoding regression before encoding fix | **2 failed, 1 passed, 1 subtests passed**, 3.12s | 1 |
| Same two nodes after test-local encoding fix | **2 passed, 2 subtests passed**, 2.85s | 0 |
| Full reverse-target health module | **19 passed, 2 subtests passed**, 6.07s | 0 |
| Health + target isolation + target DB validation + target search | **70 passed, 2 subtests passed**, 9.43s | 0 |
| Same four files in reverse order | **70 passed, 2 subtests passed**, 9.08s | 0 |
| Health + pharmacophore + frontend-static reverse-target modules | **29 passed, 2 subtests passed**, 9.27s | 0 |

The RED counts include unittest subtest reporting: the original test failed because stdout was `None`; the ASCII subtest failed because Chinese help had become question marks. These were not collection or dependency errors. All GREEN runs above had no skipped tests or warnings. The existing GBK fingerprint CLI test ran as part of the health module; RDKit was available.

Commands are reproduced in `docs/superpowers/plans/2026-09-24-reverse-target-cli-test-encoding.md`. The extra related run used the same runner with `tests/test_reverse_target_health.py tests/test_reverse_target_pharmacophore.py tests/test_reverse_target_frontend_static.py`. Runner's historical `T11A_PYTEST_EXIT` label does not imply a RAG test. Runs overlap and must not be summed as a unique test count or a whole-repository result.

## Static verification and independent review

- In-memory `compile` passed for the changed test using MedChat Python `-B -X utf8`; this compilation flag was not used for the pytest encoding reproduction.
- AST comparison with base `3567096` confirmed all original methods remain; only the original help method changed, and the new regression is the only added method. GBK fingerprint test, including decorator, is unchanged.
- `git diff --check 3567096` passed. `git diff --name-only 3567096 -- src data config .github tests/conftest.py` is empty.
- Independent specification/code-quality reviewer approved `cf7936d`, with no actionable findings. The reviewer verified actual source/AST and compile/diff, not independent pytest execution. It confirmed `--help` exits before fetcher construction/network calls, no duplicate definitions, and no parent environment mutation from subprocess configuration.

## Limits and next gate

This fixes a test transport/observability defect, not a proven production ChEMBL algorithm failure. No production scientific behavior, global console encoding, CI warning policy, shared fixture, timeout, data asset or GBK console behavior changed.

No whole-repository regression or Linux validation is claimed for this local stage. Publication/CI results must be recorded separately; a new PR requires explicit specific merge authorization. No deployment or real-model activation. T09, remaining T11 and final whole-taskbook verification remain unfinished.
