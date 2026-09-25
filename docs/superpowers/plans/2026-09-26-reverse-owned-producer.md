# Reverse Owned Producer Implementation Plan

> **For agentic workers:** Use subagent-driven-development and TDD with independent SPEC then QUALITY review. Parent owns documents and commits; one heavy test process at a time.

**Goal:** Return reverse predictions with same-call owned-data/weight receipts and current-source validation, without certifying mutable legacy state.

**Architecture:** New domain-only owned_source module handles bounded acquisition,
identity and pure validation; predictor owns strict lifecycle and a shared scoring
core with its legacy list API. No new Agent, store, builder or tool wiring.

**Tech Stack:** Python dataclasses/RLock/hashlib, NumPy/pandas/RDKit, temporary
synthetic TSV/NPY and controlled-clock/barrier tests.

Spec: ../specs/2026-09-26-reverse-owned-producer-design.md (full contract).
Baseline9a3ccd3. Approved runner SHA256
F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183.

## Files and scope

- Add src/reverse_target/owned_source.py: private generation, bounded load,
  shared popcount arithmetic, versioned byte/JSON identities, detached proof checks.
- Modify src/reverse_target/predictor.py: delegate popcount arithmetic without
  changing legacy persistence, factor one scientific core and weight resolution,
  strict operation-local limits/lifecycle/receipt APIs.
- Add tests/test_reverse_target_invocation_receipts.py. Existing
  tests/test_reverse_target_popcounts.py may change only a moved helper injection
  seam; retain all arithmetic/ownership assertions and explain any fixture change.
- No other source/test changes, production data/config/env lookup, external model,
  network, publication or deployment. Original dirty checkout untouched.

## Task 1 — reproduce real misassociation before adding APIs

- [ ] Fixture creates complete two-row TSV (CCO→Synthetic A, CCC→Synthetic B),
  actual writer FingerprintGenerator Morgan/MACCS outputs (no process pool), and
  genuine row-sum caches. No mocks of predictor load/scoring or owned generations.
- [ ] Baseline load and CCO prediction return the correct association; close all
  fixture-owned legacy mmaps, swap only TSV rows, then acquire a fresh predictor.
  Record that its CCO score1 is attached to CCC/Synthetic B. First failing node:

```python
def test_strict_source_rejects_swapped_rows(tmp_path):
    paths = make_writer_database(tmp_path)
    assert_baseline_legacy_prediction(paths, 'CCO', 'Synthetic A')
    swap_tsv_rows(paths)  # baseline mappings have already closed in finally
    fresh = ReverseTargetPredictor(tmp_path)
    try:
        with pytest.raises(ValueError):
            fresh.predict('CCO')
    finally:
        close_fixture_mmaps(fresh)
```

- [ ] Run this node before production edits. Expected DID NOT RAISE, not import/
  fixture failure. Preserve exact failure. Then change only its fresh-acquisition
  action to fresh.initialize_strict(); fresh.predict_with_receipt('CCO') for the
  newly introduced strict boundary. The legacy API is compatibility-only, not
  silently claimed repaired by that call-site migration.
- [ ] Add new API positives/negatives from the spec; missing API failures are
  secondary coverage, not the original behavioral reproduction.

## Task 2 — bounded owned acquisition

- [ ] Implement pure validate_popcount_rows(fingerprints, counts, *, chunk_size,
  check=None) in owned_source using the existing validated arithmetic, unchanged
  error messages and general-width compatibility. counts=None computes privately.
  Existing legacy wrapper still opens/closes/copies its cache, resolves old chunk
  setting, calls that helper, and saves only missing cache as before. Do not move
  filesystem/persistence into the pure helper or weaken cached-before-cast checks.
- [ ] Use operation-local check closure (monotonic absolute deadline, strict
  bool callback, asyncio.CancelledError) throughout the loader; no bound callback
  survives on a published generation. Validate limits before filesystem access.
- [ ] Load/hash the same bounded TSV bytes; validate all required cells/whole
  SMILES and logical memory budget. Load NPY with allow_pickle=False and16KiB
  header cap, reject non-array/rank/shape/dtype before private allocation. Copy
  bounded chunks to owned uint8; close every owned file/map in finally. Exact
  per-row writer/query RDKit agreement precedes publication. Validate cached or
  privately computed counts with the shared helper; prohibit save/pickle calls.
- [ ] Hash arrays with the exact spec framing/little-endian chunks, descriptor
  and native canonical JSON. Mark arrays read-only and keep all aliases private.

Conceptual publication sequence (all names implemented in predictor/module):

```python
with strict_lock:
    reject_if_loading_or_active()
    strict_epoch += 1
    strict_source = None
    strict_loading = True
    epoch = strict_epoch
try:
    paths = resolved_source_paths()
    candidate = load_owned_source(paths, max_snapshot_bytes, check, fingerprint_fn)
    with strict_lock:
        check()
        require_same_epoch_and_paths(epoch, paths)
        strict_source = candidate
finally:
    with strict_lock:
        strict_loading = False
```

- [ ] Test unavailable before initialization; valid/empty source; malformed cells,
  nonfinite assay, rows/same-popcount bit corruption and reordering; all cache
  variants; no save/pickle; budget/header/deadline/cancellation; late close/config
  mutation. Barriers/controlled clock, no sleep-only timing gates.
- [ ] Force path resolution to raise after this operation acquires loading
  ownership: source stays unavailable, its finally releases the loading flag,
  and a subsequent initialization succeeds. A caller rejected as busy must
  never enter that owning finally or clear another acquisition's loading flag.

## Task 3 — shared scoring, same-call proof, current-source check

- [ ] Extract existing predict body into a common core taking explicit frame,
  arrays/counts and captured weights. Make organism filtering explicitly use that
  frame; old _apply_organism_filter remains a thin public-field wrapper.
  Preserve stable tie/group order, threshold/slicing, link fields and every old
  list record. Core retains records plus actual final_sims.dtype internally.
- [ ] Extract exact weight conversion/default/clamp policy into one resolver,
  returning morgan/maccs and resolution_revision/defaulted/clamped. Legacy helper
  uses it; strict scoring resolves once. Optional check callback runs between
  batch chunks and record-building blocks for strict calls; legacy calls keep
  their old behavior/signatures/defaults and list-only return.
- [ ] Implement strict per-call input/timeout/cancellation checks, acquire active
  generation+paths+weight record under lock, increment active counter, then call
  shared core exactly once. Always decrement in finally; close cannot reset it.
  End checks reject stale generation/path/deadline; a historical weight change
  never substitutes new metadata for old scores. Do not auto-initialize/retry.
- [ ] Build exact spec envelope from invocation-local values and return detached
  native JSON. No last_receipt or mutable public fields used for provenance.
- [ ] Implement exact frozen ReverseSourceSnapshot and capture_prediction_source;
  current path drift invalidates sticky, disk edits alone leave loaded snapshot
  authoritative. close/reinit identity changes. A busy reload doesn't revoke the
  in-flight healthy generation or create another candidate allocation.
- [ ] Implement validate_prediction_source: pure closed/bounded JSON, field/type/
  digest/control/record checks, then expected/current/receipt source+configuration
  agreement. Use actual recorded score_dtype for threshold comparison; no generic
  tolerance. No fingerprints/search/reload/save. Reject malformed proof with safe
  ValueError, source mismatch/unavailable with ReverseSourceUnavailable; stale
  callers never revoke healthy current generation. Do not authenticate hashes.

## Task 4 — parity, races and evidence boundaries

- [ ] Actual legacy/strict shared-core exact records parity for group/no-group,
  ties, organism filters, top-k, thresholds (including float32 boundary), empty
  dataset/no-hit and real cached/private counts. Varied writer/query bit parity.
- [ ] Validate weights: default/unset/invalid, negative/over-one, NaN/±inf and
  explicit values; mutate after score calculation and verify receipt captured old
  values while fresh eligibility rejects reuse. No scientific recomputation.
- [ ] Two concurrent scoring calls use independent invocation IDs, controls and
  receipts. Close during blocked scoring rejects its late publication, active
  count remains until unwind, reload busy then succeeds after settle. Acquire
  close/reload/config mutation uses controlled barriers, no scheduling assumptions.
- [ ] Modify/delete disk files and legacy public fields after strict initialize;
  loaded snapshot stays correct, explicit reload adopts or safely rejects new
  data. Descriptors/results returned to callers are detached. No accessor leaks
  dataframe/array authority. Wrong/self-inconsistent proof cases never mutate the
  input envelope or a newer generation. Self-consistent forgery is not certified
  as authenticated; Session seals remain an explicit later prerequisite.
- [ ] Run new module, legacy popcount module, then union below. Record every run,
  exact counts/subtests/warnings/duration/exit, all intermediate failures, final
  SHA256. No skips/relaxed scientific assertions to obtain green. Worker releases
  test slot without staging/commits; parent requests SPEC then independent QUALITY.

## Commands and final gate

From D:/MedChat/molecular_chat_system_worktrees/dynamic-bindings-b1:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_reverse_target_invocation_receipts.py::test_strict_source_rejects_swapped_rows
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_reverse_target_invocation_receipts.py
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_reverse_target_popcounts.py
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_reverse_target_invocation_receipts.py tests/test_reverse_target_popcounts.py tests/test_reverse_target_health.py tests/test_reverse_target_pharmacophore.py tests/agent/test_reverse_target_complete_input.py tests/agent/test_target_tool_contract.py
```

No raw pytest/ad-hoc imports or multiple simultaneous heavy processes. Poll the
same handle until terminal. Parent compile changed Python in memory with-I-S-B,
git diff --check and original dirty-tree status, then explicit local staging.
No live scientific acceptance, strict tool routing, B reuse/resume or P7/P8 final
completion is established by this producer batch. Full goal remains unchanged.
