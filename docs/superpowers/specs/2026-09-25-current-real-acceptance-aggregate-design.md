# P8-A: Offline Strict Report Aggregation — Reviewed Local Integration, Full Decision Pending

## Scope / baseline

The parent approved this small design and behavior-group TDD. Existing P8 plan was committed alone as `dc3d035`; requested `origin/main` revision `16b91575229be987b0c5cf3d8d9039135d8ade29` was merged without conflicts as `1f92378`. Branch remains `codex/current-real-acceptance`. No push or PR. Implementation is frozen for the parent's two independent reviews, not declared reviewed or ready for live execution.

This batch adds a pure offline gate over existing evaluation results. No model/tool execution, ordinary WS launcher, filesystem/report discovery, env access, user stores, actual weights/datasets, CLI change or full-suite execution. Tests use in-memory synthetic report records, explicitly not scientific evidence.

## Public function and input boundary

Implemented function in `src/agent/evaluation/aggregation.py`:

```python
def aggregate_scientific_reports(
    cases: Sequence[EvaluationCase],
    iterations: Sequence[Mapping[str, Any]],
    *,
    evidence: Mapping[str, Any],
    expected_rounds: tuple[int, ...] = (1, 2, 3),
) -> EvaluationReport:
    ...
```

The signature uses `EvaluationCase`, `EvaluationResult`, `EvaluationReport` from `models.py`; no new scientific execution/case framework. Import the function directly from its module; no package export or CLI change. Existing scientific `_is_ordered_subsequence` and `_provenance_record_complete` are reused rather than reimplementing those checks.

- `cases`: nonempty unique approved case IDs; trusted acceptance requirements come from `expected_skill`, expected/forbidden tools/events and `scientific_acceptance`, never from a report's self-declared expectations. No case prompt copied into output. P8-A adds a small `scientific_acceptance.strict_report` policy mapping: `decision_source` (`none`, `scripted`, `live_provider`), `entry_path` (logical ordinary/isolated/component class), `outcome` (`positive`, `expected_rejection`, `preserved_partial`), and `require_new_pose` (boolean). Missing strict policy blocks acceptance; it is not guessed from filenames/category.
- `iterations`: the existing `ScientificAcceptanceRunner.run_once()` shape: `{iteration, case_count, status, results}`; each result retains existing `case_id`, `status`, skill/tools, events, truth checks, provenance and anti-hallucination fields. P8-A accepts these already-extracted iterations, not arbitrary nested CLI/family/lab report formats. No implicit fallback to top-level latest `results`. No report file reader/adapter in this batch.
- `expected_rounds`: nonempty unique positive exact integers, maximum three; final P8 policy requires `(1,2,3)`. One round is useful only for focused tests/component validation, never full P8 evidence.
- `evidence`: closed in-memory supplementary records described below. Missing instrumentation is an explicit incomplete result, not silently synthesized evidence. Duplicate, unexpected, invalidly typed or contradictory records fail closed.
- Runtime boundary accepts builtin lists/tuples/dicts and finite JSON scalars plus the specified EvaluationCase records, not generators/custom mapping hooks/path objects. Bounds: <=256 cases, <=3 rounds, <=768 case results; supplementary tree <=50,000 nodes, <=16 depth, <=2 MiB scalar text total. Reject booleans as integers, NaN/Inf, cycles, invalid statuses and malformed consumed fields. Bounds/schema failures yield fixed public reason codes, never exception/input repr.
- No input mutation, I/O, clock, network, environment lookup, hashing of disk files or callbacks. Unknown free-form legacy result fields are not echoed or given authority. Strict-policy/evidence schemas reject unknown keys.

## Evidence boundary — declarations are not proof

Supplementary input has `run_id`, `revision`, `sources`, `artifacts`, `cleanup`. The records are observations supplied by a future authorized collector, not authenticated facts merely because they are separate arguments.

- `sources[]`: exactly one record per case×round, keyed by `case_id`, `round`; records contain `run_id`, `revision`, `trace_id`, `proof_class`, `decision_source`, `entry_path`, `provider_request_ids`, `tool_execution_ids`, `demo_mode`, `fallback_used`. IDs must correlate with existing event/provenance records and must not be reused across independent rounds. A live decision requires linked provider request and decision event IDs; real tool requirements require linked execution provenance. Boolean labels alone are insufficient. Scripted/replay/contract sources cannot satisfy live-provider policy, even if `proof_class` says real. Explicit contradictory claims fail; absent collector detail remains incomplete.
- `artifacts[]`: pose-required cases need run/case/round/trace/step association, a safe relative logical artifact ID, finite non-boolean `binding_energy`, unit `kcal/mol`, positive exact byte size, producer SHA-256 and independently observed SHA-256, and matching producing tool execution ID. Both digests must be valid and equal; record must identify creation by this run, not a pre-existing artifact. Missing observations are incomplete; mismatch/nonfinite/misassociated evidence fails. A path or `pose_exists=true` alone cannot pass. Equal content hashes in different rounds are allowed when independently generated with distinct execution identities; equality is not itself stale output.
- `cleanup[]`: exactly one record per round with matching run/revision and literal booleans `ownership_released`, `process_cleanup_complete`, `state_cleanup_complete`. Unknown/null/missing is incomplete; explicit false is failed. No deletion/process management performed by aggregator.

**Limit:** Offline data cannot authenticate a provider, prove a pose exists/is newly generated, or independently attest process cleanup. P8-A checks consistency and required observations, not the truth of an invented record. Output always declares `scope=offline_report_validation`, `live_execution_verified=false`, `final_acceptance=false`; proof-class text/hash syntax does not change those fields. P8-B/P7 must later bind an authorized collector and real artifact verifier to these records. Never advertise a P8-A passed report as current real scientific acceptance.

## Aggregation rules and result shape

1. Construct exact approved case×round Cartesian product before scoring. Empty inputs, duplicate cases/rounds/results, unexpected case/round and invalid counts fail. Missing expected pair yields incomplete/partial with its identity; no dict overwrite, deduplication, averaging or vacuous pass.
2. Every round contributes. An early failed case remains failed if later cases pass; top iteration status is additional failure evidence, never permission to hide nested failures. Positive scientific partial/skipped/unavailable remains partial, regardless of process exit 0, overall `passed`, score or completion rate.
3. Recheck expected skill/tool order/forbidden tools, requested truth checks and provenance using existing scientific result semantics. Unknown/missing truth-check statuses cannot default to passed. Do not trust reported `expected_tools` over approved EvaluationCase expectations. Preserve original case scientific status separately from validation verdict.
4. Expected rejection requires its declared truth check plus explicit rejection reason/no scientific claim evidence; provider/tool outage is not automatically correct input rejection. `preserved_partial` can pass preservation validation, but original partial remains visible and `scientific_complete=false`; not positive scientific success.
5. Precedence: failed assertion/contradiction/schema error => `failed`; otherwise missing/unavailable/positive partial evidence => `partial`; only complete consistent records => offline gate `passed`. Cleanup participates in the same gate. Exit recommendation 0 only for offline gate passed, otherwise 1; it is not a live-acceptance exit code.

Return existing `EvaluationReport(mode="strict_offline", cases=[])`. Metrics contain `gate_status`, `recommended_exit_code`, expected/observed pair counts, strict passed/partial/failed counts, `scientific_complete`, and the immutable offline-scope flags above. Each EvaluationResult keeps original case ID; `details.round` distinguishes rounds, with original scientific status, validation status and fixed reason-code list. `score` is 10/0 for validation only, never an LLM/scientific quality score. Output is a closed projection: no prompts, raw messages, exceptions, provider URLs, absolute paths or arbitrary quality/evidence payloads. No persistence method is called.

## Exact file scope

- New `src/agent/evaluation/aggregation.py`: pure aggregation, strict supplementary evidence validation and public projection; use existing models only. No change to historical runner/CLI behavior in this batch.
- New `tests/agent/test_evaluation_aggregation.py`: in-memory fixtures, failing behavior regressions and positive controls. No actual report/assets read and no scientific services instantiated.
- This design file; after approval, append focused RED/GREEN command/results and limitations to the existing `docs/superpowers/plans/2026-09-25-current-real-acceptance.md`.
- No `scripts/` change, new launcher/case dataset, `models.py` schema modification, package initialization change or production/Web/P7 edit.

## TDD batch after parent approval

Write and run the complete positive-control test first, recording a missing callable as an explicit assertion rather than a collection/import error; only then add the minimal implementation. Add the rejection regressions in small groups and observe the actual incorrect acceptance or incorrect reason-code assertion before each fix. Missing import alone is not the required behavioral RED evidence. Use real aggregator calls, not mocked return values; no implementation before its failing test.

Required regression groups: complete 3-round positive control; missing/duplicate/unexpected case×round; empty/malformed/bounded inputs; first failure hidden by latest passed summary; partial+exit0; report-declared expected fields disagreeing with policy; scripted falsely labeled live; missing/mismatched provider/tool provenance; finite energy excluding bool/NaN/Inf; missing/new-run mismatch/producer-vs-observed hash mismatch and pre-existing pose; identical content with distinct new executions; cleanup unknown/false; expected rejection vs provider outage; preserved partial; closed output/privacy and input nonmutation. Each rejection test must fail for its intended condition, with a neighboring positive control to prevent an always-fail implementation.

Focused command only, through the approved isolated test environment after checking collection imports: `python -B -m pytest tests/agent/test_evaluation_aggregation.py -q -p no:cacheprovider`. Use existing evaluation tests as bounded related regression if their isolation allows; retain exact RED and GREEN outcomes. Full suite stays in parent's queue. No live or asset preflight.

## Implemented collector-facing details and limits

- `strict_report.entry_path` vocabulary is exactly `component`, `isolated`, `ordinary`. `proof_class` vocabulary is `contract`, `replay`, `scripted`, `real_tool`, `real_decision`. Scripted-policy component consistency may pass; a live-provider policy cannot accept scripted evidence. No vocabulary value grants authentication.
- Root supplementary keys are `run_id`, `revision`, `sources`, `artifacts`, `cleanup`. Revision is a lowercase 40-hex logical identity checked for consistency only; no Git read is performed. Case/run/link identifiers are 1–96 ASCII letters/digits/underscore/hyphen, starting alphanumeric. Duplicate supplementary records, execution/request/trace identities across cases/rounds, decision IDs and artifact IDs fail; matching pose content hashes across distinct executions are allowed.
- Collector linkage uses existing `events` dictionaries and `tool_provenance` dictionaries: terminal event `payload.execution_id` ↔ provenance `quality.execution_id` ↔ supplementary `tool_execution_ids`; `planning_completed.payload.decision_id` with `payload.provider_request_id` ↔ `provider_request_ids`. **These request/execution linkage fields are prospective collector instrumentation, not asserted present in current live runner output.** Missing links remain incomplete. Existing loop decision IDs/event names are reused. No marker is inferred from summaries, filenames or a fake fixture's name.
- Pose record keys are exactly `case_id, round, run_id, revision, trace_id, step_id, artifact_id, binding_energy, unit, byte_size, producer_sha256, observed_sha256, tool_execution_id, created_run_id`. One pose observation per required case×round in this bounded batch; no multi-artifact adapter yet. Energy must agree exactly with existing `truth_checks.binding_energy_numeric.binding_energy` (same reported computation, not repeat-to-repeat tolerance). No independent disk/hash computation occurs here. Missing fields do not mask known contradictory values.
- Expected rejection supports existing truth checks `invalid_smiles_rejected` with fixed reason `invalid_smiles`, and `docking_parameters_required` with fixed reason `missing_docking_parameters`. This narrow allowlist is not a generalized negative-case DSL. Arbitrary/provider execution errors cannot be laundered by a rejection label. `preserved_partial` validates preservation; a round marked partial is accepted as preservation only if every approved row explicitly expects/preserves partial.
- `details.scientific_status` preserves the input case result's `status`; the current scientific runner's field is itself an acceptance status, **not necessarily the underlying tool's raw scientific outcome**. Collector must retain raw tool outcomes separately. `scientific_complete` means positive report consistency only; the immutable `live_execution_verified=false` and `final_acceptance=false` remain authoritative limitations.
- Malformed/bounded inputs return fixed `invalid_input`; no broad exception swallowing or automatic read/repair. Unknown legacy result fields are bounded but never echoed. `EvaluationReport.cases=[]` avoids serializing prompts. Empty/duplicate inputs fail, missing pairs emit explicit per-pair partial rows; observed count is unique accepted case×round keys, while duplicate/unexpected records set global failure reasons.
- Existing `EvaluationReport` has no top-level status member: aggregate verdict is `metrics.gate_status`, exit suggestion is `metrics.recommended_exit_code`. Per-case `EvaluationResult.status` is validation status, with raw input status retained in details. No `write_json()` invocation or live success certificate is produced.

Execution/RED-GREEN ledger and exact focused node IDs are recorded in the P8 plan. Design gate is satisfied; next gate is parent-arranged dual review, not more implementation or full/live testing.

## Parent follow-up: RDKit metric fallback risk

Static source review after freeze `6ca2c5c` confirms `runner.py::chemistry_metrics` catches RDKit ImportError, sets `Chem=None`, and then counts nonempty strings as valid without parsing. Thus `valid_smiles_rate`, uniqueness/count metrics, or a passed label derived from them alone are **not real molecular-validity evidence**.

P8-A does not call `chemistry_metrics`; it reuses only the scientific tool-order/provenance-completeness helpers. Its required truth checks and generic provenance checks validate report consistency, **not that RDKit was installed or executed**. A self-declared passed truth check wrapping fallback metrics must not be promoted to genuine RDKit proof. The immutable offline/live-verification/final-acceptance limitations remain unchanged.

For the future authorized real collector, validity evidence must include the actual RDKit runtime version and successful parser/canonicalization/deduplication observations tied to the same candidate/input/output identities and execution trace, with fallback/unavailable status explicitly retained. Missing RDKit or absent execution observations blocks the real-validity gate; nonempty-string fallback is insufficient. This is a recorded acceptance requirement, not an implemented new assertion or an out-of-scope producer fix.

No code/test changes or test runs for this follow-up; implementation freeze remains `6ca2c5c`. Parent-reported coordination context only (not independently queried): PR72/G2 open at `548baca`; 4C full running. Workers do not run full/live or inspect project assets, ambient environment values or user stores.

## Euclid SPEC P2 corrections — uncommitted content freeze

Euclid's four P2 findings were reproduced against the clean starting tree at `3309dad` (code `6ca2c5c`). Initial SPEC code approval was **not** granted. The following changes stay within the original internal-consistency contract and await the **same SPEC** re-review; no authentication, collector, I/O or producer framework was added.

1. Terminal execution IDs are now checked at each provenance index as `(tool, trace_id, execution_id)`, not only as independent sets. Swapping terminal IDs is rejected for distinct tools and for repeated invocations of the same tool. Missing links remain incomplete; existing count/order/trace checks still apply.
2. Terminal event name must agree with the corresponding provenance success boolean (`tool_completed` for true, `tool_failed` for false), even with `expected_events=[]` and no payload success field. Missing provenance outcome remains incomplete. Valid failed-tool preservation is retained.
3. `decision_source=none` rejects observed event decision/provider-request IDs even when supplementary request IDs are empty (or source details missing). Unmarked static planning events remain allowed; correctly linked live-policy records remain offline-only positive controls.
4. Expected rejection cannot contain passed `binding_energy_numeric`/`vina_pose` claims, or explicit finite `binding_energy`/true `pose_file_exists` observations even under a different check name. Unknown additional check semantics are partial, not assumed negative. The existing fixed negative checks and neutral `scientific_claim_evidence` safety check remain usable; a normal positive pose case still passes report consistency. This is intentionally a narrow consistency rule, not a new scientific validator.

Behavioral RED→GREEN records and exact reused command context are appended to the plan. Final focused result: **164 passed (157 aggregator + 7 existing pure regression), no failures/skips**; two-file in-memory compile and diff whitespace check passed. All new RED results were actual incorrect-pass assertions, with neighboring positive controls preserved. No full/live, project asset/user-store/environment-secret reads, commit, staging or push in this correction batch. Private synthetic test directories remain retained under the previously documented test wrapper.

Content freeze SHA-256 (raw working-tree bytes, **not a new commit**):

| File | SHA-256 |
|---|---|
| `src/agent/evaluation/aggregation.py` | `92a15f6a79abf94b77d40b07877d838c3d73522ce41d032f0d5ea67a397f2c42` |
| `tests/agent/test_evaluation_aggregation.py` | `a24fc5925b00a428b12e1f64faf011bd760c6708f58bee401a1ecb55c73ee6df` |

Base HEAD remains `3309dad0d8f1a26504682f934061b227530f9ac6`; changed source/tests and docs are intentionally uncommitted for parent review. Both `live_execution_verified` and `final_acceptance` remain always false. No SPEC approval is claimed by these passing tests.

## Socrates QUALITY P2 corrections — replacement content freeze

Parent returned four QUALITY P2 findings against the 164-pass Euclid correction tree; QUALITY approval was **not** granted. This section supersedes the previous working-tree hashes, not the historical TDD evidence. No commit was made. The same two Python files and two documents remain the complete change scope.

The correction is an invariant pass, not four fixture-specific exceptions:

- `_observed_checks` examines all terminal records before source/policy/trace completeness gates, even when `actual_tools=[]`. Terminal and provenance sequences must each agree with the actual-tools summary. At each paired index, every available tool/trace/execution fact is compared independently; a missing execution ID cannot hide a trace contradiction. Duplicate observed execution identities fail without needing supplementary IDs. Event name, payload success and provenance success are checked wherever each comparison is possible; unknown provenance success remains incomplete, not inferred from the event.
- Every event carrying either decision or provider-request identity participates in observation checks, including a request without a decision ID. No request is removed by the `planning_completed + decision_id` filter. Duplicated observed request identities fail independently of source availability. Observed requests are compared with source declarations even when policy or source trace is missing. A fully declared orphan request lacking its decision counterpart is partial; an extra undeclared request contradicting a supplied nonempty declaration fails. Unmarked static planning is allowed; other marked event kinds remain incomplete until supported planning linkage exists. This does not authenticate a provider.
- Existing none-policy/provider-marker and live-policy/scripted-model contradictions are checked before the missing-source return. Missing policy prevents policy comparison only; it does not suppress comparisons among supplied facts. Known contradictions retain failed precedence alongside missing-field reasons.
- Required pose consistency now consumes `truth_checks.vina_pose.pose_file_exists`: explicit `False` fails, absent field is partial, present non-boolean (including null, numeric, string or container) fails the bounded input contract. `True` alone is insufficient: existing energy/hash/current-run/provenance/artifact requirements still apply. Reported pose flag and numeric-energy validity are evaluated before the missing-artifact return. No filesystem existence or hash computation occurs.

The public callable, existing EvaluationCase/EvaluationReport models and scientific helper reuse are unchanged. No producer, collector, launcher, I/O or new scientific framework was added. `live_execution_verified=false`, `final_acceptance=false` and offline report-consistency scope remain unconditional; this is not security certification or real execution proof.

Final focused result: **266 passed (259 aggregator + 7 existing pure regression nodes)**, no failures/skips, exit 0. Four grouped RED→GREEN sequences and exact command reuse are recorded in the plan. Two-file in-memory compilation and `git diff --check` passed. No full/live, assets, ambient env/secrets or user-store reads; no staging/commit/push/PR. Owned synthetic test directories remain retained, not claimed cleaned. Await the same QUALITY recheck; no review approval claimed.

Replacement SHA-256 freeze (raw working-tree bytes):

| File | SHA-256 |
|---|---|
| `src/agent/evaluation/aggregation.py` | `f9a366987d34868b4e9698abfd03f129ce5f3935a52251f6a107b5f33613c9af` |
| `tests/agent/test_evaluation_aggregation.py` | `f7369dc8bf864495f0bfb0e0e515189bfa29e1f1ff275a36a6c5530f7a5149cf` |

Branch remains `codex/current-real-acceptance`, HEAD `3309dad0d8f1a26504682f934061b227530f9ac6`. Review the uncommitted content at these hashes, not HEAD alone.

## Socrates second QUALITY — global identity ownership freeze

Parent reports the original four QUALITY findings closed, but a further P2 remained: global uniqueness previously scanned supplementary sources, while observed request/execution duplicates were checked only within a case result. Removing a source could therefore hide reuse across case×round slots. This section supersedes the preceding content hashes; no QUALITY approval or new commit is claimed.

`_global_identity_checks` now assigns each supplied identity `(kind, value)` to an owner `(case_id, round)` before per-slot source/policy/artifact completeness checks. The union includes source trace/request/execution declarations, **all** event trace/request/execution/decision markers regardless of event name or missing counterpart, provenance trace/execution IDs, and strict pose artifact ID/trace/execution fields. Another occurrence in the same slot corroborates ownership; another slot is a global failure. It cannot be masked by missing source, missing row observation, missing artifact step, or later passes. Fixed global reasons are `reused_trace_identity`, `reused_provider_request_identity`, `reused_execution_identity`, `reused_decision_identity`, `reused_artifact_identity`; values are never echoed.

This is ownership, not blanket occurrence counting: source + provenance + tool_started + tool_completed may legitimately share an execution/trace identity in one slot. Existing same-role duplicate checks remain (duplicate IDs within one source list, repeated terminal/provenance execution IDs, repeated observed provider requests and completed decisions); they are not weakened into permitted extra executions. Missing observations without reuse still remain partial. Identity namespaces are separate; run/revision, tool/step names and input/output content hashes are deliberately not globally unique. Equal pose hashes across distinct executions remain allowed.

Pose audit: the previous global `artifacts[].artifact_id` duplicate check had no source early gate and is retained through the unified owner check. Its trace/execution fields now participate even without the step/metadata needed for local pose association. No new meaning is inferred from generic `artifact_paths`, filesystem filenames or arbitrary free-form result fields; no artifact reader or path alias resolution is introduced. Malformed/missing fields retain existing schema/completeness handling. The same public API and unconditional offline/live/final flags are unchanged.

Focused evidence: original **266 tests unchanged** + 54 ownership tests = **320 passed** (313 aggregator + 7 existing pure nodes), no failures/skips, exit 0; two-file in-memory compile and diff check passed. The initial 42-case matrix actually produced **20 failed / 22 passed**, then **42 passed** after the minimal owner-check correction. Twelve further boundary/positive regression cases passed without another implementation change; they are not claimed as additional RED cycles. Detailed selections and results are in the plan.

Latest raw-byte content freeze for the same QUALITY reviewer:

| File | SHA-256 |
|---|---|
| `src/agent/evaluation/aggregation.py` | `14ae0da3afccefb9857e38497a429b5664f79b016daf73d657ff37e3c2733fb6` |
| `tests/agent/test_evaluation_aggregation.py` | `828151e4040c80fe55b8c3ef0a8d6ae6b5a63dc24e844d7ab9fb6c1af3cd9a21` |

Still exactly two Python files + two docs, unstaged/uncommitted at HEAD `3309dad0d8f1a26504682f934061b227530f9ac6`. No full/live, assets/env/user-store reads, commit/push/PR or producer change. Retained private synthetic test directories are not a cleanup attestation. `live_execution_verified=false` / `final_acceptance=false` remain unconditional. Freeze for the original QUALITY reviewer, not real acceptance.

## Parent-authorized review closure and local integration

Parent now reports **SPEC APPROVE — Euclid**, covering the approved specification, and **QUALITY APPROVE — Socrates**, covering the latest ownership correction. Parent characterizes the last revision as completion of the same invariants, not expanded scope. Socrates independently ran the 320-test baseline plus 37 identity and 1168 malformed-input checks, according to the parent; these independent results are attributed to Socrates and were not rerun or inspected as actual report files by this worker.

Historical failed reviews, counterexamples and RED outcomes above remain intact: initial Euclid four-P2 corrections, Socrates four-P2 corrections, and the second Socrates cross-slot ownership P2. Approval applies after those corrections, not retroactively to the failed freezes. Core source/test raw-byte SHA-256 remains the latest `14ae0da3...` / `828151e4...` pair recorded above.

Parent authorizes an exact four-path local commit (aggregator, its tests, this design, existing P8 plan), followed by merge of already-fetched `origin/main` at `7b5611fed039aa7aebde62063f45e45e965cf23a` (PR75 / 4D inventory). Stop for parent direction if a source conflict requires manual resolution. Then rerun the original isolated 320 selection plus pre-inspected, offline evaluation collision focus; preserve core hashes. Full remains queued behind parent's G1 heavy run; no full/live/push/PR, no other worktree or real report/asset access. PR74 ADMET remains unmerged per parent status. This is local P8-A integration only, **not Package8 completion or real acceptance**. Await a full-test slot or parent's CI-only decision after verification.

Local execution result: exact four-path commit `ba7dc505e116fdac6c803bdc8c8d7d414cfb404e`; merge `204a2e2676e5164017ccfab1be31b01d019f0e4c` includes authorized main `7b5611fed039aa7aebde62063f45e45e965cf23a`. Merge was automatic with no conflicts or manual source changes. Both reviewed core hashes remain byte-for-byte identical to the preceding ownership freeze. Incoming main changed generation/planner/registry contracts but not the existing evaluation models/scientific runner or the two existing evaluation test modules used here.

Post-merge actual verification: original isolated selection **320 passed** (2.41 s), pre-inspected collision selection **83 passed** (2.27 s), both exit 0 with no failures/skips; two-file in-memory compilation passed in both runs. Collision selection covers PR75 inventory, generation/ranking adapter integration and 20 evaluation truth/data-flow/status nodes. The exact node list and exclusions are in the plan. No actual model/service/asset execution, external key use or `run_real` invocation occurred. Tests use synthetic records, patched generation calls, RDKit on literal test molecules and non-service registry fixtures; these are not real scientific acceptance evidence.

The worker is locally frozen after a documentation-only evidence commit. Full remains unrun pending the parent's full slot or CI-only decision; no push or PR was created. Package8 is **not** declared complete, and ordinary WS/live collector/P7 dependencies remain outside this batch. Immutable live/final flags stay false.

## Latest baseline: PR74 integrated; exact-head CI remains parent-owned

Parent subsequently published draft PR76 at `e2e4bd752b8bf4893b43ba0bca5c6e723d3f4666` and selected the repository's exact-head eight-check CI gate, including all full partitions, instead of local full while G1 owns the heavy slot. This publication/CI choice is parent-reported; this worker did not push or query CI and does not claim it passed.

After verifying local `origin/main` exactly matched the newly authorized `c3195f96c4a80aac40ac958d351ecea6b12f3c31` (PR74 ADMET), the worker merged it without conflicts as `1b009f9e6f503253440de5dda329b5a164083dd0`. Core aggregator/test hashes remain exactly the approved ownership freeze (`14ae0da3...` / `828151e4...`). No manual source or test edits were needed.

Actual merged-tree verification: original **320 passed** (2.27 s), prior collision **83 passed** (2.14 s), additional pre-inspected ADMET status/unknown-evidence focus **121 passed** (2.02 s). All exit 0 without failures/skips; two-file in-memory compilation passed in every run. The plan records exact selections, isolation and evidence boundaries. ADMET cases use in-memory records and patched backends; no real environment, assets, reports, services or live launcher are accessed.

This iteration permits only local merge and original two-doc evidence commits. Parent owns the next push and PR76 CI reconciliation: the first published head's CI cannot be claimed as success for a later head. No local full was run, no CI green status asserted, and Package8 is not complete. All offline/live/final limitations remain unchanged.
