# Reference ASCII Budget Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task in the current session, as requested by the user.

**Goal:** Remove avoidable Python character-loop cost without changing JSON acceptance or scientific/security checks.

**Architecture:** Add an exact, allocation-free fast path for native unescaped ASCII strings in the existing reference preflight. Keep the original fallback and every caller unchanged.

**Tech Stack:** Python 3.10, native regex/string operations, pytest, existing real ASGI/Pi/RDKit offline integration.

## Task 1: Prove the fast-path regression before implementation

**File:** `tests/agent/test_scientific_reference_contracts.py`.

- [ ] Add a deterministic regression using a module-local `ord` seam:

```python
@pytest.mark.parametrize("text", ["", "plain ASCII", "CCN", "a" * 64, "DEL\x7f"])
def test_plain_ascii_budget_avoids_python_character_walk(monkeypatch, text):
    module = _module()
    def forbidden(value):
        raise AssertionError("plain ASCII entered Python character walk")
    monkeypatch.setattr(module, "ord", forbidden, raising=False)
    module._check_json_bounds({"text": text})
```

- [ ] Run the test with `python -B -m pytest tests/agent/test_scientific_reference_contracts.py -k avoids_python_character_walk -q --tb=short`; require failure from the original loop, not an import/setup error.
- [ ] Add exact byte-boundary differential tests using canonical JSON UTF-8 length as the independent oracle for all 128 ASCII characters, representative multibyte/astral text, mixed escaped strings, and invalid surrogates. At the exact encoded size preflight must accept; one byte below it must reject. Preserve preflight-before-serializer/secret-scan tests.

## Task 2: Minimal production change

**File:** `src/agent/contracts/scientific_references.py`.

- [ ] Add `_ASCII_ESCAPE = re.compile(r'[\x00-\x1f"\\]')` beside existing constants.
- [ ] Keep the existing type/remaining-length guard. Replace only the string accounting branch with:

```python
if item.isascii() and _ASCII_ESCAPE.search(item) is None:
    charge(len(item) + 2)
else:
    # Existing charge(2) and character loop, unchanged.
```

The comment above denotes moving the already-present fallback verbatim, not introducing a new fallback algorithm. Do not modify limits, call sites, scanning, canonical serialization, source checks, or permissions.

- [ ] Run the full contract test file; require all existing and new cases to pass.

## Task 3: Sequential verification and review

- [ ] Run `python -B -m pytest tests/agent/test_scientific_reference_browser_lab.py tests/agent/test_main_integration_acceptance.py tests/agent/test_scientific_reference_web_binding.py -q --disable-warnings --tb=short --durations=3` in the default Windows environment.
- [ ] Run the unchanged `test_real_home_socket_ack_and_reopened_lab` in five separate processes. Preserve its original deadline and report every result.
- [ ] Run all `tests/agent/test_scientific_reference_*.py` plus `tests/test_scientific_reference_target_context.py`, `tests/test_llm_security_policy.py`, and `tests/test_runtime_boundary_contracts.py`. Check compatibility with the already isolated CI dependency stack without installing into the default environment.
- [ ] Use the existing ignored payload-free timing probe without its in-memory optimization flags, to verify all 164 revalidations remain and record phase durations. Do not run timing probes concurrently with local tests.
- [ ] Run `python -m compileall -q src scripts tests/agent/test_scientific_reference_contracts.py` and `git diff --check`.
- [ ] Request independent read-only review; correct only actionable issues in scope. Keep all failures and instrumentation limitations in the validation document.

## Task 4: Delivery

- [ ] Record actual commands/results in `docs/superpowers/validation/2026-10-24-reference-ascii-budget.md`.
- [ ] Explicitly stage only the two code/test files and this task's documentation. Commit with `perf(agent): fast-path plain ASCII reference budgets` and push the existing draft PR branch.
- [ ] Inspect actual CI for that commit, update PR #216 with results and remaining risk, and verify the original checkout's edits remain intact. No merge or deployment.
