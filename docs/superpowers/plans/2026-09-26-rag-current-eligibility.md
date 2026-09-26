# RAG Current Eligibility Implementation Plan

> **For agentic workers:** Use subagent-driven-development and TDD, followed by
> independent SPEC then QUALITY reviews. Parent owns documentation and commits.

**Goal:** Reject receipts whose loaded source/configuration became stale before
the real RAG tool publishes them, with a reusable no-model freshness validator.

**Architecture:** Existing service generation lock/captured identity and actual
tool pre/post boundary. No new execution engine, no B admission/runtime change.

**Tech Stack:** Python frozen dataclass, existing native-JSON digest/validation,
temporary CSV/FAISS, HTTPX synthetic transport, pytest, existing SQLite Session.

Spec: ../specs/2026-09-26-rag-current-eligibility-design.md. Baseline2d9fbdc.

## File ownership and execution

Only src/rag/service.py, src/agent/tools/rag_search_tool.py production edits.
New tests/agent/test_rag_current_eligibility.py; narrow fixture migrations in
tests/agent/test_rag_receipt_consumption.py and tests/agent/test_rag_tool_contract.py.
No other source/test edits without parent review. Original dirty checkout untouched.

Approved runner scratch/ordinary_chat_offline_runner.py hash
F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183.
All test commands use C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B,
explicit nodes/files, one terminal process at a time. No raw pytest/imports,
host configuration/env/credentials/assets or external requests.

## Task 1: actual gap RED

- [ ] Verify clean branch baseline and runner; use the R3 actual initialized
  service fixture, real FAISS import, synthetic HTTP only. Add this behavioral
  test before production edits:

```python
def test_tool_rejects_source_closed_after_producer_return(tmp_path, monkeypatch):
    service, requests = initialized_service(tmp_path, monkeypatch)
    tool = RAGSearchTool(service)
    assert tool.execute('synthetic query')['success']
    requests.clear()
    strict = service.search_similar_molecules_sync_with_receipt
    calls = []
    def close_after(query, k=3):
        calls.append((query, k))
        envelope = strict(query, k=k)
        service.close()
        return envelope
    monkeypatch.setattr(service, 'search_similar_molecules_sync_with_receipt', close_after)
    result = tool.execute('synthetic query')
    assert result['success'] is False
    assert not result.get('evidence') and not result.get('data')
    assert calls == [('synthetic query', 3)]
    assert len(requests) == 1
```

- [ ] Run the exact node; verify RED is success=true, not fixture/network/import
  failure. Add CSV/config/reinitialize post-return variants and API absence tests
  separately. Retain counts excluding initialization/baseline queries.

## Task 2: service snapshot/validation and tool checks

- [ ] Add tests for the spec's frozen four-field RetrievalEligibility and current
  validation. Positive source fixture first; each mutation targets that source.
  Assert capture/validate do not add embedding or FAISS calls or alter disk files.
- [ ] Add service-local source projection of the existing receipt's static fields,
  reusing it in receipt production without changing old receipt values. Digest
  configuration separately from manifest path; use frozen scalar snapshot fields.
- [ ] Implement capture under existing lock, checking closed/readiness/private
  ownership plus current epoch/config/CSV. No newly read disk-index identity.
- [ ] Implement source validation with exact native query/k/snapshot types, pure
  envelope validation, fresh capture and expected/current/receipt equality.
  Do not invalidate healthy state solely on stale expected/receipt mismatch.
- [ ] Add actual-tool capability negatives and pre/post scientific-call counts;
  integrate local capture before exactly one strict call and final source check
  after formatting. Source failures empty data/evidence with generic safe error;
  malformed proof keeps invalid_output, partial remains diagnostic partial.
- [ ] Catch source compatibility errors before generic ValueError, preserving
  malformed-proof invalid_output versus source/ordinary-provider unavailable.
  Test capture, producer and postflight exception text is absent from direct
  tool and actual adapter results; do not catch BaseException cancellation.
- [ ] Migrate the two existing synthetic-fixture modules only as spec permits.
  Keep receipt-free ResultTool and existing exact-query/k/alias/metadata tests.
  Avoid always-true source validators; actual freshness evidence uses real service.

## Task 3: lifecycle, historical validation and regressions

- [ ] Cover close, observed CSV/config change and restore (sticky invalidation),
  identical-content initialize (new generation), legacy-helper revocation,
  wrong service/snapshot and malformed/bool/coercion values. A stale snapshot
  rejection must leave a newer healthy generation queryable.
- [ ] Isolate receipt mismatch with expected==current: fresh A snapshot plus a
  valid same-query/k B receipt, and rehashed/well-typed mapping or endpoint digest
  mismatch in an A receipt. Pure envelope validation first passes; service source
  validation must reject without extra scientific calls/envelope mutation and
  without revoking healthy A. This is separate from stale-snapshot negatives.
- [ ] Preserve R2 disk-index/manifest replacement/deletion and detached public
  index/frame/manifest behavior; test relative config plus absolute manifest.
- [ ] Exercise controlled concurrent reload after capture and before acceptance;
  per-call snapshots cannot leak between calls. No timing-only sleep assertions.
- [ ] Test actual registry/adapter/Session post-return drift failure and no usable
  ledger evidence. For stable result use existing seal positive control, then
  mutate source and verify seal remains valid but current-source validation fails
  without a second embedding/search or alteration of historical receipt.
- [ ] Test final formatting callback source mutation is caught by postflight.
- [ ] Run new module and eleven-module union below. Retain every failure cause,
  fix intended boundary only, freeze hashes and return exact results for reviews.
  No worker stage/commit/push. Parent commits after independent approval.

## Exact commands

From D:/MedChat/molecular_chat_system_worktrees/dynamic-bindings-b1:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_current_eligibility.py::test_tool_rejects_source_closed_after_producer_return
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_current_eligibility.py
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_current_eligibility.py tests/agent/test_rag_receipt_consumption.py tests/test_rag_owned_generation.py tests/test_rag_retrieval_outcome.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py tests/agent/test_registration_consistency.py tests/agent/test_dynamic_run_session.py tests/agent/test_decision_inputs.py tests/agent/test_decision_spec_findings.py
```

First node must be behavioral RED then GREEN. Union passes without weakened
scientific assertions/skips/timeouts. Parent compiles changed Python source in
memory (-I -S -B; no imports/bytecode), checks diff and unchanged original tree.
This is offline source eligibility, not current live model or B reuse acceptance.

## Written review gate

Cicero independently approved the written design/plan on2026-09-26 at2d9fbdc,
after two P2 amendments: isolated receipt-to-current mismatch coverage, and
safe exception classification/catch ordering. No implementation or tests were
performed during that review. Parent releases this five-file TDD scope only.

## Implementation/review chronology

Initial five-file worker freeze againste184f8f implemented Tasks1–3. All tests
used the exact approved runner prefix and serial terminal processes; no skips
or timeouts. These are retained intermediate results, not final approval:

| Order | Scope | Passed | Failed | Seconds |
|---|---|---:|---:|---:|
| 1 | Initial close-after-producer node | 0 | 1 | 2.68 |
| 2 | New module before production edits | 6 | 64 | 7.14 |
| 3 | Initial node after implementation | 1 | 0 | 2.92 |
| 4 | New module | 61 | 9 | 9.77 |
| 5 | New module after fixture corrections | 70 | 0 | 7.74 |
| 6 | Expanded lifecycle/Session module | 87 | 0 | 9.06 |
| 7 | Eleven-module union above | 1155 | 0 | 143.08 |
| 8 | Constructor-bypassed/custom snapshot tests RED | 1 | 1 | 2.86 |
| 9 | Same snapshot tests GREEN | 2 | 0 | 2.68 |
| 10 | New module | 93 | 0 | 15.33 |
| 11 | Eleven-module union | 1161 | 0 | 157.24 |
| 12 | Actual formatting mutation, direct and adapter RED | 0 | 2 | 3.01 |
| 13 | Formatting mutation, error classification and cancellation GREEN | 23 | 0 | 3.80 |
| 14 | Eleven-module union after SPEC correction | 1163 | 0 | 149.17 |
| 15 | Independent QUALITY eleven-module union | 1163 | 0 | 158.86 |

Initial behavioral RED was actual tool success=True after real strict producer
returned its intact receipt and closed the source, with successful initialization
and a baseline tool call before mutation. Expanded RED includes missing APIs and
a serialization-fixture error; it does not replace the behavioral RED. Run4's
nine failures used an incorrect ToolResult serialization method, corrected to
existing to_legacy_dict(). Run8 found AttributeError on an uninitialized snapshot;
it now produces the specified fixed safe ValueError. Failed commands exit1,
passing commands exit0. New module has3 SWIG deprecation warnings; union has7
SWIG/FastAPI deprecation warnings.

Initial independent SOURCE SPEC found one P2 despite green tests: final tool
postflight mapped every exception to unavailable. Formatting receives records
aliased by the envelope, so actual post-format record mutation makes pure proof
validation raise ValueError while source remains healthy. That must classify as
invalid_output, with RAGIndexCompatibilityError caught first and unexpected
ordinary failures still unavailable. Parent released actual-service/direct and
adapter REDs plus minimal catch ordering correction, not a spec relaxation.
Run12 reproduced this classification error through the actual service and both
direct/adapter paths: tool_unavailable instead of invalid_output. The minimal
ordered catches fixed it; run13 and run14 passed with no assertion relaxation.
Independent SOURCE SPEC rereview APPROVED the final five-file freeze, with no
remaining findings. Independent QUALITY also APPROVED with no actionable findings
and repeated the exact eleven-module union in run15: exit0, zero skipped,
7 warnings (3 SWIG and4 FastAPI on_event deprecations). Reviewer verified all five
source/test hashes and the isolated-runner hash before and after its run; no edits.
Both reviews cover offline current-source eligibility only. Tasks1–3 are complete;
the earlier checkbox lists retain the implementation sequence, not pending gates.

Parent compiled both the initial and corrected five-file freezes in memory with
Python -I -S -B, no imports/bytecode; git diff --check passed. Corrected freeze:

| File | SHA256 |
|---|---|
| src/rag/service.py | 2AD99CE60678231C55E563BEB8EAFF47FA6391ACE06FBE0959EB7B3C7503EDBD |
| src/agent/tools/rag_search_tool.py | 4E96B0BC5BC9BD48CFC021DAA3D1F2907996B84B25ED96665FFCBD57664C37F0 |
| tests/agent/test_rag_current_eligibility.py | 17F80F38237DA4B9E46DB5DAFA9AF2506417C89F371A52FBC19235F18F7953D2 |
| tests/agent/test_rag_receipt_consumption.py | F23B3155C3C98AF4877291D32BA125EE57EF2EAAD05B4B12C0B24A6993693ADD |
| tests/agent/test_rag_tool_contract.py | 6E6573520080CBC34DA4B4F49C3C9F105A40B7F2AC260C313981C523C54474C5 |

No full repository or real-model
acceptance is claimed by these offline runs.
