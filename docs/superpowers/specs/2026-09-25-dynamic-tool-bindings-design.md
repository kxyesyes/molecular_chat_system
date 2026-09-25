# P7-B dynamic scientific tool bindings — bounded design

Date: 2026-09-25. Status: **G0 / B-D0 design and written Reverse/RAG receipt amendment parent-approved, including the two existing RAG files' narrow interfaces/future allowlist. Local docs-only freeze authorized. Dependency/implementation gates remain closed; stable mmap/source ownership still requires actual proof. Not P7 completion.**

2026-09-26 current status: **§11's written approval is recorded in §11.7; §12 re-pins the reviewed ordinary source and proposes the first pure profile-view TDD slice. Neither is a code release.** The current worktree is `dynamic-bindings-b1`, branch `codex/dynamic-bindings-b1`, starting HEAD `d672497a8c7b812378dddad91e195de3d04bbb65`. Only these two B documents may change now; no code, tests, staging, commit or push. PR81 is not confirmed merged and must not be described as landed.

Historical worktree: `dynamic-tool-bindings-plan`; branch: `codex/dynamic-tool-bindings-plan`; original source base: `5db56b0c79ac30da1ba4646c2c567e7d2dd71cc5` (Planner #71); prior docs freeze: `74739aa72c36af27e1561a21359e87521860897b`. Only this file and `docs/superpowers/plans/2026-09-25-dynamic-tool-bindings.md` may be written in the current draft; no staging or commit. No implementation, tests, compilation, network, environment/credential or scientific-asset inspection, merge or push. Authority remains `AGENTS.md`, `docs/PROJECT_STANDARDS.md` and the narrower request. Read-only source/review documents are interface evidence, not live results.

## 1. Approved bounded decision and alternatives

Parent approves **one binding design with two functional implementation milestones**, B1 then B2, reusing `ModelDecisionLoop`, dynamic `WorkflowRunSession`, `BindingResolver`, registry adapters, scientific validators, CandidateSet and CandidateRanker. Each milestone includes admission, binding, acceptance, continuation and integration tests, not a chain of infrastructure-only layers. Revised generation policy: one logical invocation per root-request slot, preserving existing at-most-five internal replenishment rounds. Parent retains implementation/release decisions; neither milestone is started here.

| Option | Benefit / cost | Decision |
|---|---|---|
| B1 molecular/ADMET/reverse/local-RAG; B2 generation/ranking and dynamic report proof | Small reference protocol; single execution engine; B1 can progress independently of unfinished generation contracts | Parent-approved revised choice; neither milestone alone claims all requested final workflows |
| One B change exposing all reviewed adapters at once | Fewer integration checkpoints but combines source trust, stochastic lifetime and multi-input acceptance risks | Bounded alternative only if parent prefers one larger review |
| Pass arbitrary kwargs, `$.workflow` or a static Planner plan as model actions | Appears to cover many tools but permits fabricated inputs and loses dynamic control | Reject: no new DSL/framework or static workflow disguised as decisions |

B1 is not permission to indefinitely defer B2: design and test seams for both are specified now. A capability whose domain evidence is not supportable stays explicitly blocked, not successfully completed through a known subset. Runtime preconditions constrain valid data use; they do not prescribe an action order. After each actual observation the model chooses tool/clarify/finish again.

## 2. Exact read-only dependency observations

### Historical source observations (superseded status where noted below)

| Dependency | Inspected revision / working state | Consequence |
|---|---|---|
| Main baseline | `5db56b0c79ac30da1ba4646c2c567e7d2dd71cc5` | Four-tool decision path, typed analysis/target/reverse/RAG/activity contracts available; not normal decision `/ws` activation |
| 4C `generation-ranking-contracts` | `5e600a6b9e371f1fcc537368b1bf8cfe4d17f6cb`; read actual `generation_ranking_contract.py`, factory diff and spec/plan | Local reviewed generation/ranking adapters, **not landed in this base**. Plan records parent-confirmed SPEC/QUALITY and post-integration full 6676 passed / 2 skipped; these are recorded results, not runs by this task. Later G2 integration/publication still parent-owned |
| G1 `historical-residual-disposition` | HEAD `bf4a2cf55ffa34e96c3821d4e0c6ca5bdcf05422`, with modified report DTO/JS/tests/docs | Actual `2026-09-25-g1-evidence-report-freeze.md` says initial SPEC returned two P2s; local uncommitted corrections await re-review. Read actual projector/Web adapter: explicitly static-only, dynamic observations get `unsupported_binding`. Not landed or dynamic-report acceptance |
| A1 `web-model-decision-entry` | HEAD `aed3571e9e1a25f8c757dc937969ad04a424b98c`, modified admission/test/spec/plan during inspection | SPEC4 repairs are in progress, not approved/landed. Working notes narrow chat to covered greetings/explanations; do not assume arbitrary ordinary chat already works |
| A2 `web-decision-runtime-plan` | Frozen docs `ff02afc98817701bcdf451e165fa1a972860ee95` | Parent approved G0 bounded design only; G1 requires corrected/approved/landed A1 and separate parent release. No runtime implementation in base |
| G2 optimization input | `generator-optimization-input`, `548baca8f1c59acb6e9dbe59a7c8787b722129cd` | Source/docs improve whole-input seed fidelity; not in this base. Does not validate output core retention or guarantee novel candidates |

### Historical parent coordination update (reported, not independently fetched)

- G2 PR #72 is merged into main as `27170d95`. It is therefore a parent-reported landed dependency, **not code present in this design's `5db56b0` baseline**. Later implementation must use that merge and inspect its actual integrated behavior; no merge here.
- 4C post-P2 full result is 6676 passed / 2 skipped. After joint 525 passed + 162 subtests, 4C is now PR #73 at `beaf9cf4a57cf7c6045f5980c175c217fd42e96f`, CI pending. This supersedes the earlier local-freeze publication context; **4C is not yet landed**, and neither the prior full nor joint run is a fresh result from this sidecar.
- A1 SPEC4 repair is now `48d8356`, under recheck. The table records the earlier read-only dirty snapshot; this newer repair is not accepted as reviewed/landed merely because it has a commit. No implementation gate has opened.

Never copy other worktrees' patches or treat old doc headers as current approval. Before implementation pin landed SHAs, review decisions and relevant interfaces again. A1's **four** parent-confirmed SPEC4 cases are: `解释 logP\n对接这个分子` with tools false (actual newline); `计算 CCO 的分子量和熔点`; `计算性质（禁用 property_calculator）；SMILES: CCO`; `分析两种化合物的性质；SMILES: CCO`. The last is **one reported count case**, not two. B admission must preserve these regressions; broader supported vocabulary must not restore fallback-to-chat or partial obligation loss.

G1 evidence is genuinely inspected, not inferred from its name: its report validates stored/live full observation equality, source/presentation identity and static input hashes. Its current changes also reject forged step/source status and render actual `weights_used`. These are reusable protections, not an already available dynamic binding projector. Its deterministic frame→DOM→reference HTTP tests are not real generation, live browser or ordinary decision route proof.

### Per-invocation receipt amendment: newer source-only inspection

The dependency table and coordination bullets above are historical observations, not a current PR/landing audit. For the amendment, read `current-real-acceptance` HEAD `0d5ac1e4c79b51bbe76fbdd2e78f05ae1e818209`; local `git diff --name-only 5ad08ac -- src` returned no differences, consistent with parent's stated source equivalence to main `5ad08ac`. No fetch, environment/asset read, import or test occurred; that inspection was performed from docs HEAD `74739aa`. It updates the reverse/RAG source facts below only, not implementation release.

Gauss's two important precheck findings are confirmed by source inspection. `src/reverse_target/predictor.py::_load_or_compute_popcounts` (128) accepts a cached array on row count alone, while `batch_tanimoto_similarity` uses its values in the denominator. `_combine_similarity_scores` (150) reads/resolves/clamps Morgan weight at each call. Thus TSV/fingerprint hashes alone omit both an actually consumed cache and effective scoring configuration. `src/rag/retrieval.py::search_molecular_index` (12) skips invalid labels/non-finite scores and can return `[]` after dropping everything; `RAGSearchTool.execute` then reports successful no-hit. `RAGSystem` has mutable source/index/manifest fields, but no same-invocation receipt or `metadata` API proving this distinction. The previous plan's phrase "via existing service metadata" was insufficient and is removed.

### Historical parent-reported dependency/approval record for the receipt freeze

- 4C PR #73 **merged as `0295e996`**; the earlier PR-head/CI-pending statements above are historical.
- Main PR #76 is **`5ad08ac`**. This does not rebase or merge the docs branch's original source base.
- G1 PR #77, head **`f48039e`**, is **in CI and not landed**; do not count it as available dynamic-report proof.
- A1's current **four-file diff is blob-identical to `9d1305e` and has passed both reviews**, but **is not landed**. Preserve the exact reviewed content/landing gate; earlier SPEC4-under-recheck status is historical, not current.
- A2 revised docs are committed as **`15a903d9`**, with **no implementation**. They supersede the earlier docs-freeze status, not the runtime dependency gate.
- Parent has read and approved the written Reverse/RAG amendment and future allowlist, including narrow `src/rag/retrieval.py` / `src/rag/service.py` interfaces. Approval does **not** establish a safe snapshot: stable mmap/loaded-source ownership remains an actual implementation gate, and an unprovable case must fail closed/report scope before wider work.

These are parent-supplied coordination facts, not fresh network/CI checks by this sidecar. G2's reported merge `27170d95` remains recorded. Re-pin exact integrated revisions before authorized implementation; no production defaults, implementation or P7 completion are released by this docs freeze.

## 3. Actual code boundaries and gaps

| Source/symbol | Current fact | Required B delta |
|---|---|---|
| `contracts/decision.py::ToolDecision` | Outer v1 has JSON `arguments`; shape validation alone is not authorization | Keep outer protocol; small closed tool-specific argument grammar at runtime and in catalog |
| `harness/decision_inputs.py::resolve_decision_input` | Exactly one string `input_ref`; `user` or same-run molecular observation; no generator/ranker/target-record roles | Resolve new closed roles from sealed observations, never literal model payloads |
| `harness/decision_policy.py::authorized_catalog` | `INITIAL_TOOLS` intersects caller allowlist; requires idempotent/no-side-effects; chat/tools-off empties catalog | Explicit server profile, independent RAG permission, per-action effective execution policy; do not equate registration with permission |
| `decision_loop.py::execute` | Key hashes tool/version/input, dynamic step `required=False`, no `candidate_source` or target preconditions | Include source-role identity/proof in key; supply trusted step metadata/preconditions; required coverage stays in immutable acceptance |
| `contracts/task_requirements.py`, `decision_requirements.py` | v1 property/likeness count/SMILES/7 metrics; no cross-observation joins | Versioned closed obligations for sources, batches, generation and ranking; whole-request deterministic acceptance |
| `planning/bindings.py::BindingResolver` | Only identity/smiles_text and finite request/output/workflow selectors. `smiles_text` recognizes `smiles`, not canonical-only CandidateRecord | Server constructs bounded identity projection for canonical structures, then reuses resolver; model never writes selectors/paths |
| `runtime/run_session.py::execute_step` | Domain validation then candidate alignment then ledger/full seal; alignment keyed by trusted `candidate_source`; running checkpoint before tool attempt | Reuse ordering and journal, add only closed dynamic proof metadata. No parallel executor/store |
| `validators/semantic_inputs.py` | Target-evidence precondition verifies serializable evidence, not full user-target equality by itself | Preflight exact selected-record/user constraint matching plus existing semantic validator |
| `tooling/factory.py` | Generator labeled idempotent, adapter attempts generally one; target attempts two | Do not blindly alter shared factory flags. Dynamic generation needs effective non-replayable request view |
| `tools/llm_molecular_generator.py` | `_generate_with_retry` defaults to five bounded rounds; optimization may prepend seed; emitted `smiles` is canonicalized | Preserve replenishment within one logical invocation; count actual backend attempts and prevent outer logical replay. No claim to recover raw model spelling or novelty |
| `tools/candidate_ranker.py` | Canonical joins, property QED/logP required; ADMET risk summaries and normalized activity optional; score/weights computed by ranker | Seal all source roles and validate coverage before call; no pIC50 conversion or invented risk summaries |
| `tools/reverse_target_tool.py` | Validates batch but uses first molecule; hardcoded threshold 0.6/top_k 10; normalizes target identifiers | B1 only one molecular subject for reverse; multi-subject/threshold/top-K customization blocked, not silently reduced |
| `tools/rag_search_tool.py`, `tooling/rag_contract.py` | Injected manifest-validated local molecular retrieval; query-only typed input; tool default k=3 | Local retrieval only, no PubMed/web literature promise; requested limits beyond supported contract clarify/reject |

Scientific source reads were not executed. Adapter validation proves a shape, not source authenticity: current `TargetEvidenceValidator` accepts similarity/identity signals without proving a dataset manifest; ADMET validator's method field is not validation of every endpoint. Stronger binding acceptance must not manufacture trust flags to bridge these gaps.

## 4. Tool scope, permissions and unsupported requests

B1 profile contains the initial four plus `admet_predictor`, `reverse_target_predictor`, `rag_search`; B2 adds `llm_molecular_generator` and `candidate_ranker`. Profiles are immutable server allowlists intersected with admitted obligations and existing owners, not new browser modes. Keep default startup unchanged; the proposed §11 amendment uses the reviewed semantic async factory rather than synchronous semantic construction. No separate endpoint or settings/key store. Parent reviews how B's server profile is injected into the eventual ordinary runtime; a browser cannot select it.

- `scientific_tools=false` denies calculation/generation/ranking/reverse; `rag=false` denies actual retrieval. Parent-approved rule: an explicit local retrieval request with tools false and RAG true allows only `rag_search`; the two permissions remain separate. Chat never gains tools just because RAG defaults true.
- Ordinary `enable_rag=true` chat means permission/preference, not `retrieval_performed`. Actual retrieval is a model-chosen action on an admitted local-retrieval request and yields a source-backed observation. Do not auto-run legacy RAG or inject retrieved instructions as authority.
- Preserve A1 whole-request coverage. Known tools/metrics are provisional until every action, prohibition, count, target and qualifier is accounted for. Unsupported endpoint, unknown melting point, conflicting count, novel-compound promise, arbitrary scaffold phrase, custom scoring or docking requirement prevents whole-request scientific completion.
- Plain "ADMET" is not permission to claim all ADME/toxicity endpoints. Admit a bounded supported readout with explicit method/endpoint scope, or ask for clarification. Any unresolved false-label/unknown-evidence residual blocks the affected claim/capability; missing values stay unavailable, not zero/low risk.
- "综合评估/综合排序" without supported score semantics clarifies. A supported alternative is explicitly property-based prioritization with optional usable ADMET/activity contributions and visible missing-evidence labels. If the user requires those contributions, they are mandatory acceptance conditions.
- Exclusions that conflict with mandatory evidence clarify/reject; optional omitted evidence is recorded as absent, never interpreted as a measured good result.

## 5. Small reference grammar, no arbitrary arguments

Keep outer decision version `1`; add a server binding profile/revision in fingerprints and waiting snapshots. Closed strict argument views (new `contracts/decision_bindings.py`) reject unknown/duplicate roles, nonstrings, excessive IDs and wrong tool combinations **before dispatch**. At most five evidence references per action (candidate source + three analysis roles + target where relevant); all are bounded existing ID types. A legacy no-profile caller retains the old input_ref-only semantics.

| Consumer | Permitted arguments | Server action |
|---|---|---|
| Initial molecular tools / ADMET | `{"input_ref":"user"}` or `{"input_ref":"<molecular-or-candidate-evidence>"}` | Whole user/confirmed selection, or entire sealed batch; no model-authored SMILES |
| Reverse | `{"input_ref":"user"}` or single-molecule eligible evidence | Enforce exactly one input subject before its first-of-batch producer |
| RAG | `{"input_ref":"user"}` | Exact admitted local search text; no rewritten query, external URL fetch or arbitrary k |
| Target search | Existing user input, or `{"input_ref":"<reverse-evidence>","record_ref":"<server-record-handle>"}` | Lookup one observed candidate target record; never a model-authored target string |
| Generation | `{"input_ref":"user"}` for explicitly untargeted request; add `"target_ref":"<target-search-evidence>"` when targeted | Original admitted request/count/seed plus verified matching target evidence and trust envelope |
| Ranker | `{"input_ref":"<candidate-set-evidence>","evidence_refs":{"properties":"<eid>","admet":"<eid>","activity":"<eid>"}}` | Properties required; optional roles may be omitted only if obligations permit. Null/literal arrays/weights/top-N forbidden |

`record_ref` is an opaque deterministic server handle bound to one sealed reverse row, not a free identifier/path/index supplied as target authority. Expose eligible handles in a bounded decision observation descriptor beside the unchanged result; do not add arbitrary metadata to CandidateSet. Handle maps derive from evidence ID + row identity + canonical record digest and are regenerated/validated identically on continuation, not taken from model history. Rank-source role names are fixed. Target search evidence used for generation must resolve to exactly one eligible requested record; multiple matches clarify instead of implicit first-hit selection.

LLM still chooses the next action and eligible record. User constraints such as "highest similarity" restrict eligible records using actual tool scores and defined tie handling; unsupported selection language clarifies. A reverse hypothesis never establishes a confirmed active target or supplied docking structure. Lookup consumes only source-backed fields actually accepted by `TargetSearchInput` (`gene_symbol`, `uniprot_id`, etc.); `target_chembl_id` or `name-sha256` is not automatically a supported lookup identifier. Missing unambiguous mapping is blocked, not an external lookup guess.

## 6. Binding resolution and full data lineage

Introduce a small internal immutable `ResolvedDecisionInput` in the new binding contract module: `input_data`, ordered `input_evidence_ids`, `binding_proof`, `candidate_source`, `preconditions`, and execution policy ID. It is server-created, not a model schema or alternate task representation. Helpers in `harness/decision_bindings.py` resolve it. Preserve the existing no-profile `resolve_decision_input` two-value return contract; B loop/replay use the explicit new helper/result and adapt legacy results to empty proof metadata internally. Do not break direct callers by silently changing their unpacking contract.

For every evidence role: validate current owner/trace, full observation seal, ledger/provenance/output digest, effective request/target digest, expected producer/schema, status, source freshness/trust and original obligations. Revalidate before duplicate-result reuse as well as actual tool execution. `usable()` alone is insufficient for row-level completeness or domain authenticity. Never reach through browser source IDs, model prose, a static workflow output name, or another trace. A1's server-confirmed cross-trace molecule selection remains its separate authorized path.

`binding_proof` is a closed versioned record of role→evidence ID/output digest, source manifest/generation identity when available, target selection digest, candidate-source compound identities/order, admitted requirement digest and effective action-policy revision. No copied secrets or arbitrary source object. Include it in step metadata, dynamic result quality and ledger input binding **before** write-once sealing. Extend `evidence/ledger.py`, Session metadata propagation, integrity validation and semantic continuation replay together. Old proofless observations retain old-profile behavior; new-profile observations cannot omit proof. No DB schema change: use existing versioned metadata, still bounded by existing observation/context/snapshot limits.

Update B's `active_results` semantics explicitly: the original query-only per-tool digest is not a proof of active candidate/target lineage. Require the current immutable obligation digest and valid prior-source closure; reject cycles, missing/forward sources, stale resource generations and mismatched target/candidate roles. Distinct sets with equal structures remain distinct. A later unrelated observation cannot reactivate an old source simply by sharing the query. Legacy no-profile filtering stays unchanged. Ranked rows are not an automatic new CandidateSet and cannot become a model-selected analysis subset through `input_ref`; use the original whole set or a new owner-confirmed user selection.

### Target → generation

Validate lookup status resolved, trusted matching target identity, expiry/source quality, required structural evidence if requested, and no demo/fallback contamination. Reuse `build_generation_request`, recursive trust preservation and `serialize_target_evidence`; add existing `target_evidence` precondition to dynamic step. Bind through server-owned `BindingResolver` identity/workflow selection with exact output allowlist; never expose `$.workflow` to model. Keep raw original user query separately from structured target data. A target-dependent request cannot become untargeted if lookup fails. Target presence in a prompt is not evidence that generated molecules bind it.

### CandidateSet → property / likeness / ADMET / activity

Accept the existing `CandidateSet@1` only after `AgentResultValidator`/`sanitize_generated_candidates`/`_revalidate_candidate_set` chemical validation; there is no existing class named `CandidateSetValidator`. Preserve `candidate_id`, original/canonical SMILES, source index, validation and generation provenance exactly as stored. The compound identity is `(generator_evidence_id, candidate_id)`, never canonical string or candidate ID alone: IDs can repeat across sets.

Resolve all candidates in source order; build detached `{smiles: canonical_smiles}` rows solely for existing `smiles_text` transform. Keep the identity manifest alongside it; do not mutate CandidateSet or claim this spelling is the raw model output. Activity receives exact canonical batch plus original user target/query, not target inferred from generator/RAG prose. Set `candidate_source` to source step output key so existing Session alignment runs before sealing. Reject contradictory supplied candidate IDs before alignment can overwrite them; missing IDs may be assigned only by the existing canonical alignment with verified source manifest. Unknown/duplicate/missing rows retain alignment diagnostics and cannot satisfy full coverage. No cherry-picking one good row or joining independent sets by SMILES.

Automatic downstream use of a short/partial/invalid generator batch is blocked in the initial B2 profile. Display partial candidates without claiming count/core requirements succeeded. A later explicit single-candidate user selection may use A1/A2 reference semantics; it does not retroactively complete generation. Support for partial-batch exploratory pipelines needs a separately explicit policy, not an implicit relaxation.

### Reverse / RAG real-source boundary

Use **producer-returned per-invocation receipts**, not a tool's post-call inspection of mutable service metadata. Closed receipt views live in the already proposed binding contract module; domain functions may return plain detached envelopes without importing Agent contracts. Proposed method names below are new seams, not existing APIs. Reuse the existing prediction/retrieval core once; no second scientific call to obtain a receipt, no global `last_receipt`, no new resource framework. A receipt proves only the checks actually performed, not experimental truth.

#### Reverse: loaded popcounts and effective weights are source inputs

Propose `ReverseTargetPredictor.predict_with_receipt(...) -> {records, receipt}` for the Agent path, sharing the current prediction core and keeping legacy `predict(...) -> list` compatible. Before B uses a loaded generation, verify the actual ordered TSV/dataframe and Morgan/MACCS arrays have a coherent source generation; current equal row counts alone do not prove dataset alignment. Extend that generation's integrity record to **both actual loaded popcount arrays**, with shape/dtype/content digest and verification revision. Validate expected fingerprint dimensions/bit domain and popcount shape/integer range, and compare every popcount with the row sum of the corresponding **loaded fingerprint array**, in bounded-memory chunks. A matching-length tampered cache must fail source admission even if its altered hash can be recorded. A hash is not a consistency proof.

Perform expensive consistency validation once per stable loaded generation under existing load ownership, then reuse its immutable validation result; do not hash all assets for every tool action. Cached and newly computed popcounts must describe the arrays actually used, not whatever file now occupies the configured path. Do not silently repair/overwrite a corrupt cache to make B pass or introduce cache rebuild/download inside a B action. Existing loader cache creation is not newly authorized here. If a missing/changed/mutable mmap generation cannot be safely validated/pinned by narrow predictor-local ownership, keep reverse source binding blocked and return to parent before any loader/deployment/asset redesign. Read-only mmap flags alone do not prove protection against external in-place mutation.

Capture effective Morgan/MACCS weights **once for this invocation at their point of use**, using the existing conversion/default/clamp policy; pass those same resolved values into the unchanged weighted-similarity formula and receipt. Record effective numeric weights, resolution-policy revision and a sanitized default/clamp indicator where applicable, never raw environment text. Require a verifiable finite effective configuration for B; no new weighting strategy or user/model weight control. Include threshold, top-K, target-combine/filter controls and fingerprint algorithm/dimensions as actually applied. The B profile still fixes threshold 0.6/top-K 10 and one subject.

Reverse receipt minimum: schema/validation revision, invocation/input digest, loaded ordered-source and fingerprint identities, both verified popcount identities, effective configuration digest/weights, result digest/count/status. Tool normalization preserves this receipt and binds its raw result digest to the normalized observation in the existing seal. Empty matches require the same verified source/configuration and successful computation, not a missing-data fallback. Same fingerprints with different effective weights are **different executions**. Duplicate reuse and continuation compare the sealed receipt's source/configuration revision with a current trusted eligibility snapshot; mismatch blocks reuse, never recomputes or relabels historical scores. The actual invocation must also match the pre-dispatch snapshot or fail binding (no automatic rerun). A current metadata/config read may invalidate an old result, never fill its missing historical receipt.

#### RAG: actual source identity and empty/discard state from one call

Propose `search_molecular_index_with_receipt(...) -> {records, receipt}` in `src/rag/retrieval.py` and `RAGSystem.search_similar_molecules_sync_with_receipt(query, k)` in `src/rag/service.py`; `RAGSearchTool` consumes this strict envelope. Keep list-returning sync/async entry points as compatibility projections of the shared core, not duplicate searches. Legacy async exception-to-`[]` behavior is not B evidence. A service lacking the strict capability is unavailable for B; no fallback to an ambiguous list plus post-call metadata. No index-builder, manifest schema or scientific similarity formula change.

Capture the source dataframe/index/manifest/row mapping generation and embedding identity used by **this invocation**, including across the embedding call. The receipt comes from the same captured objects used for validation, search and row projection. It includes manifest/schema/builder revisions, source/index/row-mapping identities, loaded generation, input digest, requested/effective k, search-dispatch flag, bounded returned/accepted/discarded counts and reason codes, and result digest. Pin or verify the existing service generation through completion; mismatched source/config/model changes fail binding. A load-time identity must refer to the dataframe actually loaded, not merely a later hash of the CSV path. Narrow load/publication capture belongs in `service.py`; if stable ownership cannot be established there, report the exact wider requirement before proceeding.

| Receipt state | Evidence required | B obligation outcome |
|---|---|---|
| `valid_hits` | Successful compatible search/projection, expected response shape, finite scores/valid labels, zero anomalous discards | Source-backed records may satisfy admitted retrieval coverage |
| `valid_empty` | Successful validated retrieval with no hits and zero anomalous discards | May satisfy executed-retrieval/no-hit obligation, never a promised hit count |
| `invalid_discard` | Any illegal label, non-finite score, mismatched score/label shape/count or invalid projected record; includes all-invalid and mixed valid/invalid responses | Does not satisfy no-hit or full retrieval obligation; fail/partial with explicit anomaly, no known-subset success |
| `unavailable` | Missing/incompatible source, failed embedding/search, unstable generation or missing receipt | Unsatisfied obligation, not valid empty |

For the inspected core, a validated zero-vector index is an explicit `valid_empty` path: receipt says `index_search_executed=false`, reason `validated_empty_index`; retrieval validation completed, but no FAISS call is claimed. A nonempty index is searched with `min(k, ntotal)`; under this contract `-1`/out-of-range/nonintegral/bool labels are anomalous, not evidence of a genuine empty corpus. A zero-length/truncated response for a positive effective k is invalid shape, not `valid_empty`. Mixed good/bad rows can remain diagnostic partial data but cannot fulfill B's whole retrieval obligation or downstream binding. Existing row-skip compatibility tests must not be mistaken for strict receipt tests.

Persist the returned receipt with observation/ledger before sealing, including valid empty; future reuse/continuation checks the exact source/query/status proof. Current `service.metadata`, fields read after return, a matching query, and `data=[]` cannot reconstruct a missing or invalid historical receipt. Rows retain actual numeric/provenance fields; B1 remains local molecular retrieval, not internet literature search. No automatic RAG→generation binding, invented citations or model-authored scientific numbers.

**Bounded choice:** parent-approved direction is the two narrow producer envelopes above. Adding only post-call tool metadata is rejected because it loses popcount/weight and discard history. A generic dataset manager, new cache format, broader retrieval rewrite or new scientific/model policy is out of scope; an actual need for one stops implementation for parent decision. The companion allowlist adds only the two named existing RAG core/service files to the prior production-file scope.

### Multi-input ranker

Seal role-specific references for the same candidate generation, exact IDs/canonical mapping, matching activity target and required batch coverage. Reuse `BindingResolver.resolve('$.workflow', 'identity', ...)` only internally with exact outputs `molecules`, `properties`, `admet`, `activity` and metadata `docking_top_n`. That legacy metadata name conveys top-N prioritization, not docking authorization. Omitted optional slots use the resolver's existing empty-list representation; record absence in proof. Never take an arbitrary workflow dictionary from the LLM.

Call actual `CandidateRanker` without recomputing its algorithm elsewhere. Validate returned rows as the ordered prefix of its full ranking, ranks/IDs/unrankable coverage and count against source; preserve score, `weights_used`, null optional scores and `missing_evidence`. Current ADMET producers do not supply the ranker's risk summaries, and family activity outputs do not automatically supply `normalized_activity`/`probability`. B does **not** manufacture these fields, convert pIC50 into a probability, infer risk from missing endpoints or change scorer weights. Property-only ranking can be honestly completed if explicitly admitted; mandatory ADMET/activity scoring remains blocked until a separately reviewed compatible scoring contract exists. Analysis may still be reported as evidence not used for ranking.

## 7. Immutable requirements and supported claims

Extend existing `TaskRequirements` using a new version `2`, leaving v1 parsing/behavior available for old callers. Keep 32-KiB total bound, frozen typed tuples, forbidden tools and no expressions/JSONPath. New optional sections are closed and reference **server obligation IDs**, not future LLM-chosen values:

- Molecular coverage: source is explicit subject set or the one generation obligation; tool, exact full coverage, required closed metric/endpoint IDs and user target. Existing property/likeness v1 checks remain. Activity requires the reviewed endpoint/family/bundle semantics and preserves review/partial states.
- Retrieval: local-source retrieval completed, same-invocation receipt identity and `valid_hits`/`valid_empty` condition; validated-empty-index shortcut records no index dispatch. `invalid_discard`/`unavailable` never satisfies no-hit, including all-invalid and mixed responses. No guaranteed hit count beyond supported search API. Custom `rag_count` requirements unsupported by query-only typed input must clarify, not be ignored; default k=3 is disclosed for explicit search.
- Reverse/target: one input subject; fixed supported prediction controls; selected source row and subsequent identity match where requested. Similarity is a hypothesis, not pIC50/affinity. Unknown/ambiguous/no-hit can satisfy a search-completed observation but cannot satisfy a target-required generation obligation.
- Generation: one logical invocation slot per root request, requested valid canonical-unique count 1–10, target required/explicitly absent, exact seed if optimizing and optional explicit `core_smiles`. Existing internal limit five is server execution policy, not a new user/model input field. User count, UI generation count and prose conflicts reject; no silent truncation/padding or count taken from model arguments. Budget/cancellation may stop replenishment early; unmet count remains incomplete.
- Ranking: source generation slot, top-N 1..requested count, whole-candidate property coverage, which other evidence is required for analysis versus required for score, and fixed existing ranking policy version. Top-N cannot silently shrink when candidates are missing/unrankable.

`core_smiles` is a user-specified valid SMILES substructure, bounded like existing structures, with explicit policy: RDKit substructure match with chirality checking, all returned candidates, no generic "keep core" inference or user SMARTS language. Require RDKit and deterministic verification; unavailable validation blocks success. Existing evaluation `_has_aromatic_core` falls back to string matching on exceptions and **must not be reused as runtime proof**. Add the narrow runtime check alongside molecular validation, not new chemistry generation rules. G2's valid seed-in-prompt is not core-retention evidence.

No default novelty claim: optimization currently may return the unchanged seed. If the user asks for novel/improved/more active molecules, admission must specify and independently check supported predicates or clarify/reject. Do not label valid unique output as improved potency, experimental efficacy or synthetic feasibility. Rich free-form optimization goals remain unsupported until deterministically checkable.

Acceptance evaluates one coherent source closure, not a bag of individually successful tool names. Use exact candidate manifest and sealed role proofs; missing rows, target mismatch, unknown mandatory endpoints, missing scoring components, count/core failures and forbidden actions produce fixed unmet-obligation codes. Optional scientific failure still keeps the aggregate partial according to existing loop semantics. Do not override that to green merely because ranking returned something. `verify_finish` must require citations cover the mandatory supporting evidence closure, not just any valid evidence ID plus unrelated completed tool names. Scientific final text continues to come from deterministic formatter/validated evidence, not the model's finish prose.

## 8. One logical generation invocation, bounded replenishment and lifetime

At most **one logical generator invocation per root-request slot** across adapter, decision, Session and continuation. Preserve the existing helper's **at-most-five internal rounds**, stopping early when the valid unique count is met. Replenishing toward ten candidates inside that invocation is not journal replay and does not consume another logical slot. Completed, partial, failed, cancelled or uncertain logical generation must never be reinvoked automatically, including with a new decision ID, target, seed or continuation. A deliberate new sample requires a new explicitly admitted user request. Ten candidates are an acceptance obligation, not a promise to exceed the known bound to obtain them.

### Read-only retry verification and remaining proof gate

At baseline `5db56b0`, `llm_molecular_generator.py::_generate_with_retry` (line 277) uses `range(1, max_attempts + 1)` with default five, accumulating validated canonical-unique candidates and stopping at target count. Each iteration chooses `_generate_with_llm` (475) or `_optimize_with_llm` (501); each has one `_call_llm_sync` call. Empty/error/invalid/duplicate results consume rounds within that same bound. A preflight failure may make zero backend attempts. Current output quality includes requested/actual candidate count, **not actual backend attempt count**.

`_call_llm_sync` (542) requests `max_tokens=1000` per model call. Its async branches use a 30-second wait and close their created event loop; the synchronous branch directly calls `llm.generate` with no helper-level timeout. The inspected `src/web/models/ollama_model.py::generate` (52) makes one `sync_client.post` with client timeout 150 seconds; no explicit retry loop or retry-enabled transport is configured in this source. This supports retaining five rounds, but is **not runtime proof** about every injected client, total wall time or cancellation cleanup. Per-client I/O timeout is not a root deadline; five fresh waits must not bypass the existing loop's default 300-second deadline. Closing a loop or timing out a future alone is not proof all consumers settled.

Before B2 release, probe the landed normal generator binding with the real helper and actual client method, replacing only the lowest HTTP transport with counted offline responses/errors; also cover injected synchronous and coroutine `generate` paths. Count producer entries, helper rounds, dispatched transport requests and completed/uncertain requests separately. Exercise early completion, multi-round ten-candidate completion, five exhausted rounds, timeout and cancellation. No network/assets here. Hidden/untraceable requests, more than five transport attempts, automatic transport retry outside the known loop, or undrained consumers **fail B2 release**. Record the exact failing path and seek a narrow scope amendment if needed; do not silently substitute a one-backend-call policy.

Minimum changes proposed for later implementation:

1. Keep shared `factory.py` idempotence and retry settings untouched. Reuse existing Session checkpoint/journal and adapter `allow_retry=False` for outer execution. Add a request-local effective non-replay policy/spec in `decision_execution.py` only where actual-loop tests show Session needs it; no generic policy framework. If needed, the copied effective spec is non-idempotent with one **outer logical** attempt and a stable fingerprint, without mutating the registry. `SingleAttemptTool` currently exposes no spec; its `allow_retry=False` does not disable the producer's replenishment loop. B permission remains specific to this generator, never a blanket authorization for non-idempotent/filesystem tools.
2. Do **not** add `generation_attempt_limit`, change workflow canonicalization or extend 4C input schemas for retry control. Preserve the existing default five; any later schema/workflow change needs a demonstrated boundedness defect and parent-approved amendment. Use request-local execution telemetry/control, not shared mutable generator fields or LLM arguments. Record actual backend attempt count and round status in existing execution metadata/journal before sealing, including partial/failure paths; keep scientific CandidateSet data unchanged. Reserve/checkpoint dispatch before each internal backend call, settle its receipt afterward. A crash leaves an explicitly uncertain dispatched attempt, not fabricated completed usage or a reason to rerun the logical generator.
3. Reserve one root generation slot before producer dispatch using existing Session persistence. Bind the consumed slot to fixed inputs/proof; changed input hashes cannot allocate another slot in the same root request. Semantic rejection before dispatch may leave it unused; uncertain dispatch/commit consumes it. Duplicate actions return verified settled observations or explicit uncertainty, never invoke the adapter again. Recovery may settle a journal, not continue or restart the stochastic helper. Persist minimal slot/attempt receipts in existing execution state, no new store or database schema.
4. Include **all internal requests** in root resource accounting: generator dispatches, prompt/completion usage (or conservative reservations), elapsed time and cleanup time. Keep the loop's existing decision-model request counter (default maximum 16) and outer tool-attempt counter semantically distinct; neither already counts inner generator calls. Persist a generator subcounter and root totals/reservations alongside existing budget state; at most five generator dispatches, not five free calls. Every call reserves its prompt bound plus the existing 1000 requested completion-token limit; five calls can request up to 5000 completion tokens, **not including repeated prompts**. Existing Ollama `generate` returns only text, so actual token usage is unavailable, not zero or derivable exactly from text. Review a conservative prompt-token bound and remaining request/token allowance before dispatch, charge reservations when usage is unavailable, and block if no enforceable bound can be established. Do not invent provider usage or introduce model-controlled budgets.
5. Check the same remaining root deadline/cancellation state before every internal call; do not reset the deadline on replenishment or resume. On cancellation/budget exhaustion stop scheduling remaining rounds, then drain **all** in-flight calls, synchronous workers, event-loop tasks and owned client cleanup before A2 lease exit, model switch or shutdown. Repeated cancellation cannot release early. Keep stopped versus uncertain status truthful; an unprovable drain blocks release. Narrow generator telemetry/deadline/cleanup seams are allowed only in later authorized implementation; client changes outside the allowlist require review. Sanitize touched raw query/intent/provider-error logs.
6. Continuation snapshots persist proof, consumed logical slot, dispatch/settlement receipts, actual backend attempt count or explicitly uncertain count bounds, spent/reserved budgets and source/generator configuration generations, not live objects. Claim validates closure and inputs before CAS; no generation reinvocation, retrieval redo or ranking recalculation. A2's main-model epoch alone is insufficient: pin server-owned generator/dataset generations too. Unknown dispatch count after corruption fails closed, never resets to zero. Legacy v1 callers remain separate.

Operation keys for all B actions include tool/adapter/effective policy, canonical bound input hash, role→source evidence/output digests and requirements. Same SMILES under different targets, candidate sets or dataset generations is not the same operation. Reuse remains within same owner/trace and active obligation closure. No cross-user persistent cache or new scheduler.

## 9. Normal entry, G1 reports and P7-C

Integration waits for A1 and A2 implementation/review/landing, not just their documents. Later extend A1's server admission with bounded B vocabulary and immutable obligations; consume it through A2's same runtime/one lease/nonce protocol. No new `/api/chat`, no UI capability injection, no static workflow fallback. Whole unsupported fresh/resume requests still clarify/reject. Preserve A2 history and candidate mount/ACK/restore behavior; generation count/source evidence is separate from candidate presentation ACK.

For G1 after its reviewed landing, add only a dynamic-proof projection branch using the same owned snapshot/full-observation/ledger/source-version checks and report DTO. Read new binding proof instead of reverse-engineering static Planner input selectors. Reconstruct exact bound input and verify role hashes and candidate IDs, then render existing numeric report/weights; never re-run properties or ranking for display. Unknown/invalid proof remains `unsupported_binding`/unavailable. Same-frame actual `/ws` output→home mount→exact ACK HTTP tests are required; synthetic source fixtures are labeled, and real browser/model evidence remains package 8. If G1 is not landed, existing validated JSON/candidate frames remain truthful; **dynamic report milestone remains pending**, not completed by a static report test.

Parent approves separate, still pending **P7-C** for `molecular_docking` / real Vina structured sample. B does not expose it or the four staged helpers. C needs explicit server-owned consent tied to receptor/ligand/grid/tool/input hashes, ownership and expiry, filesystem/sandbox/timeout/at-most-once execution, artifact provenance/download authorization and cancellation/uncertain recovery. Parent-approved MAGL sample must produce a new actual Vina output, finite parsed kcal/mol and pose hash, not canned scores; no assets are inspected or run here. A ranking row's `docking_ready_for_preparation` is not consent, a pose or docking success. Separation approval is not permission to execute C's high-risk side effects.

The current-real-acceptance plan's E01–E12 are proposed final rows, not twelve existing passes. B1/B2 component tests, A2 enabled-route contracts, local reviewed 4C/G1 or property-only ranking do not prove all twelve ordinary-route cases. Vina/side effects and final repeated real entry acceptance remain pending under C/package 8. Goal stays packages 1–8, no deployment; parent decides final scope, not this sidecar.

## 10. Historical parent decision / revision gates (see §11 re-pin)

- **G0 / B-D0 and written receipt amendment approved:** B1 then B2, closed arguments/proofs, independent explicit RAG permission, full-batch coverage, actual Ranker/no rescore/mandatory unknown fail, and separate pending P7-C. Generation remains at most one logical invocation/root-request slot with existing at-most-five internal replenishment rounds, counted/budgeted/drained and never logically replayed. No new model policy or retry-field plumbing. Parent approved the amendment text/future allowlist and local two-doc commit only, not implementation/A2 activation.
- **B-D1 dependency pin (blocked):** 4C PR73 merged `0295e996`; main #76=`5ad08ac`; G2 merge `27170d95` recorded. A1's four-file diff matches `9d1305e` and passed both reviews but still needs landing. A2 docs `15a903d9` still have no implementation and require runtime implementation/review/landing. G1 PR77 `f48039e` is in CI, not landed. Pin the exact integrated content before implementation; this coordination update does not release it.
- **B-D2 domain source/obligation review (open):** ADMET labels/endpoint IDs; same-invocation reverse receipt including loaded popcount/fingerprint consistency and actual effective weights; RAG captured source plus `valid_empty` versus `invalid_discard`; reuse/continuation generation checks without historical backfill; unchanged generator logical non-replay, five-round bound, budgets and full drain; core/score compatibility. Synthetic tampered-cache and all-invalid-versus-valid-empty tests are required, not run here. Failure to establish stable loaded ownership in the named files requires parent scope review. Unsupported obligations remain blocked rather than fabricated.
- **B-D3 implementation review:** actual-loop and normal-route dataflow/cancellation/continuation/ACK tests plus exact file allowlist. Main/dataset/generator revision changes invalidate waiting runs as appropriate. No static-plan surrogate.
- **B-D4 final evidence:** parent-coordinated offline regressions, independent review, CI and explicitly authorized repeated real provider/science/browser acceptance on integrated revision; C still required for Vina scope. No new default activation/deployment inferred.

Companion plan defines precise future write ownership and tests. The historical delivery was the **parent-approved two-document local commit freeze following `74739aa`**; this is not a commit instruction for the §11 draft. No implementation/tests/assets/network/push/merge. Stable source ownership is unproven until actual implementation evidence establishes it; P7-B implementation, P7-C and final package-8 proof remain incomplete.

## 11. Proposed ordinary-profile and shared-root-budget amendment

### 11.1 Source pin, scope and compatibility

Historical §11 drafting evidence, superseded for current source/entry status by
§12 below; not a claim that B is implemented. Source was read only
through `git show 1da64f2694203de0ef3f3c875ceb775e4be0dee4:<path>`: the committed
ordinary Task7A checkpoint containing Task6 `dc1295e`. No active ordinary Web
files, configuration, credentials or assets were inspected; no imports, tests
or network calls were made. Plan §10's local ancestor record remains historical
evidence, not a fresh remote/CI check. Ordinary Task7B/C Web flow is **not frozen**
at this pin. `create_async` currently assembles capabilities but rejects semantic
entry with `ordinary_semantic_not_assembled`; do not remove that guard to test B
against the old no-carry route.

Reuse `AdmissionCarryIn`, `ActiveSegment`, `AdmissionExchange`, the existing
Session journal, loop, ownership ledger and single model lease. Source interfaces:
`ordinary_capabilities.build_capability_snapshot` currently implements only
original-four; `ordinary_admission.loop_admission` explicitly accepts only
`ordinary-semantic-original-four-v1`; `ModelDecisionLoop.run` already accepts
`admission_carry`/`admission_exchange`; `SingleAttemptTool` and adapters already
have per-invocation dispatch guards. The outer guard does not cover the generator's
inner requests. None of these observations proves a running B contract.

No-carry callers keep revision6 and their existing budgets/outputs. Existing
`ordinary-semantic-original-four-v1` keeps its exact catalog, permissions,
16-main/12-outer-tool/300-second limits, display gate and revision7 behavior.
No new B field is mandatory in either old path. Intent still proposes a class,
never tool authority; known science cannot downgrade to chat on failure.

### 11.2 Closed profiles and canonical mapping

Proposed server-only profile IDs are `ordinary-semantic-b1-v1` and
`ordinary-semantic-b2-v1`. B1 contains exactly seven canonical tools; B2 exactly
nine. Unknown profile IDs/aliases reject before dispatch. The browser, model,
continuation payload and retrieved text cannot choose a profile or permission.
Keep the ordinary policy selector `semantic_v1` distinct from this binding profile.

| Canonical tool ID | Capability feature ID | B1 | B2 | Request permission |
|---|---|---|---|---|
| `property_calculator` | `property_calculator` | yes | yes | scientific |
| `drug_likeness_assessment` | `drug_likeness_assessment` | yes | yes | scientific |
| `activity_predictor` | `activity_predictor` | yes | yes | scientific |
| `target_database_search` | `target_database_search` | yes | yes | scientific |
| `admet_predictor` | `admet_prediction` | yes | yes | scientific |
| `reverse_target_predictor` | `reverse_target` | yes | yes | scientific |
| `rag_search` | `rag_retrieval` | yes | yes | independent RAG |
| `llm_molecular_generator` | `molecule_generation` | no | yes | scientific |
| `candidate_ranker` | `molecule_ranking` | no | yes | scientific |

`ordinary_chat` is a product feature, not a tool; `molecular_docking` remains
unwired/unpermitted in both profiles. Feature IDs are never adapter lookup keys.
Use the same eleven reviewed product features, max32 features/16-KiB immutable
snapshot. B uses catalog revision `ordinary-product-b-v1` so descriptions can
describe installed B wiring without changing the old catalog's text/digest.
Use B assessment revision `whole-request-b-v1`; the accepted profile/catalog/
assessment triples are closed, leaving the original `whole-request-v1` unchanged.
Descriptions distinguish product support, wiring, permission and unknown health;
they cannot promise ADMET completeness, usable assets, retrieval or generation.

The pure builder takes closed profile, canonical registered names and an explicit
server-reviewed wired-name set from completed assembly, plus strict request
flags/permitted names. Profile membership **and** reviewed wiring **and** presence
are required for `wired`; registration alone cannot set it. Capability permission
is only an upper bound. A scientific feature additionally needs
`scientific_tools=true` and its canonical tool in the request allowlist. RAG needs
its independent strict `rag_enabled=true` and `rag_search` in that allowlist,
regardless of `scientific_tools`. Actual action authorization further intersects
whole-request admission/obligations and source preconditions. An ordinary chat
with `enable_rag=true` still has zero allowed/required tools and zero retrieval.
An explicitly admitted local search with scientific tools false and RAG true can
dispatch **only** RAG. Disabled/unsupported mixed obligations cannot be discarded.
Every current readiness remains `unknown`; no discovery, health, env or model probe.

Reuse committed `MolecularChatApp.create_async`, `_build_ordinary_capability_base`,
`project_ordinary_capabilities(base, ...)`, `_replace_ordinary_capability_base`
and `_publish_llm_config`. Future additive server-only arguments must preserve
the old signatures' defaults; do not introduce another constructor/startup path.
Capture one immutable base once under the existing reader, project the flags once,
then use that same snapshot in admission, intent, answer/display validation and
replay. When reconstructing B canonical names from features, use the inverse of
the explicit table, not the old identity-name projection. Model/capability pair
changes stage before mutation in the existing writer;
no nested writer, generator-model rebind or partial publication. Profile/catalog/
wiring changes rotate capability generation. B2 also binds its independently owned
generator generation and token-policy digest; they cannot be inferred from the
main-model epoch. Any unassembled B entry fails closed, not back to original-four.

Bind exact profile, catalog revision, full projected-view digest, model/capability
generations and original query/history/obligations in the existing admission binding
and B configuration fingerprint. Revalidate against a current trusted capture before
CAS and before releasing an answer. A snapshot cannot select a new view; same tools
with different profile, permission, source or generator generation are not equivalent.
Preserve the original ordinary display gate, including validation before persistence
or exposure, and the unchanged deterministic scientific finish/evidence requirements.

### 11.3 Recommended root ceiling, not a second main-model allowance

**Proposed choice requiring explicit parent approval: root backend ceiling16,
inclusive of main requests and generator backend requests.** Keep the existing
names/semantics: `M = total_model_requests = intent_requests + model_requests`
(intent plus decision, including schema repair), `M <= configured_main_limit <=16`;
outer tool reservations/attempt ceiling remains12. One logical generator invocation
uses its existing outer tool slot, not an extra backend-request debit. Its actual
inner requests use separate generator receipts, at most five, within the same root.

| Alternative | Consequence | Recommendation |
|---|---|---|
| Inclusive16 | Generation competes with remaining main-call allowance; bounded total work, may stop before five rounds | Adopt subject to parent budget approval |
| Main16 plus generator5, total21 | Keeps all old main slots after generation but authorizes a larger root budget | Not selected; would need a different explicit budget decision |
| Main16 plus unaccounted/free5 | Conceals internal resource use and uncertain attempts | Reject |

Define `generator_request_debits` as dispatched, uncertain and outstanding reserved
backend requests, excluding only **durably proven never-dispatched** released
reservations. `root_request_debits = M + generator_request_debits <=16` is the
conservative admission check. Preserve factual counts separately:
`generator_backend_requests` counts observed dispatches; an uncertain transport
boundary has bounded unknown status, not a fabricated zero or exact success count.
Existing main counters are not rewritten as physical HTTP counts. If reporting
`root_backend_requests`, derive actual main and generator dispatch facts from their
journals and disclose uncertainty; never label a reservation as an observed request.
Temporary pre-call reservations cannot be counted twice when converted to these
debits. Root capacity is checked before intent, every decision/repair and every
generator backend call; a candidate intent still needs two remaining main **and**
root slots. Known chat needs one. A useful finishing decision also costs a main/root
slot; budget exhaustion cannot fabricate successful completion without that step.

Before each generator dispatch, persist a unique reservation in the existing root
Session execution journal, tied to root trace/logical slot, round1..5, request ID,
input/proof digest, generator generation and token-policy digest. Recheck deadline,
cancel, remaining main/root limits and tokens adjacent to actual dispatch, after
validation/queue delays as well as at the producer boundary. On a proven pre-dispatch
stop, persist `not_dispatched`; on ambiguous dispatch/write/commit, retain the debit
and uncertainty and never rerun. Journal retries reuse the same ID and never repeat
HTTP. Persistence failure before reservation means zero dispatch; failed settlement
after dispatch cannot refund the reservation or manufacture a durable receipt.

Use separately named `b_root_budget` and `generator_requests` metadata (closed
version1 projections in existing binding contracts/Session metadata, not a new DB
or budget service). Include policy ID `b-root-inclusive-16-v1`, configured main and
root limits, main total, generator debits, root debit total, logical-slot identity/
state, and at most five generator receipts. Each receipt has its reservation ID,
round, dispatch state (`reserved`, `not_dispatched`, `dispatched`, `settled`,
`uncertain`), fixed outcome, reserved tokens, nullable verified usage and actual
request identity when known. Validate native scalar types, finite values, identities,
state transitions and all sums; bound the combined B budget view to16KiB within the
unchanged total snapshot limit. No raw prompts/responses, credentials, live objects
or absolute monotonic clocks in durable metadata. Missing/corrupt facts fail closed.

The process-local control uses Task6's **same ActiveSegment**, begun before Web
refresh/lease acquisition. Every internal round, backend wait and cleanup consumes
that credit; no per-round/retry/resume300 reset. Stop scheduling on deadline/cancel,
then physically drain workers/tasks/client consumers before lease/model close.
Timeout is not settlement. Keep the set-once saved checkpoint and post-drain/delivery
downward clamp; human waiting alone pauses credit. Keep Web TTL anchored to
`bridge_returned_at + 900`, and validate TTL/cap/budgets immediately before CAS and
again before first dispatch. If CAS succeeded then the cap expires, record failure
without dispatch; do not claim the nonce remained unconsumed.

### 11.4 B versioning composes with semantic revision7

Use B-only `decision_protocol_revision=8` plus `binding_revision='dynamic-bindings-v1'`
and `budget_policy='b-root-inclusive-16-v1'` in its fingerprint. Revision8 reuses the
validated revision7 ordinary-admission fields, exact history prefix, display replay,
carry/exchange and counter relations; it adds B proofs/root receipts, not another
loop or clock. `TaskRequirements.version=2` and outer decision envelope version1 are
separate schemas. Expected revision is selected by validated **server** profile,
not a snapshot field. Old no-carry6 and original ordinary7 keep their existing paths.
No converter promotes an old6/7 snapshot into B8 or B1 into B2. Switching profile,
root/token policy, generation or binding schema invalidates waiting before CAS.
B execution requires the validated semantic carry and matching server B profile;
no-carry plus a B profile flag is rejected, not interpreted as an old revision6 run.

B8 replay validates main journal length/intent receipt exactly as Task6, plus root
sums, generator receipt continuity, consumed logical slot and source closure. No
unknown generator dispatch resets to zero; no consumed generation resumes its inner
rounds. Completed/partial/failed/cancelled/uncertain slots remain non-replayable.
Direct old callers gain no B authority merely by adding snapshot keys. The trusted
carry stays separate from browser-derived AgentContext metadata; any optional B
binding extension must be absent in old fingerprints and preserve their exact output.

### 11.5 Token bound gate and minimal client seam proposal

At the pinned source, `LLMMolecularGenerator._generate_with_retry` defaults to five;
`_generate_with_llm` / `_optimize_with_llm` build the actual final prompt and each
call `_call_llm_sync`. That method and `_run_async_in_new_loop` pass `max_tokens=1000`.
`generation_request.py` bounds query/evidence **characters**, not tokens.
`OllamaModel.generate` sends `options.num_predict=max_tokens` to `sync_client.post`,
returns only `response`, and converts HTTP/errors to text. It has no trusted prompt
token counter, template/context enforcement contract, usage receipt or per-call
dispatch hook. `generate_async` has the same text-only boundary. A model name or
post-call usage field does not prove a pre-dispatch token ceiling.

For B2 require an immutable server-owned generator token grant, bound to the captured
generator generation and policy digest: exact backend/model artifact identity,
tokenizer and server template/system/options identity, reviewed bound-algorithm
revision, positive total generator-token allowance, per-prompt token ceiling and
verified backend context/output limit behavior. No browser, model answer or raw
environment value supplies it. Compute a conservative bound for the **complete final
prompt and backend-added template/special/system tokens** before each dispatch;
reserve that bound plus1000 against the remaining generator-token allowance. Check
context fit without silently truncating required input. Repeated prompts are charged
again. Five rounds reserve all five prompt bounds plus up to5000 completion tokens.
Unknown usage retains the full reservation; validated actual usage may be reported
separately but does not refund a same-root allowance. Usage exceeding the reservation
is a violated bound: stop further requests and block release, not truncate the receipt.

The grant is a closed frozen record in the proposed binding contracts, bounded to
4KiB, with exact fields: `version='1'`, `generator_generation`, `backend_revision`,
`model_artifact_digest`, `tokenizer_digest`, `template_options_digest`,
`bound_policy_revision`, `total_reserved_tokens`, `prompt_token_ceiling`,
`context_token_limit`, `completion_token_limit=1000`. Token values are positive
native integers (not bool); identities are bounded safe IDs/digests. Its digest is
part of the B fingerprint. A record's shape or a caller-supplied "verified" flag is
not proof: the assembly owner must validate the reviewed bound implementation and
backend identity before publishing it. The total is specifically a generator-token
reservation ceiling, not a newly asserted all-main-plus-generator token budget;
existing main token limits remain unchanged.

No numeric prompt ceiling/total token grant, tokenizer guarantee or actual usage is
invented here. Until the pinned backend/tokenizer/template can supply the above proof,
`generator_token_bound_unavailable` blocks B2 backend dispatch; it does not turn a
generation obligation into chat or a one-call workaround. B1 has no generator and
can proceed under its own later release. Full B2 remains mandatory, not completed by
this blocked state or by narrowing the original positive multi-round/count cases.

Narrow proposed extra allowlist, **requiring parent approval before code**:
`src/web/models/ollama_model.py::generate` (and `generate_async` only for the supported
async binding) may gain an optional keyword-only request-local control/receipt seam.
Legacy signatures/default behavior remain compatible; no global client mutation,
health/discovery/tokenizer download, main-model replacement or new model framework.
At the actual POST boundary this seam verifies the supplied trusted grant, checks
deadline/cancel, durably reserves and marks dispatch, then records sanitized status/
settlement and verified usage if available. The B path must not mistake the current
exception-to-apology text for successful generation. Preserve gmm, temperature,
prompt semantics, completion1000 and the five-round algorithm; no hidden transport
retry. A live enforcement/tokenizer capability missing from this client cannot be
fixed just by passing a number: return the exact missing proof to parent.

The existing adapter calls `tool.execute(payload)` without a control argument.
Before B2 implementation, prove a request-local execution view shares the **existing**
adapter concurrency reservation/owner and passes control to the real generator.
If that cannot be expressed through the existing execution wrapper, the only proposed
additional adapter change is an opt-in per-invocation control relay in
`src/agent/tooling/adapters.py` to that generator view, with default no-keyword behavior,
reset in `finally`, no shared tool fields and no new pool. This is a named scope gate,
not permission to bypass adapters or copy their executor/slot machinery.

### 11.6 Unchanged endstate and release decisions

All original positives remain: explicit B1 local RAG with same-invocation source
receipts, full ADMET obligations, reverse-to-target identity, B2 targeted/seeded
generation with multi-round completion of ten when feasible, all-candidate analyses,
actual Ranker scores/weights and full same-frame report/mount/ACK/follow-up dataflow.
No missing ADMET becomes zero; no pIC50 becomes normalized activity. B016/B017 and
the parent ordinary REAL010/DIVERSE015/multi-turn gates are not rewritten as rejection
tests. Their evidence remains pending, alongside C and package8 real acceptance.

Decisions still needed before affected code: parent approval of inclusive16 and B8;
the exact enforceable generator grant and any named client/adapter extension; final
ordinary Task7B/C source pin before Web integration. These are bounded gates, not a
request to prove a real model successful before writing B1 contracts. After written
review and a separate release, B1 core can start; no B implementation is released by
this draft and no scientific assets are declared safe by source inspection.

### 11.7 Written amendment decision — 2026-09-26

Independent written review approved the §11 design and companion plan without
P1/P2; a non-blocking adapter-class citation in the plan was corrected against
the same committed source pin. Parent chooses inclusive root16 and B-only8,
and approves the named optional client/adapter control seams as bounded design
scope only. Numeric token grants and enforcement proof remain unestablished;
the counted actual transport probe and trusted bound must precede their code
release. That checkpoint still required an ordinary Web re-pin; §12 now records
it without releasing B normal-entry integration.
This supersedes the two undecided parent choices above, not the remaining proof
gates. The two documents may be committed locally; B source implementation,
network/model execution, publication and deployment are not released here.

## 12. Ordinary source re-pin and first pure profile-view slice

### 12.1 Current source and authority

Current worktree `dynamic-bindings-b1` / `codex/dynamic-bindings-b1` starts at
`d672497a8c7b812378dddad91e195de3d04bbb65`. Its source/tests equal reviewed
`684e4a117fbf94859a539c182f611afd7ee825fa`; relative to684 only these two B docs
differ. Ordinary production source is `67ad0781d4d28ca00dd58a5c9d222ae940cfb22a`;
`e2e654bbf8ac0e73c8e96ee2efbd4f95368c757f` adds documentation, and684 adds only
schema-test compatibility/documentation, not production behavior. Parent's local
read-only checks also confirmed both B docs matched0487d79 before this edit and
ancestors `4C=0295e996`, `G2=27170d95`, `G1=be0219e`, `A1=782cd131`,
`A2=ed284b7`, `30a2353` and `684e4a1`.
These are local source facts, not new network/landing evidence: **PR81 remains
unconfirmed merged; no B push or automatic code release/commit.**

Subsequent parent verification: PR81 was explicitly authorized and squash-merged
as `a1258d978450a70a55e71156060b4d8d88a8db29` after exact-head CI8/8 and zero
unresolved reviews. Its tree equals reviewed684e4a1. The dependent B branch
integrates this baseline; plan§12.6 records the checked launcher and limited
code release. The paragraph above is preserved source-review chronology, not
the current landing status.

§11's assembly-only status describes historical1da64f2. At the current pin the
semantic Web route is fully wired through one reader, captured base/history,
carry/exchange, segment supervision and post-send waiting debit. Default policy
remains a1_closed; legacy direct `_process_message` and no-carry semantic dispatch
remain closed. Preserve this path. B still cannot execute: original-only
`loop_admission`, assessment/catalog checks and6/7 replay are unchanged. A B view
must not enable B execution under7; later server-selected B8 remains required.

### 12.2 Minimum API — parent-selected after independent written review

First future code slice writes only `src/web/ordinary_capabilities.py` and new
`tests/agent/test_decision_binding_profiles.py`. No app/route/admission/carry/
budget/DTO changes. Existing tests remain unchanged. This is a pure view, not
tool authorization, readiness, completed B assembly or B1 functional acceptance.

```python
build_capability_snapshot(*, registered_names, provider_descriptor,
    semantic_profile, intent_capable, original_four_profile, scientific_tools,
    permitted_names, model_generation, capability_generation,
    binding_profile=PROFILE_REVISION, wired_names=None, rag_enabled=False)
```

All existing arguments remain required. Keep existing constants/catalog and all
original-profile serialized views exact. Add `B1_PROFILE_REVISION` and
`B2_PROFILE_REVISION` for §11's exact profile IDs, immutable `B1_TOOLS`/`B2_TOOLS`,
`B_PRODUCT_CATALOG` and one `TOOL_FEATURE_PAIRS` tuple using exactly §11.2's
nine mappings; derive lookup/inverse from it, without aliases. B profile IDs and
`B_CATALOG_REVISION='ordinary-product-b-v1'` are exactly those approved in §11.
Use the same ordered11 feature IDs. For B catalog only, change generation's text
to `Separate local gmm molecular generation requires approved B2 controls; readiness is unknown.`,
reverse's to `Reverse target hypotheses require same-invocation source receipts; readiness is unknown.`,
and RAG's to `Local molecular retrieval requires explicit RAG permission and same-invocation source receipts; readiness is unknown.`
Other descriptions stay exact; none asserts installed wiring or actual readiness.

Parent-selected closed combinations (code release is separate):

| binding_profile | Legacy flags | New arguments |
|---|---|---|
| omitted or exact original ID | All current strict-bool combinations preserved | wired_names=None; native rag_enabled=False only |
| exact B1/B2 ID | native semantic_profile=True and original_four_profile=False required | explicit frozenset wired_names (empty allowed); native bool rag_enabled |
| unknown/alias/None/non-string | Reject | ValueError(CAPABILITY_ERROR), no fallback |

The old required `original_four_profile` selects the **legacy wiring branch**,
not a denial of the original four members inside B. B+True is contradictory and
rejects; never silently ignore it. Scientific permission remains the separate
`scientific_tools` bool. `intent_capable=False` remains a valid absent-intent-capability
view; ordinary_chat keeps its existing flag rules. Alternative flag semantics
require parent review, not implementer discretion.

### 12.3 Pure projection and counterexamples

Use existing strict frozenset/name/bounds validation. Keep the three inputs distinct:

| Input | Recommended B rule | Original-profile compatibility |
|---|---|---|
| registered_names | Full registry names allowed; unrelated/unknown/alias/noncanonical feature IDs contribute no wiring, never resolve aliases | Preserve existing extra-ignore behavior |
| wired_names | Explicit reviewed canonical subset of selected profile; outside-profile or unknown/alias/feature-as-tool names reject | New argument must be None; existing wiring behavior unchanged |
| permitted_names | Request-view canonical subset of selected profile; outside-profile or unknown/alias/feature-as-tool names reject, not silent intersection | Preserve existing extra-ignore behavior |

For B1, a registered llm_molecular_generator is harmless; putting it in reviewed
wiring or request permission rejects. Full-registry assembly must therefore
pass separately reviewed profile-scoped wiring/permission sets, not reuse the
registry set for all three arguments. This strict B permission choice is for
parent adoption after written review; it is not a change to legacy callers. `admet_prediction` cannot
register/authorize `admet_predictor`; the four identity mappings remain canonical.

For canonical t, wired requires profile membership AND reviewed wired_names AND
registered_names. Permission additionally requires t in permitted_names and
rag_enabled for rag_search, otherwise scientific_tools. ordinary_chat is not a
tool; molecular_docking stays unwired/unpermitted. All readiness stays unknown.
Reason precedence uses the existing DTO enum only: excluded/unreviewed wiring →
profile_not_supported; then absent registration → not_registered; then denied
permission → permission_disabled; else readiness_unknown. Do not add not_wired.
The first reason describes this binding view, not lack of product support.

Required counterexamples: B+legacy True; B missing explicit wiring; wired but
unregistered; registered but unreviewed; B1 generator/ranker names; alias/feature
IDs; non-bool flags; scientific=False/RAG=True allowing only the RAG **view**;
ordinary chat with RAG preference still has no execution authority. No live
registry/model/health/file/environment/asset reads or callbacks belong here.

### 12.4 Deferred work is still mandatory

Later app/carry/root contracts, B whole-request permissions, requirements-v2,
RAG/reverse same-call receipts, Session seals, B8 replay and actual Web positives
remain the full §§3–11 plan, not waived by this first slice. Do not open
loop_admission(B) before matching B8 execution. Preserve no-carry6/original7.
Approved accounting remains M<=16, G<=5, M+G<=16, tools<=12 and the same <=300s
ActiveSegment; B1 G=0 does not waive root closure. B2 token grant/actual transport
proof, C consent/Vina and P8 real normal-entry proof remain separate gates.
Companion plan §12 gives the future runner and TDD sequence. No code/tests are
released by this docs-only change; stop after parent/independent plan review.

## 13. Closed reference-argument contract slice

### 13.1 Current pin and selected approach

Pure capability views landed locally at b9f97597fa9b3501fc9dbf7280aae0f47c9fe2b8,
after independent SPEC/QUALITY and two 194-pass runs. This next component pins
that source. User delegates recommended choices; parent selects the following
bounded design under the already-approved §5 grammar.

Alternatives considered: widening the existing legacy resolver/parser now would
let incomplete v2 obligations enter an evaluator that only understands v1;
coupling every B tool, receipt and continuation in one patch would obscure the
authority boundary. Instead add the closed reference parser first, leaving
legacy execution untouched, then implement v2 and evidence resolution as the
remaining required integration. This sequencing does not replace actual-loop,
whole-request, scientific or Web acceptance.

### 13.2 Exact API and immutable values

New module `src/agent/contracts/decision_bindings.py` owns the existing B1/B2
profile constants, TOOL_FEATURE_PAIRS, B1_TOOLS and B2_TOOLS unchanged. The Web
capability module imports/re-exports those same public names; original profile,
catalog text and serialization remain exact. No contracts module imports Web.
The two private mapping proxies may remain in the Web module.

Expose `parse_binding_arguments(tool_name, arguments, *, profile_revision=None)`.
Only a native dict of bounded plain JSON is accepted, not DTO instances, dict
subclasses, arbitrary Mapping or JSON strings. Tool/profile selectors must be
native str, exact canonical B names/closed B profile; B1 forbids generation and
ranking. Original/no profile is rejected by this new parser, not upgraded.
Unknown tool/alias/feature IDs/docking/staged helpers reject. Omitted profile
and explicit None both reach the fixed payload error; Python calling mistakes
such as omitting the required tool_name/arguments positional parameters are
outside that payload-error contract. Bound both selector lengths at64 before
membership/hash comparisons.

Return frozen, strict, extra-forbid Pydantic values, reusing the existing
`EvidenceId` lexical bound (1–128 ASCII identifier characters). These references
are syntactic handles, NOT proof of existence or scientific input. For example
an identifier-shaped string CCO still requires a future same-run evidence lookup;
the parser must never treat it as literal SMILES. A reference cannot contain
path separators, whitespace, URLs, expressions or raw scientific payloads.

| Tool | Returned class | Closed fields |
|---|---|---|
| property_calculator, drug_likeness_assessment, activity_predictor, admet_predictor, reverse_target_predictor | MolecularBindingArguments | input_ref: EvidenceId, including reserved user |
| rag_search | RagBindingArguments | input_ref: Literal['user'] only |
| target_database_search | TargetBindingArguments | input_ref and optional record_ref: EvidenceId |
| llm_molecular_generator | GenerationBindingArguments | input_ref: Literal['user']; optional target_ref: EvidenceId |
| candidate_ranker | RankingBindingArguments | input_ref: EvidenceId; evidence_refs: RankingEvidenceReferences |
| nested ranking roles | RankingEvidenceReferences | required properties: EvidenceId; optional admet/activity: EvidenceId |

Target shape is either exactly input_ref=user without record_ref, or non-user
input_ref with a non-user record_ref. Generation target_ref, if present, cannot
be user. Ranking input_ref and every role cannot be user, and all ranking source/
role IDs must be pairwise distinct: one sealed producer observation cannot serve
as another producer's result. A record handle is not counted as a second source
observation; source evidence maximum is four in this grammar (under §5's five).
Optional fields may be omitted but never explicitly null. Use None internally
for absent optional fields; wire projection/round-trip uses
`model_dump(mode='json', exclude_none=True)`. Role order follows the class field
order, independent of incoming dictionary order.

No other fields are allowed: count, SMILES, query, k, paths, weights, scores,
top-N, model names, endpoint names or arbitrary nested role keys are rejected.
Missing properties, duplicate role evidence, arrays and boolean/string coercion
reject. Duplicate JSON keys remain the outer decoder's responsibility: retain
its current rejection and test the actual decoder→parser composition.

### 13.3 Bounds, failure and purity

Preflight the arguments with existing bounded plain-JSON validation before
Pydantic conversion/copy; max4096 UTF-8 bytes, max depth8, max nodes64. Use lazy
imports if required by existing package initialization; no new bounds framework.
Reject secret-bearing values with the existing secret detector after bounds.
Every parser error is a fixed `ValueError('invalid_binding_arguments')`, without
input-bearing error text or warnings. Do not run custom container/serialization
hooks. No normalization, trimming, identifier substitution, registry lookup,
file/environment/network/health/model probing or chemistry in this parser.
Return detached immutable nested values.

Existing EvidenceId has a terminal-$ pattern: add an explicit full-match check
for each reference so a trailing newline cannot pass. Do not change the shared
EvidenceId alias and thus accidentally alter outer decision/v1 behavior.
The parser is the public trust boundary; direct Pydantic constructors are not
execution authority.

### 13.4 Exact implementation boundary and remaining gates

Only new contract module, Web constants relocation and new
`tests/agent/test_decision_binding_arguments.py` may change in the code slice.
No changes to old resolver, TaskRequirements/v1 parser, Session, evaluator,
permissions, carry, B8, budgets or Web dispatch. All existing tests remain intact.

Actual B execution stays closed; future resolver must verify owner/trace,
observation seals, role producer/schema, source receipts, whole-candidate closure
and immutable v2 obligations. Target-required generation cannot be established by
optional target_ref alone; reverse still needs exactly one subject; role IDs
are untrusted until resolved. These are mandatory later tests, not passed here.

## 14. RAG retrieval outcome core (R1)

### 14.1 Root cause and selected sequencing

At fd5031801efc6c53d0b38828c7d463a30f1bf7a5, retrieval.py uses zip(scores[0],
labels[0]) then silently skips invalid labels/nonfinite scores. The list API
erases truncation/discard facts; the tool treats any list, including empty, as
success. Separate source audit also found generation switching and CSV/load
identity gaps (plan§14.2), which cannot be repaired by attaching metadata later.

Keep the end state of §6/plan§4b: actual stable loaded generation and same-call
receipt through service/tool/Session. R1 first adds truthful core diagnostics;
R2 must bind loaded source ownership and embedding identity before the strict
service/tool path uses them. This is preferable to duplicating the FAISS search
or presenting a manifest alone as loaded-data proof. The alternative of changing
legacy lists to fail on every discarded hit would break an explicit old contract
without resolving the missing source identity.

### 14.2 API and semantics

Add `search_molecular_index_outcome` in existing retrieval.py with exactly the
same keyword-only parameters as search_molecular_index. Both APIs share manifest,
source path, k and embedding validation, normalization and one index.search.
No rebuild, second retrieval, global last_receipt, new store or source asset read
beyond current manifest validation. Existing search_molecular_index list behavior
and field values remain unchanged, including legacy filtering/truncation.

New API returns a plain detached dictionary with exactly `records` and
`diagnostics`. It is NOT a source receipt. Do not add generation/model-weight
claims or mark loaded-source validation complete. The new diagnostics has:

| Field | Exact meaning |
|---|---|
| version | literal '1' |
| status | valid_hits, valid_empty or invalid_discard |
| requested_k / effective_k | actual validated positive request; min(k, index.ntotal) |
| index_search_executed | actual search dispatched; false only for validated zero index |
| score_count / label_count | first-row length only if actual array shape is (1,n); otherwise null |
| accepted_count | len(records), never expected k |
| discarded_count | actual skipped pair count for valid (1,effective_k) shapes; null when shape prevents trustworthy pairing |
| reason_codes | ordered unique closed codes below, empty on valid results |

Manifest/path/k/query-vector validation errors and backend/projection exceptions
continue to propagate. No exception is converted into valid_empty. A zero index
is validated first (including query embedding), then returns records=[], zero
counts, effective_k=0, index_search_executed=false, status=valid_empty.

Strict search output must contain two NumPy ndarrays of exactly shape
(1,effective_k). Other shapes/types, mismatched/truncated rows or zero rows when
effective_k>0 return records=[], status=invalid_discard,
discarded_count=null, reason_codes=['invalid_result_shape']; observed counts
follow the table. Do not zip malformed shapes or invent missing counts.

For valid shapes, visit pairs once. Label must be Integral but not bool and be
in manifest.row_mapping bounds. Score must be a finite Real number, not bool;
NumPy real/integer scalar types remain valid. Duplicate accepted vector labels
or projected source rows are invalid for this strict result. On an invalid pair,
increment discarded_count exactly once and add the first applicable reason in
this priority: invalid_label, invalid_score, duplicate_hit. Do not project it.
Successful projection uses the same helper/values/provenance as the old API.
Mixed good/bad results retain the real accepted rows for diagnosis, but status
is invalid_discard; all-bad is not valid_empty. Code order is first occurrence.
Any return with no anomaly and positive effective_k is valid_hits.

Returned nested dictionaries/lists must not alias caller dataframe object cells
or manifest mutable state; detach the new outcome's records, not the legacy list.
This protects return values from later source mutation, but is explicitly not
proof that a mutable source was coherent during retrieval. R2 must solve that.

### 14.3 Scope and evidence

Code allowlist: only src/rag/retrieval.py and new
tests/test_rag_retrieval_outcome.py. Use real FAISS plus synthetic temporary CSV,
manifest and index; no real service/model/host assets. New module must import
FAISS normally so missing dependency fails visibly, not silently importorskip.
Controlled malformed index.search results are labeled doubles, not scientific
results. No change to service.py, index.py, tool, typed adapter, old tests or
runtime at R1; downstream must not consume this as a verified source receipt.

Before implementation, demonstrate the anomaly on the actual existing list
API with a synthetic index (valid row + invalid label, and truncated lengths),
recording that anomalies produce a shortened list. Then RED asserts missing new
outcome API rather than an import/fixture failure. Green must prove actual mapped
rows with nonidentity mapping, shape/count correctness, one dispatch, valid empty
without dispatch, query/manifest/backend failures not successful no-hit, detached
nested values, and unchanged old API behavior. R2 generation-race reproduction,
source snapshots, service/tool receipt propagation and actual-loop integration
remain required and unimplemented by R1.
