# P7-B Dynamic Tool Bindings Implementation Plan

> **For agentic workers:** G0 / B-D0 is parent-approved with the revised logical-generation policy below. Only after separate release of the dependency-gated milestone, use `subagent-driven-development` or `executing-plans`. This sidecar is DESIGN ONLY: no implementation, tests, imports, compile, network, environment/asset inspection, merge or push. Parent explicitly authorizes a local commit of exactly this plan and its companion spec; no other writes/publication follow from that permission.

**Goal:** Expand the existing initial-four-tool model decision path through bounded, evidence-bound molecular/RAG/reverse actions and real generation/ranking dataflow, without model-authored inputs/numbers or a static workflow substitute.

**Architecture:** Extend existing reference resolution, immutable requirements, Session metadata/seals and replay; model still selects each action after observations. Two functional milestones: B1 source-backed analyses/retrieval/reverse, then B2 one logical generation invocation with existing at-most-five internal replenishment rounds, whole-CandidateSet analysis, multi-input ranking and compatible dynamic report proof.

**Tech Stack:** Existing FastAPI/native JS, Python/Pydantic/asyncio, LangGraph decision loop, SQLite Session journals, RDKit and current typed adapters. No new framework, DSL, dependency upgrade or datastore.

**Status/base:** G0 / B-D0 parent-approved REVISED design on `codex/dynamic-tool-bindings-plan`, base `5db56b0c79ac30da1ba4646c2c567e7d2dd71cc5`; B-D1 and implementation release remain blocked. Companion spec: `docs/superpowers/specs/2026-09-25-dynamic-tool-bindings-design.md`. Unchecked steps are future work, not execution evidence.

**Later parent-reported dependency update:** G2 PR72 merged as `27170d95`; 4C post-P2 full 6676 passed / 2 skipped, then joint 525 passed + 162 subtests, now PR73 head `beaf9cf4a57cf7c6045f5980c175c217fd42e96f` with CI pending; A1 SPEC4 repair `48d8356` under recheck. No network verification or merge was performed here. Design baseline remains `5db56b0`; 4C is not claimed landed, A1 is not claimed approved, and no implementation gate is open.

## 1. Preflight and dependency contract

- [x] Parent approves G0 / B-D0 revised design: B1 then B2; independent explicit RAG permission; closed source roles/proofs and whole-candidate coverage; actual Ranker/no rescore/mandatory unknown fail; one logical generation invocation/root-request slot with existing bounded five-round replenishment; P7-C separate and pending. No implementation follows automatically.
- [ ] Parent releases the relevant milestone after dependency pins and confirms its future file allowlist; docs-only freeze does not authorize execution.
- [ ] Pin corrected/approved/landed A1, implemented/landed A2, 4C and G2 versions. Earlier inspected A1 `aed3571` had dirty SPEC4 fixes; later parent reports repair `48d8356` under recheck, not approved/landed. A2 `ff02afc` is G0-approved documents only. Earlier 4C source inspection was `5e600a6`; now PR73 `beaf9cf4a57cf7c6045f5980c175c217fd42e96f` still needs CI/review/merge. Parent-reported landed G2 `27170d95` replaces the inspected pre-merge `548baca` as the later integration dependency, but is not in this baseline. Reconcile exact interfaces, not wholesale worktree copying; no implementation before separate parent release.
- [ ] Read actual G1 correction review/landed snapshot before dynamic-report work: inspected `bf4a2cf` plus dirty report corrections is static-only/unapproved. Its projector explicitly suppresses dynamic proofs; plan no longer assumes a generic dynamic report exists.
- [ ] Pin domain capability/source receipts and supported endpoint vocabulary. Unsupported full ADMET/novelty/normalized activity scoring, custom reverse/RAG limits and arbitrary core constraints must fail closed. Source manifest availability must refer to actually loaded data, not test declarations or configured filenames.
- [ ] Prepare later offline runner before importing app: temporary stores, controlled service handles, denied network, synthetic in-memory transport; never host env/key stores/assets. Reuse approved isolation procedure and safe fixture patterns. No live acceptance invocation in these steps.

## 2. Exact proposed later write allowlist

Only the two documents are written now. The following is a **reviewable future allowlist**, not advance authorization to modify dependencies. Files marked dependency-owned require their landed revision and explicit coordination. A failing need outside this list returns to parent.

| Milestone | File | Intended delta |
|---|---|---|
| B1 | New `src/agent/contracts/decision_bindings.py` | Closed role views and immutable resolved-input/proof contract; no workflow DSL |
| B1 | New `src/agent/harness/decision_bindings.py` | Source resolution/proof/role projection helpers using existing BindingResolver |
| B1 | `src/agent/harness/decision_inputs.py` | Delegate B profile binding; retain old no-profile behavior; full integrity checks |
| B1/B2 | `src/agent/contracts/task_requirements.py`, `src/agent/harness/decision_requirements.py` | Strict version-2 sections and deterministic coverage/source/score acceptance |
| B1/B2 | `src/agent/harness/decision_policy.py`, `decision_loop.py`, `decision_continuation.py` | Profile permissions/catalog, proof-aware steps/keys/finish, exact replay/fingerprint revision; minimal root-slot and generator request/token/deadline accounting |
| B1/B2 | `src/agent/runtime/run_session.py`, `src/agent/evidence/ledger.py` | Closed proof/attempt receipt propagation before seal and existing-state persistence; preserve validator/alignment/checkpoint order |
| B1 | `src/agent/tools/reverse_target_tool.py`, `src/reverse_target/predictor.py` | Narrow loaded-dataset identity receipt, no prediction/threshold/batch algorithm change |
| B1 | `src/agent/tools/rag_search_tool.py` | Executed-search/source receipt including empty result, via existing service metadata |
| B1 integration | `src/web/decision_request.py` (A1-owned) | Whole-coverage B admission and immutable obligations; no permissive chat fallback |
| B1 integration | `src/web/decision_runtime.py` (A2-planned) | Server profile injection and frozen resume obligations/source generations; no second receiver/lease |
| B2 | `src/agent/harness/decision_execution.py` | Minimal request-local logical non-replay policy only as needed by existing Session; relay existing-root budget/cancel controls and attempt receipts, no shared registry mutation |
| B2 | `src/agent/tools/llm_molecular_generator.py` (G2-owned) | Request-local actual backend attempt accounting, between-round deadline/cancel checks and demonstrated cleanup/logging seam; preserve existing bounded five-round algorithm |
| B2 | New `src/agent/validators/decision_candidate_requirements.py` | Real RDKit explicit-core check and candidate-ID coverage checks; no fallback heuristics |
| B2 report | `src/agent/presentation/evidence_report.py` (G1-owned) | Dynamic proof branch, retain static branch/source/DTO checks; no reexecution |
| B2 report | `src/web/decision_chat.py` (A2-owned) | Existing report projection ordering after candidates/before complete, same lifecycle |

Potential source-receipt gap is a hard scope gate: if RAG's loaded source identity is not obtainable through its existing interface or reverse needs a broad loader redesign, parent must approve a precise amendment before touching further files. Do not implement a generic resource manager as a shortcut. No edits to factory idempotence, CandidateSet schema, Ranker formulas, frontend framework, static HTTP/workflow templates, key stores, launchers, dependencies or deployment.

The revised generation policy removes `src/agent/orchestrators/workflow.py` and `src/agent/tooling/generation_ranking_contract.py` from the write allowlist: no `generation_attempt_limit` input field or canonicalization plumbing. Landed 4C contracts remain read-only regression dependencies. Existing loop/continuation/Session/ledger files above hold minimal consumed-slot, per-dispatch receipt and budget persistence, not a new execution store. If a counted probe finds unbounded or hidden client calls, identify that exact path and request an allowlist amendment before any model-client/schema/workflow edit; do not default to a one-backend-call workaround.

Proposed new tests:

- `tests/agent/test_decision_dynamic_bindings.py`: actual loop/Session source dataflow, keys/seals.
- `tests/agent/test_decision_binding_requirements.py`: closed v2 admission/acceptance including whole obligations.
- `tests/agent/test_decision_binding_continuation.py`: source closure/history/one-use CAS and slot replay.
- `tests/agent/test_decision_generation_once.py`: real producer, one logical invocation with up to five counted backend attempts, durable slot/budgets/lifetime.
- `tests/agent/test_web_dynamic_bindings.py`: actual assembled `/ws`, real middleware/runtime and same-frame ACK route integration.
- `tests/agent/test_dynamic_evidence_report.py`: owned stored/live dynamic proof through landed G1.

Modify related existing tests only: `tests/agent/test_decision_inputs.py`, `test_decision_loop.py`, `test_decision_requirements.py`, `test_decision_continuation.py`, `test_binding_resolver.py`, `test_dynamic_run_session.py`, `test_rag_tool_contract.py`, `test_target_tool_contract.py`, `test_candidate_alignment.py`, and G1 `tests/home_evidence_report_test.js` for actual frame input. A2 home runtime and 4C generation/ranking contract tests remain regression dependencies, not retry-field edits. Expand this test list only with named justification, not assertion weakening.

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
- [ ] Write source-receipt RED: loaded dataset generation changes, missing/mismatched receipt, expired/ambiguous target, fake similarity/provenance, and foreign owner/seal. None enables target-conditioned generation. Receipt is linked to loaded objects and source records; don't hash arbitrary user paths.
- [ ] Write RAG RED: real RAG tool/adapter over controlled manifest-service seam returns records or actual no-hit receipt; unavailable service is not successful no-hit. Query and recorded vector/source/index IDs match. Unsupported paper-search/custom k requests do not execute a different service or ignore limit.
- [ ] Implement source-binding helpers/proof and narrow producer receipt wiring; keep predictors, thresholds and retrieval algorithms unchanged. Use source role manifests in operation key, Session metadata and ledger before capture. Full source observation remains immutable.
- [ ] GREEN includes equal structures from different evidence sources: they may not share cached action results; role order canonicalization is deterministic, swapped roles reject. Validate current closure before duplicate reuse. No model-authored numeric values pass scientific finish.
- [ ] Cover `active_results` explicitly: B rejects missing/forward/cyclic/stale sources and uses current obligation/role lineage rather than query-only per-tool digest. Equal-query observations cannot reactivate stale evidence; rank rows cannot masquerade as a CandidateSet or an automatically selected subset. Legacy no-profile filtering remains unchanged.

Future focused command: `python -B -m pytest tests/agent/test_decision_dynamic_bindings.py tests/agent/test_target_tool_contract.py tests/agent/test_rag_tool_contract.py tests/agent/test_reverse_target_complete_input.py -q -p no:cacheprovider`.

## 5. B1 — normal entry and continuation vertical slice

- [ ] Add actual app `/ws` RED using landed A2 assembly with constructor-only opt-in and B profile injected by server. Obtain real anonymous session cookie through protected HTTP/matching origin. Run actual admission→bridge→loop→Session; never register a test-only route or fake scope/loop result.
- [ ] Verify selected trusted molecule, same-owner per-socket chat history, explicit local RAG request versus RAG-true chat, forbidden whole request, partial status and subsequent normal turn. No static workflow fallback.
- [ ] Write continuation RED: source-role proof or source/generator/main-model generation changes before CAS; foreign/repeated nonce; altered original obligations/history; unsupported new count on resume. Waiting record stays unchanged on invalid claim; uncertain CAS never dispatches again.
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

- [ ] Require corrected/approved/landed G1, not its currently dirty static freeze. Write RED actual dynamic Session checkpoints/ledger→G1 report: current projector returns `unsupported_binding`. Do not remove this guard without replacing it with verified binding reconstruction.
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
| Source truth | Loaded-source receipts, real provider/asset evidence when later authorized | Typed model validation or synthetic provenance strings |
| Ranking | Actual Ranker and sealed aligned role inputs, exact required contributions/top-N | LLM numbers, pIC50→probability guess, unknown ADMET risk=0 |
| G1/normal UI | Dynamic full-proof branch + same actual WS frames→mount→ACK route | Static report fixture or send treated as ACK |
| Final goal | Integrated reviewed revision, required repeated real normal-entry cases, including separately approved Vina C | B1/B2 offline pass or all twelve proposed rows merely listed |

- [ ] Parent schedules relevant focused/full Agent/root/Node/compile/CI verification only in later implementation context. Record failures/skips honestly and distinguish contract/real-tool/real-decision/browser evidence; no fake scientific passes.
- [ ] Parent reviews exact diff/file ownership, no shared factory changes, no A1/A2/G1 working-tree overwrites, and no migration of incompatible waiting runs. Any tool behavior beyond binding, observable bounded replenishment and minimal logical non-replay/budget/cleanup controls requires explicit scope amendment; removed schema/workflow plumbing is not silently reinstated.
- [ ] Separate P7-C design/release for Vina consent, receptor/ligand/grid ownership, at-most-once side effects, new finite energy/pose/hash and authorized download. Never expose staged helpers in B or label ranked candidates as docked.
- [ ] Parent decides releases and whether supported partial scientific scope is acceptable; user goal remains packages 1–8, no deployment. B1, B2, C and package-8 final proof are tracked separately; this sidecar completes none of them.

Design handoff: G0 / B-D0 parent-approved REVISED design, these two new documents only in the explicitly authorized local freeze commit; return its SHA without merge/push. Exact landed dependencies and separate parent implementation release remain required. P7-B, P7-C and package-8 normal-route real proof are pending, not completed by this documentation commit.
