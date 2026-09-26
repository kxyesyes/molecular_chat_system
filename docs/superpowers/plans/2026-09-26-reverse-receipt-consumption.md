# Reverse-target receipt consumption implementation plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development and test-driven-development. Parent owns design, review integration and commits.

**Goal:** Carry actual owned reverse predictions through the existing tool, adapter, Session ledger and persistence without certifying legacy lists or altered records.

**Architecture:** One shared pure normalizer/proof validator; existing lazy tool owns its strict predictor; existing adapter checks proof before and after normalization. No parallel execution engine or source authentication by hash alone.

**Tech Stack:** Python, RDKit/NumPy synthetic writer assets, existing Pydantic adapters and SQLite Session tests.

## Boundary and execution

Design: `docs/superpowers/specs/2026-09-26-reverse-receipt-consumption-design.md` at baseline `a8d4e3d` with written review corrections. All its requirements apply. In particular, reconstruct `{"records": entry["records"], "receipt": entry["prediction_receipt"]}`; never call a producer method under the tool state lock; catch MolecularInputUnavailable before ValueError.

Production allowlist: `src/agent/tools/reverse_target_tool.py`, `src/agent/tooling/target_contract.py`, new `src/reverse_target/receipt.py`. Test allowlist: new `tests/agent/test_reverse_receipt_consumption.py`, actual-tool fixture migration only in `test_reverse_target_complete_input.py`, `test_target_tool_contract.py`, `test_domain_result_validators.py` under `tests/agent/`. No Session, producer, registry, UI, models or permission changes. Ask parent if a missing seam requires expanding this list.

Use existing `tests.test_reverse_target_invocation_receipts.make_writer_database` to generate temporary scientific sources. A yielding fixture in the new test module can initialize and close a predictor, shared by explicit fixture imports into migrated tests. Do not import another module's autouse fixture accidentally or hand-build successful receipts.

All test commands run in the dedicated worktree via:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py <explicit-test-path-or-nodeid>
```

Runner SHA256: `F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183`.
It clears environment and isolates assets before site/imports; do not use raw pytest or application probes. Only one heavy test process. No external services, host configuration/keys/assets or activation. Record actual commands, exit codes and failures. Commands below expect success only after implementation; unexpected collection/import errors are not behavioral RED evidence.

## Task 1: Reproduce the actual tool gap

- [ ] Add `test_list_only_predictor_is_unavailable_without_legacy_call` with a counted SimpleNamespace.predict returning `[]`, assigned to the existing private injection seam. Execute real `ReverseTargetTool.execute('CCO')`; assert `success is False`, `data is None`, error code `tool_unavailable` and zero predict calls. Existing code should fail because it returns success and executes the legacy method.
- [ ] Add `test_actual_owned_prediction_keeps_same_call_receipt`: initialize writer source, inject it, count strict calls and forbid legacy predict/load. Execute real tool, require successful rows, exactly one strict call, and evidence receipt matching captured source and actual raw records. Existing code should fail because it calls legacy predict and has no receipt.
- [ ] Run each node via the approved runner before production edits. Retain both exact RED outcomes. Reuse real fixture sources for all following positives.

## Task 2: Shared pure normalization/proof boundary

- [ ] Write tests against genuine producer envelopes for hits, empty source and nonempty no-hit; construct evidence using the exact six fields in the design and normalized data using the historical helper. Include every malformed proof and mutation class below before implementing its guard.
- [ ] Create stdlib-top-level `src/reverse_target/receipt.py`, moving the old helper unchanged to `normalize_target_record(record)`. Preserve public wrapper on ReverseTargetTool.
- [ ] Implement `validate_prediction_observation(data, evidence, *, smiles=None)`: no receipt key returns None; otherwise bound native JSON before copies/digest, exactly one closed proof entry, exact revision/count and supplied SMILES, reconstruct actual envelope, construct immutable snapshot identity for pure validation, reuse producer validator and compare canonical normalized rows. Return full data/evidence digest, not a repaired result hash.
- [ ] Reject null/duplicate receipt, extra entry fields, bool/numeric coercion, forged hashes, raw record extensions, invented identifier/assay/units, altered normalized values, unbounded/cyclic/non-native structures, mismatched SMILES and normalization revision. Preserve extra generic evidence entries unchanged and include them in digest.
- [ ] Run new module's pure tests via explicit node IDs. No successful pure check claims current source or authentication.

## Task 3: Actual lazy tool integration and ownership

- [ ] Add tests for missing methods/source, invalid input with zero initialization, `_check_rdkit` and parser capability errors, provider-safe errors, strict timeout, cancellation, same-call source mutations and no output leakage. Prove valid strict baseline before injecting source mutations.
- [ ] Add optional borrowed predictor constructor argument, state lock/loading/closed/owned references. Preserve old no-arg and private borrowed injection behavior. Default first valid request lazily constructs predictor and initialize_strict; never call singleton/legacy load or predict. Concurrent first request returns unavailable. Failed initialization clears only its owned loading slot and closes owned candidate; later invocation may retry initial acquisition, never silently reload an already published invalid source.
- [ ] Implement close in two phases: under tool lock mark sticky closed/detach owned references; outside lock call close_strict. No producer method may run under tool lock. Borrowed predictor remains open. Acquire/publish and failure paths must follow the same lock order. Add controlled callback-versus-close and loading-close barrier tests that release/join all test workers.
- [ ] Start one 180s monotonic deadline at execute entry. Pass min(120,remaining) into initialization and min(300,remaining) into prediction; checks span parsing/render/postflight. Pass request-local closed callback, preserve CancelledError. Controlled-clock tests verify remaining credit and late rejection, without weakening real wall-clock gates.
- [ ] Capture bound strict methods and expected snapshot, execute strict prediction once with exact historical controls, pure validate, detach raw proof, normalize and render existing Chinese presentation, then current-source validate against same snapshot/input before publication. Test source close/reinitialize/config/path drift during formatting and late tool close. No fallback/no empty conversion.
- [ ] Return `success=True,status='succeeded',data=[]` for verified empty; evidence contains exact raw proof/rows and quality prediction status. Return safe structured unavailable/invalid_output/tool_timeout without data/proof on failure. No raw exception/path/query logging in touched paths.
- [ ] Run new tool tests and both initial RED nodes to green.

## Task 4: Adapter and existing fixture migration

- [ ] Add opt-in tests on genuine proof-bearing real-tool output passed through TargetToolAdapter, then malicious RawTool wrappers for malformed observations. Verify one scientific call, caller-validator exceptions unchanged, generic receipt-free matrix unchanged and target-search unchanged.
- [ ] In `_invoke_guarded` retain actual call and external raw_validator before catch; check optional proof at every existing nested raw_result level, binding actual payload via existing whole parser and BaseMolecularTool lexical policy. Only proof success/no error/succeeded status is certifiable. Nested snapshots do not become outer success.
- [ ] Capture full proof-covered digest before compat/normalization and compare after the existing worker normalization. A security redactor mutation must produce invalid_output, never repair hashes or bypass redaction. Catch MolecularInputUnavailable as unavailable before generic invalid-output boundary; invalid/mismatched proof input is invalid_output. Preserve CancelledError.
- [ ] Migrate actual-tool fixtures in the three allowlisted tests to genuine initialized sources and strict call counters, keeping exact parsed SMILES/default controls assertions. Empty assertion intentionally becomes [] plus proof. Move generic extra identifier/relation/units test to direct shared-normalizer assertion and add genuine-tool proof counterpart; do not add those unsupported fields to producer. Keep generic RawTool matrix/scientific assertions unchanged.
- [ ] Run new module plus three migrated modules. Record fixture corrections separately from production defects.

## Task 5: Real Session/persistence and release regression

- [ ] Follow actual Session pattern in `tests/agent/test_rag_receipt_consumption.py::test_real_dynamic_session_receipt_ledger_seal_and_persistence`, using real registry, ReverseTargetTool, dynamic Session, ledger, observation_capture seal and temporary SQLite. Cover hits and empty; exactly one strict/core scientific execution.
- [ ] Assert raw receipt and normalized data survive provenance, ledger input binding/trace/evidence ID, seal, tool execution snapshots, terminal events and persisted run. Mutate receipt and normalized rows after seal; require integrity rejection, data removal/output removal, unchanged detached ledger. Explicitly settle the existing worker owner or release/join test-owned workers. Separately verify registry close invokes tool cleanup and preserves borrowed predictors; the borrowed fixture closes its predictor afterward. Registry close is not a worker-drain operation; no production ownership changes.
- [ ] Run final union with the approved runner (all paths in one invocation):

```text
tests/agent/test_reverse_receipt_consumption.py
tests/test_reverse_target_invocation_receipts.py
tests/test_reverse_target_popcounts.py
tests/test_reverse_target_health.py
tests/test_reverse_target_pharmacophore.py
tests/agent/test_reverse_target_complete_input.py
tests/agent/test_target_tool_contract.py
tests/agent/test_domain_result_validators.py
tests/agent/test_registration_consistency.py
tests/agent/test_rag_receipt_consumption.py
```

- [ ] Verify exact existing registration test pathname before invoking; correct a filename typo rather than widening to all tests. Independently review SPEC first and QUALITY second; fix findings with regression evidence. Independent final rerun must hash checked production files before/after.
- [ ] Parent records all commands/results/failures, updated handoff, in-memory compile of changed Python files, git diff --check, exact staging and local commit. Do not publish or merge this accumulated branch wholesale. No real P8 acceptance claim from offline component tests.

## Review correction record

Initial written SPEC review required three changes: correct producer `receipt` key, producer/tool lock-order rule, explicit parser capability exception classification. The design now pins all three and adds nested diagnostic proof validation. B current-source authority at reuse/resume/finish, global budgets, generation/ranking, UI cleanup and live acceptance remain later work.

## Execution evidence

Not yet run for this consumer slice. Producer baseline is a separate completed component; its 593-passed regression is not consumer evidence.
