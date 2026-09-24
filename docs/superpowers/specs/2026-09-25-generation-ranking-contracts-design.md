# Generation and ranking non-projecting contracts — package 4C

## Status, scope and decision

Design proposal for parent review, not implementation authorization. Inspected baseline: `68b8538`, branch `codex/generation-ranking-contracts`. This task writes only this specification and its companion plan; no commit, push, model invocation, asset/secret access, or changes to another worktree. Package 4A's uncommitted analysis contract was inspected read-only as a lifecycle reference, not treated as an approved dependency.

Chosen: two tool-specific input/output schema pairs and adapters in **one scoped module**, `src/agent/tooling/generation_ranking_contract.py`, with a private shared worker-bound validation adapter. Wire only `llm_molecular_generator` and `candidate_ranker` in `src/agent/tooling/factory.py`; add focused tests. Reuse `LegacyPythonToolAdapter`, compatibility normalization and current domain owners.

Alternatives: a projecting DTO would discard extensions, insert defaults and change science; two independent lifecycle implementations would duplicate timeout/normalization handling. A new universal adapter framework is unnecessary and outside scope. Keep strict validation views, never replace observations with their `model_dump()`.

Non-goals: generation prompts/retries, model selection/quality, candidate filtering algorithms, ranking formulas/weights/tie-breaking, workflow bindings, claim policy, UI, deployments or generic adapter changes. No additional tools or dependencies.

## Grounded ownership and call paths

Read `AGENTS.md`, `docs/PROJECT_STANDARDS.md`, tooling factory/adapters/spec/registry, activity/docking/RAG contracts, both producers, `contracts/generation_request.py`, `contracts/candidates.py`, `validators/molecule_candidates.py`, `validators/result_validator.py`, workflow/dispatch/specialist code and the tests listed in the plan.

**Naming correction:** there is no `CandidateSetValidator` class in this baseline. The existing ownership is `AgentResultValidator._validate_generator_result`, `CandidateSet.from_dict`, `sanitize_generated_candidates`, and `AgentResultValidator._revalidate_candidate_set`. Do not invent a parallel validator or import a nonexistent symbol. If the parent integrates an approved extraction with that name first, adapt imports after rereading its API; do not modify that branch.

`WorkflowOrchestrator._canonical_generation_input` creates `{query, metadata, outputs}` and adds request-local temperature. `SpecialistDispatch.__call__` wraps the entire domain input as `AgentTask.inputs = {"query": deepcopy(input_data)}`; `SpecialistAgent.execute_task` passes this outer wrapper unchanged to the registry adapter. `step_templates.target_design_steps` binds ranking from `$.workflow`, with `docking_top_n` metadata and molecules/properties plus optional admet/activity. Do not mistake domain `query` text for the transport wrapper's structured `query`.

Proposed public names: `GenerationInput`, `GenerationOutput`, `GenerationToolAdapter`, `RankingInput`, `RankingOutput`, `RankingToolAdapter`. Output schema version is `1`; these are whole-observation validation views, not new wire payload versions. Existing domain contracts remain `CandidateSet@1` and `CandidateRanking@1`.

## Input routing and compatibility

| Incoming value | Generator receives | Ranker receives |
| --- | --- | --- |
| Direct string | Same string | INVALID_INPUT |
| `{query: string}` only | Same string | INVALID_INPUT |
| Direct domain mapping | Entire mapping, including metadata/outputs/trust extensions | Entire mapping with required metadata/outputs |
| `{query: domain_mapping}` only | Exactly inner mapping | Exactly inner mapping |
| Nested mapping query plus outer siblings | INVALID_INPUT: ambiguous transport | INVALID_INPUT: ambiguous transport |

Only unwrap one level. Nested structured wrappers with siblings are not produced by the inspected call path; reject rather than choose precedence or drop trust signals. A direct ranker domain mapping may contain an ordinary text `query`; its metadata/outputs must not be discarded. For Pydantic input instances inspect explicitly supplied fields plus extras, following docking's `_input_fields` approach; never materialize omitted optional fields. Revalidate instances, including mutated/model-constructed instances.

Generation: supplied `query` must be a string; metadata and outputs must be mappings when non-null. Preserve omitted query (producer uses empty text), omitted/null metadata, empty mappings, and extensions. A supplied metadata `requested_count` uses existing `validate_generation_count`: integer, not bool, 1–10. A supplied temperature is finite int/float, not bool or string; do not invent a temperature range. Text count parsing, authoritative-count precedence, length limit and target-evidence serialization remain in the existing producer/helpers, inside invocation. Do not pre-serialize/drop target evidence or rebuild the request with `build_generation_request` at this boundary. The helpers already bound requests/evidence and reject untrusted evidence; preserve their behavior and errors. Direct producer keyword `mol_count`/`temperature` APIs are unchanged, not added as new registry arguments.

Ranking: require metadata mapping with strict positive integer `docking_top_n` (no new maximum and no limit to candidate count), and outputs mapping with molecules. Accept current sequence shapes (list/tuple, not text) and `{candidates: sequence}` for molecules, including complete CandidateSet dictionaries. Candidate rows require at least one string smiles/canonical_smiles; supplied IDs are strings, with legacy absent/blank IDs still handled by the producer. Do not demand `cand-*` IDs for legacy rows. Evidence slots accept omitted/null, sequences, or the producer's `{data: list}` form. `properties` may be absent/incomplete: this remains the producer's unrankable/no-rankable outcome, not newly mandatory data. Each supplied evidence row has string `smiles`. Canonical joins, duplicate/unknown-candidate rejection and fallback IDs stay in `CandidateRanker`.

Strict boundary rejection of supplied wrong types/nonfinite known numbers is intentional; absent or explicitly nullable optional measurements are not fabricated or newly required. Direct producer calls are unchanged. Domain-invalid but well-typed optional evidence still reaches the scorer and remains unusable, as today.

## Scientific field map

All numbers below reject bool, numeric strings, NaN and infinity. Integers reject floats/bools. Unknown extension keys stay intact and opaque except for the already-owned CandidateSet exact schema and existing target trust inspection.

| Location | Validation and meaning |
| --- | --- |
| Generation raw `data` | Actual producer emits list of `{smiles, model, source: "llm"}`. Retain legacy string rows and `molecules`/`candidates`/`data` list aliases supported by the sanitizer. Rows require string smiles; supplied source/model are strings, not restricted to a newly invented vocabulary. Empty lists and chemically invalid strings remain sanitizer inputs, not new filtering logic. Null data is legal for failure, not success. Validate every supplied known candidate-list alias, without changing the sanitizer's first-nonempty selection. |
| Generation `quality` | Supplied model/output_contract/validation_method text (validation_method may be null); requested_count/actual_count/raw_count/valid_count/unique_count/invalid_count/duplicate_count are nonnegative integers; partial_generation/validated/validation_available are bools. Raw producer requested_count is 1–10 when supplied; serialized failed/unavailable CandidateSet diagnostics may retain zero. No defaults or evidence promotion. |
| Serialized `CandidateSet@1` | Use `CandidateSet.from_dict` for exact fields, IDs, counts, rejected records, status and source indexes. Use existing `_revalidate_candidate_set(..., trusted_checkpoint=False)` for chemical/rejection checks on this asserted canonical form, within the worker; never recreate hashing, RDKit canonicalization or rejection rules. Zero-count failed/unavailable sets remain legal; the input request's 1–10 rule is not imposed on diagnostic zero-count sets. |
| Candidate metadata/provenance | Retain original_smiles, canonical_smiles, source_index, generation_provenance and metadata.source/model unchanged. CandidateSet's JSON restrictions still apply; presence of a source/model label does not establish efficacy or model authenticity. |
| Ranking input properties rows | Known `properties.qed` and `properties.logp` are nullable finite numbers if supplied; missing fields leave the candidate unrankable. Do not require the entire PropertyCalculator descriptor schema. Do not add a QED input range here: current scorer clamps it. Other descriptors are extensions in this consumer contract. |
| Ranking input ADMET rows | Nullable supplied admet mapping; prediction_method text; risk_count/total_endpoints integers; demo_mode/fallback_used nullable bools. Missing summaries remain unavailable. Preserve well-typed invalid counts for the scorer's existing range rejection. Actual ADMET backend section dictionaries do not imply these summaries; never synthesize risks from them. |
| Ranking input activity rows | Supplied success bool; nullable finite normalized_activity/probability; nullable model_provenance mapping. Known model_id/model_path/weights_sha256 are nullable strings, demo_mode/fallback_used nullable bools. No defaults for missing trust flags. Missing/failed/demo/fallback/no-identity evidence stays unused under the existing scorer. Raw pIC50, value and family activity_probability are not converted to normalized_activity. |
| Ranking output `data` | requested_top_n positive int; ranked_candidate_count nonnegative int; top_candidates/ranked_candidates lists of ranked rows; unrankable_candidates list of `{candidate_id, canonical_smiles, reason}` strings. Success requires at least one ranked row. Failures may have no data. Validate supplied data even on failure/partial. |
| Ranked row | Nonempty candidate_id/canonical_smiles, score finite in [0,1], positive integer rank, missing_evidence list of strings, docking_ready_for_preparation bool. Current producer emits true, meaning preparation eligibility, not docking success. |
| `ranking_evidence` | property_score finite [0,1]; admet_score/activity_score nullable [0,1]; weights_used has properties finite [0,1], admet/activity nullable [0,1]; missing_evidence list of strings. Require keys emitted by the producer; retain null optional scores/weights. Never recompute the weighted score. |
| Ranking evidence envelope | Recognized type `deterministic_candidate_ranking` requires method `property_admet_activity_weighted_normalization` and nonnegative integer ranked_candidate_count. Inspect this named slot on every status and in failure snapshots. Other evidence records remain opaque mappings. Generation has no producer-defined scientific evidence slot. |
| Ranking quality | If supplied, output_contract is `CandidateRanking@1`, deterministic is bool, scientific_claim_scope is `candidate_prioritization`. Do not create these keys. Ranking scores are dimensionless prioritization, never binding_energy or kcal/mol. |

Ranking output consistency checks are structural, not rescoring: ranked_candidate_count equals ranked list length; ranks are consecutive from one; ranked canonical identifiers are unique; top_candidates equals the ordered prefix limited by requested_top_n; unrankable canonical identifiers do not overlap ranked rows. Row/evidence missing lists agree and match null optional components/weights. Recognized envelope evidence count agrees with data when both exist. Do not verify a new score formula, tie-break policy or weight-sum tolerance at the adapter.

## Canonicalization and partial states

The adapter must not call the mutating `validate_tool_result` to rewrite raw candidate lists, regenerate formatted tables, trim excess candidates or overwrite quality. Existing downstream validation alone converts raw rows to CandidateSet and derives domain partial/failed/unavailable outcomes. Contract tests must exercise adapter followed by that validator, proving duplicate/invalid/excess handling, IDs, source metadata, rejection records and idempotent checkpoint handling remain identical.

The raw generator reports shortfalls through `quality.partial_generation`, requested_count/actual_count and warnings; it does **not** emit an explicit partial status. Compatibility normalization currently gives this raw success a succeeded envelope before downstream CandidateSet validation derives partial. Do not introduce a false contradiction check or change this ordering in 4C. Preserve explicit partial observations, including `success=False, status=partial`, and never promote failed snapshots. Serialized CandidateSet statuses remain subject to the existing domain gate, not adapter-authored repairs. Known count cross-checks must distinguish raw-list counts from post-sanitization counts; do not compare them as though filtering already occurred.

## Envelope, lifecycle and errors

Validate raw dict or ToolResult before compat and the whole normalized ToolResult afterward. Views cover tool identity, strict bool success, allowed status, data, error, warnings, formatted/message, quality, evidence, artifacts, provenance and nonnegative integer/null elapsed_ms. Raw absent/null compat metadata retains its current defaults; normalized containers/types must be valid. Success cannot coexist with error or terminal failure status; failure cannot claim succeeded. Validate ToolProvenance using its existing parser and tool identity. Validate supplied artifact/error fields, not file existence or asset contents.

Follow only `error.details.raw_result`, at most 16 embedded snapshots with cycle rejection. Check each known scientific payload even if the outer failed result has no data. Preserve accepted errors/details, snapshots, evidence, artifacts, warnings and extensions exactly as compatibility normalization does. Compat sometimes retains raw failure data only in raw_result, and with explicit error.details may not retain it at all: validate the original before that conversion, do not add a new snapshot-retention policy. Non-projecting means no loss beyond existing compat/redaction semantics, not a new serialization format.

Reuse 4A's worker containment locally:

1. In inherited worker/slot/deadline, execute the producer once through `super()._invoke_guarded`.
2. Caller raw_validator sees the untouched raw result first. Our private invalid-output marker catches only our validation errors, not caller exceptions.
3. Validate raw observation/snapshots; let the existing compat executor normalize.
4. Run inherited data normalization/redaction **inside the same worker**, then validate the whole normalized result/snapshots. Never assume redaction preserves known numeric fields.
5. Caller-side `_normalize` attaches elapsed time only; `_validate_output` returns the already checked result, including framework-created no-data errors. No domain traversal/redaction after slot release or timeout. No shared last-payload/proof state between requests.

Contract rejection returns payload-free INVALID_INPUT/INVALID_OUTPUT; never emit Pydantic rejected inputs/errors(), provider payload or arbitrary exception text. RDKit unavailability while revalidating asserted CandidateSets must stay TOOL_UNAVAILABLE, not forged validation success; error details remain payload-free. Existing producer dependency/error reasons and timeout/capacity classifications otherwise survive. Do not alter readiness, retry count, aliases, owners, close or semaphore ownership. Late workers retain their slot until all work completes; no retry on timeout.

The already-existing downstream AgentResultValidator is outside this adapter's lifecycle; 4C does not claim to move the entire workflow's scientific validation under the adapter deadline. No expensive **new adapter** output work may escape that deadline.

## Risks and review gates

- Parent review required before implementation. No product choice is currently needed: strictness is limited to supplied known fields; optional evidence availability, scientific algorithms and successful valid transport are preserved.
- Malformed optional markers such as `demo_mode="false"` were ignored by direct ranker logic; registered execution will reject wrong types, not turn them into trusted evidence. The direct-producer regression remains unchanged; add a separate typed-boundary assertion.
- Private `_revalidate_candidate_set` reuse avoids duplicated science but couples to the current owner. Keep import lazy; test unavailable RDKit. A broader public extraction needs separate approval, not a generic rewrite hidden in 4C.
- Existing registry tests may impersonate these tools with impossible payloads. First establish whether a failing fixture tests valid transport or deliberately tests rejection; never loosen contracts or change its intended assertion just to make it pass. For deliberately contradictory consumer fixtures use an explicit generic LegacyPythonToolAdapter in that test only, preserving downstream assertions and recording why; do not rename it to another scientific tool that will become typed. Material fixture behavior conflicts return to parent.
- Any need to map raw activity into a normalized score, synthesize ADMET summaries, change partial settlement, retain new failure fields, or redefine wrapper precedence is a product change: stop and return it, do not guess.
- The 4A branch is awaiting review; mirror its containment requirement, not its unreviewed module imports. Reconcile only factory wiring if parent later combines packages.

Acceptance and exact tests are in `../plans/2026-09-25-generation-ranking-contracts.md`. This design makes no real-model, potency, docking or experimental-quality claim.
