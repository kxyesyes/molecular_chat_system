# RAG owned generation and same-call receipt (R2)

Date: 2026-09-26. Based on R1 checkpoint25c6a51 and follow-up1d5ff04.
Status: selected under the user's delegated recommended choices; independent
written SPEC/PLAN review approved after closing a P2 false-GREEN test-oracle gap.
Parent releases scoped TDD after this documentation checkpoint. This component
does not complete B or P8.

## Scope and alternatives

Retain one RAG service, the existing FAISS builder/filtering/normalization,
manifest schema and atomic index-pair persistence. Add private source ownership
and a strict sync producer entry. Public legacy lists remain compatibility APIs,
not verified B evidence. Modifying only post-call tool metadata cannot establish
loaded-data identity; a generic dataset/resource manager is unnecessary.

Production allowlist: `src/rag/service.py`, and `src/rag/retrieval.py` only if
required to reuse R1. New tests: `tests/test_rag_owned_generation.py`. Existing
tests stay unchanged unless a precise compatibility conflict is reviewed first.
Do not change index.py, scientific formulas, tool/Session/Web behavior, manifest
schema, real assets, dependency versions, timeouts or scientific assertions.

## Owned initialization and a single candidate builder

Only actual `initialize()` may publish a strict generation. Capture effective
rag configuration (source path, vector store path, model, endpoint), start a
new monotonically increasing service epoch and invalidate the prior generation.
Read the source once into private bytes; hash those bytes and parse those same
bytes through BytesIO. No later path hash can substitute for this loaded digest.
Keep the private dataframe inaccessible through public compatibility attributes.

Load a candidate using existing load_manifest/immutable_index_snapshot/read_index
and validate_manifest. Additionally require its source digest to equal the
loaded-byte digest; retain existing current-path freshness checks. The snapshot
context must clean up on every exit. Valid zero-vector indexes may be loaded.
If the parsed-byte identity no longer matches the source during initialization,
reject before any embedding/rebuild with index_status exactly
`incompatible: loaded source changed`. Never hide this coherence failure behind
a transport error or report a rebuilt index from the stale frame.

Factor one service-local candidate builder from the existing algorithm. Inputs
are explicit frame/source descriptor/embedding callback. It returns an index and
manifest candidate; it does not certify or publish a strict generation. Owned
initialization supplies its captured source and embedding request identity;
legacy helpers use their existing public dataframe/get_embedding injection.
Retain vector filtering, source-position mapping and FAISS normalization. All
failed embeddings remain unavailable, never valid_empty. Existing incompatible
index rebuild behavior may continue only for a coherent unchanged source.

Before saving/publishing, recheck epoch, configuration and source freshness.
Use existing atomic_save_index_pair unchanged for actual builds. Serialize the
service-owned commit section so a stale builder cannot overwrite a newer
service commit. No lock is held across an await. The synchronous disk commit is
not promised to have a finite wall-clock bound or cross-process transactionality.
If configuration changes during persistence, do not publish a strict generation.

A generation owns private frame, index, copied manifest/mapping, loaded digest,
actual index digest, request configuration, unique generation ID and epoch.
Public frame/index/manifest/mapping are independent copies (clone the FAISS
index; copy object cells as well as dataframe storage). Frozen dataclass alone
does not make a list or dataframe immutable. Neither returning records nor
mutating public aliases may modify the private generation.

Direct legacy _load_or_create_index/_create_index revoke strict certification;
they may continue to populate public legacy state. Manually assigned public
objects or is_initialized=True never constitute strict initialization. Share
candidate construction and loading helpers, not a second FAISS algorithm.
Revocation alone does not protect a later initialize from an incorrectly tagged
persisted index. Default legacy builds therefore also capture one transport
client/model/endpoint for the whole operation and verify source/configuration
freshness before saving. Preserve explicit get_embedding injections and legitimate
public overrides already present at operation start; compare those captured
values plus the captured effective configuration, not constructor defaults.
On drift, preserve the prior disk pair. Default build client ownership is per
operation, not per source row.

## Configuration and lifecycle

Use a service-local thread-safe lock for epoch/private publication and capture.
Queries capture one generation and actual model/endpoint before embedding. Check
effective config plus published model/endpoint/source fields before use, after
embedding, and immediately before returning a receipt. A mismatch invalidates
strict readiness and advances epoch; resetting fields later cannot resurrect
that invalidated generation. Public dataframe/index/manifest in-place changes
are isolated rather than treated as authenticated source updates.
During builds, per-row checks are cheap epoch/configuration/closed checks. Full
content hashing remains at capture/load and commit/publication boundaries, and
at strict query boundaries, not twice per row. Count full source reads in tests
across both service and index-module references: stable-build count must not
grow with row count. Private parsed bytes remain the source identity; a source
change during embedding must still prevent persistence/publication.

Initialize begins a new epoch; a failed or cancelled older initializer cannot
clear/publish over a newer initializer. `close()` is idempotent, marks the service
closed, invalidates its generation and blocks new strict calls/initialization.
It does not claim to terminate an already running transport; in-flight calls
retain their client ownership, finish cleanup and cannot return a valid receipt.
Successful legacy list calls do not establish strict readiness.

Do not retain a newly owned async client on the service. One build/query operation
owns its temporary AsyncClient; sync requests use the existing context-managed
Client. If embedding_client is explicitly injected, treat it as borrowed and
never close it. Capture the client and model/endpoint for the operation; do not
reread mutable fields for each build row. Existing public get_embedding retains
its [] error behavior and borrowed-client compatibility. A private captured
transport helper is shared with strict operations.

Owned async cleanup completes even under repeated cancellation, then propagates
cancellation; keep the cleanup task referenced and awaited, without an orphan
background task. No new retry loop for model requests. Close/reload invalidation
is distinct from client drain and does not promise immediate transport abort.
Broader app shutdown/drain integration is not included in this source component.

## Integration review amendment: legacy frame/source coherence

The clean-main integration review found a pre-capture gap in the legacy builder:
it can retain CSV A's public dataframe while hashing replacement CSV B before
the operation begins. Later freshness checks only see stable B, so an owned
initialize could accept the wrongly tagged persisted index. Source tracing is
not yet an executed reproduction; the integration plan requires RED first.

Selected under delegated recommended choices: detach the captured legacy frame
and compare it with a dataframe parsed from the same byte snapshot used for its
source digest, before loading/building/persisting. Compare row positions rather
than arbitrary pandas index labels; preserve ordered columns, values and types.
Reject an incoherent legacy frame before transport and leave the prior disk pair
unchanged. Keep existing epoch/config/source freshness checks. A later normal
initialize may rebuild coherently from B; it must not reuse A's vectors as B's.
Captured-frame detachment also prevents public in-place mutation during awaits
from changing the builder's later rows. Only this bounded legacy boundary is
amended; default/injected transports and coherent public configuration overrides
remain supported, without new schemas or another embedding algorithm.
This deliberately tightens legacy behavior: equal-looking tables with different
dtypes are not accepted as the same parsed source; matching missing values are
equal. Apply the check to both `_create_index` and `_load_or_create_index`,
including injected callback paths. Do not sort/coerce input to make it match.
This prevents newly poisoned pairs; it does not retrospectively authenticate
already persisted historical indexes.

Rejected alternatives: silently replacing the requested frame changes caller
intent; marking every legacy index unverified forces otherwise valid historical
builds to be rebuilt and changes the existing builder contract. Neither is
required when the captured frame and actual source can be checked directly.

## Strict producer entry and receipt

Add `search_similar_molecules_sync_with_receipt(query: str, k: int = 2)`.
Validate query as a nonempty string, require an owned ready generation, capture
request identity, execute one embedding request, then one R1 outcome search on
the captured private objects. Query/model/source change or embedding/backend/
validation/projection errors propagate as failure, not an empty success. No
rebuild, download or automatic second search is allowed inside a query.

Return a detached plain dictionary with exactly records and receipt. Receipt:

| Field | Meaning |
|---|---|
| schema_version | literal '1' |
| validation_revision | literal 'rag-owned-generation-v1' |
| invocation_id / generation_id | unique operation / owned generation identifiers |
| input_sha256 | SHA256 of the exact UTF-8 query supplied to transport |
| source_path / source_sha256 | captured source path and loaded-byte digest |
| source_row_count | actual captured dataframe length |
| index_sha256 | hash of the actual loaded snapshot or persisted candidate |
| row_mapping_sha256 | hash of the captured mapping serialized as compact JSON |
| vector_dimension / vector_count | actual captured FAISS shape |
| manifest_schema_version / builder_version | captured manifest revisions |
| embedding_model | actual query model; equal to validated manifest model |
| embedding_endpoint_sha256 | hash of exact endpoint used, never raw URL credentials |
| embedding_weights_verified | false: no remote weight identity is established |
| index_embedding_endpoint_sha256 | null: old manifest does not prove this history |
| diagnostics | exact R1 diagnostic dictionary, including invalid_discard |
| result_sha256 | hash of returned records using canonical JSON below |

Canonical JSON is ensure_ascii=False, sort_keys=True, separators=(',', ':'),
allow_nan=False encoded UTF-8; no default=str coercion. Mapping uses the same
encoding. Require native JSON values and string dictionary keys before encoding;
tuples or integer keys must not be silently converted. Non-JSON values fail
receipt creation, not forged string provenance. This compact receipt digest is
distinct from EvidenceLedger.output_digest's existing whitespace codec; later
integration must verify each using its named codec, not compare unlike hashes.
No raw query, endpoint, environment or credentials in receipt. Existing row
provenance fields remain unchanged. Malformed results may return invalid_discard
with diagnostic accepted rows, but never valid_hits/valid_empty; subsequent B
tool consumption must fail that whole retrieval obligation. R1 zero-index
diagnostics accurately say no FAISS search occurred.

The receipt proves this invocation's captured inputs and validated loaded data,
not experimentally validated chemistry, remotely pinned weights, historical
index endpoint identity, or a transactional lock on externally modified files.
Post-call service fields cannot reconstruct a missing receipt. Tool normalization,
current eligibility/reuse validation and Session seal consumption are mandatory
subsequent integration, not certified by this source-only component.

## Required evidence

First reproduce on the current actual initialize path a same-row-count CSV
replacement after parsing but before index validation: old code can accept a
new digest with old loaded rows. Desired rejection must fail before production
edits. New strict API absence is a separate RED, not sole proof of the race.

Positive tests use real initialize, temporary CSV/manifests and actual FAISS;
replace only HTTP transport with labeled synthetic embeddings. Cover loading,
building, zero-index loading, valid row mapping, exact model/query/endpoint,
single request/search counts, same-call digests and detached outputs. Do not
construct ready generations directly as the only positive evidence.

Negative tests: source changed during load/build/query; model/endpoint/config
changes across embedding; public aliases cannot poison private data; direct
legacy helper/readiness spoof cannot certify; reload/close/overlapping initialize
and cancellation cannot publish stale state; empty/all-failed builds unavailable;
invalid vectors, manifests and malformed outcomes remain failures/diagnostics.
Race tests count zero embedding calls for already-detected source replacement;
overlapping builds inspect the final persisted index/manifest as well as memory.
Prove owned clients closed once on success, transport/parse error and cancellation
(including a second cancellation during cleanup), borrowed clients stay open,
and index snapshots are removed. Use controlled synchronization, not short sleeps.

Run new tests then unchanged existing RAG core/index/service/tool tests through
the reviewed isolated launcher. Record exact RED/GREEN and failures. Independent
SPEC then QUALITY precede any local implementation checkpoint. No real service,
host data/config/credentials, deployment or B execution release in this batch.
