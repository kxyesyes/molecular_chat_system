# RAG strict receipt consumption (R3)

Date:2026-09-26. Baseline:f5544c3. Status:selected under delegated recommended
choices; written review precedes code release. Not completion of B or P8.

## Selected scope

Use the real RAGSearchTool, existing RAGToolAdapter and existing Session ledger/
seal. Do not add a parallel tool or execution engine. Actual RAGSearchTool must
require the R2 strict producer method; do not fall back to an unverified list.
The alternative of a model-supplied require_receipt flag is rejected: the existing
adapter reduces input to query and would discard it, and proof is not optional
model policy. Keep service list APIs for legacy ChatHandler compatibility.

Add a small pure domain module src/rag/receipt.py for the shared compact digest
and non-projecting receipt validation. It must not import Agent/Web/FAISS or
initialize clients/assets. Move the existing service digest implementation here,
retaining its private service alias for existing tests. One codec, not an altered
EvidenceLedger codec. Pydantic strict frozen validation views may be used; the
returned records/receipt remain the original detached native JSON values, never
a projected model_dump that loses CSV extension columns.

Production scope: new receipt.py; narrow digest import and receipt source-path
alignment in service.py;
tools/rag_search_tool.py and tooling/rag_contract.py. No index/retrieval formulas,
Session runtime, schema-version migration, B admission, real assets or new model.
Existing test fixtures may migrate to strict initialized services, explicitly
retaining old list-only behavior assertions separately. See test inventory below.

## Producer-envelope validation

Expose validate_retrieval_envelope(value, *, query=None, k=None), raising a
fixed non-sensitive ValueError on malformed proof. It is validation, not producer
authentication: real producer ownership is established in R2, future current
eligibility/dispatch/reuse ownership remains part of B integration.

Require an exact native dict with records(list of native dicts) and receipt(dict),
no other root fields. Native JSON/string dictionary keys, finite numbers and
cycle rejection precede conversion; no default=str. Reject model_construct or
custom object substitution. Receipt fields are exactly R2's table, with strict
types, lowercase64-hex digests,32-hex invocation/generation IDs, nonempty source
path/model, manifest_schema_version=2, builder_version='1', schema_version='1',
validation_revision='rag-owned-generation-v1', embedding_weights_verified=false
and index_embedding_endpoint_sha256=null. Reject unsupported future versions.

Counts: source_row_count/vector_count>=0, vector_dimension>=1, requested_k>=1,
effective_k=min(requested_k,vector_count). No boolean-as-int coercion. Check exact
input SHA256 when query is supplied and requested_k equality when k is supplied.
Recompute result_sha256 with the shared compact codec over records. Do not compare
it directly with EvidenceLedger.output_digest, whose codec remains different.

Diagnostics have exactly the R1 fields and version'1'. Enforce:

- valid_empty: vector_count/effective_k=0, search=false, all counts0, no reasons,
  records[]. This means a validated empty vector index, not that all source data
  or a literature corpus is empty; source_row_count may be positive.
- valid_hits: effective_k>0, search=true, score/label/accepted counts=effective_k,
  discarded_count=0, no reasons, record count equals accepted_count.
- invalid_discard with invalid_result_shape: effective_k>0/search=true, no rows,
  accepted_count0, discarded_count=null, sole shape reason; score/label counts
  either null or nonnegative, and at least one differs from effective_k.
- invalid_discard with pair reasons: correct full shape counts, discarded_count
  in1..effective_k, accepted+discarded=effective_k; nonempty unique reason codes
  drawn from invalid_label/invalid_score/duplicate_hit. Do not invent a reason
  order that cannot be reconstructed without the raw discarded pairs. The number
  of unique pair reasons must not exceed discarded_count.

For every outcome, including invalid_discard, len(records) must equal
accepted_count. Partial diagnostics cannot claim accepted rows absent from data.

Every accepted row has finite real similarity_score (not bool), native integer
source_index in bounds and provenance with source_path/source/index hashes,
embedding_model/schema/builder exactly matching receipt. vector_label is native
integer in0..vector_count-1. Accepted labels and source rows must be unique.
Preserve arbitrary valid CSV/provenance extensions. Mapping digest identifies
the producer's captured mapping; this consumer does not falsely claim to recover
the full mapping from its hash or verify remote weights.

The receipt producer uses the captured validated manifest.source_path spelling,
the same spelling used by accepted-row provenance. R2 allows equivalent relative
configuration and absolute manifest paths; pure consumer validation must not do
filesystem resolution. This one receipt field alignment preserves the configured
path, manifest, row provenance and hashes. Test actual initialize/strict retrieval
with equivalent relative/absolute paths; do not retrofit mismatching old receipts.

## Actual tool behavior

RAGSearchTool.execute calls search_similar_molecules_sync_with_receipt exactly
once with the exact query/default k=3 (explicit legacy execute k still validated).
If strict capability is missing, return structured tool_unavailable with no call
to the list method. Local is_initialized=false is an additional restrictive gate,
including direct execute calls; true alone never certifies private generation.
Producer/transport errors return existing generic unavailable
message without exception/raw query/endpoint disclosure. Malformed envelope returns
invalid_output without exposing rejected values.

Validated valid_hits/valid_empty return success=true and the actual records.
Use receipt embedding_model, never mutable post-call service fields. Keep existing
summary/aliases; valid_empty message explicitly says the verified vector index
has no searchable vectors. Do not claim an actual FAISS search occurred there.

For validated invalid_discard return success=false/status=partial with
invalid_output and a bounded generic diagnostic message/warnings. Preserve
accepted rows as diagnostic partial data, but do not render a success/no-hit
summary or count the whole retrieval obligation as satisfied.

Store the full detached receipt in evidence[0].retrieval_receipt, alongside the
existing source/record_count/embedding_model/records provenance fields. Quality
keeps tool_name/alias/database_initialized plus retrieval_status. No arbitrary
root receipt field: compat discards extra root fields. Scientific usability of
partial stays false. Error details contain a safe reason code, not the raw result.

Registration health must not claim strict availability for a list-only service.
It remains a local capability/readiness hint, not a new model/source probe.

## Adapter and Session boundaries

Retain existing generic structural RAG adapter contracts and all metadata states.
When evidence contains retrieval_receipt, validate it against the actual data
before normalization and again after normalization. With input query available,
verify input digest too. Reject multiple/conflicting receipt entries and any
success/status contradiction. Proof-bearing outcomes use a closed matrix:

- valid_hits/valid_empty: success=true, error=null; raw status may be absent,
  null or succeeded, and normalized status must be SUCCEEDED.
- invalid_discard: success=false, explicit PARTIAL status, nonempty structured
  error with code invalid_output (AgentExecutionError after normalization).
- Every other combination is invalid, including success=true/PARTIAL. Generic
  receipt-free structural states stay unchanged. Existing ledger scientific
  usability does not itself exclude a successful partial, so reject it here.

Do not normalize malformed proof into valid empty. If security normalization
changes receipt-covered data/evidence, fail closed with invalid_output; never
recompute producer receipt, weaken redaction or discard extension fields to
rescue success. Preservation of CSV/provenance extensions applies only when
unchanged under the native JSON codec after normalization. Include a token_count
extension and actual redaction-mutation regression, not just mocked normalization.

This does not retroactively certify generic synthetic ResultTool outputs without
receipts. Actual production RAGSearchTool requires proof; future B dispatch and
finish/reuse rules must independently require that proof, not trust canonical
tool name alone. Do not activate B on the strength of adapter validation alone.

No production Session change is expected: EvidenceLedger already copies evidence
before observation_capture seals the normalized observation. Prove through actual
dynamic WorkflowRunSession, ToolRegistry and RAGSearchTool with a real initialized
temporary service that receipt survives ledger, seal, events/result persistence,
and verify_observation_integrity rejects later nested receipt/record mutation.
Do not use a hand-built final receipt as the sole positive integration test.

## Tests and compatibility migration

New tests/agent/test_rag_receipt_consumption.py covers raw envelope negatives,
truthful empty/partial, missing strict capability, mutation after provider return,
actual adapter, actual Session/ledger/seal, and no double scientific execution.
Real FAISS/temp CSV plus synthetic HTTP only; normal faiss import, no skip masking.

Existing tests/test_rag_index_manifest.py retains direct legacy list skips and
projection contracts; migrate tool positives to actual initialize/captured HTTP.
Tool invalid-manifest tests must target private-source/load admission or receipt
tampering, not imply that intentionally detached public aliases authenticate
private state. Keep explicit tests proving legacy public-state rejection too.
tests/agent/test_registration_consistency.py must initialize the actual service
instead of manually blessing helper state. tests/agent/test_rag_tool_contract.py
may update only InjectedService proof fixtures and affected actual-tool assertions;
generic ResultTool structural/metadata cases remain intact. Synthetic fixtures
are not scientific evidence. Preserve all malformed-output and lifecycle checks.

Fixture migration is explicit: test_tool_cannot_bypass_unavailable_service keeps
the false readiness gate, but public manifest-copy mutation alone is not source
revocation. test_invalid_query_vectors_do_not_produce_records supplies malformed
vectors through captured HTTP, not bypassed legacy get_embedding stubs.
test_shared_search_skips_invalid_labels_and_empty_hits retains legacy public-index
checks; strict malformed-hit checks reach actual strict search (controlled FAISS
fault or negative producer), not an uninitialized generation. Every negative
integration fixture first proves its initialized strict baseline works, then
mutates the intended boundary.

Record pre-fix RED on actual tool using list-only service (must become unavailable),
and actual initialized zero-index (must preserve receipt into ledger). Broad
regression includes all R1/R2/index/service/tool/registration modules plus dynamic
Session and observation-seal tests. Retain original failures and exact commands.
Independent SPEC then QUALITY gate local checkpoint. No real models, credentials,
production data/config, push/merge/deployment in this code batch.
