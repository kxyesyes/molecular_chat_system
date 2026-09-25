# P7-A2 Web Decision Runtime Integration Implementation Plan

> **For agentic workers:** G0/revised design approved; G1 is satisfied and parent A2 implementation release is OPEN. This worker updates only the three authorized documents, explicitly stages/commits locally and stops, with no tests/push. After this freeze, the next code worker may start TDD without another user confirmation, using `subagent-driven-development` or `executing-plans`. Follow the two batches below on the existing `codex/web-decision-runtime-integration` branch, ultimately one A2 PR; serialize shared-file writes. No default activation or deployment.

**Goal:** Integrate the existing model decision loop into the actual, gated normal `/ws` with trusted admission, single-lease lifecycle, bounded socket history/continuation, honest outcomes and existing reference ACK behavior.

**Architecture:** One new Web runtime coordinator composes A1 admission with existing `ChatHandler.process_decision_message`, `ModelDecisionLoop`, dynamic `WorkflowRunSession`, shared registry/store and app request gate. A small Agent runtime ownership helper tracks real futures/executors at the existing workflow and adapter submit sites; it is not a scheduler or new background framework. Preserve static HTTP workflow routes, no-owner call behavior and default legacy startup; no decision-to-legacy fallback. A2's incremental history test covers already-admitted greetings/concepts, not arbitrary chat; P7 final ordinary-chat coverage is not narrowed or waived.

**Tech Stack:** Existing Python 3.10/FastAPI/asyncio/Pydantic/SQLite/LangGraph/model adapters, Jinja2 and native JavaScript; pytest and direct Node scripts. No dependencies added or upgraded.

**Status:** **G1 satisfied; implementation release OPEN after this documentation freeze.** Implementation branch `codex/web-decision-runtime-integration` starts this freeze clean at `20aeef5998153b99ed401c6ac0e350a44cd47a0b`, after parent cherry-picked only the approved documents onto landed A1 #78 `782cd13129cb4c2328c398a3930f61172ea3ec62`. Parent reports reviewed exact head `c4aafe2e3b7386a3653865396c5514a70310fee9`, Galileo SPEC/Peirce QUALITY approval on unchanged eight source/test files, complete CI8/8, unresolved0. Local Git confirms both reviewed/merged trees equal `a5eaff6d64e658224a96cba4616abaadf638c79f`. These include main #77 `be0219e` and Planner #71 `5db56b0`; this worker does not merge/fetch. Spec: `docs/superpowers/specs/2026-09-25-web-decision-runtime-integration-design.md`.

**Historical evidence retained:** original design base `bb11ded`, approved planning freeze `15a903d9` from `ff02afc`, rejected A1 `aed3571` (SPEC4), and later `7a239b5` plus dirty findings are historical, not erased by release. Landed A1 spec/plan record same-blob QUALITY (9 scope + 6 probes), SPEC (439 focused + 12 reject / 6 RDKit / 1 missing-input probes), 1406 focused passes, and Windows local full **7777 passed / 3 failed / 2 skipped / 7 warnings / 388.24s**, exit 1. Fixture-only 3-node and 32-test module recovery is not a passing local full. Separately, parent read GitHub run `36073938886`, job `107880850355`, exact `c4aafe2e`: Linux CI complete Agent **7781 passed / 1 skipped / 11337 warnings / 270.12s**, within complete CI8/8. This worker has not queried GitHub or rerun tests. Preserve warning counts and failure history; no incidental warning cleanup in A2.

---

## 0. Authority and executable-plan gate

Parent authorizes this spec/plan rebind plus `docs/handoff/remaining-through-step8.md`, edited only with `apply_patch`, explicitly staged in a local three-document commit, then this worker stops. No code/tests/compile/network/env/key/assets/services/push in this freeze. The following RED/GREEN sequence is released for the next code worker after the freeze, not an executed result. Parent owns packages 1–8 and publication; update only the authorized ledger facts, never close P7 from A2 or claim to have independently rerun A1 reviews/CI.

- [x] Parent approved G0: constructor `normal_chat_mode='legacy'` with opt-in `decision_a2`; `decision_wire_mode='native'` with explicit `json` alternative; no automatic fallback or default activation; socket-only 15-minute waiting; chat-only 20-pair/16-KiB history; one lease with cancel/drain; whole-unsupported rejection; actual `/ws` and ACK tests required.
- [x] Parent approved per-turn ownership at both real submit sites, reserve-before-submit/rollback, cross-thread propagation, full descendant drain plus executor join, unchanged no-owner calls, and exact history transmission using `Explain logP` then `解释分子生成的概念`.
- [x] Parent approved spec 6.1–6.2, Tasks 5–6 and allowlist, including action-root versus turn separation to avoid self-deadlock. C difference remains: A2 has no finite-time UI termination guarantee under an uncooperative worker; C requires a finite UI terminal with retained cleanup owner. Later C integration must reconcile it, not count this release as compatibility approval.
- [x] Parent supplied corrected A1 SPEC/QUALITY closure, CI8/8/unresolved0 and landed #78; explicitly released A2 after this freeze without another start confirmation. Four SPEC4 regression names in landed `tests/agent/test_web_decision_admission.py`: `test_review_explanation_boundary`, `test_review_unknown_obligation_coverage`, `test_review_explicit_negative`, `test_review_quantified_coverage`. The last covers the **one** original 两/种 count case; retain all four exact route cases in Task 3. Rejected `aed3571` remains historical, not a trusted dependency.
- [x] Pin landed `782cd131`, reviewed `c4aafe2e`, equal tree `a5eaff6d`; read landed A1 spec/plan and source/test contracts. Waiting revision is 5; actual signature bindings are below and in spec section 2.
- [x] Rebind current app/shared ownership, model-close/epoch, bridge/prefix and #77 candidate/report contracts in this freeze. Other unavoidable out-of-allowlist conflicts still require scoped review; they are not permission to widen A1.
- [ ] Confirm the resume refinement rule is supportable by landed A1: original kind/tools/metrics/targets/subjects stay authoritative; missing subject may receive one complete valid explicit molecule, not a new batch or request. If not, stop and revise with A1 owner; do not weaken requirements or write a parallel classifier.
- [ ] Next code worker verifies and reuses the already approved offline runner's pre-import isolation, including tracked fixture copy/SHA preparation when applicable, as documented in `2026-09-24-rag-service-extraction.md` and A1's fixture recovery record. `src/web/app.py` constructs a global app; autouse fixtures alone are too late. No new parent confirmation is needed to start bounded TDD under that procedure. Do not inspect host env/key stores; use synthetic config/transport and temporary test roots. An isolation failure must be resolved before imports/tests, not bypassed.

Code sketches remain design control flow, **not copy/paste production patches**. The actual pinned interfaces are:

- `app.py:188/194/209`: existing singleton reference/store getters; factor registry construction/audit from `_create_supervisor_agent`, reusing existing tool instances, no Supervisor execution/second pool. Shared registry helper, constructor mode options and epoch do not yet exist.
- `app.py:257–271,313–341`: both settings persistence and refresh replacement hold the writer and converge on `_apply_and_close_llm`; stage client/config/epoch publication at the common apply boundary, covering both routes and initial creation, preserve retired-client close. Refresh outside the only reader, capture inside it; no nested lease and no writer while draining a reader-owning turn.
- `prepare_decision_request(payload, *, session_id, trace_id, references=None, config_generation=None)` at `decision_request.py:355`; `.context` at 63 creates a fresh detached object per access. Retain the object carrying server history and a detached frozen start copy; do not lose memory by re-reading the property.
- `chat_handler.py:68` / `decision_chat.py:169`: bridge keywords are `context, decision_loop, request_kind, allowed_tools, required_tools, requirements=None, continuation_id=None, clarified_query=None`. `decision_loop.py:85`: constructor `(model, registry, state_store, *, mode='native', max_model_requests=16, max_tool_attempts=12, timeout_seconds=300, config_generation=None)`; `run` at 103 `(context, *, request_kind, allowed_tools, required_tools, event_bus=None, continuation_id=None, clarified_query=None, requirements=None)`. Proposed cancel/worker/prefix facilities are not existing parameters. Introduce planned optional ownership arguments without changing no-owner behavior.
- `decision_continuation.py:110/178`: `claim_continuation(loop, session, fingerprint, continuation_id, clarified_query, *, system_message=None, requirements=None, required_tools=())`; `validate_history(snapshot, loop, results, specs, *, session, system_message, requirements, required_tools)`. Revision is 5; context memory already participates in fingerprint, but loop seeds system/current query only. Validator uses `messages[1]`, global JSON-decision counting and replay offset 2. Change builder/run/exact prefix/suffix counting/replay/revision together, to revision >5. Use original frozen obligations/context (detached per run) plus `clarified_query`, and current captured generation; preserve owner/source/nonce checks and CAS.
- `chat_handler.py:968–984` already sends candidate frames then optional report; `decision_chat.py:221–228` still sends result/complete consecutively. Task 7 must insert existing projection once before complete. Strict `ScientificReport@1` keys/digest at `contracts/scientific_report.py:22–23,86–94` prohibit extra `turn_id`, just as candidate schema does. Preserve current-socket/expected-source-trace validation rather than filtering report as a missing-turn-ID frame. Full order: `agent_event* → agent_result → molecule_candidates* → scientific_report? → complete`; exact mount ACK remains required, report is optional/live-only and never restore evidence.

### Released execution order: two batches, one A2 branch and eventual PR

Task numbers below remain stable for review references; **numerical order is not execution order**. Section 0/Task 1 are preflight and scope, not a third implementation batch.

| Batch | Work order and boundary | Required evidence before moving on |
|---|---|---|
| 1 — low-level dependencies first | Task 5 owned-worker helper, the two existing submit hooks, `decision_execution` and necessary loop ownership binding; then Task 6 pure history/prefix builder plus loop/continuation prefix validation and revision as one coherent low-level change. New worker/history tests and existing loop regressions only; no app/handler/runtime/WS/UI wiring. Serialize loop edits. | Focused RED/GREEN for reservation rollback, cross-thread descendants, action-root versus turn drain, off-loop/off-own-callback executor join, no-owner behavior; pure bounded history, exact prefix, suffix-only count/replay, incompatible revision and empty-memory controls. These are dependency evidence, not route/nonce/UI completion. |
| 2 — integration | Tasks 2–4 actual app/WS, admission, shared registry and epoch; Task 5 route cancellation/drain/single terminal; Task 6 socket history and continuation/nonce/owner/expiry integration; Task 7 references and #77 report; Task 8 UI. | Actual-route RED/GREEN with real admission/loop/adapter and deterministic barriers, protected HTTP ACK/restore, serialized candidate/report order, strict frames and Node contracts; then planned SPEC/QUALITY/full offline gates. |

Both batches stay on `codex/web-decision-runtime-integration`, with explicit scoped staging and eventually **one A2 PR**, not new per-batch branches/PRs. The next code worker starts Batch 1 after this freeze; no additional start confirmation is required. Batch 2 consumes verified low-level dependencies and must not mark route tasks complete from Batch 1 passes. This worker stops at the local docs commit; no code/tests/push now.

## 1. Exact future file ownership

Create:

- `src/web/decision_runtime.py`: `WebDecisionRuntime`, socket state, turn ownership, resume admission and bounded history/waiting handle.
- `src/agent/harness/decision_history.py`: shared closed history-prefix builder/validator.
- `src/agent/runtime/worker_ownership.py`: per-turn real-future/executor ledger, action-root scopes, reserve/rollback, cross-thread binding and descendant/join settlement; no scheduling or persistence.
- `tests/agent/test_web_decision_runtime.py`: reusable actual-application route fixture and admission/route matrix.
- `tests/agent/test_web_decision_runtime_lifecycle.py`: actual-route cancellation/configuration concurrency matrix.
- `tests/agent/test_worker_ownership.py`: both submit sites, dynamic descendants, reserve/rollback, propagation, executor joins, isolated owners and no-owner compatibility.
- `tests/agent/test_web_decision_runtime_references.py`: actual reference HTTP and selected-input `/ws` coverage.
- `tests/agent/test_decision_history.py`: history and continuation prefix regressions.
- `tests/home_decision_runtime_test.js`: no-framework home DOM/socket contracts.

Modify only:

- `src/web/app.py`: mode/epoch/runtime assembly, shared registry helper if necessary, shutdown ordering.
- `src/web/chat_handler.py`: optional runtime early branch and additive bridge arguments.
- `src/web/decision_chat.py`: cancel event, serialized sends, projection and finalization.
- `src/agent/harness/decision_loop.py`: initial history prefix and optional ownership binding/settlement before finalization, without policy expansion.
- `src/agent/harness/decision_execution.py`: retain outer Session task, action-root descendant drain and repeated-cancellation-safe settlement.
- `src/agent/orchestrators/workflow.py`: ownership hook at the existing `_execute_step` submit site only; preserve no-owner behavior.
- `src/agent/tooling/adapters.py`: ownership hook at the existing `_execute_once` submit site only; preserve validation, slots, retries and no-owner behavior.
- `src/agent/harness/decision_continuation.py`: prefix-aware validation/replay and internal revision.
- `src/web/static/js/home/main.js`: server mode, turn controls, stable outcome UI, touched send-path payload log removal.
- `tests/agent/test_decision_chat_transport.py`: default-off assertion and transport regressions.
- `tests/agent/test_decision_loop.py`: focused opt-in ownership/settlement regressions and unchanged direct-call behavior.
- `tests/test_model_request_lifecycle.py`, `tests/test_web_app_lifecycle.py`: existing lifecycle regression coverage.

No changes by default to A1 admission/bounds/inputs, session middleware, model adapters, key/config stores, reference/report schemas/services/controller/normalizer, `ToolRegistry`, domain tools/adapters, static workflow HTTP, requirements, deployment, launchers, templates or CSS. The two shared submit hooks and `decision_execution.py` above are narrowly approved exceptions, not permission to refactor other execution behavior. A failing test outside the allowlist is a scoped revision request, not automatic expansion. Implementation uses the already created A2 branch/worktree, never A1's worktree. This freeze changes only the spec, this plan and `docs/handoff/remaining-through-step8.md`; the code/test allowlist applies to the next worker.

## 2. Task: actual normal-route fixture and default-off wiring

**Files:** new runtime test; `app.py`, `chat_handler.py`, new `decision_runtime.py`; existing transport assertion.

- [ ] Write RED `test_default_ws_retains_legacy_and_has_no_decision_admission`: instantiate actual `MolecularChatApp` using safe injected dependencies, get server cookie through protected HTTP and connect `/ws` with matching origin. Default mode must not instantiate/call decision runtime. Existing HTTP `/plan` and `/run` keep their contracts; no `/api/chat` appears.
- [ ] Write RED `test_enabled_ws_calls_existing_decision_loop_not_supervisor`: same app/route with proposed constructor opt-in. Use actual OpenAI-compatible adapter with an in-memory HTTP transport returning protocol envelopes; fail loudly if legacy `_process_message`, Supervisor execution, `generate_for_chat` or RAG retrieval is reached. Do not fake the loop/result.
- [ ] Write RED auth table: absent/expired/duplicate cookie, foreign origin, unsafe remote cleartext, missing server scope. None may reach admission/model; use actual middleware, not an injected identity field.
- [ ] Run only this task's tests and verify failure is missing wiring, not import/dependency/network isolation. Record exact RED reasons without request bodies/secrets.
- [ ] Add closed constructor modes; no startup call-site opt-in. Inject existing store/reference/registry ownership, factor registry assembly only if needed. Early handler delegation precedes `_process_message` and its decorator. Announce server mode/capabilities in `connection_ready`.
- [ ] Pass the same tests and replace the old string-search no-dispatch assertion with default-off behavior coverage. The isolated endpoint test remains supplementary.

Proposed routing contract:

```text
ChatHandler.handle_websocket(socket):
    if injected decision runtime exists:
        await runtime.handle_websocket(handler=self, websocket=socket)
        return
    existing legacy receive path unchanged
```

Future command: `python -B -m pytest tests/agent/test_web_decision_runtime.py -q -p no:cacheprovider -k 'default or enabled or auth'` (inside approved offline runner).

## 3. Task: strict normal admission, RAG distinction and result truth

**Files:** `decision_runtime.py`, `test_web_decision_runtime.py`.

- [ ] Write parameterized actual `/ws` RED with these exact cases and counters. Run actual A1 admission and loop; a provider transport double is allowed, not a canned admission result.

| Case | Required assertions |
|---|---|
| `你好`, flags absent / RAG true / RAG false | One external adapter decision, chat result, zero tools/retrieval; no fake greeting; requested RAG and `retrieval_performed=false` distinguished |
| `解释 logP 是什么` | Chat explanation, no property calculation claim |
| `计算 logP 和分子量；SMILES: CCO` | Actual property tool through Session, both metrics, provenance/evidence IDs, final numeric text from formatter |
| `计算性质及类药性；SMILES: CCO; CCN` | Both subjects and both tools required; provider's next proposal depends on prior observation, not a static prebuilt plan |
| Supported scientific request, tools false | Rejected, no model/tool/legacy fallback |
| `检索知识库中的相关文献`, RAG true | Explicit blocked/rejected, zero retrieval and no scientific success |
| `计算 CCO 的 ADMET 和性质` | Whole unsupported request rejected, not property-only completed |
| Generation/ranking/reverse/docking/mixed unknown actions | Fixed unsupported/clarification reason; no legacy planner or hidden tool |
| Invalid explicit SMILES + old confirmed selection | No rescue from selection; no successful calculation |
| SPEC4: `解释 logP\n对接这个分子`, `enable_tools=false` (`\n` is an actual newline) | Executing clause is not bypassed as chat; full disabled/unsupported request explicitly clarifies/rejects |
| SPEC4: `计算 CCO 的分子量和熔点` | Zero known-subset scientific completion; whole-request clarification/rejection |
| SPEC4: `计算性质（禁用 property_calculator）；SMILES: CCO` | No re-enabling or silently dropped tool prohibition; preserve full-request constraints |
| SPEC4: `分析两种化合物的性质；SMILES: CCO` (**one reported count case**) | Replay through actual `/ws`; preserve requested two-compound intent, clarify/reject the one-subject input, no UI count default substitution or known-subset completion |
| Body owner/tools/requirements/backend/model/generation/history fields | Reject before authority construction |
| Non-bool flags, duplicate JSON keys, NaN, oversized/non-object frame | Bounded rejection before admission/provider/store mutation |

- [ ] Run RED; A1 admission mismatches are returned to A1/parent, not patched around in runtime. Apply SPEC4 cases to supported resume-validation paths as well as fresh chat. Unsupported remainder means whole-request clarification/rejection, never silent workflow fallback or known-subset finish.
- [ ] If adding other quantifier controls, label them planned additional coverage, not reported SPEC4 cases. The parent-confirmed original report contains exactly the four cases above, including one count case.
- [ ] Implement bounded decoder/control split and A1 composition; server trace and identity only. Admission failures become fixed rejected results, one connected complete per accepted turn. Set explicit no-retrieval metadata, not fake RAG success.
- [ ] Run GREEN and assert original false/zero values, warnings, rejected/failed/partial/waiting statuses survive. Never use model post-processing for scientific numeric output.

Future command: `python -B -m pytest tests/agent/test_web_decision_runtime.py -q -p no:cacheprovider`.

## 4. Task: request ownership, leases and model generation

**Files:** `app.py`, `decision_runtime.py`; new lifecycle test; existing lifecycle tests.

- [ ] Write actual-route RED `test_ws_switch_waits_for_captured_model_and_uses_new_epoch_next_turn`: block adapter response under first turn; invoke existing app replacement path with synthetic in-memory config; assert old client not closed, epoch not published early, second admission waits, unrelated readers stay concurrent before a pending writer.
- [ ] Write RED credential-only replacement: unchanged public provider/model/URL with new synthetic runtime replacement rotates opaque epoch; waiting resume rejects before CAS/model/tool. No credential or credential hash appears in epochs/context/frames/logs.
- [ ] Write RED failed replacement leaves coherent old client/config/epoch; refresh failure fails the turn instead of using stale settings. Unsupported client lacking `decide` has no local-model fallback.
- [ ] Write RED generator isolation: main switch does not replace/reconfigure separately owned local `gmm-llama:latest` generator. HTTP background workflow/design consumers retain their original lease behavior.
- [ ] Run RED, then implement staged model/config/UUID publication under writer. Refresh before reader; capture after acquire; instantiate one request-local loop with current generation. Never add `@model_request` to runtime or bridge.
- [ ] Run GREEN with barriers and bounded waits, not sleeps as evidence. A pending-writer test must demonstrate no nested-reader deadlock.

Proposed ownership skeleton (not production implementation):

```text
turn owner:
    refresh existing config outside gate
    if cancel already requested: emit cancelled; stop
    async with app gate.request():
        capture current main model + opaque generation + fixed wire mode
        prepare new request OR validate frozen waiting request against current generation
        create ModelDecisionLoop(captured model, shared registry, shared store,
                                 mode=wire mode, config_generation=generation)
        retain server-only turn worker owner in app/runtime active-owner set
        await handler.process_decision_message(..., cancel_event=turn.cancel_event,
                                              worker_owner=turn.worker_owner)
            inside loop: end each Session action root, drain its whole descendant tree
            inside loop/bridge before terminal: seal new action roots;
                drain remaining work and executor joins, then send terminal if writable
        on every exit: drain provider/Session/worker/join children before lease release
        remove active owner only after settlement; never detach to meet a timeout
```

Future command: `python -B -m pytest tests/agent/test_web_decision_runtime_lifecycle.py tests/test_model_request_lifecycle.py tests/test_web_app_lifecycle.py -q -p no:cacheprovider`.

## 5. Task: responsive receiver, real worker drain and single finalization

**Files:** `decision_runtime.py`, `decision_chat.py`, handler bridge, `app.py`, `decision_loop.py`, `decision_execution.py`; new `src/agent/runtime/worker_ownership.py`; submit hooks in `src/agent/orchestrators/workflow.py` and `src/agent/tooling/adapters.py`; new worker-ownership test, lifecycle/transport tests and `tests/agent/test_decision_loop.py`.

Source constraint: at main `9a9eb8d`, `decision_execution.py:54–78` only joins `session.execute_step`. `workflow.py:149–168` and `adapters.py:127–166` return on timeout without joining nested threads. Do not treat current Session settlement or an asyncio cancellation as a worker-exit signal. Spec section 6.1 selects per-turn ownership over unconditional legacy joins or adapter-only tracking.

**Batch split:** the next six checks are Batch 1 low-level work (the submit/timeout limitation remains in landed `782cd131`). No Web wiring is needed for them. The actual-route checks and runtime/bridge/app implementation after them are Batch 2, not prerequisites for starting the low-level tests.

- [ ] Write focused RED for both nested orders: adapter deadline returns before blocked invocation exits; workflow deadline returns before its adapter descendant exits. Capture separate signals `worker_entered`, `timeout_returned`, `worker_exited`, `executor_joined`, `owner_settled`; a timeout result must not set the latter three.
- [ ] Write RED for reserve-before-submit and rollback on executor creation/submission failure; a fast worker completing or registering a child before future attachment must not cause an empty-ledger pass. Verify existing invocation-slot release and no execution after failed submission.
- [ ] Write RED for explicit owner/action-root lineage propagation across the outer Session thread and both nested submit paths; delayed child registration after workflow timeout is still drained. Two turns sharing an adapter drain only their own descendants. Owner state never enters input payload/context/results/continuations.
- [ ] Write RED blocking executor join after callable/future completion: no `owner_settled` or lease release yet. Verify join runs off the event loop and outside the executor's own worker/done callback; repeated cancellation cannot abandon its join helper. No-owner calls retain original timeout latency, result/error and retry/slot behavior.
- [ ] Run focused RED under the now-open G1 release in Batch 1. Implement the small ownership ledger with action-root scopes. Reserve before submission, attach real future/executor or roll back, explicitly rebind/reset owner inside submitted callables. Keep the no-owner path unchanged; do not put per-request state on shared adapters or add a scheduler/store.
- [ ] Extend `settle_action` to retain its Session task and settle that action root's descendants, not await its still-running parent loop. Turn-wide settle seals further action roots after dispatch ends. Completion requires closed roots, zero in-progress reservations, all descendant futures settled and all executor joins completed; drain dynamically registered descendants, never only a snapshot. Consume cleanup failures without exposing raw worker diagnostics and retain unresolved ownership on incomplete cleanup.

- [ ] Write actual `/ws` RED: blocked model call → ping/pong → cancel matching server `turn_id` → one cancelled result/complete → new chat succeeds on same socket. Repeat after a resumed clarification, not just the initial turn.
- [ ] Write RED: stale/foreign cancel and second concurrent chat cannot cancel current work or add a second terminal. Cancellation before model dispatch releases queued admission safely.
- [ ] Write actual-route RED for both nested timeout orders, disconnect and repeated cancellation: before worker/join barriers release, the owner remains registered, reader lease stays held, writer publishes no replacement epoch, no model/tool closes and no complete is emitted. After release, assert settled journal, no replay, one terminal if writable and close-once. Late worker success cannot replace timeout/unknown/cancelled with scientific completion.
- [ ] Write RED for send timeout, event overflow, serialization failure, cancel-vs-finish race, cancellation during cleanup, and shutdown with active + waiting sockets. Settled writable paths get one complete; disconnected/unwritable sockets do not promise delivery. Block a worker throughout a bounded observation window to prove graceful shutdown remains pending/unresolved, then release it in test cleanup; do not leak a permanent test thread or claim a finite shutdown bound.
- [ ] Implement exactly one receiver, one active owner, one serialized deadline-aware sender. Keep control validation on receiver; async refresh/admission/lease/model work belongs to owner. No queued chats.
- [ ] Add optional bridge `cancel_event` watcher and server-only `worker_owner=None` forwarding. User cancellation stops dispatch and cancels the loop child once; transport cancellation also stops delivery. Both retain and drain watcher, provider child, Session tasks, real worker futures and executor-join helpers. Final result delivery follows settlement, not cancellation of an asyncio wrapper. Finishing latch decides completion-vs-late-cancel once; no result recreation after committed terminal.
- [ ] Track app-owned active owners so shutdown stops admission and drains before writer closure. Waiting state holds no lease/model/worker owner; clear it at shutdown. Never clear an unresolved owner or close a still-used resource to meet a deadline. If a thread never exits, graceful shutdown cannot complete. Pass focused GREEN, actual-route barriers and existing overflow/cleanup regressions; no claim that all legacy or tool-internal detached threads are covered.

Batch 1 future command (inside the approved isolated runner): `python -B -m pytest tests/agent/test_worker_ownership.py tests/agent/test_decision_loop.py -q -p no:cacheprovider`. Batch 2 additionally runs `tests/agent/test_web_decision_runtime_lifecycle.py` and `tests/agent/test_decision_chat_transport.py` with the same runner/options. These are planned commands, not results of this freeze.

**C integration difference, not resolved here:** parent reports C's preparatory design requires a finite-time UI terminal while retaining a cleanup owner. A2 currently waits for owned settlement before its single terminal and does not promise finite-time termination under an uncooperative worker. This plan does not implement that terminal/cleanup split or claim C compatibility. Later C integration must separately review terminal ownership, late frames, retained leases/resources and shutdown; cancelling an asyncio task is never proof of drain.

## 6. Task: actual chat history and nonce-safe multi-turn resume

**Files:** new `decision_history.py`; loop/continuation/runtime; new history test and actual-route tests.

**Batch split:** first implement/test the pure closed-pair builder and bounds, empty/scientific-prefix rules, exact run prefix, suffix-only counting/replay and revision >5 in Batch 1, with new history tests and existing loop/continuation regressions. Keep builder/run/validator/revision coherent in this low-level batch; do not defer half the protocol update to Web integration. Socket retention, actual adapter pair proof, waiting-handle/nonce/CAS scenarios and route cancellation below are Batch 2. Batch 1 creates no runtime or Web plumbing and cannot claim real multi-turn route acceptance.

- [ ] In Batch 2 write RED history through actual `/ws` with first prompt `Explain logP`, second prompt `解释分子生成的概念`. Both remain in landed A1's `tests/agent/test_web_decision_admission.py:63–68`. Run real admission, loop and adapter with an in-memory provider transport. Let `A` be the first safe displayed response; assert the second adapter request contains the exact `user: Explain logP`, `assistant: A`, then `user: 解释分子生成的概念`, retaining existing decision/protocol system prefixes. No prefilled history, monkeypatched admission/loop or fixed runtime answer. Assert empty allowed/required tools and zero tool/retrieval calls for both turns. A different socket with the same cookie and a different user see neither retained pair; client-supplied history is rejected.
- [ ] Keep the A2 milestone claim narrow: exact pair transmission for admitted concepts/greetings, not arbitrary chat, label recall or real-model understanding. Do not add free-chat classification, bypass admission, or invent a waiting nonce for a rejected prompt. **P7 final ordinary-chat coverage remains mandatory:** retain original package-8 capability-description questions and normal multi-turn cases; current rejection is an unresolved capability gap, never a reason to rewrite them as expected rejection. Any classifier expansion requires separate design; it is not required to make this incremental A2 pair test GREEN and is not a waived final gate.
- [ ] Write RED chat-only retention: scientific/partial/failed/cancelled/waiting outcomes are excluded; 20-pair/16-KiB bound evicts whole oldest pairs; sensitive/oversized pair omission is marked. Current input remains exact.
- [ ] Write RED prefix replay: prior ordinary assistant text starting with `{` is not counted as a decision; substituted history rejects before CAS. Empty memory preserves existing direct callers; incompatible old waiting revision rejects without execution.
- [ ] Write actual-route RED two clarification cycles: missing input → waiting → full clarified supported request → waiting → full clarified request, with original trace/requirements/history preserved and a fresh nonce after each wait. Then cancel during blocked resumed model work; next fresh turn succeeds. No waiting result is treated as completed scientific work.
- [ ] Write actual-route RED table: foreign cookie, same-user other socket, stale nonce, duplicate nonce, changed model epoch/tool schema/requirements, expired/abandoned handle, revoked reference and unsupported clarification. Count model/tool calls and compare durable waiting record before/after: invalid resume causes no CAS mutation or dispatch.
- [ ] In Batch 1 implement `decision_history.py` shared expected prefix from closed pairs. `run` seeds prefix; continuation validator compares it exactly, counts suffix actions and starts semantic replay at prefix length. Bump internal protocol revision beyond landed A1's **5** in the same coherent change; preserve public decision envelope/input_ref schema. Never count historical assistant text beginning with `{` as a model decision.
- [ ] In Batch 2 implement one socket waiting record with frozen initial context/obligations, 15-minute expiry and no loop/client reference. Retain the detached `.context` carrying history; never re-read `PreparedDecision.context` and lose memory. Resume validates A1-compatible complete clarified text and original obligations, but calls loop with a detached working copy of **original** context/requirements plus `clarified_query` (loop may mutate the working query); current generation rejects stale state. Do not re-prepare original query from browser, mutate the stored original or replace constraints with fresh history.
- [ ] Implement `abandon` as local handle release, not durable cancellation. Disconnect/restart cannot reconstruct Web continuation from a client nonce alone. Run GREEN; do not relax core CAS or historical source checks to pass it.

Batch 1 future command: `python -B -m pytest tests/agent/test_decision_history.py tests/agent/test_decision_loop.py tests/agent/test_decision_continuation.py tests/agent/test_decision_continuation_store.py tests/agent/test_decision_protocol_recovery.py -q -p no:cacheprovider`. Batch 2 additionally runs `tests/agent/test_web_decision_runtime.py` and `tests/agent/test_web_decision_runtime_lifecycle.py` with the same runner/options. No tests run in this freeze.

## 7. Task: existing candidate projection and real reference HTTP boundary

**Files:** bridge, runtime-reference tests, transport tests. Service/controller/normalizer remain read-only.

- [ ] Write actual-application route RED: seed a clearly marked synthetic historical CandidateSet in a temporary real store using existing scientific-reference fixtures; obtain cookie and use existing protected restore/confirm HTTP. No fixture claims real generation. Then ordinary enabled `/ws` chat must retain confirmed selection; selected property follow-up must dispatch exact trusted canonical structure to real property tool.
- [ ] Write route RED source matrix: foreign owner, wrong revision/order/compound key, unconfirmed view, source revocation, invalid explicit replacement; no unauthorized calculation or silent use of a different molecule. Partial source retains warnings and source status; accepted specific-candidate analysis does not retroactively complete source generation.
- [ ] Add complementary bridge transport-order RED using a stored CandidateSet result fixture: `agent_event* → agent_result → molecule_candidates* → scientific_report? → complete`. Preserve both strict schemas: neither candidates nor report gets `turn_id`; the report projection digest stays unchanged. Assert one projection/publication, optional report absence/failure preserves completion, and actual transport failure stops delivery/drains. Never execute generator/tool again.
- [ ] Reuse `_send_reference_candidate_events` against settled legacy result, not redacted display envelope. It already calls `ScientificReferenceService.project` then same-store `prepare_report_event`; do not append a duplicate report/projection. Same owner/store and serialized deadline-aware sender before complete; retain source-version checks and optional sidecar failure behavior while transport timeout/disconnect still follows A2 cleanup. No new candidate-producing tool is admitted, no report promised for ordinary chat/no eligible snapshot. Restore stays protected candidate-only, no report restore or receive-as-ACK claim.
- [ ] Run GREEN actual-route ownership tests and existing reference contract tests. A bridge CandidateSet fixture alone does not satisfy the actual-route requirement or B generation acceptance.

Future command: `python -B -m pytest tests/agent/test_web_decision_runtime_references.py tests/agent/test_scientific_reference_web.py tests/agent/test_scientific_reference_execution.py tests/agent/test_scientific_reference_resilience.py tests/agent/test_decision_chat_transport.py tests/agent/test_evidence_report_contract.py tests/agent/test_evidence_report_snapshot.py tests/agent/test_evidence_report_frames.py -q -p no:cacheprovider`. Existing #77 tests are read-only regressions; A2-specific additions belong in the allowed runtime-reference/transport tests.

## 8. Task: existing home UI controls and persistent status

**Files:** `home/main.js`, new `tests/home_decision_runtime_test.js`; existing home scripts/tests read-only.

- [ ] Write Node RED with existing home script loading pattern: server-announced decision mode enables accessible Stop, Continue and Start new/abandon controls; default legacy mode does not change. First `request_accepted` sets turn ownership; Continue sends only the matching trace/nonce/full message; Stop sends only matching turn ID.
- [ ] Write RED all outcomes: complete ends stream but does not relabel waiting/partial/failed/rejected/cancelled as completed. Status stays attached to message instead of disappearing with tool-status timeout. Next turn and stale socket/turn frames cannot overwrite it. Disconnect clears pending control handle; no auto-resend.
- [ ] Write RED existing candidate/report mount workflow: candidate frames then optional report arrive before complete; exact mounted ordered keys produce confirm request, failed/partial mount produces no ACK, slow stale ACK cannot authorize new selection. Do not add `turn_id` to strict candidate/report JSON or treat a receive as mount. Associate strict frames with current socket/expected source trace, preserving existing report source/version checks; do not drop a valid report solely because it lacks turn ID. Report is live-only; protected restore does not resurrect it.
- [ ] Implement controls through existing DOM patterns with text labels and `role='status'`, not a UI framework or redesign. Keep scientific numeric rendering and original/canonical candidate data unchanged. Explain RAG enabled-but-not-performed and explicitly blocked requests without inventing results. Remove raw outbound payload logging in touched send code; never log continuation/cookie/config/key values.
- [ ] Run Node GREEN, `node --check` on changed JS and existing home candidate/reference/task-panel tests. These are DOM contract tests, not live browser proof; normal-browser acceptance stays with parent/package 8.

Future commands:

```powershell
node --check src/web/static/js/home/main.js
node tests/home_decision_runtime_test.js
node tests/home_scientific_references_test.js
node tests/home_evidence_report_test.js
node tests/home_agent_task_panel_test.js
node tests/home_structured_molecule_render_test.js
node tests/home_workflow_completion_behavior_test.js
node tests/frontend_safe_render_test.js
```

These existing filenames include #77's verified `home_evidence_report_test.js` regression. Do not invent a missing npm test task or install a framework.

## 9. Review evidence and stop criteria

- [ ] Verify implementation paths against the exact allowlist; only the named shared submit hooks/settlement helper are added execution scope. Other A1-owned files or HTTP/config/tool/schema changes require explicit scope revision. Preserve user's unrelated changes. No direct `main` changes. The present local freeze commit contains only the spec, this plan and `docs/handoff/remaining-through-step8.md`.
- [ ] Record each focused RED/GREEN result honestly. Test doubles use in-memory HTTP transport and temporary stores; they prove protocol/lifecycle, not live external inference, target availability or trained activity predictions. Required skipped/failed cases block completion.
- [ ] Parent schedules full Agent/repository tests and source compile checks in the approved isolated implementation environment, plus all applicable existing Node regressions. Do not run live health/real acceptance or providers as a side effect of this plan.
- [ ] SPEC review checks default-off wire decision, no fallback, A1 authority, admitted-only history/nonce/socket isolation, cancellation/model-switch races, complete-vs-success distinction, scientific formatting and candidate ACK order. QUALITY review checks reserve/rollback, cross-thread propagation, action-root versus turn drain, nested futures/executor joins, no-owner compatibility, bounded queues, exact scope and absence of sensitive logging. Retain the pending C terminal/cleanup integration difference explicitly.
- [ ] Record implementation commits on `codex/web-decision-runtime-integration`, dependency SHAs and eventual single A2 PR separately. No publication command is included here. Present handoff is only the local three-document release freeze: no push/PR/tests by this worker. Report hash and clean status outside the committed documents, then stop. Next code worker starts Batch 1 TDD without another user confirmation.
- [ ] Parent decides if any package-8 real external model/science/browser acceptance is authorized next. Never count lab, fake generation, static HTTP workflow or local RDKit-only evidence as that acceptance.

## 10. Coverage-to-gate map

| Requirement | Planned evidence | Gate if unavailable |
|---|---|---|
| Actual `/ws` and legitimate server context | Tasks 2–3 actual app + middleware | A2 blocked; no lab substitute |
| Real adapter decision/observation-driven next action | Task 3 in-memory adapter transport + real loop/Session/tool | Offline contract only; live inference remains package 8 |
| One lease/current model/config epoch/cleanup | Tasks 4–5, both submit paths, reservation/descendant/join barriers and no-owner controls | A2 blocked on false drain/close/leak/deadlock; uncooperative worker has no finite shutdown guarantee |
| Same-user history/nonce/socket/multi-turn cancel | Task 6 admitted prompt pair, actual routes and suffix replay | Precise transmission only; not arbitrary chat or real-model understanding; isolated loop tests insufficient |
| P7 final ordinary-chat coverage, including package-8 capability questions and normal multi-turn | Original real cases retained on final integrated P7 path; separately review required capability improvements | Still mandatory/pending; A1 rejection cannot turn the original expected capability into an expected rejection or waive the gate |
| C finite UI terminal with retained cleanup owner | Explicit difference in Task 5/spec 6.2 | Later C integration review required; no compatibility claim or C implementation here |
| RAG true ordinary chat vs explicit retrieve | Task 3 matrix | A1 revision gate for misclassification; actual RAG B remains blocked |
| Partial and scientific numeric truth | Tasks 3, 7, 8 | Never relabel as scientific completed |
| Candidates/report/ACK/restore/selected input | Tasks 7–8 route HTTP/WS + DOM boundary and #77 regressions | Strict schemas unchanged; report optional/live-only, exact mount ACK; no dynamic candidate-production or live mount claim |
| No default activation/static HTTP change | Tasks 2, 4, 8 | Parent must review any mode change |
| Broader generation/rank/ADMET/reverse/RAG bindings | Explicitly excluded | **P7-B blocked, not completed** |

This handoff is a **local three-document release freeze** on the existing A2 integration branch based on landed #78 plus approved-doc cherry-picks through `20aeef5`. Writing-plans' dependency-first decomposition and source-checked readiness bindings define the next worker's two-batch TDD start. G1 is satisfied and parent implementation release is open after this freeze; no further start confirmation is required. This worker performs no code/tests/push and stops after the docs commit. A2 completion, P7 final ordinary-chat coverage, B/C integration, package-8 live acceptance and C compatibility are not claimed; the explicit C terminal/cleanup difference remains unchanged.

## 11. Ownership-only execution and independent review closure

The preceding documentation-freeze statements are historical. Parent released only the first **ownership sub-batch**, based on `048d0999eebd16b8fe8e403668210922451ff608`, on `codex/web-decision-runtime-integration`. Approved implementation is the seven source/test files below; this plan is the only additional evidence document authorized for local commit. No spec/ledger edits. History/prefix/revision work and all Web/app/WS/UI integration remain **pending and unstarted**. This is neither Batch 1 as a whole, A2, nor P7 completion. No full suite, live providers/services/browser, default activation, push or PR was performed; the original A1 checkout was untouched.

Implemented ownership reserves at both existing real submit sites before executor creation, propagates/reset bindings explicitly through the outer Session and both executor paths, drains each action root without awaiting its parent loop, and retains shared asynchronous join helpers across repeated cancellation. Successful records are released only after executor join; failed records remain unresolved. SingleAttemptTool validation/no-retry, adapter slots, and no-owner timeout/return behavior remain unchanged. Tests include the existing no-owner `test_adapter_cannot_retry_after_outer_deadline`, whose barrier is released after awaiting the loop result. Nonterminating workers remain retained indefinitely; no arbitrary detached-thread coverage or change to the A2/C terminal policy is claimed.

### RED, failure and recovery record (separate runs, not additive totals)

| Actual worker test group | Observed RED / failure | Verification after minimal correction |
|---|---|---|
| `test_actual_nested_deadlines_drain_worker_and_join` | **2 failed / 1.09s**; both deadline orders settled before real worker exit | **2 passed / 1.07s** |
| `test_owned_loop_has_no_terminal_until_nested_workers_join` | **2 failed / 2.81s**; timeout/repeated cancellation could terminate before owned join | Combined with preceding group: **4 passed / 2.80s** |
| `test_turn_drains_other_roots_even_when_one_join_fails` | **1 failed / 0.97s**; failed join left another root undrained | Ownership module **17 passed / 1.73s**; first seven-file freeze later **190 passed / 17.67s** |
| SPEC migration: `test_outer_session_dispatch_failure_closes_only_unstarted_root` | **2 failed, 2 passed, 1 warning / 3.22s**; owned submit/task-creation failures left empty unfinished roots; no-owner controls passed. Warning: unawaited `to_thread` coroutine on task-creation failure | **4 passed / 2.40s**; with atomic abort/running-work controls **6 passed / 2.56s**; seven-file focus + SPEC probes **199 passed / 18.29s** |
| QUALITY migration: `test_failed_parent_join_does_not_abandon_late_child_cleanup` | **1 failed / 1.12s**; failed parent could register a child after the helper had cached failure, leaving the child unjoined | Included in final three-test and combined verification below |
| `test_failed_parent_pending_future_and_completion_notification` | **2 failed / 1.15s**; running and queued producer variants both cached cleanup failure too soon | Corrected three-test group **3 passed / 1.18s** |

One intermediate attempted GREEN group was **interrupted, not passed**: session `6696` printed two progress dots then hung in the queued test's cleanup. Its initial fixture made `Future.cancel()` fail but still allowed executor shutdown to discard the queued work item, fabricating a future that could never complete. Only that identified pytest child was stopped; the wrapper reported `T11A_PYTEST_EXIT=4294967295` (shell exit 1). The test fixture was corrected to preserve the injected uncancellable queue item as well as its Future; no production timeout or assertion was weakened. Subsequent tests release all worker/queue/completion barriers in `finally`. This interrupted attempt is not a full-suite run or a passing count, and is not erased by later GREEN results.

Independent findings and closure, as reported by parent:

- Galileo SPEC originally reproduced outer submission failure: **1 failed, 2 passed / 2.74s**; seven-file focus **190 passed / 17.31s** did not cover the missing boundary. Root entry and abort now share one lock: only a never-started root is closed, a late queued callable is barred, and already-running work remains owned until actual completion. Failed task creation closes its unowned coroutine. Actual loop regressions require zero tool invocations, one model call, a settled failed journal and owner count zero. Intermediate SPEC recheck **199 passed / 18.04s**, no skips/warnings, approved that correction.
- Peirce QUALITY subsequently reproduced late-child cleanup loss twice: **1 failed, 2 passed**, latest **2.73s**; seven-file + SPEC **199 passed / 17.83s** did not cover it. A failed record now prevents drain termination while its Future can still execute/register descendants, even when `active` is false. A bookkeeping-only Future completion callback wakes the condition after `run.finally`, including completion-before-attachment races; it never joins. Failed records remain unresolved and scientific actions are not retried. If failed submit supplied no Future and executor join also fails, completion cannot be proved: retain pending cleanup rather than fabricate quiescence.
- **Final Galileo SPEC APPROVE:** independently **205 passed / 19.00s**, no skips/warnings.
- **Final Peirce QUALITY APPROVE:** independently **205 passed / 19.10s**, plus an independent unknown-future probe **1 passed / 0.81s**, no skips/warnings. No important remaining findings reported. These are independent review counts supplied by parent, not extra worker executions or a new summed total.

### Final worker verification and exact commands

Final worker seven-file focus plus both unchanged review probe files: **205 passed / 19.28s**, exit 0, no skips/warnings. All seven changed Python files compiled in memory with `compile(source, filename, 'exec')`, **7 passed**, no bytecode; `git diff --check` passed. No tests were rerun merely for the documentation commit.

Used the complete existing fenced `$runner` from [RAG extraction plan](2026-09-24-rag-service-extraction.md#实际验证命令与包装), changing only `repo` to `D:/MedChat/molecular_chat_system_worktrees/web-decision-runtime-integration`. Before imports it clears inherited application/credential environment with the documented OS-variable whitelist, isolates cwd/config/DB/cache in a TemporaryDirectory, sets live-acceptance flags to zero, and uses MedChat Python with `-B`. Only Git-tracked `real_agent_cases.jsonl`, `golden_scientific_cases.jsonl`, and `diverse_scientific_cases.jsonl` are copied into temporary `data/agent_evals`, with source/destination SHA-256 assertions. No other assets, secrets or host env/config/key stores are read. Keep normal conftest and `-q -p no:cacheprovider --tb=short -rs`; no installs or full heavy suite.

```powershell
$ownershipFocus = @(
    'tests/agent/test_worker_ownership.py',
    'tests/agent/test_decision_loop.py',
    'tests/agent/test_decision_adapter_retry.py',
    'tests/agent/test_tool_adapters.py',
    'tests/agent/test_tool_adapter_compat.py',
    'tests/agent/test_workflow_orchestrator.py',
    'tests/agent/test_workflow_run_session.py'
)
$reviewProbes = @(
    'scratch/spec_p7a2_ownership_review.py',
    'scratch/quality_p7a2_ownership_review.py'
)
$runner | & 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -B -c "import sys; exec(sys.stdin.read())" @ownershipFocus @reviewProbes
```

Earlier RED/GREEN groups used the same runner with the exact test-file/node names in the table. Review scratch files were read/run unchanged and verified ignored with `git check-ignore`; they are **not staged**. Their before/after SHA-256 values were SPEC `6479f6fbb87801d9c40d13dca30c998a8b795b5501d22ba4e55641170f4f345b` and QUALITY `0af940f180a124c57cf163bea096073263f7c086bb6aa40d1e8c866f47fc5493`. Parent's separate presentation tests are not claimed as worker ownership evidence.

### Exact reviewed source/test snapshot

| Approved path | Git blob |
|---|---|
| `src/agent/runtime/worker_ownership.py` | `b8a1c8035339530c5044d4fa4de1c46ef0d03027` |
| `src/agent/orchestrators/workflow.py` | `541789263e40cd4a70cb690f456d301101a0645b` |
| `src/agent/tooling/adapters.py` | `e66f4b58a2462957792a15efd1d19059bc7723a5` |
| `src/agent/harness/decision_execution.py` | `c3cb3251d4cb396e9fe71c18d539b4bbaaf740f4` |
| `src/agent/harness/decision_loop.py` | `455c6e9a4d04039f795a4aa9418aceda0666d54b` |
| `tests/agent/test_worker_ownership.py` | `97854fbd3b77f747e04d82789057c521c1d400a6` |
| `tests/agent/test_decision_loop.py` | `6f1e7ee8f6c6943dbf6509e9861cc39f0fee3d3a` |

Parent authorizes explicit staging of exactly these seven files plus this plan for one conventional local commit, after unchanged-blob, diff and filename-only secret checks. Final commit SHA/status/hash manifest is reported outside this document. Stop after that commit for parent confirmation: history/Web remain unstarted; no push/PR, spec/ledger edit, or completion claim for A2/P7.
