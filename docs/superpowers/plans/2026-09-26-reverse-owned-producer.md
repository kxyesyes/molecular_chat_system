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

## Implementation evidence (2026-09-26; reviews pending)

Written review approved the scope after moving path resolution inside the owning
try/finally and requiring a path-failure recovery/nonowner-busy regression.
Design/plan committed as e1120be. Implementation edits only owned_source.py,
predictor.py and the new receipt test module; existing popcount tests unchanged.

The implementer's actual original RED used writer-generated RDKit arrays. Before
TSV reordering, CCO returned CCO / Synthetic A / similarity1.0. After swapping only
the TSV rows and closing old mappings, a fresh legacy predictor returned
CCC / Synthetic B / similarity1.0 for CCO. The asserted rejection failed:
`DID NOT RAISE ValueError`; 1 failed in1.31s, exit1. This is actual misassociation,
not missing-API evidence. The test action was then migrated to the new strict
initialize/predict boundary; legacy list loading remains compatibility-only.

All commands use the approved runner prefix in Commands above. Argument IDs:

- A: receipt module::test_strict_source_rejects_swapped_rows.
- B: tests/test_reverse_target_invocation_receipts.py.
- C: tests/test_reverse_target_popcounts.py.
- D: receipt module::test_deadline_expiring_during_final_path_resolution and
  receipt module::test_huge_native_number_safe_rejection.
- E: the exact six-module union in Commands above.
- F: receipt module::test_producer_never_returns_out_of_schema_link_strings.
- G: receipt module::test_receipt_control_numeric_identity_tampering,
  receipt module::test_receipt_weight_numeric_identity_tampering, and
  receipt module::test_native_threshold_identity_preserved_with_legacy_parity.

Here `receipt module::` expands to
`tests/test_reverse_target_invocation_receipts.py::`. Times are pytest-reported,
not outer wall-clock. This is implementer-provided output history, not independent
parent reruns; all intermediate failures are retained:

| Run | Args | Result | Seconds | Exit |
|---|---|---|---:|---:|
| 1 | A | 1 failed: actual legacy misassociation | 1.31 | 1 |
| 2 | B | 46 failed: new strict API missing | 2.81 | 1 |
| 3 | B | 46 passed | 4.67 | 0 |
| 4 | B | 2 failed,110 passed | 11.50 | 1 |
| 5 | B | 112 passed | 12.88 | 0 |
| 6 | C | 125 passed | 2.34 | 0 |
| 7 | D | 4 failed | 2.02 | 1 |
| 8 | D | 4 passed | 1.16 | 0 |
| 9 | B | 1 failed,136 passed | 15.68 | 1 |
| 10 | B | 137 passed | 10.61 | 0 |
| 11 | E | 540 passed,2 subtests passed | 32.14 | 0 |
| 12 | F | 1 failed | 1.38 | 1 |
| 13 | B | 138 passed | 11.78 | 0 |
| 14 | C | 125 passed | 2.54 | 0 |
| 15 | E | 541 passed,2 subtests passed | 37.43 | 0 |
| 16 | G | 9 failed,5 passed | 2.88 | 1 |
| 17 | G | 14 passed | 1.67 | 0 |
| 18 | B | 152 passed | 10.22 | 0 |
| 19 | C | 125 passed | 2.77 | 0 |
| 20 | E | 555 passed,2 subtests passed | 30.61 | 0 |

No visible run summary reported skips or pytest warnings. Failure captures do
contain RDKit deprecation diagnostics, and run4 contains an invalid-SMILES parse
diagnostic. Run2's detailed46 tracebacks were truncated in the tool output and
are not recoverable in full; only its exact summary and missing-API error classes
are retained, without reconstructed individual traces.

Intermediate corrections:

- Run4: invalid cancellation callback return was misclassified as source error;
  invalid query error exposed its string. Fixed strict input classification and
  safe error text without weakening whole-SMILES validation.
- Run7: final path resolution could consume the deadline without rejection;
  huge native integer timeout/threshold overflowed math.isfinite. Added terminal
  deadline check and checked numeric range before conversion.
- Run9: new test incorrectly assumed a float64 final vector for [He]. It now
  compares the actual legacy batch vectors and original weighting formula dtype;
  scientific computation and tolerance unchanged.
- Run12: long target name produced an out-of-schema URL. Producer now performs
  pure closed-envelope checks before returning, without another scoring call.
- Run16: parent source review identified Python equality hiding changes between
  0/0.0 and 0.0/-0.0. Nine real generated-receipt mutations with unchanged hashes
  failed rejection before the fix. Controls now use exact canonical identities;
  input digest is computed from receipt controls, and current configuration
  digest is recomputed from private paths plus receipt weights/resolution.

Frozen worker hashes before independent review:

- owned_source.py: DB5DF2D9F766A37366C6A8F7E8666613EAF48D23D61753093AA0BAD3C3CB7B81
- predictor.py: 98575957F3038CEDA1CC75212BDB27E91B29E160FCFE088CDDEB0CF599961769
- receipt tests: 6A4650A3A7865529AE24E1A83FC214C6086290C4D3A1DF9FE02D3E9F29519EC9

Parent independently confirmed these hashes, compiled all3 changed Python files
in memory using -I -S -B without imports, and ran git diff --check successfully.
SPEC and subsequent independent QUALITY review remain required at this snapshot.

### First source SPEC review: four P2 findings (not yet approved)

Independent reviewer confirmed the frozen hashes and identified:

1. NumPy's accepted-header limit is checked after its declared header body is read;
   passing max_header_size alone does not bound hostile header acquisition.
   Require a bounded length preflight and the same open file identity through
   header parsing/mapping, plus actual read-count tests.
2. Failed Futures/retained exception tracebacks can keep a retired generation or
   failed loader buffers alive after active-call accounting is released. Release
   owned large locals/unwound failure references without changing cancellation;
   add retained-exception/Future weak-reference tests across close/reload.
3. An unpaired surrogate in organism_filter passes preflight and fails UTF-8
   hashing after scoring. Reject malformed UTF-8 caller strings before scientific
   execution with the fixed input error.
4. The pre-allocation budget test raised AssertionError from np.load, which the
   loader converted into the expected source error. Assert a recorded attempted-
   access list is empty outside the raises context; normalized errors must not
   disguise a forbidden operation.

Parent authorized bounded fixes within the same3-file allowlist and the existing
acquisition/lifecycle/input contracts. No resource limit, deadline or scientific
expectation is relaxed. The first555-pass run is not treated as review approval.

Implementer follow-up (same runner): H is the receipt module's
test_npy_declared_header_cap_precedes_body_read,
test_npy_header_and_mapping_share_open_identity,
test_failed_scoring_future_releases_retired_arrays,
test_failed_acquisition_exception_releases_owned_arrays,
test_unpaired_surrogate_rejected_before_scoring, and
test_budget_guard_detects_normalized_boundary_violation nodes. I is H plus
test_logical_budget_rejected_before_fingerprint_open. J is
test_failed_source_accessor_future_does_not_pin_retired_generation.

| Run | Args | Result | Seconds | Exit |
|---|---|---|---:|---:|
| 21 | H | 22 failed | 3.59 | 1 |
| 22 | I | 23 passed | 2.93 | 0 |
| 23 | J | 2 failed | 1.65 | 1 |
| 24 | B | 176 passed | 15.34 | 0 |
| 25 | B | 188 passed | 12.24 | 0 |
| 26 | C | 125 passed | 2.34 | 0 |
| 27 | E | 591 passed,2 subtests passed | 32.07 | 0 |

Run21 reproduced3 reads requesting20,000-byte headers,6 replaced-path identity
failures,6 retained-Future/exception live weakrefs,6 malformed UTF-8 calls entering
scoring and1 false-GREEN budget oracle. Run23 reproduced2 further retired-source
references in capture/validation accessor failures. Fixes use bounded header
preflight plus the same read-only fd for NumPy parsing/mapping, clear unwound
failure frames and owning large locals, validate caller UTF-8 before scoring,
and assert attempted I/O outside exception contexts. Normal cancellation types
and actual traceback locations remain intact. All27 run summaries had no reported
skips/pytest warnings; RDKit diagnostics still appear in RED captures.

The second frozen snapshot (owned AA78AFF..., predictor032F4D..., tests59EA3B...)
entered SPEC rereview, but parent spotted a remaining pre-admission prediction
path outside the failure-cleanup guard. Worker is reproducing retained failed
Future ownership on path drift before active-count acquisition. This is the same
resource contract, not permission to relax it; independent approval is pending.

K expands to receipt module::test_predict_pre_admission_path_drift_future_releases_source
and receipt module::test_pre_admission_failure_never_releases_other_calls_active_slot.

| Run | Args | Result | Seconds | Exit |
|---|---|---|---:|---:|
| 28 | K | 2 failed: retired source weakrefs remained alive | 2.66 | 1 |
| 29 | K | 2 passed | 1.15 | 0 |
| 30 | B | 190 passed | 11.76 | 0 |
| 31 | E | 593 passed,2 subtests passed | 34.21 | 0 |

Prediction admission now lies inside the failure-cleanup boundary. An explicit
owns_active flag prevents a rejected caller from decrementing another operation's
counter; reload remains busy until the real owner settles. No skips or pytest
warnings were reported. Frozen implementation for final review:

- owned_source.py: AA78AFF79457F62FD697E001B86FE71FDBF4451F940556816FF5D5101DFFFC90
- predictor.py: 45C1A434B5B29C12EE1F9B3912C5E233327CB3BFDE1A0A9028D175242E395A8E
- receipt tests: 052E86C718B620D5FCB5735DD298BE3CF9C2B3E860FDFF88C579BB5F0E6997BD

Parent confirmed all3 hashes and git diff --check. Independent SPEC rereview is
still pending here; test passes alone are not review approval or live acceptance.

Final SOURCE SPEC rereview approves the above three-file hashes. All four P2s,
numeric identity checks and the pre-admission retention residual are closed by
source/test inspection; the reviewer did not rerun tests. Parent recompiled3
changed Python files in memory without imports successfully. Independent QUALITY
and its six-module rerun are now in progress; no publication or deployment.

### Final independent QUALITY gate

A fresh reviewer approved the frozen three-file implementation after reading
the full contract and all31 recorded runs. Independent run32 used exact E:
593 passed,2 subtests passed in36.72s, exit0 (ORDINARY_PYTEST_EXIT=0), no skips
or warnings reported. The process was polled to completion, not duplicated.
All3 source/test hashes and the approved runner hash were confirmed unchanged
before and after the run. git diff --check passed. No outstanding SPEC/QUALITY
findings remain for this slice. Parent final checks/explicit local commit follow;
no tool permissions, live model, original dirty checkout or deployment changes.

Tasks1–4 are complete for the scoped producer. The legacy path is not newly
certified. Next consumer integration must retain the raw closed13-field records
while binding any normalized target_identifier/assay additions; do not silently
rehash a changed output and claim it is the original scientific proof. Actual
tool/adapter/Session binding, B admission/reuse/resume/finish and P8 remain pending.
