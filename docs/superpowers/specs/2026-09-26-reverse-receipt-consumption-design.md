# Reverse-target strict receipt consumption

Date:2026-09-26. Baseline a8d4e3d. Recommended choices are delegated by the user.
Written review must precede code. This slice is not complete B or P8 acceptance.

## Choice and scope

Use the existing ReverseTargetTool, TargetToolAdapter and Session ledger/seal.
The actual tool requires strict owned producer methods, like the reviewed RAG
integration. Do not certify the old list API by wrapping post-call mutable fields.
Do not introduce a parallel strict tool, registry or execution engine. A model-
controlled require_receipt flag is rejected: proof is server policy, not optional
model arguments. Generic receipt-free synthetic target adapter contracts remain
compatible; they cannot gain future B scientific authority on that basis.

Production allowlist:

- src/agent/tools/reverse_target_tool.py: bounded lazy owned acquisition, strict
  call/presentation, postflight eligibility and owned-resource close.
- src/agent/tooling/target_contract.py: optional reverse proof verification before
  and after existing normalization; unchanged target-search contracts.
- New src/reverse_target/receipt.py: pure record normalization and consumer proof
  binding. Top-level standard-library imports only; reuse owned_source's existing
  validation/digest functions lazily when a proof is actually supplied.

No owned_source/predictor changes are expected. If an actual missing seam is found,
bring that bounded requirement to the parent rather than changing the producer
contract silently. No Session/loop/registry/app changes, B permission activation,
dataset/cache format, new model, scientific scoring changes or external services.

New test: tests/agent/test_reverse_receipt_consumption.py. Migrate only actual-tool
fixtures in tests/agent/test_reverse_target_complete_input.py,
tests/agent/test_target_tool_contract.py and tests/agent/test_domain_result_validators.py.
Keep generic RawTool structural/status tests and all scientific assertions. A
small shared synthetic writer fixture may live in the new test module, imported
by the three existing modules using the repository's test import convention; no
production fake-success factory. Existing registration tests should need no edits.

## Actual tool acquisition and lifetime

Construction/registration remains lazy, with no source reads, model imports or
initialization. Preserve the no-argument constructor; an optional predictor
injection is server/test-only and considered borrowed. Historical private
_predictor injection is also borrowed unless this tool explicitly created it.
Injected sources must already have strict initialized capability. Missing strict
methods or unavailable generation yields unavailable, never legacy predict/load.

For the normal no-injection instance, the first valid execute request constructs
ReverseTargetPredictor(get_reverse_target_data_dir()) lazily, then calls
initialize_strict once. Do not call the singleton get_predictor or legacy load,
which can save caches/read pickle. Existing source resolver chooses the initial
directory; the loaded configuration then stays explicit on that predictor. Merely
changing the environment does not silently adopt a different database.

Use a short-held tool state lock, loading flag and sticky closed flag. Do not
hold the lock while calling any producer method, including close, capture,
postflight validation, acquisition or scoring. Cancellation callbacks may acquire
the tool lock while the producer holds its own lock: close must mark state and
capture owned references under the tool lock, then call close_strict outside it.
A barrier-based callback-versus-close test must prove both sides drain.
Concurrent first acquisition
returns structured unavailable; it does not allocate another snapshot or wait
unboundedly. Publish only after initialize and a closed/state check. An admitted
failed acquisition closes only its owned candidate and clears its loading slot;
another subsequent user invocation may retry initial acquisition. Never retry
inside one execute, and never silently reinitialize an already published source
after close/path drift or receipt failure.

Tool close is idempotent and sticky, including during loading. It invalidates
owned candidate/publication via close_strict without joining or resetting the
producer's active counters; the existing adapter/worker owner retains descendant
drain responsibility. Late acquisition cannot republish after close. Borrowed
predictors are never closed by this tool; closed tool checks still forbid its
late result publication. No source objects or captured callback are persisted.

All phases consume one local monotonic180-second execute budget. Whole-input
validation precedes acquisition; compute remaining credit before each phase.
Own initialization receives min(120,remaining) and prediction min(300,remaining),
never a reset180/300 after initialization. _get_predictor may gain keyword-only
timeout_seconds for this trusted internal credit, not a model argument. Pass a
request-local closed-state cancellation callback to owned initialization and
prediction; preserve asyncio.CancelledError. Check deadline/closed state before
and after formatting/current-source validation. This is cooperative and not a
hard native-call kill guarantee. Earlier root/A2 cancellation credit integration
remains B work; do not claim this local budget completes it.

Keep lazy registry readiness unknown; do not add a readiness hook that probes a
source or blocks first use merely because no source has yet been initialized.

## Input, output and provenance

Retain actual whole-SMILES parser and its invalid-input/unavailable distinctions;
both _check_rdkit failure and MolecularInputUnavailable return safe structured
tool_unavailable, with no proof or data. Do not let the latter's ValueError
inheritance turn a parser capability failure into invalid_output in the adapter.
no initialization for invalid SMILES. Existing compatibility tool input may have
multiple valid structures and uses its historical first structure. B's eventual
single-subject admission is a separate mandatory boundary, not silently relaxed.

Fixed actual controls remain threshold0.6,top_k10,combine_by_target=true,
organism_filter=''. Exact selected SMILES is hashed without repair/canonicalization.
Capture bound predictor methods and expected snapshot, call predict_with_receipt
exactly once, validate the strict producer envelope, normalize/render detached
records, then validate_prediction_source with the same expected snapshot and
inputs before returning. Capture/formatting mutations of a generation must not
publish obsolete rows. No fallback to list or failure-to-empty conversion.

The producer has exactly13 raw record fields. Preserve its original records and
receipt verbatim in evidence; normalization adds only the existing deterministic
target_identifier and assay. Move that normalizer into receipt.py and retain
ReverseTargetTool._normalize_target_record as a thin compatibility wrapper.
The old helper preserves database identifiers/relation/units when explicitly
provided in generic records. Actual strict13-field producer does not supply
those extensions: do not invent them or accept unverified extra producer fields.

For verified hits and empty, return success=true, status=succeeded, data=list,
including [] for verified empty (not historical None). Keep current Chinese
presentation and default threshold disclosure. Only verified actual no-match
can claim no hits; unavailable/malformed data cannot. Optional generic explanatory
text must not claim experiments or docking affinity. No raw exception, source
path, query or structure logging in touched acquisition/execution error paths.

One proof evidence entry contains exactly:

```
source = 'local_reverse_target_fingerprints'
normalization_revision = 'reverse-target-record-v1'
input_smiles = exact selected validated SMILES
record_count = len(raw_records)
records = detached original13-field record list
prediction_receipt = detached original producer receipt
```

Quality may carry tool_name and prediction_status from verified receipt. Do not
put proof in arbitrary root keys that execute_tool_compat discards. Success data
is exactly [normalize_target_record(row) for row in evidence.records], verified
by canonical native JSON identity, not equality with bool/int/float coercions.
No new hash is substituted for the producer's result_sha256 after normalization.

## Pure consumer and adapter contract

receipt.py exports normalize_target_record(record) with the unchanged old helper
semantics, and validate_prediction_observation(data,evidence,*,smiles=None).
The latter returns None for no prediction_receipt key, otherwise a canonical
digest over the complete original data/evidence after validation. Multiple receipt
entries, null receipt, unknown fields in the proof entry or malformed native JSON
are fixed safe ValueError. Other generic evidence entries remain retained and
covered by the full digest; do not project them away.

Reuse owned_source's bounded-native checker, canonical JSON/hash and closed
validate_envelope rather than a second receipt schema. The pure check constructs
the frozen ReverseSourceSnapshot from receipt identity fields solely to satisfy
its exact-type view; this does NOT authenticate a source or establish current
eligibility. Reconstruct the producer envelope exactly as
{"records": entry["records"], "receipt": entry["prediction_receipt"]}; only the
evidence entry uses the name prediction_receipt. Validate this envelope with the
entry input_smiles and fixed controls. When caller smiles is supplied, require
its exact identity too. Check exact record_count and normalization_revision;
reject any altered normalized field or invented assay. The whole data/evidence
native structure is bounded before hashing/copying; preserve the producer's
1MiB/depth8/node10000 policy, no default=str or hash repair.

TargetToolAdapter only opts into these checks for reverse observations containing
prediction_receipt. Generic proof-free observations and target search stay exactly
as before. For a proof-bearing invocation, parse the actual payload with the same
whole-SMILES parser/default lexical policy and bind its historical first molecule;
do not trust evidence.input_smiles alone. This is structure validation, not another
similarity search. The adapter must not depend on arbitrary RawTool implementations
providing parser helpers; reuse the existing BaseMolecularTool lexical policy for
this opt-in validation only. Do not turn registration into scientific execution.
Catch MolecularInputUnavailable before the existing ValueError boundary and return
safe tool_unavailable; an invalid or mismatched proof-bearing payload instead
returns invalid_output. Preserve CancelledError and caller-validator exceptions.
Apply structural proof validation at each existing nested raw_result observation;
bind any such proof to the same actual payload. Nested snapshots are diagnostics,
not independent successful observations or new scientific authority.

Proof-bearing outcomes allow only success=true,error=null and raw absent/null/
succeeded status or canonical ObservationStatus.SUCCEEDED. Reject partial, failed,
unknown or contradictory proof-bearing claims, even if numerical rows look valid.
Failures returned by the real tool have no proof/data to certify; structured errors
are retained. Current-source/capability failures -> tool_unavailable; malformed
proof or normalized mismatch -> invalid_output; local budget expiration ->
tool_timeout (retryable=false), with generic safe messages. Preserve cancellation.

In TargetToolAdapter's existing one-worker _invoke_guarded:

1. Execute actual tool exactly once, invoke caller raw_validator unchanged.
2. Validate existing target view plus optional proof and capture full data/evidence
   digest. Caller validator exceptions remain outside this adapter's own catch.
3. Use existing execute_tool_compat/provenance and _normalize, no second execution.
4. Validate optional proof again and compare digest before returning. Security
   redaction or normalization changing proof-covered data/evidence fails closed;
   never relax redaction, discard fields or rewrite receipt hashes to rescue it.

No new adapter or registry branch is needed. Current-source validation stays the
actual tool's responsibility; B reuse/resume/finish freshness remains later work.

## Session and TDD release gates

Use actual temporary TSV plus writer-generated Morgan/MACCS and strict initialized
predictor, real ReverseTargetTool, registry/TargetToolAdapter and dynamic Session.
No hand-built successful receipt as the sole positive fixture. Count actual
predict_with_receipt/core calls; formatting/adapter checks must not score twice.

Initial actual REDs before production edits:

- List-only predictor with counted predict: actual tool currently calls it and
  returns successful empty. New expectation is unavailable and zero legacy calls.
- Actual initialized owned predictor: actual tool currently calls legacy predict
  rather than strict, and omits receipt. Assert successful current strict result
  with same-call proof and no legacy load/predict invocation. Retain exact RED.

Test true empty/no-hit, hits, whole invalid batch and selected first structure;
missing strict methods, safe provider/source errors, malformed/forged hashes,
numeric type changes, raw13-field extensions, normalized identifier/assay changes,
receipt count/status contradictions and actual security-redaction mutations.
Every actual-source negative first proves its initialized positive baseline.
Test close/reinit/weight/path mutation during formatting with no output leakage;
owned-versus-borrowed close, concurrent first load, failed acquisition cleanup and
late loading close using barriers, not sleep-only gates. Controlled clock verifies
remaining initialization/prediction credit; no wall-clock timeout relaxation.

Prove data/proof survive ledger, write-once observation seal, events and SQLite
result snapshots; nested mutations fail verify_observation_integrity. Do not
rewrite Session production or substitute static-planner tests for this boundary.

Fixture migration: complete_input positives use real writer sources and strict
call counters, retaining all exact input/invalid/no-initialization assertions.
The old successful-empty None assertion intentionally becomes [] plus receipt.
target_contract actual wrapper positives use real strict sources; generic RawTool
matrix unchanged. domain_result_validators' old synthetic extra-field list test
remains an exact normalizer/helper assertion, paired with actual tool proof tests;
it must not teach the strict producer to invent target IDs or assay units.

Approved offline runner only, temporary synthetic assets and actual RDKit/NumPy;
no host assets/config/keys, external models/services, push/merge/deployment. Record
all failures and fixture changes. Independent SPEC then QUALITY approval and
exact-file local commit are required. Legacy predictor APIs remain usable but
uncertified; B execution/freshness, B2/C and final live P8 remain pending.
