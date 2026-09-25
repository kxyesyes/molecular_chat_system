# P7-B Dynamic Tool Bindings Implementation Plan

> **For agentic workers:** G0 / B-D0, the receipt amendment and §11's written decisions are design approvals, not blanket code release. The current bounded release is §12.5: only the two-file pure capability-profile slice, using the independently reviewed offline launcher in §12.6. That implementation is frozen for independent review; see §13 for actual evidence. Other source changes require a separately scoped release. No real model, host asset/config discovery, deployment or B execution is authorized by this slice. Older docs-only instructions below describe historical checkpoints.

**Goal:** Expand the existing initial-four-tool model decision path through bounded, evidence-bound molecular/RAG/reverse actions and real generation/ranking dataflow, without model-authored inputs/numbers or a static workflow substitute.

**Architecture:** Extend existing reference resolution, immutable requirements, Session metadata/seals and replay; model still selects each action after observations. Two functional milestones: B1 source-backed analyses/retrieval/reverse, then B2 one logical generation invocation with existing at-most-five internal replenishment rounds, whole-CandidateSet analysis, multi-input ranking and compatible dynamic report proof.

**Tech Stack:** Existing FastAPI/native JS, Python/Pydantic/asyncio, LangGraph decision loop, SQLite Session journals, RDKit and current typed adapters. No new framework, DSL, dependency upgrade or datastore.

**Current status/base:** PR81 landed as `a1258d978450a70a55e71156060b4d8d88a8db29`, tree-identical to reviewed684e4a1. This branch integrated it at9f2e7a6; the first code slice starts from6bc6dfc. Companion spec: `docs/superpowers/specs/2026-09-25-dynamic-tool-bindings-design.md`. Only §12's pure profile-view slice is implemented and under review. B1 execution, B2, C and final real acceptance remain incomplete. Unchecked broader steps are future work, not execution evidence.

**2026-09-26 history/current amendment:** §11 was drafted against ordinary `1da64f2694203de0ef3f3c875ceb775e4be0dee4`; its approval is recorded in §11.8. §12 supersedes its assembly-only/source-status descriptions, not the scientific positives or separate code-release gate. Only these two docs may change now; **no commit, tests, implementation or publication**.

**Historical parent-reported dependency update:** G2 PR72 merged as `27170d95`; 4C post-P2 full 6676 passed / 2 skipped, then joint 525 passed + 162 subtests, at that time PR73 head `beaf9cf4a57cf7c6045f5980c175c217fd42e96f` with CI pending; A1 SPEC4 repair `48d8356` under recheck at that time. No network verification or merge was performed here. These earlier statuses are superseded by the current parent record below; original design source base remains `5db56b0`.

**Receipt amendment source pin:** The preceding dependency report is historical, not fresh CI/landing status. Read `current-real-acceptance` HEAD `0d5ac1e4c79b51bbe76fbdd2e78f05ae1e818209`; local `git diff --name-only 5ad08ac -- src` showed no differences. Only source/standards/tests were read, no real data or environment. This verifies the requested reverse/RAG source snapshot against local `5ad08ac`, not new implementation approval. Re-pin all landed dependencies before execution; do not copy that worktree or refresh old result receipts using its current metadata.

**Historical parent-reported record for the receipt freeze:** 4C PR73 **merged `0295e996`**; main #76=**`5ad08ac`**; G1 PR77 head **`f48039e` in CI, not landed**; A1 then **four-file diff blob-identical to `9d1305e`, both reviews passed, not landed**; A2 revised docs **`15a903d9`, no implementation**. No fresh network/CI check is claimed. Parent approved that amendment text/future allowlist and local two-doc commit; stable mmap/source proof and separate implementation release are still required. See §§10–11 for newer local source pins.

## 1. Preflight and dependency contract

- [x] Parent approves G0 / B-D0 revised design: B1 then B2; independent explicit RAG permission; closed source roles/proofs and whole-candidate coverage; actual Ranker/no rescore/mandatory unknown fail; one logical generation invocation/root-request slot with existing bounded five-round replenishment; P7-C separate and pending. No implementation follows automatically.
- [x] Read-only verification confirms Gauss's two important gaps; parent approves narrow same-invocation source/validation receipts, without new formulas, model strategy or resource framework.
- [x] Parent has read and approved this written amendment and the narrow interfaces/future allowlist for `src/rag/retrieval.py` and `src/rag/service.py`; source/receipt tests and safe-snapshot proof are not completed by this approval.
- [ ] Parent releases the relevant milestone after dependency pins and confirms its future file allowlist; docs-only freeze does not authorize execution.
- [ ] Pin integrated dependencies: 4C PR73 merge `0295e996`, main #76 `5ad08ac`, and G2 `27170d95`; A1 four-file diff matches dual-reviewed `9d1305e` but awaits landing; A2 docs `15a903d9` await runtime implementation/review/landing. Earlier A1 `aed3571`/`48d8356`, A2 `ff02afc`, 4C `5e600a6`/`beaf9cf4` and G2 `548baca` are historical inspected/reported snapshots. Reconcile exact content, not wholesale worktree copying; no implementation before separate parent release.
- [ ] Read actual G1 review/landed snapshot before dynamic-report work: current parent record is PR77 `f48039e`, CI pending/not landed. Earlier `bf4a2cf` plus dirty corrections was the inspected static-only snapshot, not current publication status. No generic dynamic report is assumed available.
- [ ] Pin domain capability/source receipts and supported endpoint vocabulary. Unsupported full ADMET/novelty/normalized activity scoring, custom reverse/RAG limits and arbitrary core constraints must fail closed. Source manifest availability must refer to actually loaded data, not test declarations or configured filenames.
- [ ] Prepare later offline runner before importing app: temporary stores, controlled service handles, denied network, synthetic in-memory transport; never host env/key stores/assets. Reuse approved isolation procedure and safe fixture patterns. No live acceptance invocation in these steps.

## 2. Exact proposed later write allowlist

Only the two documents are written now. The following **future allowlist, including the narrow Reverse/RAG receipt amendment, is parent-approved for design**, not authorization to begin implementation or modify dependencies now. Files marked dependency-owned require their landed revision and explicit coordination. A failing need outside this list returns to parent.

| Milestone | File | Intended delta |
|---|---|---|
| B1 | New `src/agent/contracts/decision_bindings.py` | Closed role views and immutable resolved-input/proof contract; no workflow DSL |
| B1 | New `src/agent/harness/decision_bindings.py` | Source resolution/proof/role projection helpers using existing BindingResolver |
| B1 | `src/agent/harness/decision_inputs.py` | Delegate B profile binding; retain old no-profile behavior; full integrity checks |
| B1/B2 | `src/agent/contracts/task_requirements.py`, `src/agent/harness/decision_requirements.py` | Strict version-2 sections and deterministic coverage/source/score acceptance |
| B1/B2 | `src/agent/harness/decision_policy.py`, `decision_loop.py`, `decision_continuation.py` | Profile permissions/catalog, proof-aware steps/keys/finish, exact replay/fingerprint revision; minimal root-slot and generator request/token/deadline accounting |
| B1/B2 | `src/agent/runtime/run_session.py`, `src/agent/evidence/ledger.py` | Closed proof/attempt receipt propagation before seal and existing-state persistence; preserve validator/alignment/checkpoint order |
| B1 | `src/agent/tools/reverse_target_tool.py`, `src/reverse_target/predictor.py` | Same-invocation records/receipt; validate actual loaded popcounts against fingerprint rows, capture effective weights once, source/config version and reuse proof; no scientific formula/threshold/batch algorithm change |
| B1 | `src/agent/tools/rag_search_tool.py` | Consume strict per-invocation envelope, preserve receipt including valid empty; invalid discards cannot become successful no-hit; no post-call metadata fallback |
| B1 receipt amendment | `src/rag/retrieval.py` | Shared core returns captured source plus records/discard counts and closed status; compatibility list projection; no similarity/index algorithm change |
| B1 receipt amendment | `src/rag/service.py` | Capture loaded generation and embedding identity coherently, strict sync receipt method, same-invocation propagation and narrow load/publication ownership checks; no index-builder redesign |
| B1 integration | `src/web/decision_request.py` (A1-owned) | Whole-coverage B admission and immutable obligations; no permissive chat fallback |
| B1 integration | `src/web/decision_runtime.py` (A2-planned) | Server profile injection and frozen resume obligations/source generations; no second receiver/lease |
| B2 | `src/agent/harness/decision_execution.py` | Minimal request-local logical non-replay policy only as needed by existing Session; relay existing-root budget/cancel controls and attempt receipts, no shared registry mutation |
| B2 | `src/agent/tools/llm_molecular_generator.py` (G2-owned) | Request-local actual backend attempt accounting, between-round deadline/cancel checks and demonstrated cleanup/logging seam; preserve existing bounded five-round algorithm |
| B2 | New `src/agent/validators/decision_candidate_requirements.py` | Real RDKit explicit-core check and candidate-ID coverage checks; no fallback heuristics |
| B2 report | `src/agent/presentation/evidence_report.py` (G1-owned) | Dynamic proof branch, retain static branch/source/DTO checks; no reexecution |
| B2 report | `src/web/decision_chat.py` (A2-owned) | Existing report projection ordering after candidates/before complete, same lifecycle |

The prior RAG interface is insufficient; this amendment explicitly adds only `src/rag/retrieval.py` and `src/rag/service.py` to the future production allowlist. Keep closed receipt validation in the already proposed binding modules and existing Session/ledger/continuation; `rag_contract.py` remains a non-projecting compatibility validator, with no new generic input schema. No new receipt module/framework/store. Reverse uses the two already listed files. If stable loaded-data/mmap ownership requires a wider loader, cache/index format, data rebuild, snapshot service, deployment change or other source file, stop and report the exact requirement before changing scope. No asset reads/rewrites are authorized by this design. No factory idempotence, CandidateSet schema, Ranker formula, frontend framework, static HTTP/workflow template, key-store, launcher or dependency edits.

The revised generation policy removes `src/agent/orchestrators/workflow.py` and `src/agent/tooling/generation_ranking_contract.py` from the write allowlist: no `generation_attempt_limit` input field or canonicalization plumbing. Landed 4C contracts remain read-only regression dependencies. Existing loop/continuation/Session/ledger files above hold minimal consumed-slot, per-dispatch receipt and budget persistence, not a new execution store. If a counted probe finds unbounded or hidden client calls, identify that exact path and request an allowlist amendment before any model-client/schema/workflow edit; do not default to a one-backend-call workaround.

Proposed new tests:

- `tests/agent/test_decision_dynamic_bindings.py`: actual loop/Session source dataflow, keys/seals.
- `tests/agent/test_decision_binding_requirements.py`: closed v2 admission/acceptance including whole obligations.
- `tests/agent/test_decision_binding_continuation.py`: source closure/history/one-use CAS and slot replay.
- `tests/agent/test_decision_generation_once.py`: real producer, one logical invocation with up to five counted backend attempts, durable slot/budgets/lifetime.
- `tests/agent/test_web_dynamic_bindings.py`: actual assembled `/ws`, real middleware/runtime and same-frame ACK route integration.
- `tests/agent/test_dynamic_evidence_report.py`: owned stored/live dynamic proof through landed G1.
- `tests/test_reverse_target_invocation_receipts.py`: synthetic loaded arrays/cache fixtures, consistency and same-invocation effective weights/identity; no production assets.
- `tests/test_rag_invocation_receipts.py`: actual retrieval/service core over controlled embeddings/index responses, valid-empty versus anomalous-discard receipts, source capture races; no external service.

Modify related existing tests only: `tests/agent/test_decision_inputs.py`, `test_decision_loop.py`, `test_decision_requirements.py`, `test_decision_continuation.py`, `test_binding_resolver.py`, `test_dynamic_run_session.py`, `test_rag_tool_contract.py`, `test_target_tool_contract.py`, `test_candidate_alignment.py`, and G1 `tests/home_evidence_report_test.js` for actual frame input. A2 home runtime and 4C generation/ranking contract tests remain regression dependencies, not retry-field edits. Expand this test list only with named justification, not assertion weakening.

Receipt-specific existing-test addition: `tests/test_rag_index_manifest.py` for strict tool versus legacy list behavior (its current invalid-label/empty-hit test accepts dropped rows), and `tests/test_rag_service_boundary.py` for the new method preserving service/import boundaries. Keep `tests/test_reverse_target_health.py` and `tests/agent/test_reverse_target_complete_input.py` as regressions; health-ready/matching row counts are not consistency receipts. Do not weaken legacy valid-data parity or factory non-projecting assertions to claim receipt coverage.

## 3. B1 — strict arguments, permissions and deterministic obligations

- [ ] Write RED table for each allowed argument shape in spec §5 and every extra key/duplicate role/literal SMILES/count/weight/path/foreign evidence variant. Use actual `ModelDecisionLoop.run`, not `resolve` alone; assert zero tool executions on rejection.
- [ ] Write RED versioned requirements: v1 unchanged; v2 rejects bool-as-count, conflicting targets/count/top-N, unsupported endpoint, unknown suffix, mutually excluded required tool, extra fields and oversized persisted snapshot. Immutable original obligations survive caller mutation and clarification.
- [ ] Include exact A1 SPEC4 cases: `解释 logP\n对接这个分子` with tools false; `计算 CCO 的分子量和熔点`; `计算性质（禁用 property_calculator）；SMILES: CCO`; `分析两种化合物的性质；SMILES: CCO`. Last is one reported count case. Added quantifier controls are new tests, not extra reported findings.
- [ ] Write RED independent permissions: chat RAG true makes no tool call; explicit local retrieval/RAG false rejects; explicit local retrieval/tools false/RAG true grants only RAG under the approved rule; disabled scientific actions stay disabled. Web payload cannot inject profile/obligations/source generation.
- [ ] Verify RED failure is the missing contract, not a missing interpreter/import. Implement minimal closed views/profile/v2 admission and evaluate mandatory evidence closure; do not rewrite static Planner or make a known metric cover unknown text. Run GREEN and old v1 tests.

Proposed schema examples, not existing callable APIs:

```json
{"input_ref":"evidence-generation","evidence_refs":{"properties":"evidence-properties"}}
```

```json
{"input_ref":"evidence-reverse","record_ref":"server-observed-record-handle"}
```

These are argument **references**, not scientific payloads. Catalog and runtime enforce exact tool-specific role sets; outer decision v1 stays unchanged. No general nested expression parser.

Future focused command in approved runner: `python -B -m pytest tests/agent/test_decision_dynamic_bindings.py tests/agent/test_decision_binding_requirements.py tests/agent/test_decision_inputs.py tests/agent/test_decision_requirements.py -q -p no:cacheprovider`.

## 4. B1 — actual source actions and role proof

- [ ] Write actual-loop RED: user subject → property/ADMET/activity; source-bound observation changes model's next decision. Use real registry/Session/validators; count domain inputs. Real RDKit is allowed only in the later approved test environment; external services are deterministic labeled doubles.
- [ ] Write reverse RED for exactly one subject and actual producer first-of-batch hazard: a two-subject request cannot reach producer. Real reverse tool over controlled predictor yields target row; model chooses only issued record handle; actual target adapter receives that row's supported identity, not original molecule or generated target prose. Unknown ChEMBL-only/name-hash mapping clarifies, no fake UniProt ID.
- [ ] Write source-receipt RED: changed loaded generation/effective weights, missing/mismatched receipt, expired/ambiguous target, fake similarity/provenance, and foreign owner/seal. None enables target-conditioned generation. Receipt is returned with actual records, not reconstructed by reading producer fields afterward. Follow the two precise producer sequences below.
- [ ] Write RAG RED through actual retrieval→service→tool→adapter: valid rows or verified empty carry a same-call receipt; all-invalid, mixed-discard, unavailable, malformed or mismatched source/query fail whole retrieval/no-hit obligations. Unsupported paper-search/custom k requests still reject; no default substitution.
- [ ] Implement narrow source-binding/proof and producer envelope seams only after RED: unchanged Tanimoto/weighted similarity and FAISS search formulas; new validation/status preserves failure facts rather than silently reducing to successful subsets. Receipt/source/config digests enter operation keys, Session metadata and ledger before seal. Full source observations remain immutable.
- [ ] GREEN includes equal structures from different evidence sources: they may not share cached action results; role order canonicalization is deterministic, swapped roles reject. Validate current closure before duplicate reuse. No model-authored numeric values pass scientific finish.
- [ ] Cover `active_results` explicitly: B rejects missing/forward/cyclic/stale sources and uses current obligation/role lineage rather than query-only per-tool digest. Equal-query observations cannot reactivate stale evidence; rank rows cannot masquerade as a CandidateSet or an automatically selected subset. Legacy no-profile filtering remains unchanged.

Future focused command: `python -B -m pytest tests/agent/test_decision_dynamic_bindings.py tests/agent/test_target_tool_contract.py tests/agent/test_rag_tool_contract.py tests/agent/test_reverse_target_complete_input.py -q -p no:cacheprovider`.

### 4a. Reverse same-invocation receipt: RED → minimal delta → GREEN (future only)

Source evidence: `predictor.py:128` checks only cached popcount row count; `:183` uses actual popcounts in Tanimoto; `:150` reads/converts/defaults/clamps Morgan weight during combination. `predict` returns a list, and `ReverseTargetTool` cannot recover these historical inputs after the call. Existing health tests can report ready without loading fingerprints; they are not proof of consistency.

- [ ] In `tests/test_reverse_target_invocation_receipts.py`, use tiny generated dataframe/fingerprint/popcount arrays and temporary synthetic cache files only. Call the real load/validation and predictor code; never stub away `_load_or_compute_popcounts` in its corruption test. A same-row-count cache with one wrong value must fail before B scientific success. Also cover wrong shape/dtype/range, Morgan versus MACCS mismatch and correct freshly computed arrays; forbid production paths and automatic cache-repair writes on corruption.
- [ ] Add same-fingerprints/different-effective-weight cases with a controlled test configuration seam, never read host environment. Capture exact weights actually consumed by the real combiner and receipt, including existing default/clamp cases. Mutate configuration after compute but before tool return: historical receipt retains original effective values. Mutation between eligibility snapshot and actual use rejects binding. Do not duplicate/recompute the scientific scoring formula in Agent code.
- [ ] Add loaded-array versus current-path/metadata replacement cases and changed generation before duplicate reuse/continuation. Stored source/popcount/result digests stay unchanged; missing old receipts are not backfilled. Concurrent invocations cannot overwrite one another via `last_receipt`. Verify validation is reused only for the identical stable loaded generation, not merely equal row count or path. If mmap stability cannot be proved, assert explicit source-unavailable rather than successful borrowed provenance.
- [ ] Implement proposed `predict_with_receipt(...) -> {records, receipt}` and shared internal core in the existing predictor/tool files. Keep legacy list `predict` behavior on valid inputs. Validate loaded popcounts against loaded fingerprint row sums in bounded-memory chunks once per eligible generation; no automatic repairs/new cache scheme. Capture and consume the same resolved weights once; retain formula, count/threshold/filter behavior. Return a detached bounded receipt including raw result digest, preserve it through normalization, and seal its mapping to normalized results.
- [ ] GREEN covers hit and genuine threshold-empty observations, exact source/configuration closure through real loop/Session, and zero downstream target/generator calls when receipt verification fails. These synthetic results are contract evidence, not real reverse-target acceptance.

### 4b. RAG same-invocation source and discard status (future only)

Source evidence: `retrieval.py:12` validates embedding/manifest, returns early for zero vectors, then silently `continue`s invalid labels/non-finite scores. `service.py:304` returns that list; `rag_search_tool.py:36` treats any returned list as successful. No existing `service.metadata` call can determine whether the list was genuinely empty or emptied by invalid discards. `tests/test_rag_index_manifest.py::test_shared_search_skips_invalid_labels_and_empty_hits` explicitly tests list skipping; preserve that compatibility separately from B's strict evidence path.

- [ ] Write RED using real retrieval/service/tool and controlled finite embeddings/index responses. Do not mock a ready-made service receipt. Fixtures: valid rows; validated zero-vector index; all invalid labels (`-1`, out-of-range, bool/nonintegral); all non-finite scores; mixed valid/invalid rows; mismatched/truncated scores/labels, including zero-length response for positive effective k. The last cases are `invalid_discard`, never `valid_empty`. A validated zero-vector shortcut is `valid_empty` with `index_search_executed=false`; no fake FAISS call.
- [ ] Assert response counters/status at the actual filtering point and propagation through tool/adapter to Session seals and requirements. Any invalid discard leaves no-hit/full-retrieval unmet, even with surviving rows. Failed source validation, embedding or search is `unavailable`, not empty. No fallback from missing strict capability to the old list API or post-call fields.
- [ ] Inject a generation/model swap during the embedding wait and a source/manifest replacement after search but before tool receipt handling. One call must use a pinned coherent source+embedding identity or explicitly fail. A receipt cannot mix old rows with new manifest/embedding identity; post-return mutation cannot alter it. Swap loaded dataframe while leaving the CSV path unchanged: loaded-generation mismatch must be detected, not hidden by a later file hash. Reuse/continuation compare current eligibility with sealed historical receipt without rerunning retrieval.
- [ ] Add `search_molecular_index_with_receipt(...) -> {records, receipt}` in `retrieval.py` and `search_similar_molecules_sync_with_receipt(query, k)` in `service.py`, sharing the core with legacy list methods. Capture coherent loaded generation at load/publication and invocation entry, propagate effective query/k/source/embedding identity and discard state from the same core. `RAGSearchTool` consumes the envelope once; no mutable global receipt cache, extra search, index rebuild, manifest version bump or new scientific/model policy.
- [ ] GREEN: legacy valid-hit sync/async field parity and list return types stay compatible; legacy async failure-to-empty remains explicitly non-evidence. Existing list skipping tests still cover the list API, while strict tool expectations intentionally reject anomaly-as-success. Run unchanged non-projecting typed contracts; verify added receipt fields are preserved, not projected into molecular records. If another production file is necessary, stop for parent approval.

Later focused command, **not run in this sidecar**: `python -B -m pytest tests/test_reverse_target_invocation_receipts.py tests/test_rag_invocation_receipts.py tests/test_reverse_target_health.py tests/test_rag_index_manifest.py tests/test_rag_service_boundary.py tests/agent/test_rag_tool_contract.py tests/agent/test_decision_dynamic_bindings.py -q -p no:cacheprovider`. New receipt tests require later implementation/dependency release; all fixtures must be synthetic and isolated from real assets/environment/network.

## 5. B1 — normal entry and continuation vertical slice

- [ ] After separate B Web release against §12's ordinary source re-pin, add actual app `/ws` RED using the reviewed async semantic factory and a B profile injected only by server. Task7A's assembly-only guard is historical: reuse the now-wired semantic carry path, retaining the legacy/no-carry closures. Do not enable incomplete B under revision7. Obtain real anonymous session cookie through protected HTTP/matching origin. Run actual admission→bridge→loop→Session; never register a test-only route or fake scope/loop result.
- [ ] Verify selected trusted molecule, same-owner per-socket chat history, explicit local RAG request versus RAG-true chat, forbidden whole request, partial status and subsequent normal turn. No static workflow fallback.
- [ ] Write continuation RED: source-role proof or source/generator/main-model generation changes before CAS; foreign/repeated nonce; altered original obligations/history; unsupported new count on resume. Waiting record stays unchanged on invalid claim; uncertain CAS never dispatches again.
- [ ] Add actual `/ws` negatives using the real producer/core fixture paths from §4a/4b: tampered same-length popcount, same fingerprint/different weight, all-invalid versus valid-empty RAG, and source change before resume. Assert no successful known-subset/no-hit completion, no static fallback, and no target/generator downstream call on rejected evidence. Sealed prior reports remain historical; current metadata cannot repair their receipts. Valid receipt route still uses existing mount/ACK tests, not a lab-only replacement.
- [ ] Add snapshot version and replay with exact resolved inputs/proofs/operation keys plus A2 history prefix. Reconstruct source descriptors from sealed data; do not trust model-authored messages/handle maps. Keep existing budgets, config epoch and one lease.
- [ ] Run GREEN actual-route and core replay. B1 only released after its source/claim gaps are closed; successful isolated bindings alone are not normal-entry completion. Begin B2 immediately under its already specified dependency gates, not a fresh framework phase.

Future command: `python -B -m pytest tests/agent/test_web_dynamic_bindings.py tests/agent/test_decision_binding_continuation.py tests/agent/test_decision_continuation.py tests/agent/test_decision_continuation_store.py tests/agent/test_decision_protocol_recovery.py -q -p no:cacheprovider`.

## 6. B2 — one logical invocation, bounded replenishment and actual target consumption

Read-only source verification at `5db56b0`: generator `_generate_with_retry` line 277 is a `range(1, max_attempts + 1)` loop, default five, accumulating canonical-unique valid candidates and stopping at count. Generation/optimization helpers (475/501) each call `_call_llm_sync` once per round. That helper (542) supplies `max_tokens=1000`; async waits are 30 seconds, while sync `generate` is called directly. `src/web/models/ollama_model.py::generate` line 52 issues one POST with client timeout 150 seconds and no explicitly configured retry transport. These are inspected source facts, not executed boundedness/cleanup tests. Current candidate quality lacks actual backend attempt count; text-only model return drops usage. Root loop default deadline is 300 seconds, with separate decision-model and outer-tool counters; none proves inner-call budget accounting today.

- [ ] Pin landed 4C/G2 and exact normal-entry generator client. First add counted offline characterization around the **real** helper, real client method and lowest-level transport (never bypass helper with a mocked aggregate result). Count logical producer entries, rounds, model calls, transport dispatches and settlements separately. Include injected sync/coroutine branches; deny network and use no assets. Existing multi-call replenishment is expected, not a defect to make RED.

| Probe | Expected within a single logical invocation |
|---|---|
| Ten valid unique candidates on first response | One backend attempt, complete count |
| Ten valid unique candidates accumulated over several responses | Two to five backend attempts, complete count; no outer reentry |
| Empty/error responses on all rounds | At most five actual dispatches, honest failure/shortfall |
| Invalid/duplicate/insufficient responses | At most five actual dispatches, partial/failure unless the exact count is reached |
| Preflight failure | Zero backend dispatches; no invented attempt receipt |
| Cancel/deadline during a round | No later round after stop; all in-flight work drained before lease exit |
| Settled or uncertain invocation proposed again/resumed | Zero additional producer entries or backend requests |

- [ ] Write RED for missing actual backend-attempt receipt and budget persistence, not merely a second legitimate backend call. Force both model-level and transport-level exceptions/timeouts; prove no automatic backend transport retry beyond the known five-round bound. An untraceable/multiplied call or unbounded helper fails B2 release; report exact source/branch and request a narrow amendment rather than introducing a one-call restriction.
- [ ] Write actual-loop RED target lookup→generation: model sees missing/ambiguous/unavailable target and chooses clarify; healthy matching record instead permits generation. Spy actual producer prompt/input for exact target/seed/count/temperature in each internal call. No fixed task order or tool-name-only assertion.
- [ ] Write RED repeated proposal with new decision ID/same purpose, changed target/seed, resume/restart, commit-then-error, running/failed/corrupt checkpoint and invalid/revoked target. One reserved logical slot per root request; adapter/decision/continuation never reinvoke completed, partial, failed, cancelled or uncertain generation. A settled shortfall is an observation, never successful ten-candidate completion. Changed input hash cannot bypass the consumed root slot.
- [ ] Reuse Session running checkpoint/journal and `allow_retry=False` for outer attempts. Add effective per-request non-replay spec only if tests demonstrate it is needed; do not change shared factory flags/retry policy. Persist logical slot and per-backend dispatch reservation/settlement through existing execution metadata, with count in final receipts even on partial/failure. Crash between reservation and receipt remains uncertain and cannot restart/continue generation. No new database schema, `generation_attempt_limit`, workflow canonicalization or 4C retry-field edits.
- [ ] Write RED root budgets: every internal dispatch charges request count, repeated prompt plus requested completion-token reservation, elapsed latency and cleanup; no fresh deadline on each round/resume. Keep existing decision-model counter distinct from generator subcounter/root totals. Existing per-call cap requests 1000 completion tokens, so five calls reserve up to 5000 completion tokens **plus all prompt bounds**, not zero-cost hidden calls. Text-only usage is unknown, not zero: charge conservative reviewed reservations and persist them. Missing enforceable prompt/token allowance blocks dispatch/release pending review; no invented usage or model-authored budget. Stop before a round when remaining allowance/deadline is exhausted and preserve unmet-count status.
- [ ] Write RED cancellation during blocked backend/client cleanup, between replenishment rounds, and repeated cancellation. Actual A2 lease blocks model switch/shutdown until **all** workers, requests, async tasks and owned cleanup settle; stop launching remaining rounds. Client timeout/closed loop alone is insufficient proof. Narrowly implement request-local telemetry/deadline/cancel/cleanup control through existing wrapper/Session; never mutable per-request fields on the shared generator. If client ownership cannot prove drain within the approved files, fail release and request exact scope amendment, not detached success. Sanitize touched raw logs.
- [ ] GREEN requires one logical entry, zero to five actual backend dispatches with honest receipts, successful multi-round ten-candidate completion when responses permit, and no logical replay across normal `/ws` cancellation/continuation. Static generation still uses its existing bounded helper and typed lifecycle without a new input parameter. A deliberate new sample after settlement requires a new user request; internal replenishment before settlement does not.

Future command: `python -B -m pytest tests/agent/test_decision_generation_once.py tests/agent/test_generation_ranking_contract.py tests/agent/test_generation_ranking_contract_integration.py tests/agent/test_generation_temperature_transport.py tests/agent/test_generator_optimization_input.py -q -p no:cacheprovider` (last file requires landed G2).

## 7. B2 — CandidateSet identity, count/core and real ranker joins

- [ ] Write actual-loop RED generation → property/ADMET/activity: actual generator over counted scripted backend, real CandidateSet sanitizer and Session alignment. Assert each tool receives all exact canonical structures, original user activity target and immutable `(source evidence, candidate_id)` mapping; preserve stored original spelling without claiming raw model spelling.
- [ ] Test duplicate/invalid/excess/shortfall candidates, wrong/missing IDs, duplicate/foreign result rows, same SMILES in different sets, partial downstream and mandatory unavailable activity/ADMET endpoints. No known-good subset completion or automatic partial-batch propagation.
- [ ] Add explicit-core RED using real RDKit and independent positive/negative structures. Missing RDKit/invalid core/chirality mismatch fails closed, never uses evaluation's string fallback. Test unchanged optimization seed: no novelty claim; explicit novelty/improvement request rejected unless separately approved check exists.
- [ ] Set trusted `candidate_source` and target preconditions on dynamic steps; validate conflicting IDs before existing alignment; preserve normalization→alignment→proof→ledger→seal ordering. Add deterministic obligation checks for count/core/full coverage without filtering/repairing source observations into success.
- [ ] Write actual `CandidateRanker` RED multi-role joins: source CandidateSet+full properties, optional absent ADMET/activity, mandatory absent scoring fields, wrong target, cross-set same canonical IDs, swapped roles, missing/unrankable candidate and top-N shortfall. Assert actual scores/weights/prefix, never a copied scoring formula.
- [ ] Use existing bounded `BindingResolver` workflow selection to build exact ranker envelope. Omit optional roles explicitly; do not synthesize risk_count, probability or normalized activity from current ADMET/family outputs. Mandatory analysis and mandatory score contributions are separate obligations.
- [ ] Add model finish evidence-closure RED: valid arbitrary evidence citation cannot hide omitted mandatory source; full chain succeeds only when coherent required coverage holds. Scientific output uses actual values and missing labels, not model-written answer numbers. Run GREEN.

Future command: `python -B -m pytest tests/agent/test_decision_dynamic_bindings.py tests/agent/test_decision_binding_requirements.py tests/agent/test_candidate_alignment.py tests/agent/test_candidate_ranker.py tests/agent/test_generated_candidate_validation.py tests/agent/test_dynamic_run_session.py -q -p no:cacheprovider`.

## 8. B2 — landed G1 dynamic report and actual normal-route ACK

- [ ] Require reviewed/landed G1; current parent record is PR77 `f48039e` in CI, not landed, superseding the earlier dirty static freeze. Write RED actual dynamic Session checkpoints/ledger→G1 report: the inspected static projector returns `unsupported_binding`; recheck the landed interface. Do not remove this guard without replacing it with verified binding reconstruction.
- [ ] Extend only dynamic source-proof branch; retain existing full observation/status/source/presentation checks, snapshot race recheck and output limits. Reconstruct exact dynamic bound input from source roles and compare hashes/IDs; no property/ranker reexecution. Preserve real `weights_used`, null optional components and partial labels.
- [ ] Use actual normal `/ws` frames for generation→analysis→ranker request, feed same candidate/report frames to existing home normalizer/DOM/controller, return exact mounted ACK bodies to actual protected confirm HTTP, then selected follow-up `/ws`. A handcrafted DTO-only test is insufficient. Mark scripted generator/provider as contract evidence.
- [ ] Negative same-frame tests: unmounted/misordered keys, foreign cookie, stale revision, altered binding proof, forged succeeded step/null source and omitted rank weights. Candidate restoration never restores unverified numeric report or reruns generation. Partial source is never promoted by later selection/ACK.
- [ ] Run GREEN, existing A2 cancellation/multi-turn/socket/config tests and G1 static report regression. Actual browser/real-provider repeats are separate parent-authorized package-8 work, not executed by Node/ASGI fixtures.

Future command: `python -B -m pytest tests/agent/test_dynamic_evidence_report.py tests/agent/test_web_dynamic_bindings.py tests/agent/test_scientific_reference_execution.py tests/agent/test_scientific_reference_web.py tests/agent/test_evidence_report_frames.py -q -p no:cacheprovider` (last file requires landed G1); `node tests/home_evidence_report_test.js`; `node tests/home_scientific_references_test.js`.

## 9. Review / completion matrix

| Gate | Minimum evidence | Not a substitute |
|---|---|---|
| Whole admission | Exact A1 SPEC4 + B count/target/core/endpoint/score coverage through real route | Keyword match, required tool names, defaulting unknown request to chat |
| Dynamic dataflow | Actual loop/Session, captured input hashes/compound IDs, observation-dependent next action | Static Planner plan/order, direct tool-only tests |
| Generation lifetime | Logical invocation at most one/root slot, backend attempts at most five and actually counted, durable receipts/root budgets and all consumers drained | Factory idempotence=true, adapter retry=false alone, treating internal replenishment as replay |
| Source truth | Per-invocation loaded-source/verified-popcount/effective-weight receipts; same-call RAG valid-empty versus invalid-discard; actual-loop and `/ws` rejection/reuse tests | Row counts/hashes alone, post-call metadata, typed shapes, synthetic fixtures claimed as real science |
| Ranking | Actual Ranker and sealed aligned role inputs, exact required contributions/top-N | LLM numbers, pIC50→probability guess, unknown ADMET risk=0 |
| G1/normal UI | Dynamic full-proof branch + same actual WS frames→mount→ACK route | Static report fixture or send treated as ACK |
| Final goal | Integrated reviewed revision, required repeated real normal-entry cases, including separately approved Vina C | B1/B2 offline pass or all twelve proposed rows merely listed |

- [ ] Parent schedules relevant focused/full Agent/root/Node/compile/CI verification only in later implementation context. Record failures/skips honestly and distinguish contract/real-tool/real-decision/browser evidence; no fake scientific passes.
- [ ] Parent reviews exact diff/file ownership, no shared factory changes, no A1/A2/G1 working-tree overwrites, and no migration of incompatible waiting runs. Any tool behavior beyond binding, observable bounded replenishment and minimal logical non-replay/budget/cleanup controls requires explicit scope amendment; removed schema/workflow plumbing is not silently reinstated.
- [ ] Separate P7-C design/release for Vina consent, receptor/ligand/grid ownership, at-most-once side effects, new finite energy/pose/hash and authorized download. Never expose staged helpers in B or label ranked candidates as docked.
- [ ] Parent decides releases and whether supported partial scientific scope is acceptable; user goal remains packages 1–8, no deployment. B1, B2, C and package-8 final proof are tracked separately; this sidecar completes none of them.

Historical design handoff: **parent-approved receipt amendment, local commit of exactly two docs following `74739aa`**. This is not permission to commit the §11 draft. B2 remains one logical invocation/at-most-five internal rounds; §11 now proposes the explicit inclusive root ceiling while retaining old main/outer limits, non-replay/drain and model strategy. Exact dependency pins, actual stable mmap/source proof and separate implementation release remain required; do not infer a safe snapshot from design approval. No code/tests/assets/environment/network/push/merge here; P7-B, P7-C and package-8 real normal-route proof remain pending.

## 10. Source re-pin audit after ordinary Task6 — implementation still closed

The earlier dependency statuses are historical. Parent and independent readonly
audit inspected **committed** ordinary source `dc1295e258c3996837bba6435eefd217c4d066ad`,
not the active Task7A app/fixture changes. Local Git confirms landed 4C `0295e996`,
G1 `be0219e`, A1 `782cd13` and A2 `ed284b7` are its ancestors. This is local source
identity evidence, not a fresh remote CI/merge check. Ordinary semantic Web work
is still unmerged/incomplete, so its runtime entry is not yet a frozen B API.

Two concrete additions are required before a B implementation release:

1. **Explicit profile/capability mapping.** Current `src/web/ordinary_capabilities.py`
   wires only the original four tools and has no independent RAG permission
   projection. `src/agent/contracts/ordinary_admission.py` accepts only
   `ordinary-semantic-original-four-v1`. The B1/B2 design must define its closed
   server profiles, canonical tool-to-capability mapping, separate RAG permission
   and immutable generation/view. Add only those necessary files to the later
   reviewed allowlist; do not silently broaden the original profile or derive
   permission/readiness from registration or model intent. Await the final Web
   factory/publication interface rather than preserving the old constructor-only
   assumption.
2. **Compose with the shared budget/continuation contract.** Existing loop calls
   now accept `admission_carry`/`admission_exchange`; semantic snapshots use
   revision7, while no-carry remains revision6. `total_model_requests` specifically
   means intent plus decision requests. B must preserve that meaning and add
   separately named generator counts/root reservations, with an explicit ceiling
   relationship in the next written amendment. Reuse active-segment credit,
   pre-CAS checks, post-delivery downward clamp and per-invocation dispatch guard;
   do not recreate old A2 accounting. The outer guard is not sufficient for the
   generator's up-to-five internal backend requests.

Minimal subsequent sequence remains: written amendment and source/allowlist
approval; B1 closed v2 bindings and same-invocation RAG/reverse receipts with real
loop/Session/seal/replay tests; B1 normal-route wiring once ordinary Web freezes;
B2 actual bounded generation, whole-CandidateSet analyses and real Ranker joins;
then dynamic report/DOM mount/protected ACK/selected follow-up on that route.
B1 core can be developed independently after release, but its component pass
does not close B1 actual-route acceptance. Keep original positive RAG, generation,
ADMET, reverse and ranking cases; missing mandatory scoring fields are not zero.

This audit changed documentation only. No implementation, tests, imports,
configuration/credential/assets inspection, network, publication or live run.
Stable source ownership, generator budget bounds, B integration, C and final
package8 acceptance remain pending; the audit is not a new passing test.

## 11. Proposed implementation amendment: profiles and inclusive root accounting

### 11.1 Draft authority and exact source facts

This section implements companion spec §11 **only after approval/release**. Draft
base is docs HEAD `a3ceb1880b61ad58000b8ad5b667a15f03411ef4`; all new source reads used
`git show 1da64f2694203de0ef3f3c875ceb775e4be0dee4:<path>`, never the dirty ordinary
checkout. Task7A at that pin exposes async assembly/capability publication but
intentionally blocks semantic Web/direct dispatch. Task7B/C execution/delivery APIs
are still unfrozen. Existing §1/§8 PR-status descriptions are historical; use §10's
local ancestry record and the new committed pin, then confirm the final integrated
revision before release. No fresh remote landing/CI claim is made here.

The source facts driving the amendment are concrete:

- `ordinary_capabilities.py` defines only `ordinary-semantic-original-four-v1` and
  tests product feature names as if they were canonical registered tool names.
  `ordinary_admission.loop_admission` rejects all other profiles/catalogs. Extend
  these boundaries explicitly, not by registering more tools or trusting an intent.
- `decision_loop.py` counts intent plus decision/repair under16 and already accepts
  carry/exchange; `decision_continuation.py` uses semantic7/no-carry6 and adjacent
  pre-CAS TTL/credit checks. Preserve these paths; B adds a profile-selected8.
- Generator `_generate_with_retry` has up to five rounds; both generation and
  optimization reach `_call_llm_sync` with1000 completion tokens. Current
  `OllamaModel.generate` owns the actual POST but returns text/error-as-text only.
  Query/evidence character bounds do not provide a proven token bound. The chain
  `GenerationToolAdapter` → `_CandidateContractAdapter` →
  `LegacyPythonToolAdapter._invoke_guarded` calls `tool.execute(payload)`; a relay must
  preserve that adapter's reservation and worker ownership, not create another pool.

No commands below were executed. All RED/GREEN descriptions are future assertions,
not expected numeric pass counts. Parent retains all historical failures/UNKNOWNs.

### 11.2 Minimal future allowlist changes and responsibilities

These additions are **proposed for review**, not silently authorized by the old
§2 allowlist. Existing B files keep their previously bounded responsibilities.

| File | Narrow future responsibility |
|---|---|
| `src/web/ordinary_capabilities.py` | Closed B1/B2 profile constants, the exact nine-tool mapping in spec §11.2, B catalog and pure independent-RAG request projection; old call defaults identical |
| `src/agent/contracts/ordinary_admission.py` | Admit only the two named B profiles/catalog/assessment revisions; reuse strict carry/binding/segment validation, keep ordinary metadata and original profile output unchanged |
| `src/web/app.py` | Server-only profile injection into existing async assembly/base projection/writer pair; retain failure cleanup, no synchronous semantic/default activation |
| Existing proposed `src/agent/contracts/decision_bindings.py` / `src/agent/harness/decision_bindings.py` | Closed B root/gen receipt validation and profile policy alongside binding proofs; no separate budget framework/module/store |
| Existing `decision_loop.py`, `decision_continuation.py`, `decision_execution.py`, `run_session.py`, `ledger.py` | Root checks, request-local generator control, durable reservations/settlement, B8 fingerprint/replay and evidence propagation using their current ownership/execution paths |
| Conditional `src/web/models/ollama_model.py` | Only reviewed optional per-request control/receipt seam at `generate` POST; async equivalent only if the supported generator binding needs it; no token discovery or default behavior change |
| Conditional `src/agent/tooling/adapters.py` | Only if existing request-local view cannot relay control: optional invocation-scoped generator relay through the same validation/slot/owner/finally path, default no extra kwargs |

The two conditional additions need parent scope approval after the source-backed
proposal below; they are not currently approved code scope. Shared factory flags,
4C generation input schema/workflow canonicalization, CandidateSet schema, Ranker
formula, model selection/temperature and five-round strategy remain unchanged.
No new config file, tokenizer dependency, asset loader, protocol service or pool.

New proposed tests: `tests/agent/test_decision_binding_profiles.py` and
`tests/agent/test_decision_binding_budgets.py`; conditional client test
`tests/test_ollama_generation_budget.py`. Use already planned
`test_decision_generation_once.py`, `test_decision_binding_continuation.py` and
`test_web_dynamic_bindings.py` for the actual vertical slices, not duplicate loops.
Related existing modification allowance: `tests/agent/test_ordinary_capabilities.py`,
`test_ordinary_app_assembly.py`, `test_ordinary_admission_budget.py`,
`test_ordinary_continuation.py` for additive compatibility negatives/positives only.
Keep old assertions/default outputs/timeouts intact. These new names are future
files, not claims about APIs or tests already present at the pin.

### 11.3 Amendment A — implement profile contracts before B admission

- [ ] Parent reviews spec §11, explicitly chooses inclusive root16 and B-only8,
  and releases this contract slice. Do not change the meaning of the original
  `ordinary-semantic-original-four-v1` or reinterpret the main counter as21.
- [ ] RED in `test_decision_binding_profiles.py`: table-test every tool→feature
  row from spec §11.2 under B1/B2, unknown profile/feature-as-tool/extra-name rejection,
  registered-but-unwired, wired-but-unregistered, false/readiness-unknown and forged
  ready cases. All original-four calls must have exactly the old serialized view.
  Required positives include B1 RAG-only with scientific=false, B2 all nine when
  permitted, and ordinary chat with RAG=true and zero allowed/required tools.
- [ ] Introduce closed profile lookup/mapping in `ordinary_capabilities.py` first;
  the existing builder gains optional server-only B arguments with old defaults.
  B requires explicit reviewed wired names, captured names/flags and independent
  RAG flag, never registry discovery. Reuse frozen `CapabilitySnapshot` bounds.
  Define B assessment revision `whole-request-b-v1` together with its existing
  whole-request admission task; original `whole-request-v1` stays exact. Extend
  `loop_admission` only to the finite profile/catalog/assessment combinations.
- [ ] RED binding mutation table: altered B profile, view digest, permission,
  catalog/assessment revision, model/capability generation and original obligations
  reject before Session/dispatch or continuation CAS. Same tool list is insufficient.
  Ordinary intent approval with a scientific/tool request grants no tool. Unknown
  classification/error does not take a chat fallback. Pure display validation still
  rejects an unavailable/unknown capability assertion against the captured B view.
- [ ] Implement the minimal B binding extension in the existing contracts and
  admission/catalog paths. One source of tool mapping, no second hardcoded aliases
  table in Web/replay. Keep actual `allowed_tools` authorization separate from view.
- [ ] RED additive assembly/writer tests: server B profile unknown rejects early;
  missing approved wiring leaves entry blocked; new model/capability stage fails
  before mutation and closes only its new owned candidate; old reader/intent/loop
  retains the old pair until drained. Snapshot captured once, then flag projection
  cannot reread app fields. Generator remains `gmm-llama:latest`, not main model.
- [ ] Implement only the existing `create_async`/base/project/writer seams in app.
  Actual B route integration remains blocked until §11.6/§12; do not make incomplete B
  work through original A2 or semantic7 dispatch. GREEN profile,
  carry, assembly and original-four tests; record actual commands/counts/errors.

### 11.4 Amendment B — shared root receipts and B8 replay

Contract-first within existing proposed `decision_bindings.py`: define strict
version1 `b_root_budget` / at-most-five `generator_requests` views before their
loop/Session consumers. Reuse the same Task6 carry/exchange; add no new wall clock.
The following is the future arithmetic oracle, **not an existing callable API**:

```python
# M preserves the existing intent + decision/repair debit semantics.
M = intent_requests + decision_requests
G = generator_request_debits  # dispatched, uncertain, or reserved; not refunded on unknown
root_request_debits = M + G
can_main = M < configured_main_limit and root_request_debits < 16
can_generator = G < 5 and root_request_debits < 16
# A candidate intent also requires two remaining main and root slots.
```

- [ ] RED arithmetic/identity tests: intent1+decision10 represented as M11 permits
  five gen requests to reach16, then zero additional main/gen; M14/G1 permits
  one more request, not both; M16/G0 rejects all generator calls; M1/G5 forbids
  a sixth even with root credit. Add configured main limits below16, schema repair,
  intent attempt0/no dispatch, and no double charge when a reservation becomes a
  dispatch. An outer logical slot is charged in the existing tool counter, not
  additionally in root requests. Tools still max12, chat still zero tool attempts.
- [ ] RED journal state cases: reservation failure→no HTTP; successful reservation
  plus proven queued cancellation→durable `not_dispatched`; ambiguous dispatch,
  commit-then-error or lost settlement→retained debit/unknown/no replay. Ensure a
  second same-ID persistence attempt makes zero extra backend calls. Reject bool,
  NaN, negative/mismatched sums, missing/duplicate IDs/rounds, >five receipts or
  >16KiB B budget view. Main `model_calls` contains only actual decision records;
  generator and intent receipts do not masquerade as decisions.
- [ ] Wire root check into existing intent/decision/repair and inner generator
  reservation points without reducing `max_model_requests` from16 or resetting
  counters after clarification. B1 gen debit is0; B2 shares remaining root credit.
  Persist reservations before dispatch, factual settlement after it, retaining
  separate observed versus conservative/unknown counts. Never store raw payloads.
- [ ] RED B8 replay: original no-carry6 and ordinary7 positive controls unchanged;
  B8 requires validated B server profile and full matching budget/binding policy.
  Inject6/7 as B, B1 as B2, removed root fields, changed token policy/gen epoch,
  impossible counters or altered generator slot/history/seals: actual CAS0 and
  provider/tool0, durable waiting record unchanged. Explicitly test old profiles
  with forged B keys do not gain B authorization. No migration/converter.
- [ ] Select B-only8 in `configuration_digest`, snapshot and claim from validated
  server profile; preserve the revision7 core fields and exact history/system
  reconstruction. Validate root/gen closure and all old science/source/display
  checks before adjacent TTL/credit/root check and actual CAS. After successful
  CAS, deadline expiry still means consumed nonce plus zero dispatch, not rollback.
- [ ] RED logical-clock tests reuse module-local aliases: start before refresh,
  charge queue/validation/executor/internal rounds/cleanup/delivery, no refund at
  snapshot, paused human wait only, TTL=`bridge_returned_at+900`. Use the existing
  100(snapshot)/120(bridge)/130(delivery) fixture: TTL1020, not1000/1030; available
  credit downclamps through130. At1010 positive credit alone is insufficient:
  require current identity/source, intact journals and unused valid CAS nonce.
- [ ] GREEN actual loop/Session/store and original budget/continuation/history
  tests. Do not claim Web delivery accounting from core tests alone.

### 11.5 B2 gate — bounded generator/client dispatch, then full dataflow

- [ ] Before client edits, present the exact proposed token grant to parent:
  the exact closed grant fields/bounds in spec §11.5, pinned generator
  artifact/tokenizer/backend template/options identities and
  bound algorithm, context-limit enforcement, total generator-token allowance
  and per-prompt ceiling. Values/proof are **not available from inspected source**;
  do not choose an arbitrary character multiplier or infer tokens from output.
  Missing proof keeps B2 backend dispatch blocked with the fixed unavailable code.
  This does not hold B1 receipt/core work or waive B2 multi-round positives.
- [ ] Parent decides the named `OllamaModel.generate` (async only if applicable)
  optional keyword-only control seam and any necessary adapter relay. First RED
  demonstrates current text-only status/usage loss and no dispatch-time hook using
  the real generator/helper/client with lowest HTTP transport replaced, not a copied
  method. Keep legacy generate result/signature behavior as a positive control.
- [ ] Define a concrete request-local control in the existing B execution wrapper:
  captured segment/cancel, validated token grant, stable root/logical IDs, and
  idempotent journal reserve/dispatch/settle callbacks. It is not input JSON or a
  mutable field of shared generator/adapter/client. At each formatted final prompt
  obtain its trusted upper bound including backend overhead, reserve bound+1000,
  then check at actual POST. If adapter relay is needed, test shared slot identity,
  worker reservation/rollback and `finally` reset with concurrent roots and queued
  cancellation; no new executor and no kwargs on no-control legacy invocations.
- [ ] RED token/request table: unknown/stale/missing grant→zero HTTP; insufficient
  prompt+1000 or context capacity→zero HTTP; exact allowance positive; repeated
  prompts charged each round; unknown usage charged at reserved upper bound;
  over-bound actual usage stops/invalidates, never erases the violation. Ensure
  actual POST gets1000 and the original prompt/temperature/gmm. Test HTTP error,
  partial response, disconnect and cancellation as factual failure/uncertainty,
  not the legacy apology string misreported as successful molecular generation.
- [ ] Preserve and extend §6 real-helper scenarios: one logical invocation,
  first-round completion, multi-round ten-candidate completion, five exhausted
  rounds, smaller root budget exhaustion, unchanged seed, and no outer replay.
  Queue/validation delay and cancellation before every inner request block new
  dispatch; blocked in-flight threads/async calls must physically settle before
  lease/close, including failure of another cleanup. Barriers release in `finally`.
  A timeout or closed event loop is not evidence of drainage.
- [ ] GREEN §6–§8 actual generated CandidateSet→all-candidate property/ADMET/activity
  →actual Ranker→full dynamic report dataflow, preserving mandatory evidence/count/
  core/target checks, exact weights/missing values and no pIC50/risk fabrication.
  Candidate ACK still confers selection authority only, not calculation success.

### 11.6 Independent sequence, Web dependency and review evidence

After written approval and separate release, B1 closed profiles/bindings/receipts
and loop/Session/B8 replay can be developed independently of unfinished Web. B2
root/gen contract work may proceed against the pinned core after its budget choice,
but backend dispatch/release waits for the proven token grant and reviewed narrow
control seam. No stock generator is silently exposed while those gates are open.

Historical gate, now source-re-pinned in §12: before actual B Web work parent
must still release its precise write scope. Compare committed `create_async`, captured base/project/writer,
runtime carry/one-lease/intent cancellation, bridge checkpoint and post-delivery
clamp APIs. Adapt §5 to those **actual** signatures before writing route tests.
Use real mounted app/middleware/A1-or-B admission/loop/Session/CAS, not source-array
mocks. Any later interface drift requires another pin, not copying active files.
Original REAL010/DIVERSE015/multi-turn and B016/B017/RAG/generation/ADMET/reverse/ranking
positives remain full gates; neither component tests nor an honest blocked request
count as their completion. No second loop/budget service/static fallback.

Future focused commands must be invoked through the parent-approved pre-import
isolated runner with its verified SHA, temp stores/config and tracked-data copies;
the following are pytest targets only, **not commands to execute in this docs task**:

```text
python -B -m pytest tests/agent/test_decision_binding_profiles.py tests/agent/test_ordinary_capabilities.py tests/agent/test_ordinary_app_assembly.py tests/agent/test_decision_binding_requirements.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_decision_binding_budgets.py tests/agent/test_ordinary_admission_budget.py tests/agent/test_ordinary_continuation.py tests/agent/test_decision_binding_continuation.py tests/agent/test_decision_continuation.py -q -p no:cacheprovider
python -B -m pytest tests/agent/test_decision_generation_once.py tests/test_ollama_generation_budget.py tests/agent/test_generation_ranking_contract_integration.py tests/agent/test_web_dynamic_bindings.py -q -p no:cacheprovider
```

Run only the released focused slice; do not invent a runner ABI or missing current
test APIs. Actual RED must demonstrate the intended boundary, not collection/import
failure. Record exact hashes, commands, counts, warnings, failed fixtures and ended
sessions; no prescribed pass count or blanket closure of previous UNKNOWNs. Parent
schedules independent SPEC/QUALITY and later broader/real gates. This draft runs
nothing, changes no production defaults and completes neither B nor C/package8.

### 11.7 Draft self-review / remaining decisions

Self-review maps spec §11.2→§11.3, §11.3–11.4→§11.4, §11.5→§11.5, and full unchanged
scientific endstate→original §§4–8 plus §11.6. Closed profiles/mapping, independent
RAG, capture/fingerprint, inclusive16 versus old main16, at-most-five non-replayed
generator requests, receipts/unknown handling and B8 compatibility are specified.
No claimed tokenizer bound, runtime pass or real scientific result has been added.

Open decisions are bounded: (1) explicit parent approval of root-inclusive16/B8;
(2) a trusted enforceable generator token grant and the named client/possible
adapter seam; (3) final ordinary Web source pin. They do not authorize a wider
model framework or weaken any positive acceptance case. Stop after this two-doc
draft/diff/hash review; no implementation release or commit follows automatically.

### 11.8 Written review and local planning checkpoint — 2026-09-26

Independent written review approved this amendment with no P1/P2. Its one P3
source-name correction is applied above: the pinned generation path delegates
to LegacyPythonToolAdapter, not a nonexistent CallableToolAdapter. The review
read only the committed source pin; it did not execute tests or verify assets.

Parent adopts inclusive root16 and B-only revision8, and approves the named
Ollama/adapter invocation seams as bounded design scope, not immediate client
code authority. Before that code slice, the actual counted transport probe and
enforceable token grant still require evidence and an explicit implementation
release. B1 profile/binding/source-receipt core may be released separately after
source re-pin; B Web remains dependent on the final ordinary Web interface.
No implementation slice is released by this documentation checkpoint itself.

Parent authorizes a local commit of these two reviewed planning documents only.
No push, merge, test run, model activation or deployment follows. All B1/B2,
C and final package8 completion gates remain pending.

## 12. Re-pinned first TDD slice: pure B capability profiles only

### 12.1 Pin, scope and stop conditions

Use spec §12.1's local pins: current worktree `D:/MedChat/molecular_chat_system_worktrees/dynamic-bindings-b1`,
branch `codex/dynamic-bindings-b1`, starting HEAD d672497; source/tests equal684e4a1,
production equal67ad078 (e2e654b/684 add docs/schema-test compatibility only).
Parent reports local ancestor checks and pre-edit B-doc equality to0487d79 as
recorded in the spec. PR81 is not confirmed merged; this is not landing evidence.

**Future code write allowlist:** modify `src/web/ordinary_capabilities.py`; create
`tests/agent/test_decision_binding_profiles.py`. No edits to existing tests, DTOs,
app, routes, loop_admission, carry, budgets or replay. No B execution under7.
Current authorization covers these two **documents only**, not those code files.

- [ ] Parent/independent plan review confirms spec §12.2's proposed exclusive flag
  semantics and precise defaults; parent then separately releases this two-file
  code slice and its single test slot. No automatic stage/commit/push.
- [ ] Confirm code base and clean unrelated state read-only. Preserve parent edits.
  Stop for pin/scope mismatch, not by silently widening this slice.

### 12.2 Future runner gate — rebuild for this worktree, never run the old pin

The ordinary launcher pins another repository. Do not execute/copy it unchanged,
even from this cwd. After code/test release, parent must rebuild/review the same
stdlib-only isolated launcher with `REPO` set to the resolved new worktree above.
No launcher file is created or executed during this docs task. Do not invent a
different runner ABI or use direct pytest/probe imports as a workaround.

- [ ] Read the rebuilt launcher before execution: verify its literal/resolved REPO
  and every child import/tracked-fixture source point here, not the ordinary tree;
  preserve synthetic temp cwd/config/store, pre-import socket ban, isolated
  `-I -S -B` startup and reviewed tracked-data copy/hash checks. No host asset/env
  discovery or provider access. Test payloads stay synthetic.
- [ ] Parent records/reviews the rebuilt SHA256. Old launcher hash
  `56584BEB8D671C6621D5F3BD8C8E5D7452F0EA216444E7A1FFEDD605A19A4B6C`
  identifies the old pin and is **not** approval of a new-path runner. If the new
  runner/hash has not been reviewed, stop before execution; do not self-approve.
- [ ] Future commands below assume that reviewed launcher is
  `scratch/ordinary_chat_offline_runner.py` in this new worktree. Run one session
  at a time; poll its handle to terminal, preserve failures, and diagnose one
  failed node before any repeat. Do not run full Agent or loosen existing timeouts.

### 12.3 RED: exact API, view arithmetic and negative table

Create only the new test module. Import the existing capability module normally;
inspect the existing function signature in the test body so missing new kwargs
fail an assertion, not collection. Use spec §12's exact builder signature,
constant names, catalog text, flag truth table and existing DTO bounds/reasons.
The first positive fixture is deliberately pure (not installed B wiring proof):

```python
def test_b1_rag_permission_is_independent():
    import inspect
    from src.web import ordinary_capabilities as caps
    assert {'binding_profile', 'wired_names', 'rag_enabled'} <= set(
        inspect.signature(caps.build_capability_snapshot).parameters)
    view = caps.build_capability_snapshot(
        registered_names=frozenset({'rag_search'}),
        provider_descriptor={'provider': 'openai_compatible', 'model': 'fixture', 'mode': 'json'},
        semantic_profile=True, intent_capable=True, original_four_profile=False,
        scientific_tools=False, permitted_names=frozenset({'rag_search'}),
        model_generation='model-fixture', capability_generation='cap-fixture',
        binding_profile='ordinary-semantic-b1-v1',
        wired_names=frozenset({'rag_search'}), rag_enabled=True)
    features = {f.id: f for f in view.features}
    assert features['rag_retrieval'].wired and features['rag_retrieval'].permitted
    assert not any(f.permitted for key, f in features.items()
                   if key not in {'ordinary_chat', 'rag_retrieval'})
    assert all(f.readiness == 'unknown' for f in view.features)
```

- [ ] Run the one node with the reviewed new-path runner (command below). Expected
  RED is absent profile parameters, not fixture/import/environment failure.
- [ ] Add table tests named `test_profile_flag_combinations_are_closed`,
  `test_b_canonical_mapping_and_wiring`, `test_b_permission_matrix`,
  `test_original_serialization_is_unchanged`, `test_builder_is_pure_and_detached`
  and `test_b_view_does_not_open_loop_admission`. Cover every §11.2 mapping for
  B1/B2 and these exact contrasts:
  - Explicit empty wiring versus missing wiring; registered-only versus
    reviewed-only; B1 excludes generator/ranker; B2 all nine permitted positive.
  - Four native scientific/RAG flag combinations; missing permitted name;
    intent_capable=False; all readiness unknown and exact reason precedence.
  - B+original_four_profile=True and B+semantic_profile=False reject; original
    default/explicit ID remain identical for every old bool combination.
    Original+nondefault B kwargs reject; unknown/None/non-string profile rejects.
  - Full registry including B1-excluded canonical generator/ranker and unknown/
    alias/noncanonical feature IDs is accepted but grants nothing extra; the
    same extras in B wired/permitted sets reject, not silently intersect.
    Original registered/permitted extra-ignore behavior stays exact. Sets/lists instead of
    frozensets and non-bool flags reject with the existing fixed error code.
  - Pin literal original expected views from the pre-change source, including
    ordered descriptions/reasons; do not compute the expected catalog from the
    modified implementation. Compare model_dump_json as well as plain values.
  - Detached descriptor/output mutation; frozen fields; no new ready value;
    existing size/schema bounds; I/O/health/registry hooks fail if invoked.
    Build a B carry using existing contracts, then assert unchanged
    loop_admission rejects it; no Session, provider, app or tool execution needed.
- [ ] Run the new module RED before editing production; record each cause. New
  assertions for already-preserved behavior may pass. Do not change old tests.

### 12.4 Minimal GREEN, unchanged regressions and freeze

- [ ] In ordinary_capabilities only, add the closed constants/immutable mapping
  and B catalog specified in spec §12.2. Validate new selector/flags/sets before
  projection. Keep the original branch's catalog/logic/output exact.
- [ ] Implement only spec §12.3's intersection and reason precedence using the
  existing CapabilityFeature/CapabilitySnapshot constructors. Reuse existing
  validators/error handling; add no DTO reason or new service/factory/discovery.
- [ ] Run the new module GREEN, then the unchanged capability and assembly
  regression modules with it. Do not claim RAG dispatch or B8 coverage from views.

Future exact commands, **not executed by this documentation task**, from the
new worktree after the runner/hash and code slice are released:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_profiles.py::test_b1_rag_permission_is_independent
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_profiles.py
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_profiles.py tests/agent/test_ordinary_capabilities.py tests/agent/test_ordinary_app_assembly.py
```

- [ ] Freeze after self-review: report exact diff/files/hashes, runner hash/pin,
  RED/GREEN commands/counts/warnings/durations/exits and no active sessions. Parent
  coordinates independent SPEC then QUALITY. No commit/stage/push by worker.
- [ ] Keep later app/carry/root/requirements/receipts/Session/B8/Web work explicitly
  incomplete under the existing full plan; retain B1/B2 positives and C/P8 gates.
  This first slice neither activates B nor replaces any final acceptance case.

### 12.5 Written approval and bounded implementation release

Independent SPEC/plan review approved the two-document re-pin at source684e4a1,
with no P1/P2. Parent adopts the exact exclusive flag and three-set rules in
spec§12; this is not permission to weaken original serialization or B execution
guards. PR81 is still awaiting the explicit per-PR merge response. This local
dependent branch may progress without describing that PR as landed.

Parent now releases **only** the two-file pure profile-view slice in §12.1 for
offline TDD, after this document checkpoint. A fresh implementation worker owns
`src/web/ordinary_capabilities.py` and new
`tests/agent/test_decision_binding_profiles.py`. It must not touch app, contracts,
admission, budgets, replay, existing tests or defaults. No B tool execution, B
publication, real model call, asset discovery or deployment is released.

Before test imports, parent recreates the ignored launcher for this worktree,
verifies that only its literal REPO differs from the reviewed ordinary launcher,
and obtains independent source review/hash confirmation. That reviewed launcher
and the serialized test slot are mandatory. Stop after the focused RED/GREEN,
unchanged regression modules and frozen code/test report; independent SPEC then
QUALITY must approve before a local source checkpoint or another code slice.

### 12.6 Landed ordinary dependency and launcher gate

The user explicitly authorized PR81 squash after its exact-head CI8/8 and
unresolved-review count0. Parent merged it as
`a1258d978450a70a55e71156060b4d8d88a8db29` and fetched origin/main.
Reviewed684e4a1 and merged a1258d9 trees both equal
`4619c55bb7ea5338979c1dce644f2ba764c0dbeb`. The B branch integrates that landed
baseline without changing source/tests. This supersedes the earlier pending-merge
status; no original worktree changes were staged and no deployment occurred.

Independent source review approved the new-path ignored launcher:
SHA256 `F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183`.
Normalized source differs from the reviewed ordinary launcher only in literal
REPO, now this dynamic-bindings-b1 worktree. Isolation, fixture hashing, normal
conftest, child flags and cleanup remain unchanged. No imports/tests were run
for the review. The §12.5 two-file TDD release can now use this launcher; all
broader B app/admission/root/receipts/B8 and B2/C/P8 gates remain pending.

## 13. Pure profile-view implementation evidence

The only production change is `src/web/ordinary_capabilities.py`; the only new
test is `tests/agent/test_decision_binding_profiles.py`. Existing tests, DTOs,
app assembly, admission, root budgets and execution are unchanged. Original
profile serialization is pinned independently; B1/B2 canonical mapping and
explicit wiring do not authorize execution. The original admission guard still
rejects B carries, with an original-profile positive control.

All implementation commands used MedChat Python with `-I -S -B` and the
§12.6 launcher in this worktree. Exact commands are in §12.4; evidence is:

| Run | Result | Exit | Duration |
|---|---|---|---|
| First new RAG-permission node, before implementation | 1 failed: required API kwargs absent | 1 | 0.98s |
| New profile module, before implementation | 33 failed, 1 passed: missing API assertions, not collection failures | 1 | 1.64s |
| New profile module, after implementation | 34 passed | 0 | 1.77s |
| New module plus unchanged capabilities/app assembly | 194 passed, 7 warnings | 0 | 14.77s |

Warnings are three SWIG and four FastAPI warnings. No skipped tests or fixture
failures. The implementation test session ended; no live scientific result is
claimed. No full Agent suite or CI has been run for this B slice.

Independent SOURCE/SPEC approved at HEAD6bc6dfc with no P1/P2. Frozen SHA256:

- Source: `BDEB4A6E0848940B8AED1D382B981D6C4D6DD198AD1EC686806EC0DDDDC540F4`.
- Test: `1DF3190F3353BDE61BC9C2D6CCCC809A4747E69B95C97E9D5DB6998C3BAF3A3A`.

Independent QUALITY also approved with no P1/P2, then ran the same three-module
command once: **194 passed, 7 warnings, 15.32s, exit0**. Its session reached
terminal; source/test/launcher hashes were unchanged. Parent verified
`git diff --check` and the two source/test hashes again. Both review gates now
permit a local checkpoint of these two files plus this plan and the ledger.
No B branch publication, execution activation or next implementation slice is
implied. This is a component milestone, not B1 normal-route or package8 acceptance.

## 14. Reference-argument contract TDD slice

**Pin:** b9f97597fa9b3501fc9dbf7280aae0f47c9fe2b8.
**Design:** companion spec§13. Parent uses the user's delegated recommended-choice
authority; independent written review must approve before code release.
**Files:** new src/agent/contracts/decision_bindings.py; modify only the B constant
definitions/imports in src/web/ordinary_capabilities.py; new
tests/agent/test_decision_binding_arguments.py. Do not edit existing tests.

- [ ] Review spec§13 and confirm the current four-tool serialization is unchanged.
- [ ] Write first RED without collection/import failure:
```python
def test_rag_user_reference():
    import importlib.util
    name = 'src.agent.contracts.decision_bindings'
    assert importlib.util.find_spec(name) is not None, 'binding contract missing'
    import importlib
    bindings = importlib.import_module(name)
    result = bindings.parse_binding_arguments(
        'rag_search', {'input_ref': 'user'},
        profile_revision='ordinary-semantic-b1-v1')
    assert result.model_dump(mode='json', exclude_none=True) == {'input_ref': 'user'}
```
- [ ] Run only that node; expected failure is absent new module, not bad fixture.
- [ ] Build table tests through a shared module-presence assertion. Cover all nine
  canonical tools in B2 and seven in B1; B1 generation/ranking rejection; missing/
  unknown profile/tool; input_ref=user versus evidence; both target forms;
  generator with/without target; rank properties-only/all roles/role order.
- [ ] Negative table: each unknown key and role, explicit null optional values,
  empty/oversize/whitespace/newline/URL/path IDs, user in evidence-only roles,
  duplicate rank IDs including input source, missing properties, wrong field types,
  booleans/bytes/list/tuple/subclass and custom Mapping or dict hook objects.
  Use synthetic markers only; assert fixed errors and zero custom-hook calls.
- [ ] Test actual outer decode_protocol_json rejects duplicate JSON keys before
  invoking parser; a valid outer tool envelope composes with parser. Preserve
  legacy parse_task_requirements rejection of version2 and original capability
  admission guards; do not claim this pure contract executes tools.
- [ ] Test preflight byte/depth/node boundaries including cycles, NaN/Infinity,
  lone surrogates, overlong multibyte text and secret-shaped synthetic fields.
  Test caller mutation, frozen nested fields, detached snapshots, stable JSON
  round-trip and reordering. Warm required lazy imports before I/O guard test;
  then fail all environment/file/network/registry/health callbacks if touched.
- [ ] Run module RED and retain actual failing reasons/counts before implementation.
- [ ] Implement frozen classes and shape-specific validators exactly per spec.
  Reuse EvidenceId, bounded JSON validation and existing secret detector.
  Public parser flow (all exceptions replaced by the fixed code):
```python
# lazy bound helper import may avoid existing harness/contract initialization cycle
validate_json(arguments, max_bytes=4096, max_depth=8, max_nodes=64,
              reason='invalid_binding_arguments')
# validate exact profile/tool/native dict, secrets, then chosen strict DTO
# full-match every reference, enforce only spec's closed shape rules
# return chosen_model.model_validate(arguments, strict=True)
```
- [ ] Move only identical B constants to the new contract; Web imports/re-exports
  preserve public names and identities. New parser/DTOs stay uncalled by runtime.
- [ ] Run new module GREEN once, then the joint regression below. If failure,
  diagnose its specific node before any retry; never relax timeouts/assertions.
- [ ] Freeze three source/test files with hashes, exact RED/GREEN results and
  no running sessions. Independent SPEC then QUALITY; only parent stages/commits.

All commands use the already approved worktree-specific launcher SHA256
F2DAB87A0C1648D8059E6104DC5EB460BE44C018363F8E0AB507D1F081ACC183.
Single test slot only; no ad-hoc app imports, full suite or external models.
The existing reviewed launcher may read/copy its three tracked evaluation JSONL
fixtures; existing app-assembly regression may import app and use synthetic
configuration inside that isolated environment. No real user configuration,
credentials or scientific runtime assets may be read. New argument unit tests
remain pure-contract tests. Do not alter the launcher or existing tests; poll
the same process until terminal.

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_arguments.py::test_rag_user_reference
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_arguments.py
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_decision_binding_arguments.py tests/agent/test_decision_binding_profiles.py tests/agent/test_ordinary_capabilities.py tests/agent/test_ordinary_app_assembly.py tests/agent/test_task_requirements.py
```

The prior read-only v2 audit found the old evaluator only consumes v1 molecular
sections. Keep its parser unchanged until closed v2 sections and deterministic
evaluators are integrated together; a widened parser alone is not safe execution.
This reference grammar is a prerequisite, not a replacement for that work or the
remaining actual-loop, receipts, B8/root, Web, B2, C and P8 acceptance gates.

### 14.1 Written review and implementation release

Independent review found two P2 documentation inconsistencies: omitted profile
would have raised a Python TypeError before the fixed-error boundary; blanket
import/asset wording contradicted the already-approved isolated fixture/app
regressions. Both are corrected explicitly in spec§13 and this plan. The same
reviewer rechecked and approved with no remaining P1/P2. No tests were run for
that review. Parent adopts the recommendation under delegated user authority
and releases exactly the three-file slice after this two-document checkpoint.
Use a fresh worker, the unchanged reviewed launcher and serialized test slot;
freeze for independent SPEC then QUALITY before source commit. No broader B
execution, model activation or publication is released by this checkpoint.

### 14.2 Read-only RAG source-ownership audit for the next integration

A separate source audit at78675e4 traced adapter → RAGSearchTool.execute →
service.search_similar_molecules_sync → embedding → retrieval.search_molecular_index.
No assets were opened and no runtime reproduction was performed. It identifies
these concrete proof gaps, not verified fixes or passing scientific evidence:

- service.initialize parses CSV before later manifest path hashing; dataframe
  rows can therefore differ from the later path identity despite equal count.
  Index creation similarly awaits embeddings before hashing the final CSV path.
  A same-byte private load snapshot and coherent generation publication are
  required; a receipt assembled afterward cannot establish loaded identity.
- Retrieval rereads service fields after embedding; index/dataframe/manifest
  publication and the mutable row_mapping list lack a coherent generation owner.
  Existing index snapshot tests bind index hash/read only, not the dataframe,
  row mapping and embedding request. Private ownership plus captured invocation
  identity and change checks are needed; object-id equality alone is insufficient.
- retrieval.py zips scores/labels and drops invalid hits; old list projection
  loses malformed/discard facts. rag_search_tool.py treats the resulting list as
  successful and reads model identity afterward. Strict same-call receipts must
  distinguish valid_hits, validated zero-index valid_empty, invalid_discard and
  unavailable without changing legacy list semantics or reexecuting retrieval.
- Endpoint/model-name capture proves the local embedding request configuration,
  not immutable remote model weights. Do not relabel configured names as weight
  identity or backfill old index provenance. Any stronger identity interface or
  wider load/index change must have a narrow reviewed amendment before release.

Next source tests must reproduce CSV replacement after parse, equal-length
dataframe replacement/in-place mutation, row_mapping mutation, generation change
during embedding, post-return mutation, truncated responses, and all/mixed
invalid hits. Existing manifest test importorskip(faiss) must not turn missing
core validation into a passing truth claim. Review evidence is source-only;
runtime reproductions and fixes remain pending outside the three-file contract.

## 15. Reference-argument implementation evidence

At base78675e4, the implementer added the128-line closed contract and425-line
test module, and moved only B constants out of the Web builder (3 added/15 removed
lines). Existing tests/runtime/v1 parser are unchanged. Syntax handles are not
scientific inputs or proof; no B runtime calls this parser yet. Parent's §14.2
source audit is separate from the worker's three-file changes.

Commands are the exact isolated node/module/five-module commands in §14. Every
session reached terminal and no timeout or prior assertion was modified:

| Stage | Result | Exit | Duration |
|---|---|---|---|
| First node RED | 1 failed, missing-module assertion | 1 | 0.88s |
| Whole new module RED | 64 failed, same missing-module assertion | 1 | 2.70s |
| Whole new module GREEN | 64 passed, no warnings | 0 | 1.24s |
| Five-module joint regression | 315 passed, 7 warnings | 0 | 21.80s |

Joint warnings are3 SWIG and4 FastAPI deprecations. Independent SOURCE/SPEC
approved with no P1/P2 and verified the frozen SHA256 values before/after:

- contracts/decision_bindings.py:
  `3D9ECFB5BDB3A18D22E9DBFDA0D3EF7C15B7A82F22FAF6A60F3E3088ABD6B77A`.
- web/ordinary_capabilities.py:
  `949599399B54D716DD910337FF91A2947D23CB621BB0BAFFE5B9388632F9B05E`.
- tests/agent/test_decision_binding_arguments.py:
  `07FD2E0129C465065D83E8F6D9D2C89AD8A88082B0FB7B8FFCC88FF87DE74B40`.

Parent source review and diff-check agree. Independent QUALITY approved with no
P1/P2 and ran the same five-module command exactly once:315 passed,7 warnings,
22.53s, exit0. Source/test/runner hashes remained unchanged; its session56642
reached terminal with no retry. Both gates now allow a local checkpoint of the
three source/test files, this plan and the completion ledger. No full Agent suite,
B CI, real tool/model or browser acceptance is claimed. This component does not
close B1, B2, C or package8.
