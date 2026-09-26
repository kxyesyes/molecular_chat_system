# Focused RAG current-source eligibility integration

Date:2026-09-26. This is focused P7 integration of previously reviewed R4 code,
not a claim that B/C/P8 or real-model acceptance is complete. User delegated
recommended choices and subsequent qualified PR merges; independent review and
complete exact-head CI remain mandatory.

## Base and requirements

Preparation base is PR84 headf050c5ae975069f22840b78071c557964243c507, still open.
Do not publish before PR84 lands. After its merge, verify source-tree equality,
then cleanly transplant only this branch's scoped commits onto the actual squash.
Preserve PR83 source/frame coherence and dtype-copy fixes, PR84 proof-bearing
security-transform validation, Session/SQLite controls and concrete FAISS tests.
Do not copy entire old service or receipt-test files over these later repairs.

Historical source is1e9bb3456f7324c72a5ec639d78b4d4be9864369 against2d9fbdc.
The accompanying design and original implementation plan are historical reviewed
requirements/evidence. Their old local-only release language is not permission
to skip this fresh integration's review or runtime checks.

Exact production scope: src/rag/service.py and src/agent/tools/rag_search_tool.py.
Add tests/agent/test_rag_current_eligibility.py; apply only the synthetic source
contract migrations to test_rag_receipt_consumption.py and test_rag_tool_contract.py.
No changes to receipt validation, generic adapter, Session, model transport,
scientific algorithms, runtime settings or Web entry.

## Implementation and evidence steps

1. Copy the new current-eligibility test from the historical reviewed commit.
   Apply the four existing-file diffs as narrow patches, retaining all later R2
   and R3 repairs. Compare exact source delta and assert the service's original
   guarded dtype/coherence path and adapter remain unchanged.
2. Repin only the approved isolated launcher's REPO path; never run raw pytest,
   discover host credentials/configuration or use actual scientific assets/models.
   One local heavy-test process at a time; obtain explicit slot handover first.
3. Historical tests still intercept faiss.IndexFlatIP rather than the loaded
   concrete type. Before adjusting, use existing opt-in native/base-IP fixture
   to reproduce missed counted/fault searches. For no-search guards, add an
   explicit guard-reachability control after the genuine no-search assertions.
   Keep strict initialized baselines, real temporary FAISS/CSV/SQLite, existing
   counts and all scientific assertions. Change only test interception, not
   production loading or index algorithms, and retain RED/GREEN evidence.
4. Independent SOURCE/SPEC checks the approved requirements and final delta;
   any new real defect gets a behavioral RED before a minimal fix. Run focused
   actual-tool positives/negatives and the eleven-module union below, recording
   every terminal failure/pass and warnings. Historical1163-pass results are not
   fresh results for this integrated tree.
5. Fresh independent QUALITY repeats the union with frozen hashes. In-memory
   compile, diff checks, explicit staging, scoped filename-only secret scan,
   original dirty-checkout preservation and exact clean-transplant tree comparison.
6. One draft PR to main after prerequisite landing, attach it, require complete
   exact-head CI and no unresolved review findings before delegated squash merge.
   No deployment or real-model enablement.

## Exact regression

From this focused worktree, after sole-slot release:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_rag_current_eligibility.py tests/agent/test_rag_receipt_consumption.py tests/test_rag_owned_generation.py tests/test_rag_retrieval_outcome.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py tests/agent/test_registration_consistency.py tests/agent/test_dynamic_run_session.py tests/agent/test_decision_inputs.py tests/agent/test_decision_spec_findings.py
```

Capture and validation must perform no embedding, search or rebuild. Stable
partial remains diagnostic partial; malformed proof is invalid_output, changed
or unavailable source is tool_unavailable. Stale snapshots cannot revoke healthy
new generations; formatting must not bypass the final source check. A boundary
freshness check is not filesystem atomicity or remote-weight verification.

## Current state

Preparation only. No R4 implementation/test result is claimed on this branch.
Task5 worker currently owns the sole local test slot; do not start a competing run.
