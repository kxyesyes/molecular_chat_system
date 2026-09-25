# RAG Receipt Consumption Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use
> superpowers:subagent-driven-development with independent SPEC then QUALITY.

**Goal:** Consume strict producer receipts through the actual tool/adapter and
prove preservation in the existing Session ledger/seal, without legacy fallback.

**Architecture:** Pure shared domain receipt validation plus minimal actual-tool
and adapter integration. Existing Session/persistence stays in place. Generic
structural adapter compatibility does not grant B evidence authority.

**Tech Stack:** Python/Pydantic, HTTPX, FAISS, pytest, existing Session/SQLite.

Spec: [R3 design](../specs/2026-09-26-rag-receipt-consumption-design.md).
Code baseline:f5544c3. No implementation release before written review.

## Files and boundaries

- Add src/rag/receipt.py: strict non-projecting envelope/receipt validation and
  shared compact native-JSON digest; no Agent/Web/FAISS/client imports.
- Modify src/rag/service.py only to import/re-export the existing digest helper
  and emit captured validated manifest.source_path in the receipt, matching row
  provenance for equivalent relative/absolute configured paths.
- Modify src/agent/tools/rag_search_tool.py: strict service call/status/evidence.
- Modify src/agent/tooling/rag_contract.py: validate supplied receipt before and
  after normalization, retaining all unrelated existing structural contracts.
- Add tests/agent/test_rag_receipt_consumption.py: consumer and real Session tests.
- Narrow migration of tests/test_rag_index_manifest.py,
  tests/agent/test_registration_consistency.py,
  tests/agent/test_rag_tool_contract.py as specified below. Do not delete old
  negative scientific/lifecycle assertions or alter timeouts/counts to pass.
- Parent owns these documents and the remaining-work ledger. No Session code,
  index algorithm, source/manifest assets, model activation or B admission change.

All execution uses reviewed ignored scratch/ordinary_chat_offline_runner.py
SHA256F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183,
MedChat Python -I -S -B, explicit test paths/node IDs, one process at a time.
No raw pytest, ad-hoc app imports, host config/env/credentials/assets or real HTTP.

## Task 1 — Actual tool RED and pure contract

- [ ] Verify branch/status/runner hash and read spec plus actual producer, tool,
  adapter and Session boundaries. Use normal faiss import in new tests.
- [ ] Add this direct-production-tool regression, without requiring a new import:

```python
def test_actual_tool_refuses_list_only_service():
    from src.agent.tools.rag_search_tool import RAGSearchTool
    class LegacyOnly:
        is_initialized = True
        vector_index = object()
        embedding_model_name = 'synthetic-only'
        def __init__(self):
            self.calls = 0
        def search_similar_molecules_sync(self, query, k=3):
            self.calls += 1
            return []
    service = LegacyOnly()
    result = RAGSearchTool(service).execute('synthetic query')
    assert result['success'] is False
    assert service.calls == 0
```

- [ ] Add actual zero-index initialize fixture using temporary CSV, real FAISS
  IndexFlatIP(2), manifest schema2/builder1 and atomic_save_index_pair. Patch only
  HTTPX transport to return a labeled synthetic embedding. Call actual tool and
  require evidence[0].retrieval_receipt with valid_empty/no-search diagnostics.
  Before edits, missing receipt is RED; don't bless a hand-built receipt instead.
- [ ] Run those exact two nodes before production edits and record real failures.
- [ ] Add receipt validator tests from the R2 real producer envelope: mutate each
  hash/version/count/bool/shape/status/reason/row bound/provenance/duplicate field,
  query or k, native JSON types, nested aliases and unsupported fields. Assert
  fixed safe ValueError, no rejected content disclosure; keep valid records and
  extensions identical. Mark generated/malformed test envelopes as synthetic.
- [ ] Include all-outcome len(records)==accepted_count and pair reason-count
  <=discarded_count regressions. Test actual producer relative/absolute path
  equivalence before changing its one receipt field; validator does no filesystem
  resolution or retrospective receipt repair.
- [ ] Implement pure validation per spec and move canonical digest unchanged.
  Keep service `_canonical_digest` alias. Run R2 digest/owned-source regressions
  alongside new validator tests, without changing their scientific assertions.

## Task 2 — Strict tool and normalized adapter proof

- [ ] Add missing-capability/no-fallback, provider failure, malformed envelope,
  valid rows, valid empty and invalid_discard tests against actual RAGSearchTool.
  Missing/disabled service stays unavailable without invoking embedding. Use
  receipt values for evidence even if a service public model field changes after
  its method returns. Verify exactly one strict producer call, no list call.
- [ ] Implement tool envelope mapping as specified: valid outcomes successful;
  invalid_discard unsuccessful partial with accepted diagnostic rows, safe error,
  no success/no-hit summary. Store proof in evidence, never extra root fields.
- [ ] Add actual adapter tamper tests before and after normalization. Supplied
  receipts must match data and input query, duplicates/conflicting entries fail,
  status contradictions fail. Generic receipt-less ResultTool structural tests
  remain unverified compatibility, not B execution/acceptance.
- [ ] Enforce closed proof status matrix: valid outcomes true/no error/succeeded
  (raw absent/null status allowed); invalid_discard false/explicit partial/nonempty
  structured invalid_output. Reject successful partial and every contradiction.
- [ ] Test actual security redaction, including token_count extension mutation:
  changed receipt-covered payload fails invalid_output. Never rehash producer
  proof, weaken redaction or remove extension fields to make it pass.
- [ ] Implement receipt-aware validation using existing adapter worker/concurrency/
  timeout/normalization, without a second invocation or new execution wrapper.
  Verify raw and normalized metadata, warnings, evidence, data and artifacts.

## Task 3 — Migrate fixtures at the actual trust boundary

- [ ] Update InjectedService in test_rag_tool_contract with explicitly synthetic
  strict envelopes; preserve exact-query/default-k, alias and unrelated generic
  ResultTool state/extension assertions. Adjust synthetic record manifest/builder
  values only where required by R2's current version; don't drop malformed cases.
- [ ] Initialize actual services in registration and root tool positives with
  correct temporary vector_store_path and HTTPX transport. Manual legacy helper
  readiness is no longer a strict proof. Retain helper/list tests themselves.
- [ ] For mutable public-manifest/index tests, retain legacy rejection/filtering
  assertions. Separately assert strict private results remain unchanged where
  public aliases are intentionally detached. Reject invalid source/load/producer
  proof at the real authoritative boundary, rather than corrupting an unrelated
  public copy and expecting the private source to follow it.
- [ ] Distinguish direct legacy list malformed-hit behavior from strict tool
  unsuccessful partial diagnostics. Use actual malformed FAISS search transport
  or controlled negative producer fixtures; all success integration remains real
  initialize+actual FAISS. Record every intentional expected-behavior change.
- [ ] Keep direct false-readiness rejection in
  test_tool_cannot_bypass_unavailable_service; true alone is not source proof.
  In test_invalid_query_vectors_do_not_produce_records inject malformed vectors
  via captured HTTP, not legacy overrides bypassed by strict retrieval. In
  test_shared_search_skips_invalid_labels_and_empty_hits separate legacy public
  index filtering from strict actual-search faults. Prove each initialized
  strict baseline succeeds before mutation; an unavailable fixture is not valid
  evidence that the intended malformed output was rejected.
- [ ] Run affected old modules; investigate any unplanned failure instead of
  relabeling a positive scientific requirement as expected rejection.

## Task 4 — Existing Session ledger/seal integration

- [ ] Build real initialized temporary RAG + actual registry and SQLiteAgentStateStore,
  AgentEventBus, WorkflowOrchestrator, WorkflowRunSession(dynamic=True). Reuse
  existing seal_observation as observation_capture, initializing its seal map as
  the real loop does. No production Session hook is added just for the test.
- [ ] Append a real rag_search WorkflowStep with query, output_key and server-style
  decision/input-binding metadata; execute it through the Session and actual
  adapter. Excluding initialization, assert one query embedding: zero FAISS
  searches for valid_empty, one search for a nonempty index. Assert receipt in
  normalized result, ledger/evidence, seal, terminal-event payload and actual
  SQLite readback, with exact trace/evidence links.
- [ ] Cover valid rows, valid_empty and diagnostic partial. Only valid outcomes
  are scientifically usable. First prove the untouched actual Session observation
  passes verify_observation_integrity; independent cases then mutate nested
  receipt or accepted records and assert rejection and removal of usable outputs.
  This proves preservation/tamper checks, not new B eligibility/reuse semantics.
- [ ] Run new module, R1/R2 and existing RAG/registration contracts, plus dynamic
  Session and decision-observation tests below. Retain intermediate REDs/results.
- [ ] Freeze changed file hashes and test ledger; no worker stage/commit/push.
  Independent SPEC then QUALITY; parent explicitly stages approved scope and
  checkpoints locally. Full B, current-source eligibility/reuse and P8 remain open.

## Exact verification commands

From D:/MedChat/molecular_chat_system_worktrees/dynamic-bindings-b1:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_receipt_consumption.py::test_actual_tool_refuses_list_only_service tests/agent/test_rag_receipt_consumption.py::test_actual_empty_tool_retains_receipt
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_receipt_consumption.py tests/test_rag_owned_generation.py tests/test_rag_retrieval_outcome.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py tests/agent/test_registration_consistency.py
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_dynamic_run_session.py tests/agent/test_decision_inputs.py tests/agent/test_decision_spec_findings.py
```

First command RED must demonstrate old list-only success and missing actual empty
receipt, not collection/import/network failure. Final commands must pass without
relaxing scientific checks or timeouts; otherwise preserve exact failed scope.

## Written review gate

Independent SPEC reviewer Boyle approved the amended spec/plan on 2026-09-26
against f5544c3. Five initial P2 issues (count consistency, path spelling, proof
status matrix, security normalization, fixture ownership) and two Session test
clarifications (empty-index execution counts, positive integrity/persistence
controls) were resolved in writing before code release. This is design approval,
not an implementation or live acceptance result.

## Implementation evidence (2026-09-26)

Worker freeze against836c59a implements all four tasks above. Independent source
SPEC and QUALITY reviews approved this eight-file freeze. Independent QUALITY
reran the complete ten-module union below; all1,068 tests passed,0 failed,0
skipped,7 deprecation warnings in133.91s, ORDINARY_PYTEST_EXIT=0/process exit0.
No Session runtime or generic receipt-free ResultTool contract was changed.
The actual empty vector-index result preserves proof; malformed retrieval is
unsuccessful partial; receipt changes across normalization fail invalid_output.
The actual temporary Session/FAISS tests check terminal events and SQLite readback,
intact observation seals before mutation, and independent receipt/record attacks.
No real model or production source was used.

All worker commands used the exact approved prefix shown above; target sets:

- A: the two exact actual-tool RED nodes in the first verification command.
- B: tests/agent/test_rag_receipt_consumption.py.
- C: B plus tests/test_rag_owned_generation.py and tests/test_rag_retrieval_outcome.py.
- D: B plus tests/test_rag_index_manifest.py, tests/agent/test_rag_tool_contract.py,
  tests/agent/test_registration_consistency.py.
- E: the complete seven-module command above.
- F: tests/agent/test_rag_receipt_consumption.py::test_direct_tool_cannot_treat_invalid_k_as_unspecified.
- G: the three dynamic Session/decision modules in the third command above.

| Order | Targets | Passed | Failed | Seconds | Exit |
|---|---|---:|---:|---:|---:|
| 1 | A | 0 | 2 | 3.56 | 1 |
| 2 | B | 0 | 143 | 21.21 | 1 |
| 3 | C | 477 | 3 | 37.62 | 1 |
| 4 | D | 535 | 19 | 35.70 | 1 |
| 5 | E | 902 | 0 | 75.56 | 0 |
| 6 | F | 5 | 1 | 2.39 | 1 |
| 7 | E | 912 | 1 | 70.99 | 1 |
| 8 | E | 914 | 0 | 74.89 | 0 |
| 9 | G | 154 | 0 | 76.72 | 0 |
| 10 | A | 2 | 0 | 2.83 | 0 |

Initial actual-tool RED was the old list-only success=true and missing actual
empty-index retrieval_receipt. Expanded RED included the missing pure module;
it is not substituted for the two behavioral REDs. First implementation exposed
three fixture issues (Pydantic base construction and two non-triggering redaction
inputs). Compatibility migration retained legacy boundary checks and fixed an
accidental interception of caller-validator exceptions. Self-review found
explicit k=None could bypass optional-k validation; its new RED is retained.
The last intermediate failure was nested fixture clients passing transport
twice, fixed using HTTP transport-level substitution rather than weakened checks.
Final E has7 SWIG/FastAPI deprecation warnings, G none, no skips. All sessions
reached terminal before the next command (no overlapping heavy tests).

Parent git diff --check passed. Parent syntax compiled all8 changed Python files
in memory with Python -I -S -B and compile(bytes,path,'exec'), no imports/bytecode;
this is not a full repository compileall or real-model acceptance claim.

| Frozen file | SHA256 |
|---|---|
| src/rag/receipt.py | B139DF03F10DB742F2F67C13EF95C099B9FCD9A05C7B6DA8AF892A60F7EAA252 |
| src/rag/service.py | 3B63ADE67AEF35892603725597A6769E12FB55FDB7C7452D251E78B0DD6904E3 |
| src/agent/tools/rag_search_tool.py | 9FA02424E8CDCC9E49EF57AAFB292DDB4A4B3808CE1A0690A4C60283ADD74FFE |
| src/agent/tooling/rag_contract.py | 59000A7ADFAB3CA013A0B76599D97CA647F7DD11F7A28B0B7775FD712121C14C |
| tests/agent/test_rag_receipt_consumption.py | 3F1CF53DE7D6A3B8DEEE068BF7ABCA587C2B03599B273036865BF0C9A0B4D7EF |
| tests/test_rag_index_manifest.py | 104F354B0A782AE0568BF71C624694951AAF217C821477B7543A46E56CD01817 |
| tests/agent/test_registration_consistency.py | 8CCE0A4A24017EA6A59C4B8AF60B02A3F9C233E26A6725D318EABC88D3C7060F |
| tests/agent/test_rag_tool_contract.py | F877984C1AEADA20EAD240124B1F927D763EFE82542172810B10CFD8FEE8F174 |

Independent verification command (same approved worktree/runner):

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_receipt_consumption.py tests/test_rag_owned_generation.py tests/test_rag_retrieval_outcome.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py tests/agent/test_registration_consistency.py tests/agent/test_dynamic_run_session.py tests/agent/test_decision_inputs.py tests/agent/test_decision_spec_findings.py
```

Quality reviewer confirmed hashes before/after, no source edits or additional
reproduction tests. All test processes terminal. This local checkpoint closes
R3 only: actual-source current eligibility, B admission/dispatch/reuse, reverse
producer ownership, B2/C and P8 remain pending. No push/merge/deployment or real
scientific-model success is claimed.
