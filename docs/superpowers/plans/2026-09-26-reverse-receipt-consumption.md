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

- [x] Add `test_list_only_predictor_is_unavailable_without_legacy_call` with a counted SimpleNamespace.predict returning `[]`, assigned to the existing private injection seam. Execute real `ReverseTargetTool.execute('CCO')`; assert `success is False`, `data is None`, error code `tool_unavailable` and zero predict calls. Existing code should fail because it returns success and executes the legacy method.
- [x] Add `test_actual_owned_prediction_keeps_same_call_receipt`: initialize writer source, inject it, count strict calls and forbid legacy predict/load. Execute real tool, require successful rows, exactly one strict call, and evidence receipt matching captured source and actual raw records. Existing code should fail because it calls legacy predict and has no receipt.
- [x] Run each node via the approved runner before production edits. Retain both exact RED outcomes. Reuse real fixture sources for all following positives.

## Task 2: Shared pure normalization/proof boundary

- [x] Write tests against genuine producer envelopes for hits, empty source and nonempty no-hit; construct evidence using the exact six fields in the design and normalized data using the historical helper. Include every malformed proof and mutation class below before implementing its guard.
- [x] Create stdlib-top-level `src/reverse_target/receipt.py`, moving the old helper unchanged to `normalize_target_record(record)`. Preserve public wrapper on ReverseTargetTool.
- [x] Implement `validate_prediction_observation(data, evidence, *, smiles=None)`: no receipt key returns None; otherwise bound native JSON before copies/digest, exactly one closed proof entry, exact revision/count and supplied SMILES, reconstruct actual envelope, construct immutable snapshot identity for pure validation, reuse producer validator and compare canonical normalized rows. Return full data/evidence digest, not a repaired result hash.
- [x] Reject null/duplicate receipt, extra entry fields, bool/numeric coercion, forged hashes, raw record extensions, invented identifier/assay/units, altered normalized values, unbounded/cyclic/non-native structures, mismatched SMILES and normalization revision. Preserve extra generic evidence entries unchanged and include them in digest.
- [x] Run new module's pure tests via explicit node IDs. No successful pure check claims current source or authentication.

## Task 3: Actual lazy tool integration and ownership

- [x] Add tests for missing methods/source, invalid input with zero initialization, `_check_rdkit` and parser capability errors, provider-safe errors, strict timeout, cancellation, same-call source mutations and no output leakage. Prove valid strict baseline before injecting source mutations.
- [x] Add optional borrowed predictor constructor argument, state lock/loading/closed/owned references. Preserve old no-arg and private borrowed injection behavior. Default first valid request lazily constructs predictor and initialize_strict; never call singleton/legacy load or predict. Concurrent first request returns unavailable. Failed initialization clears only its owned loading slot and closes owned candidate; later invocation may retry initial acquisition, never silently reload an already published invalid source.
- [x] Implement close in two phases: under tool lock mark sticky closed/detach owned references; outside lock call close_strict. No producer method may run under tool lock. Borrowed predictor remains open. Acquire/publish and failure paths must follow the same lock order. Add controlled callback-versus-close and loading-close barrier tests that release/join all test workers.
- [x] Start one 180s monotonic deadline at execute entry. Pass min(120,remaining) into initialization and min(300,remaining) into prediction; checks span parsing/render/postflight. Pass request-local closed callback, preserve CancelledError. Controlled-clock tests verify remaining credit and late rejection, without weakening real wall-clock gates.
- [x] Capture bound strict methods and expected snapshot, execute strict prediction once with exact historical controls, pure validate, detach raw proof, normalize and render existing Chinese presentation, then current-source validate against same snapshot/input before publication. Test source close/reinitialize/config/path drift during formatting and late tool close. No fallback/no empty conversion.
- [x] Return `success=True,status='succeeded',data=[]` for verified empty; evidence contains exact raw proof/rows and quality prediction status. Return safe structured unavailable/invalid_output/tool_timeout without data/proof on failure. No raw exception/path/query logging in touched paths.
- [x] Run new tool tests and both initial RED nodes to green.

## Task 4: Adapter and existing fixture migration

- [x] Add opt-in tests on genuine proof-bearing real-tool output passed through TargetToolAdapter, then malicious RawTool wrappers for malformed observations. Verify one scientific call, caller-validator exceptions unchanged, generic receipt-free matrix unchanged and target-search unchanged.
- [x] In `_invoke_guarded` retain actual call and external raw_validator before catch; check optional proof at every existing nested raw_result level, binding actual payload via existing whole parser and BaseMolecularTool lexical policy. Only proof success/no error/succeeded status is certifiable. Nested snapshots do not become outer success.
- [x] Capture full proof-covered digest before compat/normalization and compare after the existing worker normalization. A security redactor mutation must produce invalid_output, never repair hashes or bypass redaction. Catch MolecularInputUnavailable as unavailable before generic invalid-output boundary; invalid/mismatched proof input is invalid_output. Preserve CancelledError.
- [x] Migrate actual-tool fixtures in the three allowlisted tests to genuine initialized sources and strict call counters, keeping exact parsed SMILES/default controls assertions. Empty assertion intentionally becomes [] plus proof. Move generic extra identifier/relation/units test to direct shared-normalizer assertion and add genuine-tool proof counterpart; do not add those unsupported fields to producer. Keep generic RawTool matrix/scientific assertions unchanged.
- [x] Run new module plus three migrated modules. Record fixture corrections separately from production defects.

## Task 5: Real Session/persistence and release regression

- [x] Follow actual Session pattern in `tests/agent/test_rag_receipt_consumption.py::test_real_dynamic_session_receipt_ledger_seal_and_persistence`, using real registry, ReverseTargetTool, dynamic Session, ledger, observation_capture seal and temporary SQLite. Cover hits and empty; exactly one strict/core scientific execution.
- [x] Assert raw receipt and normalized data survive provenance, ledger input binding/trace/evidence ID, seal, tool execution snapshots, terminal events and persisted run. Mutate receipt and normalized rows after seal; require integrity rejection, data removal/output removal, unchanged detached ledger. Explicitly settle the existing worker owner or release/join test-owned workers. Separately verify registry close invokes tool cleanup and preserves borrowed predictors; the borrowed fixture closes its predictor afterward. Registry close is not a worker-drain operation; no production ownership changes.
- [x] Run final union with the approved runner (all paths in one invocation):

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

- [x] Verify exact existing registration test pathname before invoking; correct a filename typo rather than widening to all tests. Independently review SPEC first and QUALITY second; fix findings with regression evidence. Independent final rerun must hash checked production files before/after.
- [x] Parent records all commands/results/failures, updated handoff, in-memory compile of changed Python files, git diff --check, exact staging and local commit. Do not publish or merge this accumulated branch wholesale. No real P8 acceptance claim from offline component tests.

## Review correction record

Initial written SPEC review required three changes: correct producer `receipt` key, producer/tool lock-order rule, explicit parser capability exception classification. The design now pins all three and adds nested diagnostic proof validation. B current-source authority at reuse/resume/finish, global budgets, generation/ranking, UI cleanup and live acceptance remain later work.

## Execution evidence

Written design/plan reviewed independently by Boole; corrected envelope mapping,
lock order and capability-error taxonomy, then corrected the plan's registry-drain
wording. Approved documents committed as `9cabb11`. Producer baseline is separate;
its 593-passed regression is not consumer evidence.

### Implementation run ledger (Singer, 2026-09-26)

Every invocation used the exact isolated prefix above. `T` below denotes
`tests/agent/test_reverse_receipt_consumption.py`. Group definitions are explicit
below the table. All rows except those marked failed returned exit0.

| Invocation arguments | Actual result |
|---|---|
| T::test_list_only_predictor_is_unavailable_without_legacy_call | 1 failed, exit1; `assert True is False` confirms old legacy empty certification |
| T::test_actual_owned_prediction_keeps_same_call_receipt | 1 failed, exit1; `Failed: legacy predict/load must never run` |
| Pure group | 27 failed, exit1, because new module absent; not behavioral RED; then27 passed |
| Tool group | 25 failed, exit1 |
| T | 54 passed |
| Adapter group | 31 failed,13 passed, exit1 |
| Four modules | 76 failed,301 passed,1 error, exit1 |
| T + tests/agent/test_target_tool_contract.py::test_elapsed_preserves_legacy_producer_scope_and_object_semantics | 146 passed |
| Four modules | 378 passed |
| Session group | 4 failed,9 passed, exit1; then13 passed |
| T | 114 passed |
| Final10-module union listed in Task5 | 942 passed,7 deprecation warnings,2 subtests passed,84.02s |

Pure group: `T::test_pure_genuine_observation`, `T::test_pure_rejects_mutation`.

Tool group: `T::test_generation_mutation_during_formatting`,
`T::test_strict_capability_failure`, `T::test_parser_preflights_no_acquisition`,
`T::test_owned_remaining_credit_and_borrowed_close`,
`T::test_one_deadline_rejects_late_results`,
`T::test_failed_acquisition_closes_and_next_invocation_retries`,
`T::test_concurrent_loading_close_cannot_republish`,
`T::test_callback_versus_close_lock_order_drains`.

Adapter group: `T::test_adapter_proof_status_matrix`,
`T::test_adapter_rejects_proof_mutation`,
`T::test_adapter_full_digest_survives_normalization`,
`T::test_adapter_parser_unavailable_and_caller_exceptions`.

Four modules: T, `tests/agent/test_reverse_target_complete_input.py`,
`tests/agent/test_target_tool_contract.py`, `tests/agent/test_domain_result_validators.py`.

Session group: `T::test_real_dynamic_session_receipt_ledger_seal_and_persistence`,
`T::test_registry_close_owns_only_created_sources`,
`T::test_actual_tool_rejects_malformed_or_stale_producer`.

The first four-module failure included an incompatible private validator-signature
change, unmigrated list fixtures and a misplaced new test block. The signature
was restored, genuine strict fixtures replaced only actual-tool fixtures, and
the misplaced block was corrected. Session tests initially expected a wrong
seal error: corrected to existing `observation_already_sealed`, with no Session
production change. Generic RawTool matrices and scientific assertions retained.
Verified-empty None intentionally becomes [] plus proof. Generic assay extensions
remain a direct normalizer test plus genuine producer counterpart, not invented
producer columns.

Production hashes before/after implementation's final union matched:

- reverse_target_tool.py: `81470ECD3A0BF3D58BE2EA838B05EAA7425BE6DD2F99D0B815DA3EF6D5CB4C93`
- target_contract.py: `2486CD07B3B9B445BD02C54354E271996D8BB14C6BD9FFDFA4D3DCA6D09B7C12`
- receipt.py: `A2B095FB4F459579FBFAA97A59E92B52670CB07FEEFE1DDD452A3096E1BCFAC5`

Implementation reports runner hash unchanged and git diff --check clean. Source
SPEC and QUALITY approval, independent rerun, parent compile and final commit
remain pending at this ledger entry. No real source/model/service or deployment.

Worker follow-up: the76-failed run took27.18s; its14,538-token output exceeded
the3,500-token tool budget. Complete tracebacks and exact misplaced-block/teardown
terminal exceptions are unavailable; no invented per-cause count is assigned.
Initial pure-group and adapter-group outputs were also truncated. Both mandatory
initial behavioral REDs and the final union output were untruncated. Final warnings
are three SWIG DeprecationWarnings (SwigPyPacked, SwigPyObject, swigvarlink missing
__module__) and four FastAPI on_event deprecations (two application locations,
two framework occurrences). These warnings are retained, not silently suppressed.

### Independent release checks

Einstein independently reviewed the seven Python files against the approved design:
SPEC APPROVE, no actionable deviations. Read-only review; no tests claimed.
Leibniz then independently reviewed QUALITY and ran the exact Task5 ten-module
union with the approved offline runner: exit0, **942 passed,0 skipped,7 warnings,
2 subtests passed,177.16s**. Its output was untruncated. No actionable quality
findings. Runner and all three production hashes above matched before/after.
The slower repeat is retained as observed; no unmeasured cause is assigned.

Parent compiled the seven changed Python files in memory using MedChat Python
`-I -S -B` plus builtin compile(Path.read_bytes(), path, 'exec'); no project
imports or bytecode writes. git diff --check passed. A filename-only scan of
these seven files and the two new documents for long sk-/ghp_/github_pat_ patterns
returned no matches (rg exit1); it is a narrow credential-pattern check, not
proof that every possible secret pattern is detectable. Original checkout still
has its same13 pre-existing status paths and was not modified.

All five tasks are complete for this slice, including reviewed fixture migration.
This changes the actual reverse tool from uncertified legacy list execution to
owned strict execution, rejecting unavailable or altered observations while
retaining verified empty as [] with evidence. It does not change scoring, activate
B permissions, authenticate arbitrary hash-shaped data, or implement B reuse,
resume-before-CAS, finish freshness, generation budgets or final live P8 checks.
