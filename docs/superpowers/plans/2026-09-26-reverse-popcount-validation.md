# Reverse Popcount Validation Implementation Plan

> **For agentic workers:** Use subagent-driven-development and TDD, then independent SPEC and QUALITY reviews. Parent owns documents and commits.

**Goal:** Reject incorrect cached fingerprint row counts before actual reverse prediction.

**Architecture:** Narrow correction to the existing shared loader helper; preserve
legacy missing-cache handling and scientific scoring. This is a prerequisite for
later owned-generation receipts, not their implementation.

**Tech Stack:** Python/NumPy/RDKit/pandas, temporary .npy/TSV fixtures, pytest.

Spec: ../specs/2026-09-26-reverse-popcount-validation-design.md.

## Task 1 — reproduce through actual load

- [ ] Add tests/test_reverse_target_popcounts.py with a temporary database fixture:
  two complete TSV records for CCO/CCC, real compute_query_fingerprints arrays,
  np.save both matrices and correct row-count vectors. No metadata pickle needed.
- [ ] Baseline actual load and predict succeed; create a second independent
  predictor after changing one Morgan cached count by1, preserving shape.

```python
def close_fixture_mmaps(predictor):
    for name in ('morgan_fps', 'maccs_fps', 'morgan_popcounts', 'maccs_popcounts'):
        handle = getattr(getattr(predictor, name, None), '_mmap', None)
        if handle is not None:
            handle.close()

def test_actual_load_rejects_same_length_wrong_cache(tmp_path):
    predictor = make_database(tmp_path)
    try:
        predictor.load()
        assert predictor.predict('CCO')[0]['final_similarity'] == 1.0
    finally:
        close_fixture_mmaps(predictor)
    # Windows cannot reliably overwrite the baseline's live mapped cache.
    counts = np.load(predictor.morgan_popcount_path)
    counts[0] += 1
    np.save(predictor.morgan_popcount_path, counts)
    original = predictor.morgan_popcount_path.read_bytes()
    fresh = ReverseTargetPredictor(tmp_path)
    try:
        with pytest.raises(ValueError):
            fresh.load()
        assert not fresh._loaded
        assert fresh.morgan_popcount_path.read_bytes() == original
    finally:
        close_fixture_mmaps(fresh)
```

- [ ] Run exact node through approved runner before production edits. Expected
  RED DID NOT RAISE. Record output/count/duration/exit, correct fixture errors
  separately rather than substituting missing APIs for behavioral evidence.

## Task 2 — helper validation and tests

- [ ] Add spec negatives/positives using real .npy files and bounded chunks.
  Test controlled chunk size1 plus corruption in a later row. Include both count
  and fingerprint shape/dtype/range validation; preserve old valid arrays.
- [ ] Implement narrow helper sequence in src/reverse_target/predictor.py:

```python
# Validate fingerprint rank/integer-or-bool dtype/width before any disk write.
# Resolve existing chunk-size setting as before.
# If cache exists, np.load using the existing read-only mmap path; validate
# vector shape/integer dtype, copy only its O(rows) data, close owned mmap in
# finally. Convert ordinary load errors to fixed ValueError without path text.
# For each bounded fingerprint row chunk, reject nonbinary values; sum axis=1
# using uint32. Compare cached values exactly (including range), or fill the
# original uint16 missing-cache output. No scientific query/scoring call here.
# Convert fully validated cached counts to detached uint16, matching missing
# counts, before returning. Do not cast prior to exact/range validation.
# Bad existing cache raises; do not silently rebuild. Missing valid counts use
# the original best-effort np.save block. Return detached validated counts.
```

- [ ] No changes to legacy predictor.load ordering, formula, batch or weights.
  Missing-cache writes remain legacy-only; do not add speculative strict flags.
- [ ] GREEN exact original node, new module, then unchanged regression union.
  Retain every failed run and don't weaken assertions or add skip/timeout.

## Task 3 — review and checkpoint

- [ ] Worker self-review, exact final hashes/results, release test slot, no commits.
- [ ] Independent SPEC against written contract; any finding gets targeted RED
  and minimal fix before separate QUALITY/independent regression.
- [ ] Parent in-memory compile two changed Python files (-I -S -B, no imports or
  bytecode); git diff --check; original-tree status check; explicit local staging.
  Record completion as cache arithmetic only; don't label B execution complete.

## Commands

Run from D:/MedChat/molecular_chat_system_worktrees/dynamic-bindings-b1. Prefix:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_reverse_target_popcounts.py::test_actual_load_rejects_same_length_wrong_cache
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_reverse_target_popcounts.py
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_reverse_target_popcounts.py tests/test_reverse_target_health.py tests/agent/test_reverse_target_complete_input.py tests/agent/test_target_tool_contract.py
```

Runner SHA256 F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183.
One heavy process at a time; poll same handle to terminal. No raw pytest, host
env/config/credentials or scientific asset lookup. No external model or network.
No changed timeout, threshold or scientific formula; all recorded science here
uses explicitly synthetic temporary test data, not real acceptance.

## Written review

Independent read-only review found a Windows fixture hazard: the successful
baseline retains mapped cache handles. The first RED now explicitly closes
fixture-owned mappings before cache corruption and on exceptional exits. No
production loader ownership or method is stubbed to obtain the RED.

## Source-review refinement

Parent source review and independent SPEC identified a downstream arithmetic
issue: preserving valid int8/uint8 cached storage can overflow the denominator's
addition in batch_tanimoto_similarity before later subtraction. The original spec
did not require cached dtype preservation. Amend it to canonical uint16 only
after full original-value validation, matching existing missing-cache counts.
This is safe for the actual166/2048-bit predictor; no arbitrary-width scoring
promise or formula expansion is introduced. First reproduce with actual helper
and batch scorer (100/int8,128/uint8), then replace the test-only dtype-preservation
expectation with canonical dtype/value and cached/missing/no-cache score parity.
No implementation correction is released until the behavioral score RED is known.

## TDD evidence and source-review correction

All commands below used the exact approved runner prefix and the current worktree,
one terminal process at a time. No skipped tests or modified timeouts.

| Order | Scope | Passed | Failed | Subtests passed | Seconds | Exit |
|---|---|---:|---:|---:|---:|---:|
| 1 | Actual-load first RED | 0 | 1 | 0 | 2.31 | 1 |
| 2 | New module RED | 56 | 65 | 0 | 4.77 | 1 |
| 3 | Actual-load GREEN | 1 | 0 | 0 | 0.90 | 0 |
| 4 | Module GREEN | 121 | 0 | 0 | 2.42 | 0 |
| 5 | Four-module union | 390 | 0 | 2 | 15.29 | 0 |
| 6 | Actual score overflow RED,2 dtypes x2 widths | 0 | 4 | 0 | 2.29 | 1 |
| 7 | Canonical cached dtype RED | 1 | 7 | 0 | 1.56 | 1 |
| 8 | Original node plus score/dtype GREEN | 13 | 0 | 0 | 1.44 | 0 |
| 9 | Corrected module | 125 | 0 | 0 | 2.99 | 0 |
| 10 | Corrected four-module union | 394 | 0 | 2 | 15.18 | 0 |
| 11 | Independent QUALITY four-module union | 394 | 0 | 2 | 15.69 | 0 |

Run1 failed DID NOT RAISE ValueError after the actual baseline load/predict passed;
not a fixture/import failure. Run2 also exposed unsafe error messages and borrowed
cache ownership/detachment gaps. Initial RED output included RDKit deprecation
notices and run2 one ComplexWarning; GREEN commands reported no warnings.
Run6 directly confirmed narrow integer overflow: int8 self-score0.0 and uint8
self-score2.9802322e-08 versus missing-cache/no-popcount baselines1.0. This justified
the spec refinement, not weakening a scientific assertion. Canonical uint16 is
applied only after every original value passes validation; scoring code unchanged.

Corrected frozen SHA256:

| File | SHA256 |
|---|---|
| src/reverse_target/predictor.py | C35C3C0E5C6B61836FFA47DDF2965C73521F74ACD051F08B5F0A5A90991E3347 |
| tests/test_reverse_target_popcounts.py | D7E9C9504BD69233C4FB827B5FD97C8F2D47278C60B65E7F35618B324C9BD9F0 |

Parent compiled both Python files in memory (-I -S -B, no imports or bytecode),
and git diff --check passed. Independent SPEC rereview APPROVED after the P2
overflow correction; independent QUALITY APPROVED and repeated the exact union
in run11 with zero skips/warnings. Both source/test hashes and approved-runner
hash matched before/after the independent run. No unresolved review findings.
Tasks1–3 are complete; earlier checkboxes preserve the sequence, not open gates.
This is not a full-repository, live-model or stable-source ownership test.
