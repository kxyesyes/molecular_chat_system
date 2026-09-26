# Attached-source hooks: focused main integration

## Scope and current state

Base is actual main fb62f5322e1e299e6e871970476e46379817fc23 (PR88 landed).
Extract only the reviewed435ae32 additions to RAGSearchTool and ReverseTargetTool,
the exact historical test_current_source_tool_hooks.py, and its implementation
plan. Do not copy either whole old production file, the accumulated B branch,
the old completion ledger, or subsequent loop/continuation/Web changes.

The accompanying 2026-09-26 plan preserves historical component evidence and
its then-current branch references. The bounded contract below is this slice's
integration scope; references there to unpublished B execution are future work,
not declarations that those modules are integrated or enabled on main.

## Existing reviewed contract to preserve

Both actual tool classes add:

```python
validate_current_observation(self, data, evidence, *, input_data,
                             expected_source=None) -> dict
```

- Reuse existing strict producer/pure receipt validation; no source manager,
  new executor, scientific scoring, receipt repair, registry or schema change.
- Validate the combined native input/data/evidence/expected-source object within
  64KiB before copies or source calls. RAG input additionally respects16KiB UTF-8.
- Optional expected source is a closed native projection: exact kind, lowercase
  hashes/generation ID, exact nonnegative integer RAG epoch (not bool/float).
- Validate exactly one original receipt and its unchanged records/input against
  the currently attached strict source. Valid hits and verified empty are eligible;
  missing/discard/altered proof is not. Source identity is not invocation authority.
- RAG projection has kind, generation_id, epoch, configuration_sha256 and
  source_identity_sha256. Reverse projection has kind, generation_id,
  source_sha256 and configuration_sha256. Return a detached plain dict.
- No initialization, search, prediction, embedding, loading or hidden source
  acquisition. Full freshness checks may perform I/O: eventual callers must use
  existing owned-worker scheduling, not the event loop.
- Snapshot reverse attachment under its lock, release before producer calls,
  then reject closed/loading/replaced attachment; do not close borrowed sources.
  RAG checks attached-service identity/initialized state again before returning.
- Ordinary exceptions expose only current_source_unavailable; cancellation and
  other BaseException behavior remain intact. Never mutate caller data/evidence.
- Session status, owner, seals, authorization and ongoing B reuse/resume/finish
  scheduling stay outside this hook's API. Hash-shaped data alone proves none.

## Integration and verification gates

1. Parent applies only historical addition hunks through apply_patch, leaving
   PR87 execute/publication eligibility and PR88 consumer normalization untouched.
   Verify production delta equivalence plus exact historical new-test/plan blobs.
2. Independent SOURCE checks current producer/receipt/tool interactions and actual
   test fixture imports; any adaptation requires a source-grounded amendment.
3. After explicit transfer of the sole local test slot from normal-Web Task5,
   run the existing approved isolated launcher (only literal worktree path changes).
   No raw pytest/application probes or secret/config/model/data asset discovery.
4. Run the historical six-module union and current eligibility/producer tests:
   tests/agent/test_current_source_tool_hooks.py;
   tests/agent/test_rag_receipt_consumption.py;
   tests/agent/test_reverse_receipt_consumption.py;
   tests/test_rag_retrieval_outcome.py;
   tests/test_reverse_target_invocation_receipts.py;
   tests/agent/test_rag_tool_contract.py;
   tests/agent/test_rag_current_eligibility.py;
   tests/test_rag_owned_generation.py;
   tests/agent/test_target_tool_contract.py.
5. Fresh QUALITY then independent exact union, hashes, in-memory compile, narrow
   credential-pattern/diff checks, exact staging. One draft PR, fresh exact-head
   CI/review checks and reviewed/landed tree comparison before authorized merge.

Historical evidence is four missing-method REDs5.44s, expanded218 failures78.40s,
then218 passed12.93s; six-module1217 passed42.82s and independent1217 passed40.84s.
The old expanded RED output was truncated, as retained in the copied plan. These
are not current-main test results. No current-main tests have run at extraction.
No live provider, production activation, deployment or P7/P8 completion claim.

Parent extraction verification: production addition/context hunks equal435ae32
after excluding Git index/range headers; test blobdce02d8438c217ac581f450b4a67c865e4a3b2bc
and historical plan blob7258118598b812d085682a3cac5d9bc6b249a069 match exactly.
New scratch launcher equals the approved B1 source after newline normalization
and only literal REPO substitution; SHA256
A016EFA2E9BA96C418FCA74890C8E630BAD61B02D539558E24AA77060076CC0F.
This file is ignored/local and must not be committed. Independent SOURCE review
is in progress. Linnaeus owns the only local scientific test slot in the B1 tree;
no tests may start here before explicit release and transfer.

Archimedes independent SOURCE/SPEC approves the extraction and current-main
producer/receipt/tool interactions. It independently verified all deltas, blobs
and hashes and found no adaptation or missing repository dependency. The hooks
retain producer semantics: reverse owns its loaded generation and does not adopt
disk replacements without reload; RAG validates full CSV/configuration freshness.
Fresh Peirce QUALITY starts read-only. Neither review is a current-main test run.

Exact planned union, historical six first followed by three current RAG modules:

```text
scratch/ordinary_chat_offline_runner.py tests/agent/test_current_source_tool_hooks.py tests/agent/test_rag_receipt_consumption.py tests/agent/test_reverse_receipt_consumption.py tests/test_reverse_target_invocation_receipts.py tests/agent/test_target_tool_contract.py tests/agent/test_rag_tool_contract.py tests/test_rag_retrieval_outcome.py tests/agent/test_rag_current_eligibility.py tests/test_rag_owned_generation.py
```

Current-main parent exact nine-module union is terminal74835/54d0e1 (summary
3eb774):1813 passed,3 SWIG deprecation warnings69.72s,exit0. All three code/test
hashes and launcher remain unchanged. Peirce independently approves QUALITY
source and is explicitly granted the sole test slot for the same union. Its
repeat has not completed at this checkpoint; no publication/merge approval yet.

Independent Peirce QUALITY repeat is now terminal96270:1813 passed, zero failures,
errors or skips,3 SWIG deprecation warnings67.91s, ORDINARY_PYTEST_EXIT=0 and
process exit0. All four hashes match before/after; scoped diff check passes and
no files were changed by the reviewer. SOURCE and QUALITY approve this freeze.
The local test slot was explicitly released and transferred back to normal-Web
Task5. This is offline integration evidence, not live scientific/model acceptance.
Publication still requires exact-file staging and fresh remote CI/review gates.
