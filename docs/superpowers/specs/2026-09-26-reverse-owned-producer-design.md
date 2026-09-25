# Reverse-target owned producer and same-call receipts

Date:2026-09-26. Baseline9a3ccd3. Recommended decisions delegated by user.

## Scope and choice

Dynamic B still needs source-bound reverse observations, not receipts assembled
from mutable predictor fields after scoring. The cache arithmetic fix is a
prerequisite, not proof of ordered TSV/fingerprint correspondence or ownership.

Choose a private, RAM-backed validated generation with explicit acquisition
limits. Reject silent borrowed mmap authority. Temporary owned disk snapshots
would require a separate cleanup/storage design and are not an automatic fallback.
Use one new focused domain module src/reverse_target/owned_source.py for acquisition,
identity and pure proof checks; predictor.py retains lifecycle and shared scoring.
This is a bounded amendment to B's two-file producer allowlist, to avoid another
large inlined loader/codec in predictor.py. No builder/cache-format/new-store or
framework changes. Test only new tests/test_reverse_target_invocation_receipts.py
and the existing popcount test if its private helper seam moves; retain assertions.

Implement producer and current-source APIs together. Later tool/adapter/Session/B
integration is still required; this slice must not change their permissions.

## Acquisition and resource contract

Predictor adds initialize_strict(*, max_snapshot_bytes=512*1024**2,
timeout_seconds=120, cancelled=None), capture_prediction_source(),
close_strict(), predict_with_receipt(...), validate_prediction_source(...).
No implicit call to legacy load/get_predictor or pickle. Existing constructor,
load, predict/list, batch, pharmacophore and no-argument get_predictor stay usable.
Future strict tool can construct a lazy predictor then initialize explicitly;
no new singleton/factory is needed in this batch.

Limits are trusted server parameters, never model arguments. max_snapshot_bytes
is a positive native integer (not bool); timeout is native finite positive <=300.
cancelled is None or a callable returning a native bool. True propagates
asyncio.CancelledError; bad return type is a fixed safe input error. Deadline
checks use monotonic time before/after file operations, each copy/validation chunk,
each row fingerprint computation, scientific batches and final publication.
This is cooperative cancellation, not preemption of an individual OS/RDKit call.
Never publish after observed cancellation or expiration.
Acquisition controls are operation-local and discarded when it returns; they are
not stored on the generation. predict_with_receipt has independent keyword-only
timeout_seconds=300 and cancelled=None, validated as above and converted to one
fresh absolute deadline per call. These operational controls are excluded from
scientific input identity; a caller supplies its earlier remaining root budget.
Legacy scoring has no new deadline or cancellation policy.

TSV byte read is limited to min(32MiB, max_snapshot_bytes//8); read in chunks no
larger than1MiB and bound the actual read, not only stat size. NPY headers are
limited to16KiB. Parse and hash those same bytes (BytesIO), no second
CSV read for metadata. Required columns: molecule_chembl_id, canonical_smiles,
target_name, standard_type, standard_value, organism. Keep original row order.
Require nonblank strings except organism may be empty/unknown; standard_value
must parse as finite nonnegative float. Reject an invalid row, never silently
drop it. Empty source with those headers and correctly shaped arrays is valid.

Before owned fingerprint allocation require:
len(TSV bytes) + dataframe.memory_usage(deep=True).sum() + rows*(2*2214+32)
<=max_snapshot_bytes. The conservative array allowance includes copying/count
scratch. Also reject malformed array dimensions/types before copying. This is
logical snapshot allocation accounting, NOT a hard process-RSS guarantee;
pandas/RDKit/runtime overhead and future scoring allocations are not certified.
Do not claim real datasets fit: actual size/acquisition latency are unmeasured.

Load .npy with allow_pickle=False; acquire/close only local read-only handles.
Require uint8 binary arrays shaped (rows,2048) and (rows,166). Copy to private
C-contiguous arrays in bounded chunks (at most4096 rows); never publish aliases
through legacy df/morgan_fps/maccs_fps fields. Compare every owned row against
the actual query fingerprint implementation on its whole TSV SMILES (no parsed
name, whitespace suffix or invalid structure). Tests also compare writer's Morgan
generator path with the query implementation on varied actual RDKit molecules.
This proves computational row correspondence, not biological annotation truth.

Existing popcount files are detached, validated in full against owned rows with
the shared9a3ccd3 arithmetic checks, then canonical uint16. Missing files compute
privately and never save. Extract the existing arithmetic loop for reuse rather
than duplicate it; the legacy wrapper keeps best-effort missing-cache persistence.
Never open fingerprint_metadata.pkl, repair assets, write a cache or inspect a
production dataset. Reject mismatched/swapped TSV rows and caches before publish.

All owned arrays become read-only; private dataframe/arrays remain internal.
Return only detached descriptors/records, not the generation object. Standard
Python private-state trust applies; this is not a sandbox against arbitrary code.

## Lifecycle

Use a separate strict RLock/epoch/loading flag and active-call counter. Validated
initialize_strict first rejects if an acquisition or strict scoring call is active;
it does not invalidate an active healthy generation on this busy rejection. Once
admitted, it revokes the old generation, captures configured resolved source paths, loads outside
the lock, then publishes only if its epoch/configuration is still current.
Concurrent initialization is rejected, not duplicated or silently joined. Failed
acquisition leaves strict source unavailable; legacy _loaded/public data untouched.
close_strict increments epoch and removes publication, including during loading;
late work cannot republish. Busy ownership ends in finally. Explicit later init
may reopen. In-flight calls retain their local RAM generation until settled and
release the active-call counter in finally, even on cancellation/exception. A close
does not reset that counter; new acquisition stays busy until those calls finish,
so retired generations and new load buffers cannot accumulate across reloads.

Loaded private generation is authoritative. Replacing/deleting files alone does
not mutate it; explicit initialize adopts replacements with a new generation ID,
even for identical content. Changes to configured resolved TSV/fingerprint/count
paths revoke observed eligibility; restoring paths cannot resurrect it. No
per-query full-file hashing or automatic rebuild. This is an explicitly loaded
snapshot policy, not a promise to follow latest on-disk data.

## Shared scientific core

Keep formula, stable sort/tie order, grouping/counts, threshold, slicing, organism
filter and generated link fields unchanged on valid legacy inputs. Extract a
core taking explicit frame/arrays/popcounts/effective weights; both old predict
and new strict predict call it. No second scientific implementation. Preserve
legacy public helper signatures/default behavior. A narrow optional check hook
may be added to batch/scoring helpers for strict cooperative bounds; old callers
do not acquire new deadline/validation policy.

Factor existing weight conversion/default/clamp into one resolver; strict calls
resolve once and pass the resulting Morgan/MACCS pair to scoring and receipt.
Do not read environment again inside the combiner. Legacy helper uses that same
resolver, preserving invalid-string/default, negative/over-one and nonfinite
clamp behavior. Tests control environment only inside the approved runner.

Strict input: native nonblank whole SMILES <=8192/no whitespace, positive native
top_k1..100, native finite threshold0..1 (not bool), native bool combine_by_target,
native organism_filter string<=256. Invalid input fails before query scoring.
predict_with_receipt requires an already initialized strict generation, never
falls back to legacy data or auto-loads. Capture generation, paths and effective
weights locally; compute exactly one prediction; verify generation/path eligibility
before return. A weight change after capture may leave a correctly historical
receipt with the captured weights; current-source validation must reject it when
compared with current weights. It must never mix old scoring with new metadata.

## Proof and current eligibility

Frozen ReverseSourceSnapshot has exactly generation_id (32hex), source_sha256
(64hex) and configuration_sha256 (64hex). It is server-local state, not a model
argument, credential or authentication token. Capture requires eligible loaded
generation; configuration digest covers resolved paths and the complete captured
weight-resolution record, defined below.

Owned source descriptor is closed native JSON: revision='reverse-owned-v1',
generation_id, source_name (TSV basename, not full machine path), tsv_sha256,
row_count, morgan_sha256, maccs_sha256, morgan_popcounts_sha256,
maccs_popcounts_sha256, morgan_bits=2048, maccs_bits=166, morgan_radius=2,
rdkit_version, numpy_version, array_codec='reverse-array-v1',
row_alignment_verified=true, cache_origins mapping exact keys morgan/maccs to
verified_cache/computed. All JSON hashes use UTF-8 json.dumps with ensure_ascii=False,
sort_keys=True, separators=(',', ':'), allow_nan=False, no implicit conversion of
non-native objects. Array SHA256 framing is b'MedChat.reverse-array.v1\n' followed
by canonical JSON {dtype:'uint8' or 'uint16',shape:[...],order:'C'}, b'\n', and
C-order little-endian element bytes in bounded chunks. This hashes owned content,
not raw .npy file bytes. Source SHA256 covers this whole detached descriptor.

predict_with_receipt returns exactly records and receipt. Receipt fields:
schema_version='1', validation_revision='reverse-owned-v1', invocation_id(32hex),
source (above descriptor), source_sha256, input_sha256, configuration_sha256,
controls, weights, weight_resolution_revision='reverse-weights-v1',
weight_resolution, score_dtype ('float32' or 'float64'), result_sha256,
status ('verified_hits' or 'verified_empty'), record_count.
controls is exact threshold/top_k/combine_by_target/organism_filter; weights is
exact morgan/maccs pair with finite values0..1 and maccs==1.0-morgan.
weight_resolution is {defaulted:bool,clamped:bool}: unset/invalid-string uses0.7
and defaulted=true; otherwise preserve existing float/clamp behavior, flagging
clamped when parsed value differs (including NaN). No raw configuration is stored.
Input SHA256 covers exactly {smiles:exact_string,controls:controls}. Configuration
SHA256 covers exactly {paths:{tsv,morgan,maccs,morgan_popcounts,maccs_popcounts},
weights,weight_resolution_revision,weight_resolution}, with captured resolved
path strings in the local hash input only. Raw paths are never returned.
Result SHA256 covers the returned unnormalized record list.
score_dtype is the dtype actually used by the shared final_sims vector, not a
post hoc guess. Preserve that internal diagnostic when extracting the shared core;
legacy predict still returns only records. All JSON values are finite/native.
No-match comes from the same shared core with validated source; no failure-to-[]
conversion, last_receipt mutable field, fake pIC50 or affinity claim.

validate_prediction_source(envelope, *, smiles, threshold=0.6, top_k=10,
combine_by_target=True, organism_filter='', expected) validates exact snapshot
types, closed proof/control/source fields and hashes, record count/status/scores,
then matches expected to freshly captured current snapshot and receipt source/
weights to that snapshot. Return detached envelope without recomputing chemistry,
reloading or changing historical receipt. Stale expected/receipt must not revoke
a newer healthy generation. Tampering is malformed proof; current unavailable/
source mismatch is source unavailable, with fixed safe errors and no raw paths.
This does not authenticate a self-consistent forged receipt: actual dispatch and
Session owner/trace/seals remain required in the next consumer stage.

Record schema is exactly the13 existing keys: target_name,organism,canonical_smiles,
molecule_chembl_id,standard_type,standard_value,morgan_similarity,maccs_similarity,
final_similarity,row_index,chembl_search_url,uniprot_search_url,similar_count.
Require native bounded strings (<=8192; only organism may be empty), finite native
numeric standard_value>=0 and similarities0..1 (not bool), native integer unique
row indices0..source.row_count-1, similar_count1..source.row_count, count<=top_k.
Require descending final_similarity (ties preserve producer order but cannot be
authenticated by a hash), target uniqueness when grouped, similar_count==1 when
ungrouped, and sum(similar_count)<=source.row_count. For threshold consistency,
compare the stored final scores using the recorded score_dtype and the same NumPy
array/scalar comparison as the core. A float32 value just below the Python float
threshold may legitimately match that threshold after NumPy coercion; do not reject
valid parity or invent an arbitrary tolerance. This is validation, not rescoring.
Status is verified_hits iff nonempty, otherwise verified_empty. Pure validation
checks native acyclic JSON, depth<=8, nodes<=10000 and accumulated UTF-8 string
bytes<=1MiB before hashing/copying; reject unknown fields, bool/coercion and bounds
violations with a fixed safe ValueError. Current unavailable/mismatch instead uses
a domain ReverseSourceUnavailable subclass of ValueError. Preserve cancellation.

## TDD/release criteria

First actual behavioral RED at baseline: real temporary TSV/real RDKit arrays,
successful legacy prediction, swap TSV rows while leaving arrays unchanged;
close baseline fixture-owned mmaps, instantiate a fresh legacy predictor, call
existing predict and assert rejection (old code must fail DID NOT RAISE). Also
explicitly record that fresh result associates the query's fingerprint score with
the wrong TSV SMILES/target. Reusing a loaded baseline would not reproduce this.
Then migrate only that test's action to initialize_strict/predict_with_receipt
to test the explicitly new strict boundary with the same corruption/rejection
requirement. Preserve the original RED output and state that legacy list paths
remain compatibility-only, not newly certified. Missing-API RED alone is not a
substitute for demonstrating the old misassociation. Positive source fixtures
use the actual writer Morgan/MACCS implementation, not only predictor-generated
arrays or injected owned generations.

Cover whole-row correspondence, same-call weights and legacy parity (grouping,
ties, filters, threshold-empty), real/correct cached and privately computed counts,
no pickle/writes, publication failure/close/reload races via controlled barriers,
configuration drift sticky invalidation, disk/public-copy ownership, true empty,
bounded acquisition/deadline/cancellation, fixed errors, exact proof/inputs/types,
forged/mutated source/weights/results, and zero chemistry in source validation.
Pre/post initialization/scoring callback mutations must not publish mixed evidence.
Use actual service/core; no ready-made success receipt stand-ins for positives.

Only approved offline runner, temporary synthetic files and actual RDKit/NumPy;
no host config/keys/assets, external model/network, push/merge/deployment. Independent
written review, TDD, SPEC then QUALITY. All intermediate failures retained. Actual
tool/adapter/B admission/reuse/resume/finish and B2/C/P8 remain pending.
