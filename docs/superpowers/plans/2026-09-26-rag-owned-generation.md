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
