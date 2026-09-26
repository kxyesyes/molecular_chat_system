# B1 attached-source revalidation implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Steps use checkbox syntax. Parent owns this plan and final commits; one implementation worker owns the production/test allowlist. Independent SPEC then fresh QUALITY review are mandatory.

**Goal:** Let the actual RAG and reverse tools verify a prior observation against their current attached source without rerunning scientific work; this is the necessary source boundary for B1 dispatch/reuse/finish/resume, not completion of B1 execution or Web entry.

**Architecture:** Extend the two existing tool classes with one matching method. Reuse their existing strict producer and pure receipt codecs; do not introduce a source manager or execution engine. The later binding resolver owns status, seals, provenance and tracked worker scheduling.

**Tech Stack:** Python, existing RDKit/FAISS temporary source fixtures, pytest and the approved isolated runner.

## Scope and safety

- Baseline: `66039eb` on `codex/dynamic-bindings-b1`.
- Production edits only `src/agent/tools/rag_search_tool.py` and `src/agent/tools/reverse_target_tool.py`.
- New tests only `tests/agent/test_current_source_tool_hooks.py`; reuse existing temporary fixture helpers, do not weaken prior tests.
- No edits to scientific producer/service/scoring, registry, adapters, thresholds, schemas, Web entry or user assets. No external API/model, credentials, environment discovery, asset lookup, service launch, push or merge.
- Method contract is the Live source checks section of `docs/superpowers/specs/2026-09-26-b1-decision-execution-design.md`.
- Every test uses actual temporary strict sources except explicit fault injection; an injected HTTP transport is synthetic, never real-model evidence.

## Task 1: Reproduce absence of the actual consumer boundary

- [ ] Add a focused regression with the actual initialized reverse predictor and actual tool, first execute once, then validate the returned data/evidence after forbidding all scientific/load paths. The expected projection is:

```python
expected = {
    'kind': 'reverse',
    'generation_id': receipt['source']['generation_id'],
    'source_sha256': receipt['source_sha256'],
    'configuration_sha256': receipt['configuration_sha256'],
}
assert tool.validate_current_observation(
    result['data'], result['evidence'], input_data='CCO') == expected
assert tool.validate_current_observation(
    result['data'], result['evidence'], input_data='CCO',
    expected_source=expected) == expected
```

Use `make_writer_database` from `tests/test_reverse_target_invocation_receipts.py`, initialize_strict, close_strict in fixture finalization. Block `_get_predictor`, initialize_strict, load, predict and predict_with_receipt only after the real execute baseline. Preserve input deep copy and verify unchanged after success/failure.

- [ ] Add equivalent actual RAG initialized-service test using `initialized_service` from `tests/agent/test_rag_receipt_consumption.py`. After execute, capture the expected service snapshot and forbid initialize/search/embedding, not capture or validation. Verify the query request count stays one and the returned projection is exactly:

```python
expected = dict(kind='rag', generation_id=captured.generation_id,
                epoch=captured.epoch,
                configuration_sha256=captured.configuration_sha256,
                source_identity_sha256=captured.source_identity_sha256)
assert tool.validate_current_observation(
    result['data'], result['evidence'], input_data='synthetic query',
    expected_source=expected) == expected
```

- [ ] Run the new module before production edits; record the actual missing-method RED, do not claim a passing fixture as RED.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_current_source_tool_hooks.py
```

## Task 2: Implement the two load-free hooks

- [ ] Both signatures are exactly:

```python
validate_current_observation(self, data, evidence, *, input_data,
                             expected_source=None) -> dict
```

The body performs these ordered operations, with no result mutation: allocation-bounded native JSON validation; closed expected_source validation if present; input nonblank exact str validation; snapshot the already attached source; validate the exact original receipt/records; capture and validate fresh source; compare exact expected projection; recheck attachment/state; return a detached plain dictionary. Any Exception becomes `ValueError('current_source_unavailable') from None`; do not catch BaseException.

Use lazy `decision_bounds.validate_json` with max_bytes=64*1024 before dictionary/list copies. Expected-source field validation must reject bool epoch, float epoch, unknown fields, missing fields, uppercase or malformed hashes, wrong kind, subclass/coercion objects, cyclic/deep/oversize/non-native values before provider calls. Never use default=str or JSON round trips before bounded validation. Input itself fits the same bound; RAG additionally cannot exceed16KiB. Reject all receipt-free evidence even where generic adapters permit it.

- [ ] Reverse implementation reuses `validate_prediction_observation(data,evidence,smiles=input_data)` and requires its non-None digest. Select exactly one prediction_receipt entry, use its original records/receipt with `CONTROLS`. Under `_state_lock`, reject closed/loading/missing attached predictor and capture its identity; release lock before all producer calls. `capture_prediction_source` gives the exact ReverseSourceSnapshot and `validate_prediction_source` proves its match. At the end reacquire the tool lock and reject close/loading/attachment replacement. Do not call `_get_predictor` even on uninitialized input; do not close injected predictor. Return the three snapshot fields plus kind.

- [ ] RAG implementation snapshots `self.rag_system` by identity, rejects uninitialized/missing service or required methods, requires exactly one retrieval_receipt, and reconstructs only `{'records': data, 'receipt': original_receipt}`. Use `validate_retrieval_envelope(..., query=input_data, k=3)`, reject invalid_discard, capture `RetrievalEligibility`, then `validate_retrieval_source` with that exact snapshot. Recheck `self.rag_system is service` and initialized flag before returning. Do not read legacy FAISS arrays, repair receipt hashes or suppress existing full CSV freshness checks. Keep ownership with the injected service.

- [ ] Run focused tests again, record GREEN or real failures; do not alter preexisting tests or increase timeouts.

## Task 3: Negative and lifecycle regressions

- [ ] Test both positive hit and verified-empty observations, exact query/SMILES mismatch, stale generation/reinitialization, changed config, closed producer/service, missing/duplicate proof, raw and normalized tamper, non-native/cyclic/oversize data and evidence. Expect the sanitized error and unchanged caller data. Generic evidence extensions are retained, not granted authority.
- [ ] Reverse: tool closed/loading/unattached/legacy-only must fail with zero load/predict calls. Replace the attached predictor during capture and during validation; reject without closing either borrowed source. A producer callback taking `_state_lock` must complete, proving the hook does not hold that lock during capture/validate. Use deterministic threading Events for close-race, never performance assertions or sleeps.
- [ ] RAG: replace attached service during capture/validation, change source CSV bytes, change embedding model/endpoint/store and reinitialize. Reject without issuing another embedding request. Retain a healthy newer generation after rejection of an old snapshot; restore source/config only via existing authorized initialization in the fixture.
- [ ] Both: malformed expected projections fail before provider calls; mutate the returned dictionary and prove the provider snapshot/next projection unchanged. `asyncio.CancelledError` propagates rather than appearing as valid or unavailable success.
- [ ] The future loop still must supply owned scheduling, deadline debit, seals, same-run role closure and continuation replay. Hook tests do not claim those integrations.

## Task 4: Independent verification and local evidence

- [ ] Run the new module plus these actual consumer/producer regressions, serially through the same approved wrapper:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_current_source_tool_hooks.py tests/agent/test_rag_receipt_consumption.py tests/agent/test_reverse_receipt_consumption.py tests/test_reverse_target_invocation_receipts.py tests/agent/test_target_tool_contract.py tests/agent/test_rag_tool_contract.py
```

Confirm the final path exists before running; if it differs, use `rg --files tests/agent -g '*rag*contract*'` and record the exact actual path. No broad tests with hidden host configuration. Do not run another heavy test process concurrently; poll live handles instead of restarting them.

- [ ] Independent SPEC review verifies the approved method contract, failed-path confidentiality and no hidden execute/load calls. Fresh QUALITY review inspects code and repeats the focused union; reviewers run no production service or host asset discovery. Fix each reproduced finding and preserve original failed evidence.
- [ ] Parent checks `git diff --check`, compiles changed Python text in memory without imports/bytecode, records all exact commands/counts/warnings and updates `docs/handoff/remaining-through-step8.md`. Exact staging only. Commit message `feat: revalidate attached scientific tool sources`. No publication/merge claim.

## Required follow-on integration (not replaced by this increment)

Closed v2 obligations and proof shapes; actual seven-tool ModelDecisionLoop with input/record roles; optional Session pre-ledger preparation and conditional ledger proof; logical-action/final-key reuse; owned source checks with root credit; revision8 pre-CAS reconstruction and post-callback checks; final scientific acceptance; normal Web admission and real /ws; B2 generation/ranking, C cleanup and package8 real repeated/UI acceptance. These remain required by the full B1 design and original eight-package ledger.

## Execution record

- Initial written review identified three B1 protocol gaps; parent specified closed source projections, deferred B-only owner finalization and nonresetting resume deadline before implementation.
- Harvey's read-only review approves the two-hook plan release at baseline66039eb. This is component implementation authorization, not code approval or the full B1 release. No tests were run by the written reviewer.
