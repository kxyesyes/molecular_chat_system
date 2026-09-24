# P8-A: Offline Strict Report Aggregation — Approved, Review Freeze

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
