# Focused RAG receipt-consumption integration plan

> **For agentic workers:** Use subagent-driven-development with independent SPEC then QUALITY gates. Parent owns this focused extraction and publication; no parallel test processes.

**Goal:** Preserve verified same-invocation retrieval evidence through the actual RAG tool, adapter and existing Session without replacing the Agent architecture.

**Architecture:** Reuse the reviewed R3 implementation from `2d9fbdc992ee98954ef2f186bfe448f46290338a`, its pure receipt validator, existing RAGSearchTool/RAGToolAdapter and Session ledger. Preserve PR83's newly reproduced legacy source-coherence and exact-dtype-copy repairs. No new scientific calculation, Web activation or model access.

**Tech stack:** Python, Pydantic2, actual temporary FAISS/CSV/SQLite, pytest, synthetic HTTPX transport fixtures.

## Base and scope

Preparation starts at reviewed PR83 head `950880a95ae138dcc07452d53da2f2020b1edc70` while that PR's CI runs. Do not publish this dependent branch before PR83 merges. After merge, require identical parent trees and transplant only this branch's committed delta onto the actual main squash; no edits to the original mixed checkout or accumulated B1 branch.

Pre-extraction source-parent comparison is empty for the existing tool/adapter, three modified compatibility test files, Session, generic adapter and test conftests. `src/rag/service.py` intentionally differs because of PR83's two integration repairs.

Selected production files:

- `src/rag/receipt.py` (new pure native-JSON validator and existing digest codec).
- `src/agent/tools/rag_search_tool.py` (strict producer required, honest empty/partial/errors).
- `src/agent/tooling/rag_contract.py` (verify proof before/after normalization; no repair of altered proof).
- `src/rag/service.py` (only R3 digest extraction/import and manifest source-path spelling).

Selected tests:

- `tests/agent/test_rag_receipt_consumption.py` (new, actual tool/adapter/Session).
- `tests/agent/test_rag_tool_contract.py`.
- `tests/agent/test_registration_consistency.py`.
- `tests/test_rag_index_manifest.py`.

Documents are this plan and the exact historical R3 specification. Its initial design-release statements are historical; this is integration of previously reviewed code, not a claim that old tests ran on this branch.

## Task 1: Exact extraction with repair preservation

- [ ] Copy the seven non-service source/test files from commit `2d9fbdc` through apply_patch, checking Git blobs. Copy no accumulated handoff or unrelated runtime modules.
- [ ] Apply only the service delta: remove its local `_canonical_digest` and `json` import, import `canonical_digest as _canonical_digest` from `src.rag.receipt`, and change receipt `source_path` from `str(config.source)` to captured `manifest.source_path`.
- [ ] Verify `_copy_frame` still explicitly constructs object Series with their original dtype; `_legacy_candidate` still hashes/parses same CSV bytes and rejects mismatched detached frame before load/build/transport. Keep all PR83 tests byte-identical.
- [ ] Repin only the existing isolated launcher's REPO path to this new worktree using apply_patch. Record its hash; no host environment/configuration/model/asset reads by tests.

## Task 2: Fresh integration review and regression

- [ ] Independent SOURCE/SPEC review against the approved R3 spec and this base. For any new defect, reproduce RED before minimal repair; preserve failures and do not weaken contracts, assertions or deadlines.
- [ ] After explicit sole-slot handover, execute the actual-tool focused positives and the ten-module union below. Existing source TDD is historical evidence, not a reason to fabricate a new RED for unchanged integration.
- [ ] Independent fresh QUALITY reviewer checks the frozen scope and reruns the same union; report exact counts, skips/warnings, durations and terminal handles.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_receipt_consumption.py::test_actual_tool_refuses_list_only_service tests/agent/test_rag_receipt_consumption.py::test_actual_empty_tool_retains_receipt
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_receipt_consumption.py tests/test_rag_owned_generation.py tests/test_rag_retrieval_outcome.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py tests/agent/test_registration_consistency.py tests/agent/test_dynamic_run_session.py tests/agent/test_decision_inputs.py tests/agent/test_decision_spec_findings.py
```

Historical R3 source evidence: initial two behavioral RED nodes failed because list-only services were accepted and actual empty retrieval lost its receipt. Corrected source independent ten-module union was1068 passed/0 skipped/7 warnings/133.91s. PR83 added14 preserved tests; this is not a prediction or a passing result for the new union.

## Task 3: Focused publication

- [ ] In-memory compile the eight Python files, diff-check, original-worktree preservation check, exact staging and filename-only credential pattern check. Never stage scratch launchers or runtime assets.
- [ ] Require PR83 actual merged tree equals preparation base, then rebase only clean new commits onto that squash and reconfirm identical complete source tree. If base changed beyond that equality, re-review/retest affected integration instead.
- [ ] Create one draft PR to main, attach it, require latest complete CI and no unresolved review findings, then delegated squash merge and verify reviewed/merged tree equality.

## Non-goals and remaining work

No current-source eligibility activation in this slice; no B dispatch/reuse/resume, normal Web generation/ranking or final live P8 acceptance. Pure receipt validation is not producer authentication, does not verify embedding weights and does not retroactively authenticate old index files. Missing capability or invalid proof must remain unavailable/failed/partial, not successful fallback.

The sole local test slot is currently held by the B1 continuation implementer. Preparation/review may proceed without tests; wait for explicit handover before starting pytest.
