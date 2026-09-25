# Reverse-target cached popcount validation

Date:2026-09-26. Baseline1e9bb34. Recommended choice delegated by the user.

## Boundary and decision

Before exposing reverse-target observations to dynamic B bindings, close the
actual loader's demonstrated source-code gap: _load_or_compute_popcounts accepts
an existing cache when its first dimension matches, without comparing its values
to fingerprints. These values enter the Tanimoto denominator. This batch must
first reproduce the behavior using real temporary files and predictor.load().

Selected: validate cached counts against binary fingerprint row sums in bounded
chunks at load time; fail on corrupt existing files. Alternatives rejected:
silently overwrite the cache (hides corrupt scientific input), or implement the
whole source-receipt/runtime integration before isolating this arithmetic input
defect (larger change and weaker attribution). Missing cache keeps the existing
compute-and-best-effort-save compatibility behavior; this is not the future
strict no-write acquisition path.

Production scope only src/reverse_target/predictor.py, specifically the shared
popcount helper and a narrowly extracted validator if required. No score,
threshold, weight, sort/group, organism-filter or endpoint changes. New tests
tests/test_reverse_target_popcounts.py. No other production/test edits.

## Contract

- Fingerprint input is a two-dimensional NumPy array with integer or bool dtype,
  binary 0/1 values, and positive width fitting the existing uint16 count format
  (<=65535). Zero rows are valid. Do not require widths2048/166 in this helper:
  existing legacy tests use smaller binary arrays; strict generation validation
  is a separate boundary.
- Existing cache must load safely as a one-dimensional integer (not bool or
  float/object) vector with exactly one value per fingerprint row. Every value
  is in [0,width] and exactly equals that row's number of set bits. Validate all
  rows, not a sample or just totals. Same-sized Morgan/MACCS cache swaps reject
  when values differ. Equal counts alone never prove fingerprint-to-TSV alignment.
- Validate cached counts against bounded row chunks using uint32 accumulation,
  preserving the existing chunk-size configuration/default/clamping behavior.
  Never allocate another full fingerprint matrix or recompute query fingerprints.
  After validating original cached values, return detached canonical uint16
  counts, matching missing-cache computation. Do not preserve int8/uint8 storage
  dtypes: downstream count-plus-query-count can overflow before subtraction for
  the actual166/2048-bit predictors. Never narrow before validation, which could
  hide corrupt negative/oversized values. This does not extend the scientific
  predictor to arbitrary65535-bit vectors; that bound is only helper count storage.
  A detached O(number-of-rows) cached count vector is allowed so returned counts
  cannot change when the cache file is subsequently edited. Close only mmap
  handles newly owned by this helper, including exceptional exits; never close
  caller-owned fingerprint arrays.
- Existing unreadable, malformed or inconsistent caches raise a fixed safe
  ValueError without overwriting/deleting them. Predictor.load must remain
  _loaded=False on this failure. Do not catch cancellation/BaseException.
  Tests verify all existing cache bytes remain unchanged and no missing sibling
  cache is created when the corrupted cache is the first one examined.
- With no cache, validate fingerprint chunks before writing, calculate exactly
  the same uint16 counts and retain the existing best-effort np.save/OSError
  behavior. Invalid fingerprints must not produce a cache. Correct cached and
  uncached valid-data predictions retain exact numerical/list behavior.
- This is load-time arithmetic integrity, NOT stable ownership of fingerprint
  mmaps/dataframes, atomic database generations, current-source eligibility,
  TSV row alignment, invocation receipts, true no-hit proof or model accuracy.
  Later strict producer acquisition must not call legacy auto-writing load.

## Verification

First RED: two valid temporary TSV/real RDKit2048/166 fingerprint rows, valid
cached counts and successful baseline load; alter one Morgan count without
changing cache shape, instantiate a new predictor, call actual load, require a
ValueError. Old code must fail with DID NOT RAISE, not an import/fixture error.
Neither load nor _load_or_compute_popcounts may be stubbed in this reproduction.

Cover cached shape/scalar/2D/length, integer range, bool/float/object, wrong values
with unchanged total, corrupt bytes, binary input validation in later chunks,
zero rows, both fingerprint widths, missing-cache computation/write failure,
unchanged cache bytes after rejection, detached returned counts and mmap cleanup.
Keep real-score positive cached/missing parity via predict, not just np.sum tests.
Add synthetic actual batch-score controls with100 set bits/int8 cache and128 set
bits/uint8 cache at widths166/2048. Self-similarity must be1 and equal missing-cache
and no-popcount calculations; preserve these as arithmetic, not biological tests.

Only approved isolated runner, temporary synthetic assets and controlled test
environment; no real data/model/API/network/config/credentials. Run unchanged
reverse health, complete-input and target-tool contract regressions. Independent
SPEC then QUALITY review. No publication/deployment in this slice. Original dirty
checkout stays untouched; full B/C/P8 goal remains pending.
