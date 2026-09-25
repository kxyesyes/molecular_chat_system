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
