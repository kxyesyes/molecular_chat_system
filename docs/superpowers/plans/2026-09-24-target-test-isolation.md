# Target Test Isolation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans. Follow TDD and review checkpoints; no production code changes.

**Goal:** Make the two target test modules independent of ambient database/cache paths and Windows subprocess encoding, while forbidding accidental network use in cache-hit tests.

**Architecture:** Per-case scoped environment isolation and unittest cleanup, local CLI UTF-8 contract, targeted HTTP guards. Preserve production environment precedence and every existing scientific/count/path/HTTP assertion. Do not add a global fixture or runner framework.

**Tech Stack:** Python, unittest, pytest/MonkeyPatch, subprocess, Requests test doubles.

## Files and boundaries

- Modify `tests/test_target_search.py`: case setup/cleanup and the two cache-hit calls.
- Modify `tests/test_target_db_validation.py`: case setup/cleanup and real CLI encoding.
- Add `tests/test_target_test_isolation.py` only for boundary regressions that exercise the real existing TestCase setup/cleanup and CLI test, without collecting imported TestCases a second time.
- Maintain this plan and `docs/handoff/target-test-isolation.md`.
- Do not modify `src/`, production data/config, shared conftest, CI, time limits, expected counts or required assertions.

## Task 1: Reproduce and isolate each case

- [ ] Add regression cases covering both existing TestCase types. Use a temporary outer database/cache, call the actual setUp, and verify `get_db_path(case.root)` and `get_cache_dir(case.root)` remain rooted under that case. Exercise two different cases to prove paths differ. Always invoke doCleanups in finally.

```python
outer = {"TARGET_DB_PATH": str(tmp_path / "outside.sqlite"),
         "TARGET_CACHE_DIR": str(tmp_path / "outside-cache")}
for key, value in outer.items():
    monkeypatch.setenv(key, value)
case = case_type(methodName="runTest")
try:
    case.setUp()
    assert get_db_path(case.root) == case.root / "data/target_db/target_database.sqlite"
    assert get_cache_dir(case.root) == case.root / "data/target_db/cache"
finally:
    case.doCleanups()
assert {key: os.environ.get(key) for key in outer} == outer
assert not case.root.exists()
```

- [ ] Also exercise the actual unittest runner with a synthetic assertion failure, verifying cleanup runs despite failure; preserve unrelated environment variables and restore initially absent variables. Use synthetic fixtures only, no user environment reads.
- [ ] Run these new tests with MedChat Python under the established sanitized temporary runner. Record assertion failures before changing setup. Existing diagnostic RED (10 failures) is supporting evidence, not a substitute for the new regression RED.
- [ ] Minimal setup in both existing cases, using scoped MonkeyPatch for only the two keys and registering cleanup immediately after resource creation:

```python
environment = pytest.MonkeyPatch()
self.addCleanup(environment.undo)
environment.delenv("TARGET_DB_PATH", raising=False)
environment.delenv("TARGET_CACHE_DIR", raising=False)
self.temp_dir = tempfile.TemporaryDirectory(prefix="existing_prefix_")
self.addCleanup(self.temp_dir.cleanup)
self.root = Path(self.temp_dir.name)
```

Retain each existing temporary prefix. Remove now-redundant tearDown directory cleanup only after failure-path tests prove equivalent cleanup. Existing nested patch.dict tests must still override the isolated environment and prove configured path precedence unchanged.
- [ ] Re-run new regression and both existing modules. Record which failure remains before changing CLI encoding.

## Task 2: Explicit CLI encoding and network guard

- [ ] Add a regression that wraps the real `subprocess.run`, asserts parent encoding and child output encoding, and still invokes the actual CLI. Preserve strict exit=1 and parsed PDE missing-target assertions. Use Chinese fixture text to ensure a non-ASCII round trip, not just ASCII JSON.
- [ ] Run RED before the CLI change. Then change only this call:

```python
completed = subprocess.run(
    command, capture_output=True, text=True, encoding="utf-8", cwd=PROJECT_ROOT,
    env={**os.environ, "PYTHONIOENCODING": "utf-8"},
)
```

- [ ] In `test_local_download_returns_cached_file_without_network` and `test_send_to_docking_uses_readable_chinese_message`, guard the real downloader HTTP request and retain all original assertions:

```python
with patch("src.target_search.downloader.requests.get",
           side_effect=AssertionError("cache hit must not access HTTP")) as request:
    prepared = service.prepare_structure_file(structure["id"], requested_format="cif")
request.assert_not_called()
```

Use the equivalent context around `service.send_to_docking(structure["id"])`. Add a boundary regression that forces the cache path to miss and proves the guard rejects an actual download attempt; do not make that guard the product behavior and do not replace downloaded data with fallback results.
- [ ] Run GREEN in the original shared absolute path runner without `-X utf8`; run both modules in reversed order. No real HTTP/model opt-in. Check no reader-thread UnicodeDecodeError appears.

## Task 3: Review, combined regression and handoff

- [ ] Run `python -B -m pytest tests/test_target_test_isolation.py tests/test_target_db_validation.py tests/test_target_search.py -q -p no:cacheprovider` through the sanitized temporary environment from the existing RAG plan. Also run reversed existing-module order with the same hostile outer target paths.
- [ ] Run related target/cache and Agent target-contract tests identified with `rg --files tests`; preserve platform skips and record exact paths/counts. No blanket network guards across unrelated suites.
- [ ] Independently review spec compliance, then code quality; close any findings with RED/GREEN. The coordinator may run regressions while review is active, without editing the implementer's files.
- [ ] Run `git diff --check`, in-memory compilation of modified Python, and `git diff --name-only 4ff859e -- src data config .github` (must be empty). Review assertion diff and secrets scan before staging exact task files.
- [ ] Write handoff with baseline, commands, first failures, final results and remaining limits. Record PR57 separately as already merged `9eed743`; this batch does not change Web partial. Commit only approved test/docs files, no push/merge without the corresponding publication gate.

## Self-review

All approved sections map to the three tasks: isolated roots/restoration/failed-test cleanup, nested configuration overrides, real UTF-8 CLI, cache guards, reversed order and unchanged business/CI/scientific contracts. No all-suite pass claim is permitted from the focused checks.
