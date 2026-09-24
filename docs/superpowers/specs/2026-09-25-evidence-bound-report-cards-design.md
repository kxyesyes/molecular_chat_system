# Evidence-bound Chinese report and independent property cards — design

Status: **DESIGN FOR PARENT REVIEW; NOT IMPLEMENTED / NOT APPROVED FOR IMPLEMENTATION.**

Date: 2026-09-25. Scope: historical residual **G1 only**. Companion: [implementation plan](../plans/2026-09-25-evidence-bound-report-cards.md). The accepted [residual audit](../../handoff/historical-residual-disposition.md) remains a historical snapshot, not a claim that G1 is complete.

## 1. Baseline, authority and scope

- Worktree/branch: `historical-residual-disposition` / `codex/historical-residual-disposition`.
- Audit-only commit: `1581f3e`. Requested main `3a682649c3392727f642f33f56bd3f94c7d77707` was merged locally as `e0d73d1`; this is the common design/plan baseline. Local `origin/main` resolved to the requested commit. PR #66 merged/CI 8 passing is parent-supplied status; no network verification here.
- Package 4A / PR #67 is separately reviewed at **`9f3ce84`**, CI pending per parent. Its `analysis_contract.py` is a validation boundary, not property evidence projection. It is **not merged into this design worktree**. Future implementation must combine only the subsequently approved exact head and rerun integration checks; no claim of merged/green status.
- Only the accepted audit was committed before the authorized main merge. These two new design/plan documents remain uncommitted for parent review. No application/test implementation, original dirty-branch edits, source migration, asset access, deployment, push or PR creation.
- In scope: one bounded presentation contract, a read-only evidence snapshot, pure projection, additive WebSocket delivery, Chinese DOM report, four independent property values on existing cards, and regression design.
- Out of scope: G2 optimization/legacy extraction; permanent recovery tests; sandbox diagnosis; new tool activation/RXN; ranking/model algorithms; new evidence ledger or persistence schema; main-model prompts; normal-entry decision activation (package 7); real acceptance/deployment. Parent owns those separately.

## 2. Source findings that constrain the design

1. `src/web/chat_handler.py::_candidate_event_from_observation` strictly accepts CandidateSet@1 observations; `main.js` queues those before `complete` or partial `message`. `molecule_candidates.js::normalize` rejects unknown shape fields. **Adding properties inside that event is not backward-compatible.**
2. `main.js::renderMoleculeCandidates` deliberately refuses `candidate.metadata.properties`. Existing Node tests enforce that refusal and prohibit the old property-fetch fallback.
3. `validators/candidate_alignment.py` preserves independent property rows and candidate IDs, with missing/discarded counts; it does not create card properties. A matching SMILES alone is not proof of source, step or trace.
4. `EvidenceLedger.register_tool_result` binds observation identity, provenance/evidence/artifacts and input identity, but its evidence-ID payload does **not include `result.data` directly**. Static observations may have `provenance.output_digest=None`. A copied evidence ID or freshly computed digest is not enough to authenticate a row. `EvidenceLedger.output_digest` and `WorkflowOrchestrator._input_hash` use **different JSON encodings**; they are not interchangeable.
5. `persistence/scientific_references.py::_source` already provides an owner-scoped, bounded consistent checkpoint snapshot and a `source_version` over run identity + all checkpoints. Current `sources()` returns only generator observations. `ScientificReferenceService.project` compares complete live observations with stored sources before publishing a reference. Property observations need their own read-only snapshot join, not a second publication ledger.
6. `scientific_references.js` checks the **exact** restore response keys; its current confirm request is `{trace_id,presentation_id,revision,ordered_keys}`. Do not append fields to either. ACK means a particular candidate manifest mounted, not that properties or a report were endorsed.
7. Static `target_design_steps` binds properties to `$.outputs.molecules` / `smiles_text`; checkpoint metadata retains `candidate_source`. Ranking binds selected workflow outputs plus `docking_top_n`; `BindingResolver` inserts `[]` for absent optional outputs. Exact replay of that pure binding is required for input-hash verification.
8. `CandidateRanker` already returns actual requested Top-N, ranked/unrankable candidates, scores, missing evidence and weights. It is prioritization, not docking energy or experimental potency. `RunSession` owns authoritative outcomes, counts, ledger and result assembly. The old raw-array Chinese presenter must not be reactivated.

## 3. Options and recommendation

| Option | Compatibility / trade-off | Decision |
|---|---|---|
| **Separate `scientific_report` v1 event**, consumed by a small independent presentation helper | Existing candidate/ACK/complete protocols remain unchanged; old clients ignore it. New UI gains an explicit Chinese report block and evidence-bound card values. Requires one bounded join/cache for the active turn. | **Recommended.** |
| CandidateSet/card event v2 with embedded properties | Would require negotiated schema changes, new normalization, reference identity/restore migration and duplicated generator/property authority. | Reject for G1. |
| Reuse metadata or fetch values on card render | Easy display, but no exact observation binding; could recompute/change values, bypass partials and trust generator metadata. | Reject on scientific grounds. |

The Chinese report is an **additive DOM section beside the existing assistant body**, not a replacement of `final_answer` or `complete.content`. Existing prose remains for compatibility. Avoiding duplicate prose is a later UX decision, not permission to overwrite scientific results. The new report is deterministic Chinese labels plus accepted data, with no main-model summarization. No new browser mockup/service is needed for this contract design.

## 4. Architecture and ownership

```text
accepted terminal execution envelope + owner-scoped checkpoint snapshot
                |                 (read-only, same source_version)
                v
    verify observations / static bindings / row identities
                v
       detached ScientificReport@1 projection
                |
 unchanged candidate events -> new report event -> unchanged terminal frame
                |                    |
   existing mount + ACK       safe Chinese report DOM
                +-----------> independent card-value join
```

Proposed units (future files, not created now):

- `src/agent/contracts/scientific_report.py`: fixed v1 DTO/validation, bounds and display reason enums. No execution methods, selectors, expression language or raw metadata slots.
- `src/agent/presentation/evidence_report.py` plus minimal `__init__.py`: pure `build_evidence_report(snapshot, execution, candidate_events) -> dict | None`, validation and immutable/detached projection. Inputs are bounded detached plain JSON. No global app/provider imports, tool calls, `AgentResult` writes or storage writes.
- `persistence/scientific_references.py::report_snapshot` and one `SQLiteAgentStateStore.get_scientific_report_snapshot` facade: small **read-only** method, reusing `_source` under one transaction. No new table, migration or persistent report namespace.
- `src/web/scientific_report.py`: prepare the snapshot/projection off the event loop and gate delivery. Does not own candidate publication or ACK.
- `src/web/static/js/home/evidence_report.js`: independent strict normalizer, active-turn buffer, pure row lookup, text-node report/property rendering. Does not change selection authority.
- Minimal hooks in `chat_handler.py`, `main.js`, and script include/cache token in `index.html`; no Supervisor/RunSession/ledger/ranker/whole-input changes.

Read-only snapshot returns detached `{run, latest, source_version, presentations}` to trusted server callers only. `run` includes trace, owner identity for internal comparison, workflow version/status/query/skill and bounded metadata. `latest` includes checkpoint IDs, step/tool/version/input hash, parsed output/error and step metadata. `presentations` contains existing views selected by the supplied **server-generated** reference pointers. This internal snapshot is never serialized to the browser; no user/session ID, query, raw error, artifact path or raw metadata is transmitted.

Use existing `_source` limits (256 checkpoints, 4 MiB checkpoint payload budget, 2 MiB run metadata and existing query bound); reject rather than expand them. This G1 snapshot is eligible only for owned completed/succeeded/partial runs. No fallback to an unowned `get_run`, previous run or network service. In a live turn, compare all referenced checkpoint observations to the corresponding `tool_result_sequence` members after removing only the transport `step_id` wrapper. Missing/duplicate/changed step observations cannot supply positive data.

## 5. Source verification and property alignment

### 5.1 Common observation proof

For each positive generator/property/ranker source, require all of:

1. Exact trace/owner/workflow match; current latest checkpoint for that step; no duplicate step/output-key match. Run and execution terminal outcomes agree (`succeeded` storage maps only to `completed` UI).
2. Checkpoint and full live observation equality, including warnings/evidence/artifacts/provenance/status and all data/extensions. Do not compare only IDs/digests or normalize away differences. Serialization must be bounded exact JSON, not `default=str`.
3. `success is True`, status `succeeded` or `partial`, no contradictory error; matching tool/version and input hash/provenance; `demo_mode is False`, `fallback_used is False` after strict provenance deserialization. Missing provenance does not become positive by default.
4. Reconstruct expected evidence ID on a **detached temporary ToolResult and temporary ledger**, then compare with `quality.evidence_id`; where the live execution ledger is available, compare its complete record too. Never call mutation-capable integrity validators on live objects or assign newly prepared provenance to them.
5. Compute `data_digest = EvidenceLedger.output_digest(detached_data)` and `observation_digest = sha256(reference_json(full_observation).encode('utf-8'))`. If recorded `provenance.output_digest` exists, it must equal `data_digest`. If absent, transmit `output_digest_origin='checkpoint_snapshot'`, not `'recorded'`: trust is from full equality to the owner-bound checkpoint and source-version pin plus input proof, **not from computing a hash**. Never backfill recorded provenance.
6. Validate generator shapes/canonical chemistry on a detached object using the current CandidateSet/validator path; require unchanged data/status after validation. Validate properties using PR #67's strict observation view once that dependency is integrated, with a presentation-specific four-descriptor allowlist. This is no scientific recalculation. If validation/RDKit unavailable, refuse positive projection.

Digests are corruption/correlation checks within trusted server execution/storage, **not signatures or protection against a malicious database administrator**. Browser fields never establish trust or get imported into a ledger.

### 5.2 Static property input and row proof (required G1 positive path)

- Resolve `checkpoint.metadata.candidate_source` against one accepted generator observation's `quality.output_key`; require the same accepted generator used in that collection's checkpoint-backed reference. Do not pick the first generator, first canonical match, latest observation across tools, or tool-name-only dictionary.
- Reconstruct the exact property input using existing `BindingResolver.resolve('$.outputs.<output_key>', 'smiles_text', ..., outputs)` on the validated full generator set. Compare `WorkflowOrchestrator._input_hash(reconstructed_input)` with the **property checkpoint input hash** and recorded provenance input digest. Do not use the output-digest algorithm for this comparison.
- `quality.candidate_alignment` must be consistent with accepted data and generator IDs: source/aligned counts, unique matched rows, missing candidate IDs and discarded count. A partial source may provide valid matched rows but must retain partial status/warnings. Missing or inconsistent alignment proof => unavailable values, not a new alignment pass silently repairing it.
- For each actual accepted `property_calculator` row, validate RDKit canonical equivalence of `row.smiles` with the selected `CandidateRecord.canonical_smiles`, and exact `row.candidate_id` equality. Bind **(trace_id, generator checkpoint ID, candidate ID, canonical SMILES)**; candidate ID alone can repeat across sets/runs.
- Keep original accepted row index; include a row digest over the entire accepted row (not just the four visible numbers) and source observation/data digests. Ignore no duplicate/foreign row silently: any contradiction in the purported aligned source invalidates that source's positive projection. A generator with two otherwise eligible property observations has `ambiguous_source` unless authoritative bindings identify exactly one; never last-write-wins.
- Values come only from that row's `properties.{molecular_weight,logp,tpsa,qed}`. Exact finite `int/float`, never bool/coerced string; QED within [0,1], MW > 0, TPSA >= 0; LogP may be negative. Do not clamp, invent 0, copy a neighboring row, or use generator `metadata.properties`.
- v1 displays these four fields only. Use existing display conventions (MW Da, TPSA Å², LogP/QED dimensionless); values are the producer's recorded rounded numbers, not recalculated. Display method is the known `property_calculator` RDKit descriptor producer; tool version is recorded, RDKit package version is **null / 未记录** if absent. Do not invent model/backend versions.

### 5.3 No-store, stale and unavailable behavior

If no owned consistent snapshot or no accepted reference is available, existing candidates may still display, **without numeric card enrichment**. A report can retain verified source-level summaries from a valid snapshot even when no collection mounted; never create additional cards from it. No snapshot => no v1 event; existing truthful failure/partial/content paths remain unchanged.

At emission, re-read/check the current source version against the snapshot and all referenced published views (including TTL and exact ordered keys). If changed, discard the proposed report; do not retry by mixing sources. No new storage writes other than the already-existing candidate publication/confirmation workflow. During projection, checkpoint/run/ledger/result bytes must remain unchanged.

## 6. Concrete additive transport: ScientificReport@1

One `scientific_report` event per accepted trace per active turn, sent after existing `molecule_candidates` events and before `complete` (completed) or terminal `message` (partial). No new fields in those old frames. The UI ignores unknown schema versions **for this event only**. Future versions require their own parser; never reinterpret unknown version fields as v1.

Exact top-level fields (all required, nullable fields explicitly indicated):

| Field | v1 definition |
|---|---|
| `type`, `schema_version` | literal `scientific_report`, literal string `1` |
| `trace_id`, `source_version`, `projection_id` | existing trace; 64-lowerhex source snapshot digest; 64-lowerhex SHA256 of `reference_json(event without projection_id)` |
| `run_status` | `completed` or `partial`, directly normalized from authoritative outcome; never recomputed from available rows |
| `target` | `{label: string|null, origin: 'workflow_plan'|'not_provided'}`; bounded accepted plan label, not an efficacy claim |
| `sources` | array of SourceV1 records below; no arbitrary map/metadata |
| `generations` | array of GenerationV1 records below, in observed step order |
| `collections` | array `{reference, generator_observation_id}`; reference is **exact current** `{trace_id,presentation_id,revision,ordered_keys}` |
| `property_rows` | array of PropertyRowV1 records below |
| `steps` | array `{step_id,tool_name,status,source_observation_id,reason_code,message}`; source ID nullable; status from observation/skipped list or literal `unknown` |
| `ranking` | RankingV1 record below (including explicit unavailable form) |
| `warnings`, `omitted` | array of bounded safe strings; fixed `{generations,steps,property_rows,ranking_rows,warnings}` nonnegative integer display-omission counts |

**SourceV1** exact fields:

`{observation_id,step_id,tool_name,tool_version,evidence_id,status,input_digest,data_digest,observation_digest,output_digest_origin,model_name,model_version,method,backend_version}`.

First five strings are bounded identities; status succeeded/partial for positive sources; three digests lowerhex64; origin `recorded|checkpoint_snapshot`; last four provenance strings nullable. `method` nullable except known property producer display label `rdkit_descriptors`. A missing model/version displays 未记录; not a fabricated positive. Sources contain no filesystem paths or arbitrary extra scientific claims.

**GenerationV1** exact fields:

`{source_observation_id,status,requested_count,valid_count,unique_count,invalid_count,duplicate_count,displayed_count}`.

Copy contract counts from the validated generator CandidateSet; do not infer valid/unique counts from visible cards. `displayed_count` is server expected display subset, clearly labelled 显示数 (UI may mark fewer mounted); never overwrite requested/actual. Generation status is exactly `succeeded|partial|unknown`. For `unknown`, source ID and all counts are null. Otherwise source ID is required and all counts are safe integers consistent with the validated contract. Failed/skipped generation remains in `steps`, not invented as a successful empty set.

**PropertyRowV1** exact fields:

`{generator_observation_id,candidate_id,canonical_smiles,source_observation_id,source_row_index,row_digest,state,reason_code,values}`.

- `state`: `available|partial|not_provided|unavailable|invalid|ambiguous`.
- `values`: exact `{molecular_weight,logp,tpsa,qed}`, each finite number or null. No supplied unit strings; UI owns fixed labels.
- Positive values require a positive SourceV1 plus all §5 proof; `available` requires all four values and a succeeded source; a partial source/row stays `partial` even if all four values exist. With strict four-field validation, invalid/missing field(s) produce null for **all** four in v1, avoiding scientific repair of a malformed source.
- For all other states values are all null. Source ID/index/digest are nullable only when no accepted row exists; no numeric values are legal in that case. `source_row_index` is 0-based accepted data index; `row_digest=EvidenceLedger.output_digest(full_row)`.
- Reason enum: `none|missing_source|source_unavailable|source_failed|source_mismatch|input_unverifiable|alignment_missing|row_missing|invalid_value|ambiguous_source|stale_source|candidate_not_displayed|partial_source|unsupported_binding`. Fixed Chinese translations in the UI; never raw error text as markup.

**RankingV1** exact fields:

`{state,reason_code,source_observation_id,generator_observation_id,requested_top_n,ranked_candidate_count,top_candidates,unrankable_candidates}`.

State `available|partial|not_provided|unavailable|invalid|ambiguous`; nullable source/generator/counts for nonpositive cases; arrays empty in those cases. It uses the same bounded reason enum as property rows; a ranker failure such as no rankable candidates maps to `source_failed` plus the preserved failed step, not a new unbounded reason grammar. Each top row is exact `{candidate_id,canonical_smiles,score,missing_evidence,ranking_evidence}`; `ranking_evidence` is exact `{property_score,admet_score,activity_score,weights_used,missing_evidence}`, with weights exact `{properties,admet,activity}`. Scores and weights must be finite numbers in [0,1] or null where the ranker allows missing evidence; `score` and `property_score` are required numeric for a positive row. No defaults/recalculation. Missing-evidence arrays contain only `admet|activity` without duplicates. Each unrankable row is exact `{candidate_id,canonical_smiles,reason}` from the ranker, rendered as text.

Reject malformed whole events (unknown keys, duplicate identities, cross-references outside arrays, contradictory numeric/state combinations, exotic/accessor/prototype objects, nonfinite values, invalid Unicode, cycles, budgets). All indexes/counts must be exact nonnegative safe integers, never booleans; identity fields are never truncated into a different identity. A valid event may contain explicit unavailable/partial rows. Do not silently accept a malformed event after deleting its bad fields.

The server computes/verifies the canonical projection/data/row hashes. The browser checks their syntax and exact source/row/reference correlations, not a second cross-language serialization of scientific floats; Python `1.0` versus JavaScript `1` must not create a spurious digest algorithm. It has no full raw observation with which to recompute row/source hashes, and a browser-presented digest is never scientific proof. Server-origin transport plus the server's source verification is the trust boundary; v1 adds no signature scheme.

Before bounding display strings, scan the original string for secret material using the existing redaction/safe-text utilities; omit sensitive diagnostics rather than hide a secret beyond a truncation boundary. Reject sensitive identity/source labels rather than rewrite them to apparently matching identities. Only strings may pass through this display sanitizer; scientific numeric fields are validated separately and never redacted/coerced into substitute numbers. Do not log rejected raw payloads.

### Bounds

Wire JSON <= 128 KiB UTF-8; depth <= 12; nodes <= 8192; max 24 SourceV1, 8 generations/collections, 32 property rows/top rows/unrankable rows/steps, 32 warnings. Identity <=128 chars; canonical SMILES <=8192; target <=128; diagnostic text/warnings <=256; provenance label <=128. Apply UTF-8 byte and structure bounds **before** expensive copying/hash/DOM work (server and JS). Any invalid scientific source is unavailable; budgets never turn it into success. Eligible display subsets follow the existing candidate lifecycle's exact ordered manifest; excessive sections are omitted with counts and explicit 中文“展示已截断”, while true generation/request/ranking counts remain unchanged. If a consistent subset cannot fit, omit the entire new event, leaving old rendering intact.

## 7. Chinese report, actual Top-N and outcomes

DOM section title: `靶点候选设计报告` (target text separately appended). Required sections: `执行结果`, `候选生成与来源`, `独立性质证据`, `实际 Top-N 排序`, `未完成步骤与限制`, `科学解释边界`.

- Header is `部分完成` whenever the authoritative outcome is partial, even if every displayed property is valid. A missing source/step is 未提供/未知, never 完成. Use fixed translations for every actual `ObservationStatus` plus `skipped`, `skipped_precondition`, `unknown`; keep raw status as a bounded text label if useful.
- Target comes from server-produced `workflow_plan.metadata.target_hint`; missing/ambiguous target is 未提供靶点, not model inference. A target label does not prove target engagement. Preserve generation model/version and requested/actual/rejected counts from sources. Do not count rendered cards as total generated success.
- Top-N must be from accepted `candidate_ranker` `CandidateRanking@1`, not a new sort in the report. Reconstruct the **actual static ranker binding** using checkpoint metadata's `workflow_output_keys`, optional keys and metadata keys, accepted full upstream outputs, original bound query and recorded `docking_top_n`; compare its checkpoint input hash. Use existing `BindingResolver` including its absent-optional `[]` behavior. Missing query/bindings after redaction => `input_unverifiable`, no ranking scores.
- Require ranker's full data equality to its checkpoint, evidence/provenance proof, unique rows and generator identities; `top_candidates` must be the prefix of actual `ranked_candidates` of length `min(requested_top_n,ranked_candidate_count)`. Copy returned order/scores/weights/missing evidence; **do not execute CandidateRanker, recompute scores, guess omitted rank positions or hide ties/unrankable rows**. Display actual Top-N and unavailable reasons, with truncation label if display-bound.
- Ranker success with missing ADMET/activity stays its recorded success status, but the display explicitly states evidence limitations; display `ranking.state=partial` for incomplete evidence coverage without changing the tool/run outcome. Conversely no coverage or unknown ranker is not a successful ranking. `docking_ready_for_preparation` is not rendered as docking performed.
- The v1 report adds no ADMET/activity prediction numbers or docking energies. It reports those steps' actual outcome/provenance and limitations. Existing independent tool output remains intact. Fixed caveat: `计算生成与描述符/排序仅用于候选研究优先级，不代表已验证抑制剂、实验活性或临床结论；对接是否执行以实际工具证据为准。` Never hard-code “未执行对接” solely from the old workflow shape.

## 8. Frontend lifecycle, safe DOM and ACK compatibility

1. Start/clear/disconnect/new-request handlers reset the new report buffer in parallel with existing candidate lifecycle. Buffer at most one report for one active trace. Trace must match the run observed through this request's existing agent events/candidate events; a report alone cannot adopt a new trace. If multiple traces are ambiguous, suppress report just as candidate draining refuses mixed runs.
2. Buffer only after strict normalization; reject late events after terminal, events before an active request, and a second differing projection for the same turn. Exact duplicate is idempotent. Never retain raw or normalized reports in local/session storage.
3. At existing terminal render, drain candidate payloads normally. Render the Chinese report through `document.createElement` + `textContent`/`appendChild` only; no `innerHTML`, markdown parser, URL/link creation or inline handler from source text. This avoids relying on the legacy assistant formatter for the new report. Numeric formatting accepts only the validated finite values and preserves reported precision; no `|| 0`.
4. Mount existing cards/manifest unchanged. Enrich only cards actually mounted under an exact matching reference pointer/revision/ordered_keys and generator observation ID + candidate ID + canonical SMILES. A mismatched or deduped-away collection cannot receive another collection's values. No rows create cards or change order/counts. On subsequent pagination renders within that mounted collection, use its immutable validated report subset, not candidate metadata/global previous-report state.
5. Wrap enrichment per card so a rendering failure cannot prevent the existing renderer returning `{mounted,ordered_keys}` and cannot cause a false ACK. Property failure leaves the current explicit 未提供 message. Report-only mount never calls confirm, sets selection or substitutes ordered keys. Property values are a snapshot of computation, **not dependent on ACK success**; selection remains disabled until existing candidate ACK succeeds. Mark reference unavailable separately if ACK fails.
6. Neither confirm nor restore HTTP request/response shapes change. **v1 live enrichment is not restored after refresh**: the existing reference-only restore renders structures with a clear `本次恢复未加载独立性质证据`/unprovided property state. It does not reuse local cached numbers, refetch models/properties, or append fields to exact restore payloads. A future independently authorized report-restore endpoint/version is outside this G1.
7. Historical rendered reports are labelled `本次结果快照`; new requests/clear invalidate only live pending report state, and clearing chat removes DOM as today. Snapshot values do not authorize future tool input. Server revalidates scientific references in the existing continuation path.

Failure isolation: report snapshot/projection/normalization/DOM failure must not change authoritative outcome, lose old completion, skip candidate delivery/ACK, invoke fallback models, or expose raw exception/secret text. Emit no new event when projection fails; keep existing candidate property refusal and terminal path. Successful report emission itself is not a scientific pass.

## 9. Package 7 coordination without activation

Package 7 can call the same Web presentation seam after its normal decision entry reaches an accepted terminal outcome; do not fork a report framework or wire normal-entry decisions in G1. Static candidate-source binding is the positive G1 contract.

For future dynamic properties, permit positive mapping only when the finalized accepted observation already carries intact `input_evidence_ids`, `request_input_digest`, `operation_key`, full output digest and exact source identity, with the same accepted candidate input and per-row proof. The existing decision integrity verifier **mutates on failure**, so the presentation code must not call it on live Session data. Package 7 must provide detached, already verified observations or a read-only check. Until its integration fixture proves that linkage, G1 returns `unsupported_binding`/no positive properties/ranking for that path; it does not infer a static `candidate_source`, add provenance, approve a claim or change decision routing. Browser projection DTOs are never accepted as decision evidence/input references.

## 10. Mandatory acceptance / parent review gates

- All original source files remain untouched. Existing original test **intent** is retained via current-protocol tests: canonical dedupe, no fragment/Markdown-derived cards, actual numeric descriptors, Chinese target-specific caveat (PDE5A and EGFR), requested/actual counts, partial/unavailable steps. Do not copy obsolete `complete.molecules` assertions or execute the dirty tree. The original global-extractor tests remain G2; retain the current whole-input suites unchanged.
- Current tests mandatory: `test_candidate_contracts`, `test_generated_candidate_validation`, `test_candidate_alignment`, `test_candidate_ranker`, `test_property_report_boundaries`, `test_explicit_molecular_input`, `test_admet_whole_input`, `test_reverse_target_complete_input`, `test_chat_handler_agent_events`, `test_chat_handler_partial_results`, all `test_scientific_reference_*` suites with normal environment-specific skips disclosed; PR #67 `test_analysis_contract` after approved integration. Existing Node task-panel/completion/structured-molecule/reference suites must continue passing, including refusal of metadata properties. The unchanged legacy no-property message must remain present when no new independently verified event is supplied; additive positive cases cannot weaken that original negative test.
- New unit tests: complete snapshot equality/input/output/row digests; optional static output digest origin; forged/stale evidence ID; same candidate in two traces/collections; wrong owner; foreign/duplicate/missing/partial/failed/unavailable property rows; booleans/NaN/Infinity/strings; missing versions; no mutation before/after and after caller edits returned DTO; actual ranker order/Top-N/limits; malicious text, getters/prototypes, cycles/UTF-8 budgets; no secret/path leakage.
- New end-to-end **frame test** must use real local WorkflowExecutor/RunSession + actual RDKit PropertyCalculator + CandidateRanker + SQLite temporary store, deterministic offline generator/target fixtures, normal ChatHandler WebSocket processing and actual reference confirmation API. Capture candidate event -> new report -> original complete/partial message order; assert matching compound row keys/digests, actual numbers and partial labels. Feed those same captured frames to the real JS normalizer/rendering harness and compare mounted values/ordered ACK keys; no live server/provider or patched-away projection. A separate failed terminal case emits no positive report/cards; another corrupt report does not lose completion. This is offline execution evidence, not model-quality/real scientific acceptance.
- Parent must approve: separate live-only event, additive report beside existing body, strict snapshot-required numeric enrichment and explicit no-numeric-restore v1 boundary. Implementation is blocked on this design approval and the later exact dependency integration/review gate, not on a request to enable models or read assets.

## 11. This turn's verification and self-review

Only source/docs/Git metadata were inspected; no tests or runtime services were run for the unimplemented design. Prior audit's 425/Node/probe results remain historical results on `ecd6cca`, not results for this proposal. Checked schema cross-references, status monotonicity, static-vs-dynamic proof, read-only boundaries, strict ACK/restore compatibility, original-test intent coverage and exact plan tasks. Original 13/13 scoped fingerprints still match the audit; original HEAD remains `f377443` and its dirty status is retained. Both new documents' relative links resolve and whitespace checks pass. No feature-completion claim is made.
