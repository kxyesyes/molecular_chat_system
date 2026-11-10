# Reference ASCII budget optimization: verification

## Scope and diagnosis

The approved change adds only an unescaped-ASCII byte-counting branch to
`src/agent/contracts/scientific_references.py`. The existing per-character
fallback handles escapes, controls and all non-ASCII text unchanged. Tests
are added in `tests/agent/test_scientific_reference_contracts.py`.

Payload-free local diagnostics measured 164 reference revalidations taking
approximately 3.2–4.2 seconds, and Pi startup taking approximately 1.8–2.1
seconds. Individual pipe frame reads took milliseconds. A ten-revalidation
profile identified the JSON budget character walk as an avoidable CPU hotspot.
These instruments affect timings; they are not acceptance tests.

Three alternating blocks on the same persisted source, each retaining 40 full
revalidations, measured original/ASCII prototype times of 0.7710/0.6069,
0.7480/0.5681 and 0.6884/0.5764 seconds. All 240 checks returned valid. The
16–24% improvement applies to those revalidation blocks, not the entire request.
No validation-result cache, reduced check count, dependency update or scientific
algorithm change is part of the fix.

## TDD and exact semantics

```powershell
python -B -m pytest tests/agent/test_scientific_reference_contracts.py -k avoids_python_character_walk -q --tb=short
python -B -m pytest tests/agent/test_scientific_reference_contracts.py -q --disable-warnings --tb=short
```

Before implementation the five deterministic fast-path cases failed because
ordinary ASCII entered the original `ord` loop. After implementation the full
file passed **234 tests**. New coverage includes every ASCII character as both
key and value, canonical UTF-8 byte sizes at and one byte beyond the limit,
Unicode/astral and escaped text, and surrogate rejection before serializers or
secret scans. Existing node/depth/cycle, plain-type, privacy and revision tests
remain unchanged.

## Windows default environment

The unchanged three-file command was:

```powershell
python -B -m pytest tests/agent/test_scientific_reference_browser_lab.py tests/agent/test_main_integration_acceptance.py tests/agent/test_scientific_reference_web_binding.py -q --disable-warnings --tb=short --durations=3
```

Before this optimization: **52 passed, 3 warnings**, 73.14 seconds.
The first run after implementation: **1 failed, 51 passed, 3 warnings**, 63.98
seconds. The failure was the first turn's 10-second `molecule_candidates`
deadline, before the second turn's repeated selected-reference validation.
This failure is retained. The ASCII hotspot is real but is not a demonstrated
explanation or complete fix for that first-turn stall.

Five planned separate processes then ran the original two-turn test, with no
retry logic: **1 passed in each of five runs**. Full two-turn test durations
were 10.82, 11.08, 11.90, 12.15 and 11.86 seconds. These durations include two
independent receive budgets, HTTP confirmation, reopening, and teardown; no
single receive deadline was increased.

A later payload-free phase probe on the production patch passed with a second
turn of **6.1861 seconds**, including **164 reference revalidations** totaling
1.8289 seconds. Machine/load differences prevent attributing that entire
end-to-end improvement to this patch. Re-running the three-file suite with
phase-only observers produced **52 passed, 4 warnings**, 52.23 seconds; the
original case took 9.09 seconds overall. It did not reproduce the earlier stall,
so its precise stalled phase remains unconfirmed.

The complete default-environment reference/security suite passed **631 tests,
7 warnings** in 101.92 seconds. Its selection is reproducible with:

```python
from pathlib import Path
import pytest
targets = sorted(str(p) for p in Path('tests/agent').glob('test_scientific_reference_*.py'))
targets += ['tests/test_scientific_reference_target_context.py',
            'tests/test_llm_security_policy.py', 'tests/test_runtime_boundary_contracts.py']
raise SystemExit(pytest.main(targets + ['-q', '--disable-warnings', '--tb=short', '--durations=3']))
```

## Review, compatibility and remaining checks

Independent read-only review found no actionable correctness/security issue;
the reviewer did not run tests concurrently with timing measurements. Checks
remain allocation-bounded before JSON serialization, without invoking custom
hooks. Cookie/Origin, reference freshness, permissions, scientific assertions,
frame budgets, 10-second receive deadlines and worker settlement are unchanged.

Compatibility verification with the previously isolated CI dependency stack
passed **631 tests, 11 warnings** in 92.27 seconds. The same suite selector was
used after prepending `outputs/timeout_probe/pydantic250` to `sys.path` only in
that diagnostic process; the installed environment was not changed.
`python -m compileall -q src scripts tests/agent/test_scientific_reference_contracts.py`
and `git diff --check` passed. Actual Linux CI is pending at commit creation
and will be reported in PR #216; it is not inferred from local tests.

No local full-repository, separate JavaScript, deployment-health or paid-model
acceptance run is part of this Python-only optimization. Diagnostic scripts and
databases remain ignored and uncommitted. No existing user change, unrelated
process, environment version, merge or deployment is altered. The first-turn
Windows timeout remains a disclosed unresolved risk, not a passing check.
