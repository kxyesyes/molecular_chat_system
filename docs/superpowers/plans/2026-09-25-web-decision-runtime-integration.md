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

## 12. Pure history execution and independent review closure

Section 11 remains an unchanged historical record. After parent released reviewed ownership commit `78d0a32e2bbb752c8af71eb33aedbc1546e6b4d2`, this same branch implemented only Task 6's **pure history subtask**: the four source/test files below. All other ownership source/tests retain their reviewed blobs; only the shared loop has the approved history delta. This plan is the sole newly authorized evidence edit; no spec/ledger changes.

Completed: closed detached `{user, assistant}` string pairs, 20-pair/16-KiB serialized bounds, explicit retention eligibility and omission markers, oldest-whole-pair eviction, exact system/history/current-query prefix, scientific requests without remembered text, and revision **6** with exact frozen-prefix validation before CAS and decision-suffix-only counting/replay. Pair validation checks exact dict type and length before key-set construction, then plain key/value types before object hooks. Original context/requirements remain unchanged; revision-5 or substituted-prefix snapshots reject without dispatch/CAS. Both wire modes cover two clarification cycles and capture the first actual adapter/loop answer for the already-admitted concept pair. Provider transport is in-memory: this is neither live inference nor actual socket retention/isolation evidence.

Twenty bounded pairs and two clarification cycles pass. With further clarification, the unchanged transport input limit accepts 64 messages (65 on the wire after its own protocol-system prefix); the next loop request with 66 input messages fails explicitly before provider dispatch. No history/current-query truncation, transport guard increase, or promise that all 16 model rounds simultaneously fit other budgets. Scientific missing-input behavior remains clarification with zero calculation; no fixed scientific test response is real missing-input evidence.

### Worker RED/GREEN and failure history (separate runs, not summed)

| Run | Actual evidence |
|---|---|
| Initial history tests | **16 failed / 4.54s**. Included missing history transmission and invalid-memory acceptance. Two revision-5 tests also had fixture interference: fake CAS returned true without real status transition, causing `RunClaimConflict`. The spy was changed to record and fail immediately if invalid history reached CAS; assertions were not weakened. |
| Expanded helper/revision group | **12 failed, 2 errors / 2.71s**. Oversize parameter auto-ID contained 6,000 Chinese characters; setup/teardown errors and output truncation prevented confirmation of the complete exception cause. Only explicit short IDs `oversize`/`sensitive` replaced auto-IDs. |
| Clean intended RED | **27 failed / 4.53s**; behavioral failures and explicit missing-helper assertions, without the above fixture errors. |
| Initial implementation GREEN | **27 passed / 3.34s**, exit 0, no warnings/skips. |
| Parent source-check regression RED | `test_wide_pair_rejected_before_materializing_keys` and `test_pair_keys_rejected_before_equality_hooks`: **2 failed / 1.62s**, proving premature key-set materialization and non-plain key equality invocation. Minimal shape/type guards corrected both. |
| Final history module | **68 passed / 11.17s**, exit 0, no warnings/skips. |
| Final 13-file focused run | **832 passed / 72.63s**, exit 0, no warnings/skips; session `39129` completed. |

The first in-memory compile command passed the helper, then failed on the loop with `UnicodeEncodeError: 'utf-8' codec can't encode character '\udc80' in position 12185: surrogates not allowed`. This was the PowerShell-to-Python stdin encoding pipeline, not a source change: explicit Python `-X utf8` made all four `compile(source, filename, 'exec')` checks pass, without bytecode. Final tracked/new-file whitespace checks passed. Earlier read-only path lookups also hit a nonexistent `test_decision_transport.py` and a PowerShell literal wildcard path; these were corrected to existing explicit paths, not counted as test executions. No failures were hidden by a full-suite rerun.

### Independent SPEC and QUALITY (parent-reported, same four blobs)

- **Galileo SPEC APPROVE:** five Task-6 files **511 passed / 61.78s**; independently repeated 13-file focus **832 passed / 73.36s**. Initial scratch review **74 passed, 2 failed / 13.49s** used two fixed-CCO response fixtures that did not prove missing-input behavior; the failed file remains. Diagnostic tests plus real RDKit missing-input controls, together with the 68 history tests, were **74 passed / 12.66s**, confirming zero calculation without scientific input. No production fix was required for those fixture findings.
- **Peirce QUALITY APPROVE:** five files **511 passed / 62.37s**, plus verified scratch/SPEC diagnostic **18 passed / 6.50s**, no warnings/skips; four-file compile and diff checks passed. Initial scratch **2 failed, 14 passed / 6.03s** omitted the existing temporary system feedback on the protocol-correction round. The original failing file remains; corrected exact-layout tests and empty-history controls passed, with no production modification.
- Scratch evidence paths: `scratch/spec_p7a2_history_review.py`, `scratch/spec_p7a2_history_diagnostic.py`, `scratch/quality_p7a2_history_review.py`, `scratch/quality_p7a2_history_verified.py`; associated `scratch/quality_p7a2_history_runner.py` is also ignored. These review artifacts are not staged, rewritten, or presented as worker reruns. The distinct initial failures and later diagnostic results above remain part of the record.

### Exact worker command and reviewed snapshot

Reuse the full approved `$runner` from the RAG extraction plan as described in section 11, changing only its repository directory to this worktree. It uses the pre-import OS-variable whitelist, temporary cwd/config/DB/cache, zero live flags, normal conftest, and only the three tracked `real_agent_cases.jsonl`, `golden_scientific_cases.jsonl`, `diverse_scientific_cases.jsonl` copies with SHA-256 equality checks. Test arguments are paths/node IDs only; the runner itself supplies `-q -p no:cacheprovider --tb=short -rs`. No extra assets, host secrets/configuration, installs, live services, or full suite were used.

```powershell
$historyFocus = @(
    'tests/agent/test_worker_ownership.py',
    'tests/agent/test_decision_loop.py',
    'tests/agent/test_decision_adapter_retry.py',
    'tests/agent/test_tool_adapters.py',
    'tests/agent/test_tool_adapter_compat.py',
    'tests/agent/test_workflow_orchestrator.py',
    'tests/agent/test_workflow_run_session.py',
    'tests/agent/test_decision_history.py',
    'tests/agent/test_decision_continuation.py',
    'tests/agent/test_decision_continuation_store.py',
    'tests/agent/test_decision_protocol_recovery.py',
    'tests/agent/test_decision_transport_boundaries.py',
    'tests/agent/test_decision_migration_boundaries.py'
)
$runner | & 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -B -c "import sys; exec(sys.stdin.read())" @historyFocus
```

History-only runs used the same invocation with `tests/agent/test_decision_history.py`; the two parent-check RED nodes used that filename followed by `::test_wide_pair_rejected_before_materializing_keys` and `::test_pair_keys_rejected_before_equality_hooks` respectively. No tests are rerun merely for this evidence/local-commit step.

| Approved path | Frozen Git blob |
|---|---|
| `src/agent/harness/decision_history.py` | `ed331ee6d23859ca7dea08ae1dff268d30f7c183` |
| `tests/agent/test_decision_history.py` | `550dced7bbe1e1da24ced5abbc52ce68ab0dea6f` |
| `src/agent/harness/decision_loop.py` | `e7ebe3d5d9a609318d7047982daa0348e9796049` |
| `src/agent/harness/decision_continuation.py` | `8807180b330fb19ff4f8f8d19c9a7afcb3880e73` |

Parent authorizes exactly these four unchanged reviewed files plus this plan for a conventional **local** commit after diff, scope and filename-only secret checks. Commit/tree/status and all five blobs are reported outside this document. Pure history is completed, **not Web integration, A2 or P7 completion**. Batch 2 is next under the released plan and needs no renewed start confirmation; this evidence/commit step does not implement it. No push/PR/full/live/default activation, and no changes to the original checkout.

## 13. Web foundation checkpoint: actual-route evidence and review closure

Parent released the foundation implementation on `codex/web-decision-runtime-integration`, starting at clean HEAD `3faec3547cc09d3b5d665d4485ac1fd52c4786e0`, tree `50e1ba250a6c6f580d0ae022f72d0065cd1e015b`. Dependencies are reviewed ownership `78d0a32e2bbb752c8af71eb33aedbc1546e6b4d2` and the history commit at that HEAD. Both remain read-only. This checkpoint contains exactly the seven reviewed source/test blobs below plus this evidence-only plan append. No spec or handoff-ledger changes, publication, default activation, or further feature work are authorized in this commit step.

Implemented foundation: constructor-only `normal_chat_mode='legacy'` default and explicit `decision_a2` with closed `native`/`json` modes; early delegation on the actual mounted `/ws`; existing session/origin middleware and protected reference-restore HTTP used to obtain the cookie; shared registry/store/reference ownership without Supervisor execution; real A1 admission, OpenAI-compatible adapter, ModelDecisionLoop, Session and RDKit tools with an in-memory HTTP transport. Runtime adds one responsive receiver, serialized deadline-aware sending, server turn IDs, a reader lease around dispatch/physical settlement, opaque model-generation UUIDs, basic cancel/ping/next-turn handling, and a shutdown drain entry. Strict candidate/report envelopes are not extended with turn IDs. Candidate/report projection and UI consumption are not yet implemented by this checkpoint.

The actual-route regression matrix covers admitted greetings/concepts, requested-RAG versus `retrieval_performed=false`, whole-request unsupported/disabled rejection (including all four reported SPEC4 cases), selected raw-frame/authority boundaries, real property calculation, and a two-subject property/likeness sequence whose next protocol proposal depends on the previous observation. Scientific output still comes from the existing formatter; no model-number rewrite or synthetic scientific result is introduced. MockTransport proves protocol/lifecycle contracts, **not live external inference**, real model understanding, generation, trained activity prediction, or normal-browser acceptance.

### Worker runs: RED, fixture failures, and GREEN (not additive totals)

Every test run below used the same approved isolated runner described after the table. Unless otherwise stated, each had **7 warnings** (existing SWIG type/FastAPI `on_event` deprecations), no skips; failing runs exited 1 and GREEN runs exited 0. Fixture failures and diagnostic reruns are retained, not relabeled as behavioral RED or hidden by subsequent passes.

| Actual run | Result and cause |
|---|---|
| Initial actual-route module | **10 failed, 1 passed / 3.69s**: default legacy behavior passed; enabled/auth/mode cases failed because the constructor lacked `normal_chat_mode`. |
| First real-owner bridge probe | **1 failed / 2.82s**: injected fixture omitted three required registry tools (`admet_predictor`, `candidate_ranker`, `llm_molecular_generator`). Reused real core-tool construction instead; registration rules were not changed. |
| Corrected bridge RED | **1 failed / 2.88s**: missing `worker_owner` bridge parameter. |
| Bridge GREEN and existing transport regressions | **19 passed / 5.11s** after optional ownership forwarding and preserving WorkerCleanupError instead of fabricating failed result/complete. |
| Initial actual-route lifecycle group | **2 failed / 2.44s**: missing constructor wiring. |
| First runtime/transport combination | **4 failed, 28 passed / 11.87s**: complete-send bookkeeping raced disconnect and next-turn acceptance, propagating cancellation or rejecting the next turn. |
| Corrected first combination | **32 passed / 7.85s**. Delivery completion is recorded after the actual send; physical settlement precedes the final complete, and delivery tasks remain retained. |
| Expanded actual-route request matrix | **6 failed, 38 passed, 2 errors / 12.76s**: overbroad capabilities, non-boolean flag handling, and science-fixture errors. The oversized raw-string auto-ID also caused fixture setup/teardown errors; output truncation prevented retention of the complete exception cause. Explicit short IDs replaced auto-IDs; no size expectation was weakened. |
| Science fixture diagnostic 1 | **2 failed / 4.85s** with `model_decision_unavailable`; unique protocol call IDs alone did not resolve the failures. |
| Science fixture diagnostic 2 | **2 failed / 4.98s**: captured MockTransport errors identified a fixture `KeyError('tool_name')` on the observation shape. The fixture now consumes the real observation evidence ID; no loop/adapter/schema change. |
| Request matrix recheck | **1 failed, 44 passed / 12.60s**: the text assertion `999999` matched a genuine float tail in likeness output. A unique model-prose marker replaced the substring assertion; actual scientific numbers were not rewritten. |
| Parent source-boundary migrations | **3 failed / 3.33s**: waiting for the sender lock escaped the total deadline; failed request-accepted send left a registered owner with no task; task creation failure propagated and lost its coroutine. |
| Foundation combination after fixes | **107 passed / 17.41s**. Lock acquisition and send share one deadline; registration follows successful scheduling; failed creation closes the coroutine and does not leave an owner. |
| First frozen foundation combination | **106 passed / 17.68s**, session `74162` completed. The one-count reduction removed the obsolete source-string no-dispatch assertion, replaced by the actual default-off route test; no behavioral test was skipped. |
| Formal SPEC-finding migration RED | **9 failed, 4 passed / 6.85s**: run/watcher creation rollback failures in owned/no-owner paths and four oversize-frame cases. Exact 24-KiB ping/chat and no-watcher compatibility were positive controls; the RuntimeError deadline-creation control already closed its coroutine in Python 3.10. |
| Extended creation-failure RED | **4 failed, 1 passed / 4.40s**: actual-route run/watcher failures plus deadline creation under ValueError/CancelledError left unsubmitted coroutines open. The preserved watcher diagnostic observed terminal followed by a still-live real provider (`provider_entered=true`, `provider_exited=false`, `child_done=false`, reader count 0). |
| Two-finding targeted GREEN | **15 passed / 5.55s**. Correct early cancellation may prevent provider dispatch entirely; assertions require every created child settled, unscheduled coroutines closed, and provider exit if it started. No unconditional `provider.entered` wait is required for GREEN. |
| Final five-file foundation combination | **121 passed / 20.67s**, session `78499` completed, exit 0. No sessions remained active; no restart/termination of this run. |

The earlier combined runtime session `91589`, expanded-matrix session `85943`, matrix recheck session `40391`, and 107-test session `60318` also completed normally with their recorded exit statuses. The other worker test calls returned completion directly. The first shell HEAD/tree lookup used an unquoted PowerShell `HEAD^{tree}` argument and failed parsing; the correctly quoted retry verified the exact supplied HEAD/tree. It was not a test failure or a repository-state change. No complete-suite, live, Node, browser, or new source-compilation run was performed merely to create this evidence commit.

### Independent SPEC and QUALITY on the same seven final blobs

These are parent-reported independent runs, not worker reruns or totals to add together:

- Initial foundation SPEC focus: **106 passed, 7 warnings / 17.20s**. Initial scratch probe **3 failed, 1 passed / 4.53s** and exact probe **4 failed, 3 passed / 5.30s** identified two real findings. P1: watcher creation was outside the bridge's cleanup guard, permitting result/complete and reader/owner release while a loop/provider child survived. P2: the decoder inherited the 32-KiB protocol default instead of the specified **24-KiB raw Web frame** limit; 24,577-byte and 25,000-leading-space ping/chat frames were accepted, with one admission/provider call for chat.
- Both findings were migrated into formal tests before minimal production changes. Run and watcher creation now share the outer `try/finally`; unsuccessful scheduling closes its coroutine, and all successfully scheduled children are cancelled/drained before returning an error. Deadline scheduling closes its unsubmitted coroutine for all creation exceptions, including cancellation. Runtime calls `decode_protocol_json(raw, max_bytes=24 * 1024)`; A1 and scientific bounds are unchanged. Exact 24,576-byte ping/chat remain positive controls; oversized frames produce `invalid_frame` with zero admission/provider calls. The original `scratch/spec_p7a2_web_foundation_review.py` was preserved (SHA-256 `a4039c588ecf21288ff3a78a30c5eda69ba54c89c1a8fe219042f204770e2005`). No production await was added to force provider startup for a test.
- **SPEC re-review APPROVE, same seven blobs:** **121 passed, 7 warnings / 19.52s**, plus **11 independent probes / 5.17s**; seven-file in-memory compile and diff checks passed.
- **Peirce QUALITY APPROVE, same seven blobs:** **121 passed, 7 warnings / 20.04s**. Initial independent probe **9 passed, 2 failed, 7 warnings / 8.69s** repeated the `999999`-substring collision with genuine floating-point output. Independent unique-prose-marker diagnostics were **2 passed, 7 warnings / 5.20s**; the original scratch evidence remains. No production change was made for this fixture issue. Parent also reports seven-file compile/diff checks and all three evaluation-JSONL SHA checks passed, with no active sessions.

### Exact five-file command and reviewed Git blobs

Extract the complete fenced `$runner` from [RAG extraction plan](2026-09-24-rag-service-extraction.md), substituting only its repository directory with this integration worktree. As in sections 11–12, it retains only OS environment variables before imports; uses temporary cwd/config/DB/cache, zero live flags, normal conftest and MedChat Python `-B`; and copies only the three tracked evaluation JSONLs with SHA equality assertions. Runner argv are paths/node IDs, not pytest flags; it supplies `-q -p no:cacheprovider --tb=short -rs` internally. No credentials, provider services, real model/structure assets, dependency installs, or full heavy suite are part of these commands.

```powershell
$foundationFocus = @(
    'tests/agent/test_web_decision_runtime.py',
    'tests/agent/test_web_decision_runtime_lifecycle.py',
    'tests/agent/test_decision_chat_transport.py',
    'tests/test_model_request_lifecycle.py',
    'tests/test_web_app_lifecycle.py'
)
$runner | & 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -B -c "import sys; exec(sys.stdin.read())" @foundationFocus
```

The two-finding targeted invocation used the same runner with these four exact nodes (15 parameterized cases at final freeze): `tests/agent/test_web_decision_runtime.py::test_exact_raw_frame_boundary_through_actual_route`, `tests/agent/test_web_decision_runtime_lifecycle.py::test_watcher_creation_failure_cannot_release_running_provider`, `tests/agent/test_decision_chat_transport.py::test_no_owner_bridge_scheduling_rollback`, and `tests/agent/test_decision_chat_transport.py::test_deadline_scheduling_failure_closes_unsubmitted_coroutine`. The extended RED used the second and fourth nodes only. Prior groups used the corresponding module or named node documented above. No tests are rerun in this append/stage/local-commit step.

| Reviewed path | Frozen Git blob |
|---|---|
| `src/web/app.py` | `ca41c9adf3e8b5bf834890aba21ac0be9c75a93d` |
| `src/web/chat_handler.py` | `969bd0ee50aebc64e596adb10b3f0ed1334b20a3` |
| `src/web/decision_chat.py` | `5357cffad8db3b9c04c63c6ae7cb59327774935b` |
| `src/web/decision_runtime.py` | `81dfd706afce6c78fe2bd5cd6285ced4db51ef3f` |
| `tests/agent/test_decision_chat_transport.py` | `1c5059ca0f1b97cad64924540b62a55d7c347d49` |
| `tests/agent/test_web_decision_runtime.py` | `202d80311a0ccb08464d3e2e8b2ff28126c732d1` |
| `tests/agent/test_web_decision_runtime_lifecycle.py` | `3d3cd369086464f1ff24738bc89278b206044f9b` |

### Remaining gates and next parent release

**Tasks 2–5 are still incomplete.** Task 2 lacks the missing-server-scope regression and full static HTTP contract verification (route presence alone is not that acceptance). Task 3 still needs the complete reference/invalid-explicit and resume matrices. Task 4 still needs credential-only epoch rotation, replacement/refresh failures, full reader/writer concurrency and generator/background-consumer isolation coverage. Task 5 still needs actual-route nested timeout orders, worker/future/join barriers, repeated cancellation, disconnect/overflow/serialization/finish races, and active/waiting shutdown verification. Basic foundation tests and supplementary bridge tests do not substitute for these gates.

**Web Tasks 6–8 are unstarted:** socket history retention, nonce-safe multi-turn resume and constraint validation, candidate/report projection and actual protected reference/ACK boundaries, and home UI controls/persistent status. The already committed pure history dependency in section 12 is not Web Task-6 completion. Full A2 and P7 remain pending; original package-8 ordinary-chat/capability and multi-turn acceptance remains mandatory, not rewritten as expected rejection. P7-B, C integration and live model/science/browser acceptance are not completed or newly authorized.

WorkerCleanupError remains unresolved ownership, not a normal failure result: runtime retains owner/task/reader and does not emit terminal or close still-used resources. There is no automatic join retry, tool replay, successful-settlement fabrication, or finite shutdown guarantee. **Actual failed-join route verification is still pending.** Parent approved its later isolated-child strategy: use the real route in an independently created offline child with OS-only environment, temporary root, no live services/secrets, and hidden Windows process; use controlled synchronization/state snapshots to prove active owner/reader retention, `modelclose=0`, no terminal and shutdown pending. Terminate only that confirmed-owned child; timeout/failure paths also terminate/wait/drain output in `finally`. Forced isolation-test termination is explicitly **not graceful cleanup**. No production escape/test flag may be added. Ordinary blocked-worker tests instead release their barriers in `finally` and verify real physical cleanup. The A2 versus C finite-UI-terminal/retained-cleanup difference stays unresolved.

Both reviewers approved only the implemented foundation on the seven blobs above. Parent authorizes this plan-only append and exact local commit of those seven files plus the plan after same-blob, diff/scope and filename-only secret checks. Commit/tree/clean status and all eight blobs are reported outside the document. Then stop: parent will release lifecycle-core completion (Tasks 4/5 and the approved failed-join child) before history/resume/references/UI, without another user confirmation merely to start that released batch. This sequence neither expands nor reduces the final requirements; no push, PR, merge, full/live run, or further implementation belongs to the current commit step.

## 14. Lifecycle checkpoint — independently reviewed, not full A2

This checkpoint follows foundation `07131a48eea41dc95be55c68bdfc8c508564a3d9` on the same A2 branch. Only four source/test files change, plus this evidence append. No default activation, deployment, live inference, model training, credentials or production assets are involved. The original mixed checkout is unchanged.

Implemented and verified within the released Tasks 2–5 lifecycle subset:

- The common model publisher stages consumer bindings before publishing the new model/configuration/opaque epoch. A consumer failure restores previous bindings and closes an unpublished client; the independently owned generator stays unchanged.
- Cancellation during runtime task creation closes the unscheduled coroutine. Shutdown crossing the accepted-frame send boundary cannot dispatch a late turn.
- Actual mounted WebSocket and HTTP tests cover default-off/static contracts, a separately labelled missing-scope boundary, failed refresh, credential-only epoch rotation, reader/writer concurrency, and unsupported-client no-fallback behavior.
- Actual Session/workflow/adapter submissions cover both nested timeout orders, delayed descendants, repeated cancellation and disconnect. A completed future does not imply executor join; owner/reader and resources remain retained until physical settlement. Late worker completion does not convert timeout/cancellation to scientific success.
- Send/serialization/overflow, stale cancellation, concurrent chat, queued admission, creation and finish races, plus active/idle shutdown are covered. No timeout or scientific assertion was relaxed.

The permanently failed-join test uses an independently created hidden, isolated child with real executor/future attachment. It observes retained owner/reader, no terminal, no model close and pending shutdown, then terminates and waits for only its own child. **Forced test-process termination is not graceful cleanup.** No production escape, fake settlement, retry or replay was added. Ordinary blocked tests release their barriers and verify joins. A2 still has no finite terminal/shutdown guarantee for an uncooperative worker; C's finite-UI-terminal policy remains a separate unresolved integration requirement.

### Actual commands and results

All Python runs used the approved pre-import OS-only isolation runner from `2026-09-24-rag-service-extraction.md`, temporary configuration/database roots, live flags off, and copying only the three tracked real/golden/diverse JSONLs with SHA verification. The five-file invocation was:

```powershell
$runner | & C:/Users/xkx52/.conda/envs/MedChat/python.exe -B -c "import sys; exec(sys.stdin.read())" `
  tests/agent/test_web_decision_runtime.py `
  tests/agent/test_web_decision_runtime_lifecycle.py `
  tests/agent/test_decision_chat_transport.py `
  tests/test_model_request_lifecycle.py `
  tests/test_web_app_lifecycle.py
```

The runner applies `-q -p no:cacheprovider --tb=short -rs` with the normal conftest. Results are focused offline integration evidence, not a complete suite or real provider/browser acceptance:

| Run | Actual result |
|---|---|
| Implementer final five files | 163 passed / 7 warnings / 99.10s |
| Independent SPEC five files | 162 passed / 1 failed / 7 warnings / 100.54s |
| SPEC original failed node plus two cancellation probes | 3 passed / 7 warnings / 6.53s |
| SPEC original ordered first five cases | 5 passed / 7 warnings / 7.05s |
| Independent QUALITY five files | 163 passed / 7 warnings / 81.96s |
| QUALITY original node plus SPEC two and QUALITY two probes | 5 passed / 7 warnings / 6.90s |

No skips. Warnings remain the existing SWIG/FastAPI deprecations. SPEC's `[24576-chat]` failed at a three-second receive wait; its cause remains **unknown**. Subsequent unchanged reruns are not a fix or proof that the first failure was environmental. Keep it visible through the later full/CI gates; do not replace its failed result with a passing summary. Review probes remain ignored scratch files, not publication assets.

Both independent reviewers approved this lifecycle checkpoint on unchanged seven-file blobs, with no important findings. They did not approve unfinished Tasks 6–8 or full A2. Seven-file in-memory compile and diff checks passed. Implementer also passed filename-only secret-pattern/scope checks. All returned test sessions ended; no remaining test child or Python process was observed at freeze.

### TDD and unsuccessful-run history

| Earlier run | Actual result and disposition |
|---|---|
| Common publisher RED | 4 failed / 6.60s; real early-publication/rollback defects |
| Nested first run | 8 failed / 45.67s; incorrect invocation-boundary and enum observer fixtures |
| Single-case diagnosis | Interrupted, result not collected; no process killed; not counted as a pass |
| Following single-case diagnosis | 1 failed / 9.20s; waited for complete while its own join barrier remained held |
| Corrected nested observation | 5 failed / 3 passed / 32.74s; invalid assumption about which executor joins first |
| Queued/creation/shutdown group | 4 failed / 2 passed / 4.57s; two real runtime defects, two premature outer-task completion assertions |
| Static HTTP first run | 2 failed / 1 passed / 3.01s; tool name used instead of workflow policy |
| Static HTTP second run | 2 failed / 3.22s; expected raw results instead of the persisted safe projection |
| Display/transport group | 1 failed / 5 passed / 6.18s; missing barrier to establish overflow |
| Five-file first combined run | 1 failed / 162 passed / 42.06s; fixture's 0.2s total budget exhausted before dispatch |

These fixture corrections are not labelled production RED. Intermediate GREEN groups contained 42, 8, 63, 2, 1, 20, 3, 15 and 3 passes; a combined 163-pass / 57.21s run preceded the final strengthened assertions. None supersedes the independently observed, still-unexplained SPEC timeout above. No broader reliability attribution is made.

### Reviewed blobs and next work

| File | Reviewed blob |
|---|---|
| `src/web/app.py` | `c5a5ff21c9e7ca7f902a4fec66cec5ffbac77c22` |
| `src/web/chat_handler.py` | `969bd0ee50aebc64e596adb10b3f0ed1334b20a3` |
| `src/web/decision_chat.py` | `5357cffad8db3b9c04c63c6ae7cb59327774935b` |
| `src/web/decision_runtime.py` | `7748014c3e1482679e856eac7b86d4bc2191f09c` |
| `tests/agent/test_decision_chat_transport.py` | `1c5059ca0f1b97cad64924540b62a55d7c347d49` |
| `tests/agent/test_web_decision_runtime.py` | `2905503a36f885a00c92038f0bfd0fe033d18851` |
| `tests/agent/test_web_decision_runtime_lifecycle.py` | `fca58900c73df51ceb053a157c0f96731e5b7248` |

Next is Task 6's real socket history and nonce-safe continuation, including frozen original obligations, pre-CAS refinement, two clarification cycles, resumed cancellation, stale epoch and waiting shutdown. Tasks 7–8 reference/ACK/report and frontend controls follow separately. The pure history dependency is not Web history acceptance; active/idle shutdown is not waiting-handle shutdown acceptance. Original final ordinary-chat/capability and multi-turn cases, B bindings, C integration, full offline gates and package-8 real model/science/browser evidence remain pending. This checkpoint is a local scoped commit only, not a push, PR, merge or deployment.

## 15. Task 6 Web history and continuation checkpoint

Base: `eab355a1ed62fd5d5c1ead18f9e478f1d1328ad4`. This checkpoint changes only the Web runtime and its two existing actual-route test files, plus this evidence append. Shared history/loop/continuation/CAS, A1 admission, scientific parsers and reference service remain unchanged. No provider, production model, asset, deployment or default entry is activated.

Implemented: socket-only ordinary-text history using the shared 20-pair/16-KiB whole-pair policy; actual displayed text retained only after successful complete delivery; explicit omission/eviction metadata; detached original context/obligations for continuation; two clarification cycles with fresh nonces; expiry/abandon/socket/owner/configuration and source checks; pre-CAS input refinement using existing scientific parsers; resumed cancellation and fresh-turn recovery; shutdown cannot restore cleared history/waiting state. Scientific, partial, failed, cancelled and waiting responses do not become successful ordinary-chat history. The exact admitted concept pair is still incremental history proof, not arbitrary normal-chat understanding.

### Independent findings and minimal fixes

1. **SPEC P2, reproduced:** a resumed turn cancelled before the loop or failing configuration refresh emitted a non-waiting terminal but retained the old socket handle. Reusing its nonce caused one additional CAS and model invocation. Failed/cancelled terminal construction now clears local authority before delivery, including failed delivery, without modifying the durable waiting audit. Rejected invalid refinement still preserves a valid handle. SPEC reproduced four failures with native/json, then closed the finding on the fixed snapshot.
2. **QUALITY P2, reproduced:** `type: []` or `type: {}` reached set membership before type validation, raised TypeError and disconnected the socket, clearing history and valid waiting state. The receiver now checks native string type before control dispatch; malformed controls receive fixed `invalid_control` with no admission/model/CAS calls. Subsequent ping and valid resume still work. QUALITY closed the original two failures on the final snapshot.

These are narrow lifecycle/input fixes, not broader protocol admission, model capability expansion, tool execution or scientific-validation changes. No timeout or assertion was weakened.

### Actual offline evidence, including failures

Runs used the approved pre-import OS-whitelist runner, temporary configuration/database/cwd, live flags off, and three tracked JSONL copies with matching SHA. The five-Web-file invocation is the exact one in section 14. The separate core invocation selected `test_decision_history.py`, `test_decision_loop.py`, `test_decision_continuation.py`, `test_decision_continuation_store.py` and `test_decision_protocol_recovery.py` under `tests/agent/`, with the same runner and `-B -q -p no:cacheprovider --tb=short -rs`.

| Run | Actual result |
|---|---|
| Initial completed implementation, five Web files | 216 passed / 7 warnings / 153.92s |
| Unchanged shared history/loop/continuation core | 511 passed / 0 warnings / 117.13s |
| Independent SPEC first five Web files | 216 passed / 7 warnings / 156.17s |
| SPEC original probes plus positive control | 4 failed / 3 passed / 7 warnings / 15.53s; retained-handle finding |
| Migrated SPEC regression RED | 4 failed / 2 passed / 7 warnings / 14.36s |
| Failed-terminal-send regression RED | 2 failed / 7 warnings / 7.72s |
| Fixed handle matrix / five Web files | 36 passed / 7 warnings / 39.41s; 224 passed / 7 warnings / 158.94s |
| Independent SPEC recheck | 36 passed / 7 warnings / 50.52s; first P2 closed |
| Independent QUALITY first five Web files | 224 passed / 7 warnings / 184.49s |
| QUALITY malformed-control probes | 2 failed / 1 passed / 7 warnings / 9.02s; no fixture correction |
| Migrated control-type RED / GREEN group | 2 failed / 6 passed / 7 warnings / 19.49s; 22 passed / 7 warnings / 28.19s |
| Final implementation, five Web files | 232 passed / 7 warnings / 159.09s |
| Final SPEC original probes/new types/positive controls | 21 passed / 1 failed / 7 warnings / 49.59s |
| SPEC unchanged eight-type recheck | 8 passed / 7 warnings / 16.58s |
| Final QUALITY original probe/type/history/nonce/send controls | 16 passed / 7 warnings / 30.62s; second P2 closed |

No skips in these runs. Seven-warning groups retain existing SWIG/FastAPI deprecations. Final SPEC's `unknown-string` case timed out in the three-second receive wait during its `Explain logP` warmup, **before sending the malformed control**. The cause remains unknown. The unchanged eight-case rerun is not a fix or proof of environmental causation. Keep both this failure and section 14's original `[24576-chat]` timeout visible through later full/CI gates; neither is declared resolved.

Earlier implementation failures are also preserved:

| Run | Actual result / cause |
|---|---|
| History initial RED | 2 failed / 6.57s; second request lacked prior pair |
| Omission/eviction group | 4 failed / 24.63s; missing metadata plus an oversized-response fixture crossing the earlier protocol bound |
| Displayed-text fixture correction | 1 failed / 3 passed / 23.37s; raw trailing whitespace incorrectly compared with actual displayed text |
| Resume initial RED | 1 failed / 6.95s, then 9 failed / 14.29s; resume still returned invalid_control |
| Resumed history RED | 1 failed / 10 passed / 16.66s; retained original instead of clarified completed question |
| Shutdown RED | 1 failed / 8 passed / 15.99s; late complete restored cleared state |
| HTTP fixture corrections | 5 failed / 5 passed / 14.31s, missing httpx import; 4 failed / 6 passed / 14.43s, duplicate cookie |
| Persisted-shape fixture correction | 1 failed / 6 passed / 16.92s; Python tuple compared with JSON list |

Fixture errors are not counted as production RED. No shared-core source changed during the two final minimal fixes, so the 511-case result was not rerun or attributed to a later full suite. Original independent scratch probes were retained unchanged and remain ignored. Reviewers' attempts to diff previously unstored blob hashes produced `bad object`; these are Git inspection errors, not pytest failures. Final QUALITY verified the tiny delta by removing it in memory and recomputing Git blob hashes. Three-file in-memory compilation and diff checks passed; implementer filename-only credential scanning found no match. All returned test sessions ended and no leftover Python test process was observed at freezes. Failed-join test-child forced termination remains containment evidence, not graceful cleanup.

### Frozen review and next boundary

Both SPEC and QUALITY approve only Task 6 on the same final blobs, with both reproduced P2 findings closed and the receive timeouts explicitly unresolved:

| File | Reviewed blob |
|---|---|
| `src/web/decision_runtime.py` | `b45782733e56a02717ea64e17ff7c77c37a20778` |
| `tests/agent/test_web_decision_runtime.py` | `e807b373e1d7fc73bad983f0fe5ac1baf4502ad0` |
| `tests/agent/test_web_decision_runtime_lifecycle.py` | `886f44042ccea79f172ea878d67aa9cb010f3fc7` |

Parent authorizes the local four-file checkpoint commit after same-blob and scoped-stage checks. Next is Task 7's single existing candidate/report projection and actual protected reference/mount-ACK matrix, then Task 8's frontend controls/status. Projection cancellation must retain its actual offloaded store work, and report transport errors must not masquerade as optional sidecar success. These are future integration checks, not fixes claimed here. No push, PR or merge occurs at this checkpoint. Full A2/P7, original ordinary-chat/RAG/repeated scientific requirements, C lifecycle/topology and package-8 real/browser evidence remain pending.

## 16. Task 7 reference projection and delivery checkpoint

Base: `a01c46f6d4df786057f112c0c86ac5b4861819a0`. This checkpoint changes the bridge, existing helper's opt-in transport behavior, runtime delivery failure handling, existing transport tests and new actual-route reference tests. Scientific reference service/controller/normalizer, shared loop/ownership and scientific tools are unchanged. No UI, generator admission, model configuration, live provider or deployment is activated.

Actual mounted `/ws` tests use the real middleware identity, protected reference HTTP routes, temporary SQLite, A1/loop/Session and RDKit. Historical CandidateSets are explicitly synthetic fixtures, not real generation evidence. Confirmed selection survives ordinary chat and dispatches the exact trusted canonical structure to properties. Foreign owner, incorrect revision/order/compound key, unconfirmed view, revoked source and invalid explicit replacement cannot authorize calculation. Successful selected-candidate analysis does not change the original partial source or its warnings.

The bridge invokes the existing helper once with the full settled legacy result, not the display envelope. Frame order remains result, candidates, optional report, complete, after tool events. Candidate/report schemas gain no `turn_id`; report contents/digest and legacy default helper behavior remain unchanged. Optional report computation failure remains optional; opt-in report transport failure is fatal to delivery. Projection has its own retained, uncancelled task after the tool worker ledger is sealed. Cancellation stops further presentation and waits for actual offloaded reference/report work, without shutting down the shared executor or treating wrapper cancellation as thread completion.

### Reproduced findings and approved narrow fixes

- Initial route did not project results; disconnect could release the owner before reference work exited; report send errors/timeouts could be swallowed and followed by complete. Actual-route and transport REDs preceded the fixes.
- Independent SPEC reproduced post-result projection-task scheduling failure: `RuntimeError` left a writable, ping-responsive socket without complete; `CancelledError` published completed then cancelled results. Task ownership is now established before publishing the first result. Failure before publication receives one failed/cancelled result and matching complete; persisted successful computation is not rewritten.
- Additional service-error injection reproduced a related post-publication hang. Parent approved a bounded fail-closed extension: after real drain, an unrecoverable projection exception or cancellation after result publication attempts one fixed `1011 / Decision result delivery failed` close under the existing send lock and shared send deadline. It emits neither a contradictory second result nor a fake complete, clears socket-local authority, and never claims failed close delivery succeeded. Service exceptions sharing transport exception classes are distinguished by the sender's actual writable state. The receiver stops only after its turn has finished and is not cancelled again after peer-disconnect cleanup starts. These are fault-injection robustness results, not proof of a naturally occurring storage incident.

### Actual verification, including failed runs

All Python runs used the approved pre-import OS-whitelist runner, temporary config/database/cwd, live flags off and three tracked JSONL copies with matching SHA. Normal conftest and original deadlines remained enabled. The planned eight-file command is in Task 7 above; the six-file Web group is section 14's five files plus `tests/agent/test_web_decision_runtime_references.py`. Compilation below was in-memory; no source import, model call or asset loading was used for that check.

| Run | Actual result |
|---|---|
| Initial fixture correction | 8 failed / 12.95s; fixture incorrectly supplied noncanonical `OCC` as canonical |
| Actual route projection RED | 1 failed / 7 passed / 10.37s |
| Bridge projection RED | 3 failed / 4.09s; delivery helper absent |
| Actual disconnect/drain RED | 1 failed / 6.20s; owner removed before thread exit |
| Report transport RED | 2 failed / 2 passed / 9.25s; failure swallowed and complete sent |
| First six Web files | 253 passed / 7 warnings / 181.42s |
| Planned eight-file Python subset | 244 passed / 10 deselected / 7 warnings / 98.43s; not the full planned command |
| Full eight files, including ten existing Node DOM tests | 254 passed / 7 warnings / 121.84s |
| Independent SPEC first probes/control | 3 failed / 11 passed / 7 warnings / 17.60s; one scratch assertion confused stored `succeeded` with public `completed` |
| SPEC after that fixture-only correction | 2 failed / 10 passed / 7 warnings / 13.91s; genuine scheduling P2 reproduced |
| Migrated actual-route scheduling RED | 2 failed / 1 passed / 7 warnings / 7.39s |
| Scheduling fix, two files | 49 passed / 7 warnings / 34.26s |
| Scheduling fix, eight / six files | 257 passed / 7 warnings / 73.98s; 256 passed / 7 warnings / 179.14s |
| Additional service RuntimeError diagnostic | 1 failed / 7 warnings / 4.70s; post-result open-socket hang, not yet fixed at that point |
| Service-close RED | 2 failed / 7 warnings / 10.67s |
| Expanded close matrix RED | 7 failed / 7 warnings / 10.63s; close method absent |
| Post-result actual-thread cancellation RED | 1 failed / 7 warnings / 7.00s; duplicate cancelled result |
| Service ConnectionError/TimeoutError RED | 2 failed / 7 warnings / 6.99s; mistaken for actual transport failure |
| Peer-disconnect race RED | 1 failed / 7 warnings / 7.24s; receiver cancelled again while cleaning up |
| Intermediate fixes | 20 passed / 7 warnings / 17.38s; 63 passed / 7 warnings / 32.67s |
| Intermediate eight / six files | 271 passed / 7 warnings / 126.78s; 270 passed / 7 warnings / 179.41s |
| Receiver race fix focused group | 30 passed / 7 warnings / 21.12s |
| Final eight files, ten Node DOM tests included, no deselection | **275 passed / 7 warnings / 112.80s** |
| Final six Web files | **273 passed / 1 failed / 7 warnings / 143.87s**; normal scheduling control's original three-second `entered.wait()` timed out |
| Exact-node diagnostic, unchanged deadline | 1 passed / 7 warnings / 4.29s; not a fix or full rerun |
| Final independent SPEC new probes/phase diagnostic | 4 passed / 7 warnings / 5.29s |
| SPEC first second-group launch | 1 collection error / 1.32s / exit 4; old scratch could not import a test helper; no tests executed |
| SPEC with only explicit isolated test-helper import path corrected | 71 passed / 7 warnings / 31.38s |
| Independent QUALITY formal/lifecycle group | 72 passed / 7 warnings / 28.07s |
| QUALITY independent native/json shutdown and legacy/report controls | 9 passed / 7 warnings / 7.06s |

There were no skips in these Task 7 runs. Seven-warning groups retain SWIG/FastAPI deprecations. Existing Node tests were initially deselected because the implementer interpreted “no Task 8 UI tests yet” too broadly; parent explicitly confirmed the ten pre-existing Python-driven DOM tests were already in Task 7's approved scope. Final eight-file runs include them without changes or dependency installation.

**Three historical warmup timeouts remain UNKNOWN**, including sections 14/15 and the final six-file failure above. Independent safe phase sampling observed approximately 1.45 seconds from send to projection scheduling; a one-second stack sample was in `observe_call`/`deepcopy`, before the provider mock. It did not reproduce the timeout or establish its cause. No deadline was weakened, no environment diagnosis is claimed, and a passing single-node rerun does not erase a failed group. Keep these failures visible through the final full/CI gates; this checkpoint is not a claim that all tests were green.

Independent review used `scratch/spec_p7a2_task7_review.py` (original evidence retained), `scratch/spec_p7a2_task7_final_review.py` and `scratch/quality_p7a2_task7_review.py`; none is staged. Final SPEC ran the new scratch, the two changed test files, the old normal-only control and selected lifecycle controls. QUALITY ran the two changed files plus resumed-cancel/history-commit/shutdown controls, then new scratch plus existing report-frame legacy/snapshot/send-failure/source-race controls. Both used the approved isolated runner; the SPEC helper-path correction changed only runner setup, not the old scratch or production code. All reported test sessions exited, and no Python test process remained at the freezes. Five-file compilation, diff-check and filename-only credential-pattern checks passed.

### Reviewed snapshot and next work

SPEC and QUALITY approve **Task 7 only** on these same blobs, with reproduced P2 findings closed and the UNKNOWN timeouts explicitly retained:

| File | Reviewed blob |
|---|---|
| `src/web/decision_runtime.py` | `2fd1152a7942f998f2a2ec3f14639475620592af` |
| `src/web/decision_chat.py` | `71d67d2f03b7588ca13a70ec7781b441d309356e` |
| `src/web/chat_handler.py` | `d18be7e7dcc45a500b86afef2c9d81aa6d88f5fe` |
| `tests/agent/test_decision_chat_transport.py` | `c7a4deda64866dd5b90125393c2bad19d28aaa7f` |
| `tests/agent/test_web_decision_runtime_references.py` | `053af24437dcb10e80096fa09183e1739dffa468` |

Parent authorizes only a local six-file checkpoint after same-blob and explicit-stage checks. No push, PR, merge or deployment occurs here. Task 8 UI controls/persistent status/actual DOM ACK remain next, followed by full A2 review and verification. Original final ordinary-chat/capability/semantic follow-up, B tool bindings, C consent/topology/lifecycle integration and package-8 real-model/scientific/browser acceptance remain incomplete. These offline fixtures must not be reported as real molecular generation or browser acceptance.

## 17. Task 8 UI controls, connection ownership and independent review

This section supersedes only the preceding statement that Task 8 has not started. Parent released Task 8 on `cf28246e911b7e4a59fb721d0fc0c6f71daef465`, then three narrowly scoped compatibility changes: the active homepage script cache query, its existing exact-tag assertion, and the missing `querySelectorAll()` method in the existing structured-render fake document. No scientific assertion, controller, strict schema, backend or model configuration changed in this sub-batch.

The home script now waits for every socket's server announcement before sending, including the first connection. Decision mode has server-owned turn/trace controls, socket-only continuation, a response-bound abandon wait and persistent per-message outcomes. It does not queue or replay user input. Uncorrelated old `invalid_control` errors cannot terminate a newer pending request or release an outstanding abandon. Real request rejection codes still permit honest recovery. Completed transport does not promote partial/failed/rejected/cancelled/waiting science to success. Candidate/report frames retain their strict no-`turn_id` shape and existing source checks; confirmation follows actual ordered DOM mount, not receipt. Touched transport logs no longer print raw input, payloads or exception data.

Tests load the complete actual home script with a small DOM fixture and the existing candidate/reference/report modules; they do not implement a second event handler. The positive same-frame tests use the existing RDKit/SQLite report fixture, explicitly synthetic historical candidates, and assert zero model generation calls. They are not live molecular generation or real browser acceptance.

### RED and diagnostic history retained

Separate runs below are not additive totals. Missing timing/warning summaries are not inferred.

| Stage | Observed result and disposition |
|---|---|
| Initial controls/status, reconnect and late-stream development | 1P/1F; 1P/10F to 11P; 11P/2F to 13P; mount/ACK coverage to 18P, identity/disconnect to 20P; 20P/1F to 21P. The old active-cache assertion also failed before its approved update |
| Initial Python/whole-main frame fixture | Session 44575: 70P/141.85s, then 3P/1F plus nonexistent store `close()` cleanup error, exit 1. Cross-VM plain-object handling and fixture cleanup were corrected without production edits; sessions 25127 and 77303: 70P/71.17s + 4P, and 70P/63.87s + 4P, exit 0 |
| Parent abandon-response race | In-memory complete-script probe 22P/1F/4.54s; formal 21P/4F/0.75s to 25P/4.29s. Final 25P/4.47s, approved legacy checks passed; session 46083 report 70P/182.99s + same-frame 4P, exit 0 |
| SPEC: late old cancel kills new pending turn | Independent 1P/1F/0.584s; formal 28P/2F/4.33s to 30P/4.47s. Final 30P/4.49s and old probe 2P; report session 51628: 70P/182.85s + 4P, exit 0 |
| SPEC: late old cancel releases abandon wait | Independent 1P/2F/0.624s proved duplicate control/premature resume, not unauthorized calculation. Formal 31P/2F/4.48s to 33P/4.60s; final 33P/4.48s, probes 2P/3.96s and 3P/3.79s; report session 37675: 70P/86.84s + 4P, exit 0 |
| QUALITY: first-open send before ready loses ownership | Original and corrected full-script probes each 1P/1F, 0.454s/0.453s. The first probe's claim that A1 rejects `timestamp/client_id` was wrong and is withdrawn; A1 permits them. The UI failure remained after correcting that assumption. Formal ready-gate RED 33P/3F/4.42s to 36P/4.47s |
| Existing structured-render fake document | Original test failed at line 1034/3.82s, then line 1057/3.67s after removing an unnecessary initial-variable change; diagnostic exit 1/3.96s. `clearToolStatus()` called missing `document.querySelectorAll`, closing the fake socket. A one-method in-memory correction passed/4.08s. Parent approved only that missing method; no assertions or production guard were weakened |

The four original ignored Node probes remain unchanged: `spec_p7a2_task8_review.js`, `spec_p7a2_task8_abandon_review.js`, `quality_p7a2_task8_review.js` and `quality_p7a2_task8_review_corrected.js`. Their later passes do not retroactively validate the withdrawn A1 hypothesis. They are not staged.

### Final five-file verification

All following direct Node commands exited 0 on the final frozen five files:

| Command | Worker result |
|---|---|
| `node --check src/web/static/js/home/main.js` | Passed / 3.66s |
| `node tests/home_decision_runtime_test.js` | 36 passed / 4.35s |
| `node tests/home_scientific_references_test.js` | Passed / 3.96s |
| `node tests/home_evidence_report_test.js` | Hostile preflight passed / 3.55s; not positive same-frame coverage |
| `node tests/home_agent_task_panel_test.js` | Passed / 3.50s |
| `node tests/home_structured_molecule_render_test.js` | Passed / 3.86s |
| `node tests/home_workflow_completion_behavior_test.js` | Passed / 3.84s |
| `node tests/frontend_safe_render_test.js` | Passed / 3.73s |
| Original SPEC / abandon / QUALITY / corrected QUALITY probes | 2P/3.77s; 3P/3.89s; 2P/3.82s; 2P/3.64s |

Final session **13248 exited 0**: isolated `pytest.main` on `tests/agent/test_evidence_report_frames.py` with `-q -p no:cacheprovider --tb=short -rs` reported **70 passed / 182.85s**, followed by **4 same-frame passed** from `node tests/home_decision_runtime_test.js --frames-stdin`. The wrapper did not print a warning summary. It reuses the approved RAG-extraction plan's pre-import OS whitelist, temporary cwd/config/database/cache and three tracked JSONL copies with matching SHA. Only the child invocation changes: after pytest succeeds, the existing fixture/capture helpers create a partial historical result with a valid 32-character trace, assert zero model generation calls, and pass UTF-8 JSON frames to Node. The temporary fixture factory is restored in `finally`; the store has per-operation connections, not a `close()` API. No live model, user asset or configuration is loaded.

Final independent **SPEC approves Task 8 only**: 36P/1.201s, original probes 2P/0.572s and 3P/0.568s, plus syntax and all six existing Node scripts. Final independent **QUALITY approves this UI checkpoint only**: 36P/1.125s, corrected QUALITY 2P/0.453s, SPEC 2P/0.438s and abandon 3P/0.468s, plus syntax and six existing scripts. Both used OS-whitelisted Node subprocesses, verified unchanged blobs before/after, and reported all sessions ended. Neither final re-review reran Python positive frames; standalone report remains hostile preflight only.

### Actual-route timeout remains unresolved, not waived

During the earlier QUALITY review, an additional real mounted-route probe in `scratch/quality_p7a2_task8_frames.py` failed after acceptance: **1P/1F/7 warnings/31.83s** (the passing test independently generated RDKit/SQLite frames and passed all four whole-main DOM contracts). Its exact route rerun failed **1F/7 warnings/27.30s**; a safe phase-only diagnostic failed **1F/7 warnings/27.80s**. All retain the original three-second `ActualSocket.receive()` deadline.

The diagnostic observed ready at 1.438s, accepted at 1.454s and timeout raised at 10.235s. Await-chain locations included the existing graph `ainvoke` and serialized deadline-aware sends. No locals, prompt or environment data was printed. This snapshot does not locate the delay or prove whether the provider transport was entered; neither profiler nor environment is established as the cause. These three failures and the three earlier backend warmup UNKNOWN cases remain visible. The DOM fixture correction does not fix them; no timeout was relaxed and no passing rerun erases a failed group. A separate causal investigation and final full/CI gates are still required before A2 publication/merge.

### Reviewed blobs and checkpoint boundary

| File | SPEC/QUALITY reviewed blob |
|---|---|
| `src/web/static/js/home/main.js` | `d6b5a4dc26ad034d5bcbb56a65060370d875db5f` |
| `src/web/templates/index.html` | `603b0a5ef4a6cb0988a5f96508055b9406a9930c` |
| `tests/home_decision_runtime_test.js` | `caa3f2a8fdbf4731b267e59be79a650161e797f3` |
| `tests/home_workflow_completion_behavior_test.js` | `ee4038586665a75b699f1fb7a0ca9cad3c26e3a2` |
| `tests/home_structured_molecule_render_test.js` | `c86e168034ccbc0b5462cd400a31e723d287a2a7` |

The five Task 7 backend/test files remain byte-identical to `cf28246`. Diff and filename-only credential-pattern checks passed. Parent authorizes a local checkpoint of these five files plus this evidence section after explicit staging; no push, PR, merge, activation or deployment. A2 overall review, unresolved route diagnostics, full offline gates, final ordinary-chat behavior, B/C integration and package-8 real provider/scientific/browser acceptance remain unfinished. Completing this Task 8 subtask does not complete work package 8 or P7.

## 18. Causal diagnostic and bounded fixture-readiness follow-up

Task 8 checkpoint is `2769e1eca86220c5c3be3a749392148133979d53`, tree `370989c7bf890028f25865f1883ec7e6877fa347`. Overall source/SPEC inspection of its 26-file A2 increment found no new P1/P2, but is not full runtime approval. The accompanying spec's busy-code spelling is corrected from `request_in_progress` to the existing backend/frontend `turn_in_progress`; no protocol changes.

### Evidence changes the next action, not the previous results

Euler's isolated controls locate a repeatable mechanism: `decision_loop._run()` synchronously imports `langgraph.graph` on the event-loop thread; the actual-app fixture's current-thread `sys.setprofile(observe_call)` amplifies this import. Loop entry through the following `context_value()` was approximately 8.17s with the observer and 2.59s without; subsequent context/catalog work was not comparable, and Session.start was approximately 0.11–0.14s. Four failing handshake/payload controls had admission=1, provider MockTransport calls=0 and event-loop lag 8.67–9.06s. This is not provider-response latency in those reproductions.

| Separate isolated run, original deadlines | Result (each 7 warnings, no skips) |
|---|---|
| Original runner and unchanged exact probe | 1P / 5.77s; unexplained initial pass, not failure erasure |
| Before/after ready, full payload, observer on | 1F / 27.90s; 1F / 28.80s |
| Before/after ready, minimal payload, observer on | 1F / 27.43s; 1F / 28.03s |
| Before ready/full payload, observer off | 1P / 20.13s; not admission/CAS observer evidence |
| Prelude measurement, observer on/off | 1F / 28.67s; 1P / 19.86s |
| Unchanged exact probe, cold import/original observer | 1F / 27.82s |
| Same exact probe and observer, only dependency pre-imported | 1P / 21.39s; pre-import 2.156s; original provider/admission assertions retained |

**Runner correction:** ignored scratch files lie outside `tests/`. The prior scratch runner did not explicitly load `tests/conftest.py`; earlier descriptions of scratch runs as automatically using all normal conftest hooks were too broad. The new `scratch/causal_p7a2_task8_runner.py` explicitly loads that existing conftest as a plugin; all new causal controls share this setup and assert its configuration marker. Both wrappers retain pre-import OS-whitelist/temporary config/cwd/DB/cache isolation. The new wrapper also retains network blocking, tracked three-JSONL copy/SHA checks and original pytest options; `-rP` only exposes safe phase/count output. No credentials or real assets were read. Original runners/probes are preserved, not silently repaired.

Diagnostics are ignored files `causal_p7a2_task8_matrix.py`, `causal_p7a2_task8_prelude.py`, and `causal_p7a2_task8_import_control.py`, invoked one parameter at a time through that wrapper. The import control calls the unchanged original exact probe with all assertions. It does not mock graph, Session, store, admission or returned result. All sessions exited and sampled active owners settled to zero. No tracked code changed during diagnosis.

### Approved design and implementation sequence

The user delegates recommended choices. Parent selects **fixture readiness before observation**, not increased receive timeouts or removal of the observer. Moving production dependency loading is a different lifecycle change and is not smuggled into this test correction. Uninstrumented production cold-start latency remains a documented risk for final real acceptance; a warmed protocol test is not proof of a sub-three-second cold start. The three separate earlier warmup UNKNOWN cases are not automatically attributed to this mechanism.

Only `tests/agent/test_web_decision_runtime.py` may change in the next TDD sub-batch, followed by evidence in this plan. Keep every existing scientific assertion, profiler observation, original timeout, actual middleware/model adapter/Session/store and cleanup path. No source, dependency, fixture-data, UI or production configuration change. Legacy fixture construction does not gain a mandatory graph preload.

- [ ] Add a RED readiness-order test. Wrap `importlib.import_module` only to delegate to the real import and record its successful return. Wrap `sys.setprofile` only to delegate to the original and check that the fixture's `observe_call` is installed after graph preparation in decision mode; retain actual admission/provider assertions. Do not delete `sys.modules` or replace graph classes. The order assertion must fail on the current fixture even if other tests have already loaded the dependency.

```python
@pytest.mark.parametrize('mode', ['decision_a2', 'legacy'])
def test_fixture_dependency_readiness_precedes_observer(actual_app, monkeypatch, mode):
    marks = []
    original_import, original_profile = importlib.import_module, sys.setprofile

    def observed_import(name, *args, **kwargs):
        module = original_import(name, *args, **kwargs)
        if name == 'langgraph.graph':
            marks.append('graph_ready')
        return module

    def observed_profile(callback):
        if getattr(callback, '__name__', '') == 'observe_call':
            marks.append('observer')
        return original_profile(callback)

    monkeypatch.setattr(importlib, 'import_module', observed_import)
    monkeypatch.setattr(sys, 'setprofile', observed_profile)

    async def run():
        async with actual_app(mode=mode):
            expected = ['graph_ready', 'observer'] if mode == 'decision_a2' else ['observer']
            assert marks == expected
    asyncio.run(run())
```

- [ ] Add actual-route first-request controls for before/after ready and minimal/full legacy-compatible payload, using the existing `ActualSocket` and `result_of`. Assert one accepted/completed result, one provider call, one observed admission and no scientific tool execution. Keep the original three-second receive and five-second close limits.
- [ ] Run the new order regression to observe RED, preserving its actual output. The diagnosed cold original probe already supplies separate behavioral RED evidence; it is not replaced by the deterministic ordering test.
- [ ] At the start of the fixture's existing async `build()` (before new application/client resources and before its observer), add only this preparation:

```python
if mode == 'decision_a2':
    importlib.import_module('langgraph.graph')
```

- [ ] Re-run the order/first-request controls, original exact scratch through the corrected conftest-loading runner, and the four observer-on handshake/payload matrix nodes separately. Confirm admission/provider counts and the retained observer. A post-fix matrix with a prepared fixture is not a fresh production-cold measurement; preserve pre-fix cold records separately.
- [ ] Run the whole `tests/agent/test_web_decision_runtime.py` and `tests/agent/test_web_decision_runtime_lifecycle.py` modules through the approved isolated runner with normal conftest and original assertions; include actual nonce/CAS positive and rejected controls. Any failure remains visible and receives diagnosis, not timeout relaxation or a selective-pass replacement.
- [ ] Independently SPEC then QUALITY review the unchanged final test blob. Compile the changed test in memory, diff-check and explicitly stage only the approved test plus evidence; parent owns any local checkpoint. No full suite or publication in this worker.

Direct targets above use `python -B -m pytest <absolute-targets> -q -p no:cacheprovider --tb=short -rs` inside the approved isolated child, not an unisolated shell. The original scratch node is `scratch/quality_p7a2_task8_frames.py::test_pre_ready_legacy_payload_really_completes_on_a2`; the four matrix parameters are `pre-full-on`, `post-full-on`, `pre-minimal-on`, `post-minimal-on` in `scratch/causal_p7a2_task8_matrix.py::test_matrix`. Scratch execution must explicitly load the existing conftest as above. Parent authorizes this small written plan under the delegated-choice instruction; implementation remains a separate TDD action after this documentation checkpoint. Overall A2 full verification/CI, broader P7 and package-8 real acceptance remain open.

## 19. Isolated failed-join test: distinguish bootstrap from ownership

### Evidence and limits

The section-18 fixture increment is frozen at blob `dc7adcf075694f65cbb72aed0b56d3504339502f` (+70 lines, not yet committed). Readiness-order RED was 1F/1P/20.50s; six new controls passed in 31.15s, unchanged original scratch passed in 20.34s, and four independent observer-on matrix controls each passed (20.75/20.43/20.91/20.27s). These do not replace the two-module regression: **174P/1F/646.18s**, then **174P/1F/575.26s**. Only the second failure was safely identified as `test_permanent_failed_join_retains_route_owner_in_isolated_child`, `snapshots.get(timeout=20)` raising `queue.Empty`; the first failure identity was not retained.

Further exact-target diagnostics: 1F/29.70s; delegated phase probe: 1F/29.34s. The phase probe received zero ownership snapshots and was awaiting `retained`. Relative to child bootstrap, lifecycle import completed at 6.938s, helper import at 11.563s, fixture factory at 16.984s, graph preparation at 19.000s; no build-ready/request/model/adapter/retain stage was observed before timeout. The original checkpoint deadline includes process startup and imports. The uninstrumented exact failure has no phase measurements; do not copy the probe's times into it.

A separate 45-second bootstrap observation control passed in 10.95s, but bootstrap itself was faster (ready 6.437s, retain 7.344s). This is **not** a causal proof that splitting deadlines fixes slow runs, and 45 seconds was diagnostic only. Startup variability's underlying cause remains unknown. Sessions 18248/6277/29067 exited; owned diagnostic children were force-terminated/reaped by the original finally and readers reached zero, not graceful settlement. Ignored probes `p7a2_child_phase_probe.py` and `p7a2_child_bootstrap_control.py` are retained unchanged.

### Selected design (test contract change, not production optimization)

Under the user's delegated-choice authority, select a bounded startup handshake rather than a larger undifferentiated ownership timeout or reverting dependency readiness. This explicitly adds a **60-second bootstrap budget**; it is not a claim that the original total wall-clock budget is unchanged. The three ownership checkpoints each retain their existing **20-second** budget, and the actual socket receive/close and retain waits remain 3/5/5 seconds. Startup deadline failure remains a failure and is not retried or skipped. Production cold-start latency and the three older UNKNOWN cases remain open.

Scope: only `tests/agent/test_web_decision_runtime_lifecycle.py`, in addition to the frozen section-18 test change; evidence appended here. No production source, timeout/configuration, science assertion, dependency or real asset change.

Child emits `P7A2_CHILD` JSON with only `stage='bootstrap-ready'` and its PID after the real fixture, cookie/socket opening and `socket.ready()` complete, **before** sending the scientific test request. It then requires the parent command `begin`. Parent verifies the bootstrap stage, owned PID and live Popen handle, then writes/flushed `begin`; only then does the unchanged ownership sequence run. The existing reader/prefix, stdin protocol, original three ownership assertions and forced-finally cleanup are reused. EOF, unexpected stage, foreign PID or exited child must fail, including during bootstrap. Bootstrap is not a scientific-success snapshot.

One small test-local helper centralizes the existing stage/PID/liveness checks and distinct budgets, not the scientific assertions:

```python
def _wait_failed_join_checkpoint(snapshots, child, owned_pid, stage):
    facts = snapshots.get(timeout=60 if stage == 'bootstrap-ready' else 20)
    assert facts['stage'] == stage, 'isolated child did not reach controlled checkpoint'
    assert facts['pid'] == owned_pid and child.poll() is None
    return facts
```

No production lifecycle abstraction or test framework is introduced. In the parent `try`, call this helper for bootstrap, send `begin`, then call it instead of the existing get/stage/PID statements in the unchanged three-stage loop. Inside the child immediately after `await socket.ready()`:

```python
print('P7A2_CHILD ' + json.dumps({'stage': 'bootstrap-ready', 'pid': os.getpid()}), flush=True)
assert (await asyncio.to_thread(sys.stdin.readline)).strip() == 'begin'
```

### TDD and verification

- [ ] Add deterministic policy tests before the helper exists, then observe RED. A scripted queue records `get(timeout=...)` and returns stage/PID facts; a small child double exposes only `poll`. Assert exact budgets `[60, 20, 20, 20]`, unchanged facts, rejection of wrong stage/EOF/foreign PID/exited child, and propagation of `queue.Empty` in bootstrap and ownership. These are timeout-policy tests, not real-process performance evidence; never label doubles as ownership proof.
- [ ] Implement only the helper and bootstrap/begin additions shown above. Keep every original ownership/science assertion and finally cleanup statement. Do not edit the frozen readiness test.
- [ ] Run those policy nodes GREEN in the approved isolated runner with normal conftest. Then run the unchanged real isolated-child test node to validate actual bootstrap, three ownership checkpoints and forced cleanup. A passing result is current integration evidence only, not erasure of recorded failures.
- [ ] Run both whole runtime/lifecycle modules once using the same isolation and safe failure-node reporter; record every failure. Do not retry until green, skip nodes, increase deadlines further or fake thread settlement.
- [ ] Independent SPEC then QUALITY review both exact test blobs; allow independent focused execution sequentially. Memory-compile, `git diff --check`, preserve original probes/hashes, record exact counts and cleanup. Parent owns explicit staging/local checkpoint only after review. Full A2 offline/CI/PR gates remain separate.

Commands inside the approved OS-whitelisted/temp-cwd/config/DB/cache child with tracked JSONL copies and normal conftest: `python -B -m pytest <absolute-lifecycle-path> -k failed_join_checkpoint -q -p no:cacheprovider --tb=short -rs` for policy tests; exact `.../test_web_decision_runtime_lifecycle.py::test_permanent_failed_join_retains_route_owner_in_isolated_child` for real ownership; both absolute module paths for the full pair. No external model, credential, user database, network call or deployment is authorized in this sub-batch. This written follow-up is locally checkpointed before implementation.

## 20. Fixture/child-boundary correction: executed review checkpoint

Section 19 design was checkpointed as `d0bf6f923bfdf8150a7ee94534e25d2ef89c33a1` and independently source-reviewed before implementation. Sections 18/19's bounded test implementation and focused verification are now complete; their earlier checklist text records the planned sequence, not an outstanding code release. Overall A2 remains pending.

Only these test blobs are approved; no production files change in this checkpoint:

| File | Reviewed Git blob |
|---|---|
| `tests/agent/test_web_decision_runtime.py` | `dc7adcf075694f65cbb72aed0b56d3504339502f` |
| `tests/agent/test_web_decision_runtime_lifecycle.py` | `0e01612bceaabc540cce141bc2974942b46e0bc6` |

The first file adds 70 lines as recorded in section 19. The lifecycle file adds 64/removes 3: small checkpoint helper, 11 policy cases, child bootstrap/begin handshake, and reuse of the helper in the original three-stage loop. Original ownership/scientific assertions, subsequent shutdown/cancel commands and finally cleanup are preserved. The startup 60-second budget is new; it must not be described as preserving the original total wall-clock budget.

| Actual execution | Result |
|---|---|
| Policy RED, helper absent | 11 failed / 13.53s, expected missing-helper NameError |
| Policy GREEN | 11 passed / 8.42s |
| Actual isolated failed-join child | 1 passed / 31.42s |
| Whole runtime + lifecycle modules, one run | **186 passed / 7 warnings / 0 skipped / 648.58s**, exit 0 |
| Independent SPEC focused execution | **18 passed / 7 warnings / 0 skipped / 59.19s**, exit 0 |
| Independent QUALITY focused execution | **18 passed / 7 warnings / 0 skipped / 60.92s**, exit 0 |

Each reviewer independently ran six readiness controls, 11 checkpoint-policy cases and the actual child once, not the full pair again. SPEC and QUALITY approve only this two-test-file correction on identical before/after hashes. Existing warnings are SWIG/FastAPI deprecations, not hidden failures. All implementation/review sessions exited; review sessions were 20115 and 12185. Actual child PID/liveness, forced terminate/wait, stdout-reader join and original cleanup assertions passed. This is **forced isolated-child cleanup, not graceful production shutdown**.

Invocation is the existing ignored `scratch/p7a2_fixture_failure_runner.py`, which delegates the approved OS-whitelisted pre-import/temp-cwd/config/DB/cache runner and adds only safe per-failure node/type/stack reporting. Formal tests load normal conftest; three tracked JSONL copies have matching SHA. No real provider, key, user data or network is used. Full-pair command:

```powershell
& C:/Users/xkx52/.conda/envs/MedChat/python.exe -B scratch/p7a2_fixture_failure_runner.py tests/agent/test_web_decision_runtime.py tests/agent/test_web_decision_runtime_lifecycle.py
```

Review command selects the exact nodes `test_fixture_dependency_readiness_precedes_observer`, `test_actual_first_chat_completes_with_observer`, `test_failed_join_checkpoint_uses_distinct_budgets_and_returns_facts`, `test_failed_join_checkpoint_rejects_invalid_child_state`, `test_failed_join_checkpoint_propagates_queue_timeout` and `test_permanent_failed_join_retains_route_owner_in_isolated_child` through that same runner. Memory compilation and `git diff --check` pass. Existing ignored probes are unchanged and not staged.

The two historical 174P/1F groups are not erased. The first failure identity, three older warmup UNKNOWN cases, startup-variability root cause and production cold-start risk remain explicitly unresolved. The passing full pair validates this current test boundary; it is neither a performance repair nor full Agent/repository/CI evidence. Parent may explicitly stage the two reviewed tests plus this evidence for a local checkpoint. Full A2 review/offline/CI/unique PR, ordinary-chat/B/C integration and P8 live acceptance still require subsequent work; no push, merge, default activation or deployment occurs here.

## 21. Full offline findings and bounded configuration-fixture correction

Checkpoint `ec8a60506c1db517a436cab2e9a3bb228ec50a67`, tree `7e4032f2ab6811a53749e026177127d89fa2b9c6`, is clean and independently SOURCE/SPEC and SOURCE/QUALITY approved for the complete 26-file A2 increment, conditional on runtime/CI gates. Production source remains identical to `2769e1e`. This is not merge approval.

### First complete offline gate results (failures retained)

| Group, each executed once | Passed | Failed | Skipped | Warnings | Seconds |
|---|---:|---:|---:|---:|---:|
| Agent | 8109 | 5 | 2 | 7 | 1729.74 |
| sandbox-api | 143 | 0 | 10 | 0 | 15.71 |
| sandbox-core | 1912 | 0 | 65 | 0 | 165.95 |
| task-runtime | 1547 | 2 | 25 | 0 | 197.63 |
| root, excluding activity | 2055 | 4 | 143 | 5 | 477.87 |
| root-activity | 1851 | 0 | 10 | 2 | 714.43 |

Root also reports 173 passed subtests. Collection confirms 15883 cases across six non-overlapping partitions, with 30 activity files separated as in CI. Sessions 93612, 62265, 37070, 66883, 83110 and 79985 exited. Skips retain platform/symlink, disabled real acceptance, missing Docker/promtool and existing conditional reasons. `pytest-timeout` is absent locally, so sandbox `--timeout=60` was not supplied; no outer process-kill limit was added. Activity duration exceeds the CI 600-second command budget: local results are not CI-equivalent evidence. Node/static and CI are still pending.

The ignored repository runner changes only pytest argument forwarding to support absolute targets/options, preserving approved pre-import OS-whitelist, temporary cwd/config/DB/cache, normal conftest, three tracked JSONL copies/SHA, network denial and safe failure reporting. It does not enable models or read user assets.

The five Agent failures are original credential-guard `[0..4-many_urls]` subprocess 10-second timeouts. An unchanged single-node control passed in 4.91s; a phase control passed in 5.00s (import 1.299s, valid envelope 0.159s, 15 suffix checks 3.016s, final rejection 0.170s). An original-order prefix diagnostic through the last affected node passed 2119 cases, skipped 1 and deselected 5996 in 180.31s, with 7 warnings. That prefix is not a full gate. Its detailed passing-case phase observations were captured by pytest and not returned; do not invent them. Original timeouts' causes remain UNKNOWN; no guard algorithm, credential coverage or 10-second budget changed.

Task-runtime failures are separate: managed child startup did not reach `child-started` within its original five seconds (cause pending); metrics HTTP was blocked by the ignored runner's blanket network denial despite the test owning its loopback server. Neither is fixed in this sub-batch. A narrowly owned-local-server harness exception is being source-designed separately; no general network permission is implied.

### Reproduced root configuration-test cause

Original `tests/test_user_llm_routes.py` once: **4 failed / 5 passed / 0 skipped / 7 warnings / 17.51s**, session 47778 exit 1. The passive type-only fourth-case probe once: **1 failed / 7 warnings / 8.72s**, session 56787 exit 1. All failures match the root gate identities:

- `test_save_restart_blank_clear_and_new_checkout`
- `test_changed_endpoint_never_borrows_key_for_save_or_test`
- `test_refresh_clear_delete_and_malformed_file_fail_closed`
- `test_connected_socket_revalidates_config_before_each_message`

The factory returns a bare `Mock()`, whose `tools.items()` is not iterable. Actual configuration publication snapshots real Agent tool/model bindings at `app.py:367`. The first three failures repeat this TypeError; the fourth passive trace confirms `connection_ready → refresh → app.py:367 TypeError → error`, while the unchanged test expects `status`. No full frame/config/secret was printed. This is a test-double contract mismatch, not justification for a production fallback or weakening error handling.

### Approved minimal design and TDD sequence

Under delegated recommended-choice authority, extend the A2 test allowlist **only** to `tests/test_user_llm_routes.py`; append resulting evidence here later. Preserve every original assertion, synthetic configuration, temporary path, error status, timeout and existing cleanup. Do not change application/handler/config persistence code, add defensive fallback, enable a provider or inspect real credentials.

Replace only the bare factory Mock with an explicit configuration-only stub local to the fixture:

```python
class ConfigurationAgent:
    def __init__(self, model):
        self.tools = {}
        self.llm = model

    def set_llm(self, model):
        self.llm = model

monkeypatch.setattr(MolecularChatApp, '_create_chat_agent',
                    lambda self: ConfigurationAgent(self.model))
```

No scientific tool or model response is implemented by this stub. The actual app, config persistence, model objects, publication/retired-model cleanup and WebSocket handler remain in use. Unexpected scientific execution is not silently accepted by a broad mock.

- [ ] Add `test_factory_agent_rebinds_model_on_config_save` before changing the fixture. Obtain the actual app/client; assert `agent.tools == {}` and `agent.llm is app.model`. Save a default configuration with synthetic model name `synthetic-rebound-model`; assert HTTP 200/success, model identity changed, and both `agent.llm` and `app.chat_handler.model` are the new `app.model`. No generation/API request.
- [ ] Run this new case RED with the old fixture, preserving the original four-case RED evidence above. Then implement exactly the local stub shown above.
- [ ] Run the whole user-config route module GREEN with all original assertions unchanged. Run `tests/test_model_request_lifecycle.py` and `tests/test_web_app_lifecycle.py` as related lifecycle regressions through the approved isolated repository runner. Record failures instead of retrying until green.
- [ ] Independent SPEC then QUALITY review the exact final test blob and execute the affected module serially. Memory-compile and diff-check; parent explicitly stages only this test and evidence after review, not old scratch probes. Remaining offline/CI/full confirmation gates and broader P7/P8 stay pending.

Commands use MedChat Python `-B`, ignored `scratch/p7a2_repository_offline_runner.py`, absolute selected test paths, and its unchanged pytest `-q -p no:cacheprovider --tb=short -rs` options inside the approved isolated child. This section is documentation-checkpointed before code changes. No push, PR, merge or deployment is performed by this sub-batch.
