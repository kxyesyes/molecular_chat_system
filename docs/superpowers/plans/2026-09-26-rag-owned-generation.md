# RAG Owned Generation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development task-by-task, with SPEC then QUALITY.

**Goal:** Bind a strict retrieval result to the actual loaded CSV/index generation
and actual query embedding request, preserving normal positive retrieval.

**Architecture:** One private generation in the existing service; one shared
candidate builder; captured operation-owned transport; R1 diagnostics plus a
same-call receipt. Public legacy interfaces remain unverified compatibility paths.

**Tech Stack:** Python, pandas/NumPy, FAISS, HTTPX, pytest, existing manifest code.

Design: [owned-generation specification](../specs/2026-09-26-rag-owned-generation-design.md).
Starting code:25c6a51; planning baseline1d5ff04. Written review precedes release.

Written review: Goodall approved the revised spec/plan with no remaining P1/P2.
Initial P2 was a potentially false-GREEN race test: readiness=false alone could
hide a failed unintended rebuild. Counted successful transport, zero-request and
exact coherence-status assertions now close that gap; original review retained
here. Parent releases the following scoped TDD after committing these documents.
No further user selection is needed under delegated recommended choices.

## File responsibilities and execution boundary

- Modify src/rag/service.py: capture/publish private generations, shared candidate
  load/build, captured transport and strict receipt method; legacy entry methods
  remain thin compatible callers of shared scientific logic.
- Modify src/rag/retrieval.py only if shared R1 use requires it; prefer no change.
- Create tests/test_rag_owned_generation.py: actual initialize/race/lifecycle and
  receipt evidence, synthetic assets and HTTP transport only.
- Parent owns this plan and the spec/ledger. Worker freezes code/tests without
  staging/commit; parent checkpoints after independent reviews. No index.py,
  Web/tool/Session change, new manifest, host assets or service calls.

Only run tests via scratch/ordinary_chat_offline_runner.py SHA256
F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183.
One process at a time, poll the same session until terminal. The launcher clears
host configuration, uses temporary synthetic assets and denies external sockets.
Do not replace it, call raw pytest, or relax old assertions/timeouts.

## Task 1 — Reproduce loaded-row/digest mismatch and absent strict API

- [ ] Read current initialize/load/build/retrieval and the exact design. Verify
  branch/status and runner hash. Read only code/tests/docs, not real config/assets.
- [ ] Create the new module with normal faiss import (no importorskip). A small
  actual load fixture can be built as follows:

```python
import asyncio
from dataclasses import replace
from pathlib import Path

import faiss
import httpx
import numpy as np
import pandas as pd

from src.rag.index import (
    CURRENT_SCHEMA_VERSION, RAGIndexManifest, atomic_save_index_pair, file_sha256,
)
from src.rag.service import RAGSystem

def make_loaded_source(tmp_path, *, vectors=True):
    source = tmp_path / 'molecules.csv'
    source.write_text('SMILES\nCCO\nCCC\n', encoding='utf-8')
    store = tmp_path / 'vectors'
    index = faiss.IndexFlatIP(2)
    if vectors:
        index.add(np.asarray([[1., 0.], [0., 1.]], dtype=np.float32))
    manifest = RAGIndexManifest(
        schema_version=CURRENT_SCHEMA_VERSION, source_path=str(source),
        source_sha256=file_sha256(source), index_sha256='',
        embedding_model='synthetic-model', vector_dimension=2,
        vector_count=int(index.ntotal), row_mapping=[0, 1] if vectors else [],
        created_at='2026-09-26T00:00:00+00:00',
    )
    manifest = atomic_save_index_pair(index, Path(f'{store}.index'), manifest,
                                      faiss_module=faiss)
    rag = RAGSystem({'rag': {
        'csv_path': str(source), 'vector_store_path': str(store),
        'embedding_model': 'synthetic-model',
        'embedding_endpoint': 'http://synthetic.invalid/api/embeddings',
    }})
    return rag, source, store, index, manifest

def test_loaded_rows_must_match_manifest_bytes(tmp_path, monkeypatch):
    rag, source, store, index, manifest = make_loaded_source(tmp_path)
    requests = []
    def embed(request):
        requests.append(request)
        return httpx.Response(200, json={'embedding': [1., 0.]})
    client_type = httpx.AsyncClient
    monkeypatch.setattr(httpx, 'AsyncClient', lambda **kwargs: client_type(
        transport=httpx.MockTransport(embed), **kwargs))
    read_csv = pd.read_csv
    def replace_after_parse(*args, **kwargs):
        frame = read_csv(*args, **kwargs)
        source.write_text('SMILES\nCCN\nCCCC\n', encoding='utf-8')
        atomic_save_index_pair(
            index, Path(f'{store}.index'),
            replace(manifest, source_sha256=file_sha256(source)),
            faiss_module=faiss,
        )
        return frame
    monkeypatch.setattr(pd, 'read_csv', replace_after_parse)
    async def run():
        try:
            await rag.initialize()
        finally:
            # The old initialize retains its owned client; clean up RED as well.
            client = getattr(rag, 'embedding_client', None)
            if client is not None:
                await client.aclose()
    asyncio.run(run())
    assert not rag.is_initialized, 'old loaded rows must not acquire new CSV digest'
    assert requests == [], 'coherence failure must precede embedding/rebuild'
    assert rag.index_status == 'incompatible: loaded source changed'

def test_strict_retrieval_api_exists():
    assert callable(getattr(RAGSystem, 'search_similar_molecules_sync_with_receipt', None))
```

- [ ] First run those two exact nodes. Expected RED: first sees old service ready
  with wrong loaded bytes, second absent API. No real embedding is needed for the
  compatible old load. The counted synthetic transport prevents false GREEN from
  a denied rebuild connection; assert the explicit source-coherence status too.
- [ ] Record actual failure reasons/counts. If fixture/import errors occur, fix
  them before implementing. Do not report API absence alone as race reproduction.

## Task 2 — Own initialized source generations without changing the algorithm

- [ ] Add tests for actual successful initialize/load and actual create (index
  absent), with both HTTPX Client and AsyncClient transport factories returning
  labeled synthetic [1,0]/[0,1] vectors. Verify actual source rows/mapping and
  persisted manifest. Keep factories' original client classes and count closure.
- [ ] Add race tests using controlled callbacks/events: source swap during build;
  model/endpoint change while embedding; older init completes after newer init;
  cancelled initializer cannot clear newer state; close prevents publication.
  Inspect the persisted index/manifest pair, not only memory/readiness, after
  overlapping builds to prove a stale builder did not replace the newer files.
- [ ] Add real zero-index load and all-failed embedding build tests; these must
  distinguish valid_empty from unavailable. Watch desired tests fail.
- [ ] Implement service-local captured configuration/generation value objects,
  private byte hash+parse and one shared load/build candidate core. Owned initialize
  only publishes a validated candidate at matching epoch/config/source; separate
  public copies preserve old attribute injection behavior. Direct legacy helper
  calls invalidate strict generation. Keep legacy status/error behavior.

Essential guard shape (names may follow the existing service style):

```python
with self._generation_lock:
    if self._closed or epoch != self._generation_epoch:
        raise RAGIndexCompatibilityError('RAG generation changed')
    # Compare captured effective config and current publication fields here.
    # Actual save/publication is serialized; no await inside this section.
```

- [ ] Use one builder callback for row embeddings; do not duplicate normalization,
  filtering, mapping or atomic persistence. Re-run affected new tests, then old
  index-manifest tests before proceeding. Report any old-contract conflict.

## Task 3 — Strict same-call outcome and receipt

- [ ] Write positives calling the actual new service method after initialize;
  compare records/diagnostics and input/result/mapping hashes with independently
  calculated expected hashes. Assert one HTTP request and one search (zero for
  valid empty), exact captured model/endpoint/query, unknown remote weights.
- [ ] Write negatives for spoofed readiness/helper-only state, config/source
  changes inside embedding transport, unavailable vectors/manifests and malformed
  counted search outputs. Detached records/public copies must not poison later
  strict results. Test mutation in both directions and nested object cells.
- [ ] Run desired nodes RED before implementing strict service method. Implement
  capture → embedding → post-check → R1 outcome → canonical receipt → final-check.
  Use a compact service-local digest helper with no default=str:

```python
def canonical_digest(value):
    body = json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':'), allow_nan=False).encode('utf-8')
    return hashlib.sha256(body).hexdigest()
```

- [ ] Preserve exact record and R1 diagnostics fields; return only records/receipt.
  Validation/transport/serialization failures propagate, not valid_empty. No
  retry/rebuild/query regeneration; no model-generated receipt or post-call guess.
- [ ] Run full new module and existing R1/index/service/tool modules. Old list
  filter/truncation semantics remain; strict B consumption is not yet wired.

## Task 4 — Resource ownership and final freeze

- [ ] Add tests for owned async/sync client close-once success/error, borrowed
  client not closed, cancellation during embedding/build and repeated cancellation
  while cleanup awaits a controlled event. Do not use short wall-clock sleeps.
- [ ] Retain the actual cleanup task until awaited; propagate cancellation only
  after cleanup. Do not add provider retries or background orphan cleanup.
- [ ] Verify index snapshot cleanup on failure, plus close idempotency and no
  valid receipt from in-flight query after close. Add any missing RED, then fix.
- [ ] Run exact commands below. Record actual timing/warnings/skips and all failed
  runs, not just final success. Freeze changed files/hashes/diff, no commit.
- [ ] Independent SPEC then QUALITY. Parent records reviewed evidence, explicitly
  stages only approved files and commits locally. B remains unpublished until
  complete integration gates; no automatic model/deployment activation.

## Commands

From D:/MedChat/molecular_chat_system_worktrees/dynamic-bindings-b1:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_rag_owned_generation.py::test_loaded_rows_must_match_manifest_bytes tests/test_rag_owned_generation.py::test_strict_retrieval_api_exists
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_rag_owned_generation.py
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_rag_owned_generation.py tests/test_rag_retrieval_outcome.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py tests/agent/test_registration_consistency.py
```

First command should fail before implementation for the two reasons above;
final two commands must pass after fixes. These are offline source/contract tests,
not scientific accuracy, actual model, complete B or P8 evidence. Completion of
R2 still requires tool/adapter/Session integration in the full B plan afterward.

## Downstream source inspection while R2 implementation runs

Read-only inspection, not code release or executed integration evidence:

- RAGSearchTool currently calls the legacy list method and reads embedding_model
  after return. This must not be used as B source proof; R2 only supplies the
  missing producer API, and does not automatically switch the tool.
- RAGToolAdapter reduces its validated input to query alone. An arbitrary new
  require_receipt keyword in the resolver would not reach the tool through that
  existing path. Select the eventual strict binding explicitly and test actual
  adapter dispatch; do not claim a discarded flag enforces strictness.
- execute_tool_compat preserves evidence/quality but not arbitrary extra root
  envelope fields. EvidenceLedger stores evidence and selected input-binding
  metadata, not every quality field. A receipt left only at an extra root key or
  only in quality cannot establish the planned persisted ledger proof.
- WorkflowRunSession registers validated evidence before observation_capture;
  the decision callback seals the full normalized observation before persistence
  and event callbacks. Reuse that sequence, ensure the validated receipt reaches
  evidence before registration/seal, and test valid-empty as well as real rows.
- Receipt result_sha256 uses the explicitly compact R2 JSON codec; existing
  EvidenceLedger.output_digest uses default whitespace. Verify both separately,
  never equate unlike digests or change the existing ledger codec incidentally.

Future strict result/status/schema/digest/ownership validation and pre-dispatch
plus reuse source eligibility remain mandatory. These observations do not
authorize a legacy fallback in B, scientific success on malformed receipt, or a
new runtime. No tool/adapter/Session code was changed during this inspection.

## Initial R2 implementation evidence (before independent code review)

Worker freeze at2ed7af0 changes only service.py (+405/-202) and new
test_rag_owned_generation.py (863 lines). Retrieval/index and old tests unchanged.
All runs used the approved isolated launcher; no skips. Full recorded chronology:

| Run | Scope | Passed | Failed | Warnings | Seconds |
|---|---|---:|---:|---:|---:|
| 1 | Two initial exact nodes | 0 | 2 | 3 | 0.84 |
| 2 | New module | 0 | 13 | 3 | 1.12 |
| 3 | New module + index | 65 | 1 | 7 | 5.91 |
| 4 | New module | 12 | 50 | 3 | 3.12 |
| 5 | Six modules | 672 | 0 | 7 | 16.25 |
| 6 | New module | 84 | 1 | 3 | 3.00 |
| 7 | New module | 85 | 0 | 3 | 2.76 |
| 8 | New module | 87 | 10 | 3 | 3.75 |
| 9 | New module | 97 | 0 | 3 | 3.29 |
| 10 | New module | 108 | 0 | 3 | 3.50 |
| 11 | Malformed configuration nodes | 0 | 3 | 3 | 1.01 |
| 12 | New module | 111 | 0 | 3 | 3.71 |
| 13 | Six modules | 721 | 0 | 7 | 18.36 |
| 14 | Repeated cancellation nodes | 2 | 2 | 3 | 0.93 |
| 15 | New module | 113 | 0 | 3 | 3.80 |
| 16 | Six modules | 723 | 0 | 7 | 19.56 |

First RED actually accepted old parsed rows under a replacement CSV digest;
the other node lacked the strict API. Subsequent REDs covered owned client leaks,
source/configuration races, stale initializer persistence, missing strict API,
failure paths skipping invalidation, incompatible reasons overwritten, malformed
configuration not invalidated, and cleanup failure swallowing cancellation.
Warnings are three SWIG plus four FastAPI deprecations on combined runs.
Final six-module command is the one above. Last worker session93305 exited0,
with no live reviewed runner or unfinished cleanup task reported.

Initial frozen SHA256 (review may require follow-up):

- service.py: D66C88B3988D36EF1617E5FB31641982E033A92D1FE53C1C258BA0A8B844E768
- new test: B750B05AF872FF41D9D34A72009DA9D07C17ED29331B7CCDA79FC4EF99AB7BD3

SPEC then QUALITY are pending. Parent raised a source-only performance concern:
the build's per-row operation checks reread/hash the entire CSV twice per row.
That needs counted-I/O reproduction and review before approval, not a timing
threshold relaxation. No runtime reproduction of that concern is claimed yet.

### SOURCE/SPEC changes requested and narrow repair release

Goodall confirmed two P2 findings on the initial frozen files:

1. Legacy default build captured manifest config but reread transport model,
   endpoint/client per row, and checked only epoch/closed before persistence.
   Mid-build drift can persist mixed vectors labeled with old config, then be
   accepted by a later owned initialize. Required TDD uses actual default legacy
   transport with synthetic HTTP mutation and verifies prior disk pair preserved;
   capture one default operation client/config, retain explicit get_embedding
   injection and legitimate public overrides, and check source/config before save.
2. Owned build checked full source digest twice per embedding row, introducing
   2N whole-file reads and O(N * source bytes) work. Required TDD counts actual
   source hashing through service and index references for two row counts. Use
   cheap epoch/config checks per row; retain complete source checks at the
   lifecycle/commit/query boundaries and preserve source-change failure tests.

Parent releases these two scoped repairs in the same worker files, retaining the
initial failures/hashes above. Specification now makes both constraints explicit.
No old scientific assertion, timeout, formula or prior failure is removed. Worker
must freeze again after the full six-module command; independent SPEC re-review
and QUALITY still gate the implementation checkpoint.

### P2 repair evidence and second freeze

Worker reproduced both findings through the approved runner. Exact new nodes:
test_legacy_default_build_drift_preserves_previous_pair,
test_legacy_default_build_captures_one_client_and_existing_public_overrides,
test_owned_build_full_source_hash_count_is_row_independent. Initial RED:
1 passed/11 failed/3 warnings/1.90s. Drift tests observed replacement of the old
pair, mixed model/endpoint requests and a subsequent owned initialize accepting
the incorrectly tagged pair. Two rows opened two default legacy clients.
Real full-source file_sha256 counts were10 atN=2 and24 atN=9 (both service and
index references counted); this is measured call-count evidence, not a runtime
benchmark on a real dataset.

After repair, default legacy builds own/borrow one captured operation client and
compare initial effective config separately from initial public overrides.
Request-time and cleanup-time drift reject before replacing the prior pair;
explicit callback injection remains compatible. Owned per-row checks no longer
rehash the source; both row counts now require6 full-source file checks
(service5 + index1), in addition to the initial private-byte digest. Required
load/commit/publication/query checks and old scientific assertions remain.

| Repair stage | Passed | Failed | Warnings | Seconds |
|---|---:|---:|---:|---:|
| New three-node RED | 1 | 11 | 3 | 1.90 |
| Legacy fixes + existing index module | 64 | 0 | 7 | 5.43 |
| Both fixes, new module | 125 | 0 | 3 | 4.02 |
| Pre-save drift/client-switch coverage | 136 | 0 | 3 | 4.31 |
| Six-module regression | 746 | 0 | 7 | 19.26 |

All runs have0 skips; failures exit1 and green runs exit0. Same approved runner;
last session35269 terminated. Only the same two worker files changed. Revised
SHA256 for independent SPEC re-review then QUALITY:

- service.py: D41A9082F210F2A9949665BF094BDDC900B4B9071B37807C3BE7D073F6632611
- tests: DAC01F2EEDF6CFDA28315604A18FDF6AAB5E30CBA7D4626A9A339ECE1EB67144

No final code-review approval or actual-model/full-B success is claimed yet.

### Final R2 source-component approval

The preceding pending-review state is superseded only for this source component.
Goodall's independent SOURCE/SPEC re-review approved the second freeze, explicitly
closing both P2s. Wegener's independent QUALITY review approved with no actionable
P1/P2 and repeated the exact six-module command: **746 passed, 0 failed, 0 skipped,
7 warnings, 19.82s, exit0**. The single session37804 was polled to completion.
Both source/test hashes and the ignored launcher hash matched the second freeze
before and after review. No timeout, old test or scientific assertion was changed.

Parent also compiled service.py and test_rag_owned_generation.py in memory with
MedChat Python -I -S -B:2 files passed, no imports/bytecode writes; this is not a
full compileall. git diff --check passed. Parent stages only these two worker
files, this plan/spec and the remaining-work ledger for a local checkpoint.

R2 now establishes the strict producer API and source ownership. It does NOT
switch RAGSearchTool, add B dispatch permission, integrate receipt validation
into the adapter/Session seal, or prove real embedding/scientific accuracy.
Those full-B requirements, C integration and P8 real/UI acceptance remain open.
No push/merge, production model, service activation or deployment in this batch.
