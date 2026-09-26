# RAG current loaded-source eligibility (R4)

Date:2026-09-26. Code baseline2d9fbdc. Selected under the user's delegated
recommended choices; independent written review precedes code release.

## Purpose and choices

R2 owns a coherent loaded generation; R3 preserves/validates its result proof.
The actual tool still accepts an otherwise valid receipt if the service changes
after the strict producer returns but before the tool publishes its result.
Close that real boundary with pre/post source checks. Supply the same no-model
validation API for later B duplicate/resume/finish checks, but do not claim those
currently four-tool loop paths are already integrated.

Selected: two narrow service APIs plus actual-tool consumption. Rejected:
post-call metadata reconstructed into receipts (changes history), or a new cache/
source manager (unnecessary framework). Keep the actual loaded generation, not
the latest on-disk index, authoritative; this preserves R2's tested snapshot
ownership. Persisted index/manifest replacement/deletion alone does not alter
the already-owned index. Explicit initialize adopts a new generation. Current
CSV bytes and effective source/store/model/endpoint configuration remain checked.

## Service contract

Add frozen `RetrievalEligibility` in src/rag/service.py with exactly:

- generation_id:str (32 lowercase hex)
- epoch:int (native, >=0)
- configuration_sha256:str (64 lowercase hex)
- source_identity_sha256:str (64 lowercase hex)

This internal immutable value is per-call server state, not a model argument,
credential, issued-invocation certificate or new persistent schema. A copied or
constructed matching snapshot is not authenticated authority. Existing trusted
tool dispatch plus Session ownership/sealing remain necessary.

`capture_retrieval_eligibility()` requires an owned generation, service not
closed and is_initialized=true. Under the existing generation lock, perform
the existing epoch/effective-config/published config/current CSV-byte checks.
Return the detached frozen value. No embedding, FAISS search, rebuild, index
write, network, transport ownership or model probe. Public frame/index/manifest
copies remain non-authoritative. A true readiness flag alone cannot certify.

Configuration digest uses the existing compact native-JSON codec over source
configuration path spelling, vector-store configuration, model and exact endpoint
SHA256. It is separate from manifest provenance path spelling. No raw endpoint
is returned. Source identity digest covers exactly R3 receipt fields except
invocation_id, input_sha256, diagnostics and result_sha256. Thus it includes the
generation ID, manifest source path/digest/row count, index/mapping identities,
shape/revisions and captured model/endpoint identity, plus the explicit unverified
weights/history flags. A service-local helper may share this projection with
receipt creation; it must not change the R2/R3 receipt shape, version or values.

`validate_retrieval_source(envelope, *, query, k, expected)`:

1. Require exact native nonempty query string, native positive k, and a well-typed
   exact RetrievalEligibility value. No bool/int coercion, subclasses, custom
   objects, or optional None query/k bypass. Wrong inputs use a fixed safe error.
2. Reuse R3 pure envelope validation with the exact query/k, returning detached
   records/receipt. Do not rehash or repair a producer result.
3. Under the existing lock, freshly capture current eligibility, including CSV
   content hashing. Require expected==current and receipt source projection hash
   ==current.source_identity_sha256. Native snapshot types are checked first.
4. Return the validated envelope unchanged. A well-formed invalid_discard remains
   diagnostic partial; this method validates source identity, not whole retrieval
   success. Only consumers' existing valid_hits/valid_empty policy grants success.

Observed source/configuration drift revokes generation using existing R2 logic;
resetting bytes/fields afterward cannot resurrect it. Close and reinitialize
invalidate prior snapshots, even with identical loaded content. An old/wrong
expected snapshot or receipt must NOT invalidate a healthy newer generation.
No automatic retry, rebuild or scientific recomputation. Errors never expose
query, endpoint, filesystem paths, rejected objects or raw exceptions.

Boundary checks are not filesystem transactions. Changes and restoration wholly
between checks cannot be detected. Disk index replacements are adopted only on
explicit reinitialization; this API does not promise latest-index availability.
It cannot prove remote weights or authenticate an invocation against a forged
self-consistent receipt. No invocation registry/signature is added in this batch.

## Actual tool integration

RAGSearchTool requires all three strict capabilities: capture, strict retrieval,
and source validation. Missing methods/readiness fail tool_unavailable without
legacy fallback or scientific execution. registration_health only checks local
state/callability, not file freshness or model/network availability.

Capture once before the producer call. Preserve exactly one strict producer call,
R3 input/count validation and status/evidence construction. At the final return
acceptance boundary after formatting, call validate_retrieval_source with that
same snapshot/query/k and envelope. Source mismatch/unavailability returns safe
tool_unavailable with empty data/evidence; it never publishes the stale result.
Malformed pure proof still returns invalid_output; partial status and generic
receipt-free adapter behavior are unchanged. Catch ordinary capture/producer/
postflight exceptions at the tool boundary and return generic unavailable without
logging or returning raw exception text. Do not let them reach the generic adapter
handler which can stringify provider errors. RAGIndexCompatibilityError subclasses
ValueError: classify it as source-unavailable before generic malformed-proof
ValueError (invalid_output). Unexpected ordinary postflight errors are unavailable,
not fabricated malformed data or success. Do not swallow BaseException cancellation.

Keep per-call expected state local, not on a shared tool or adapter. Formatting
does not grant authority. No Session, adapter, loop, continuation or Web change.
Later B work must use this source check after full observation-seal/owner checks,
before duplicate reuse, restore CAS and final evidence consumption, binding the
original query/k. R4 tests validate that future seam without claiming admission.

## TDD and regression scope

Production allowlist: src/rag/service.py and src/agent/tools/rag_search_tool.py.
Pure receipt/index/retrieval algorithms, Agent runtime, budgets and schemas stay
unchanged. Test allowlist: new tests/agent/test_rag_current_eligibility.py;
tests/agent/test_rag_receipt_consumption.py and test_rag_tool_contract.py only
for affected synthetic fixture contracts. Preserve legacy/scientific assertions.

First behavioral RED: actual initialize/temp CSV/real FAISS/synthetic HTTP,
working baseline; wrap bound strict producer, call once, close service before
returning its untouched receipt. Actual RAGSearchTool currently reports success;
new behavior must reject it. Add CSV/config/reinitialize variants. No missing-API
failure may substitute for this behavioral proof.

Cover hit/empty/diagnostic partial positives; immutable/detached snapshots; exact
query/k and relative-config/absolute-manifest paths; no embedding/search during
capture or validation; zero producer calls on stale preflight and one on drift
after return; reload-identical content, stale expected without new-generation
revocation, cross-service mismatch, sticky observed drift, close/legacy-helper
revocation, and preserved disk/public-copy ownership. Use controlled barriers
for concurrent reload/capture rather than short sleeps. Test source mutation from
the formatting hook so postflight really occurs at the final tool boundary.

Actual registry/adapter/Session must reject post-return drift without scientifically
usable evidence. For no-recompute historical validation: untouched sealed actual
Session result passes seal+current source, then source changes; seal still intact
but fresh source validation rejects with zero additional scientific calls and
unchanged historical receipt. This is not actual decision-loop reuse acceptance.

Independently test receipt-to-current agreement while expected/current snapshots
match: service A snapshot with a valid same-query/k receipt from service B, and
current-generation receipts with well-typed mismatching row_mapping_sha256 or
embedding_endpoint_sha256. Pure R3 validation accepts these structures; source
validation must reject without more scientific calls, changing the envelope or
revoking healthy A. Test raw exception safety for capture/producer/postflight
through both direct tool and actual adapter, including the ValueError subclass.

Synthetic envelope fixtures may test contracts, not source authenticity. Do not
add always-successful eligibility stubs merely to restore green tests. Prefer
actual initialized backing services and real validation; if a deliberately
synthetic contract fixture is retained, it must check query/k and snapshot/source
identity agreement, be labeled synthetic and not stand in for actual positives.

Use only approved isolated offline runner; one process at a time. Independent
SPEC then QUALITY, retain exact failed and passing results. No models, production
assets/config/credentials, network, push/merge/deployment. Full B/C/P8 stay open.
