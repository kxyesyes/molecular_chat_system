# P7-A2 Web Decision Runtime Integration Implementation Plan

> **For agentic workers:** G0 and the revised worker-ownership/history design text are parent-approved; G1 remains blocked. Only approval-status updates, explicit staging and a local commit of the two documents are authorized now; no implementation execution or push. Use `subagent-driven-development` (recommended) or `executing-plans` only after corrected A1 is approved and landed and parent separately releases A2. App/handler/loop integration writes must be serialized against that pinned dependency revision.

**Goal:** Integrate the existing model decision loop into the actual, gated normal `/ws` with trusted admission, single-lease lifecycle, bounded socket history/continuation, honest outcomes and existing reference ACK behavior.

**Architecture:** One new Web runtime coordinator composes A1 admission with existing `ChatHandler.process_decision_message`, `ModelDecisionLoop`, dynamic `WorkflowRunSession`, shared registry/store and app request gate. A small Agent runtime ownership helper tracks real futures/executors at the existing workflow and adapter submit sites; it is not a scheduler or new background framework. Preserve static HTTP workflow routes, no-owner call behavior and default legacy startup; no decision-to-legacy fallback. Ordinary history covers already-admitted greetings/concepts, not arbitrary chat.

**Tech Stack:** Existing Python 3.10/FastAPI/asyncio/Pydantic/SQLite/LangGraph/model adapters, Jinja2 and native JavaScript; pytest and direct Node scripts. No dependencies added or upgraded.

**Status:** **G0 and revised design text parent-approved; A1 unlanded and G1 blocked.** Parent authorizes approval-status updates, explicit staging and a local documentation-only commit of the revised pair based on planning HEAD `ff02afc`, branch `codex/web-decision-runtime-plan`; no merge/push/implementation/test/compile/network/env/services. This supersedes the preceding uncommitted re-review hold. Original design base: `bb11ded0bdb62d919ca64969730cfd2b85d77980`. Original A1 snapshot `aed3571e9e1a25f8c757dc937969ad04a424b98c` was **SPEC4 returned for fixes, not approved for integration**. The subsequent read-only risk review compared main `9a9eb8d` and A1 `7a239b5` plus dirty admission/spec/plan/tests; neither that working copy nor its historical test totals establish landed A1 approval. Parent reports Planner #71 merged at `5db56b0`; no base merge is performed here. Spec: `docs/superpowers/specs/2026-09-25-web-decision-runtime-integration-design.md`.

---

## 0. Authority and executable-plan gate

Parent approved the revised spec/plan text. Only approval-status edits, explicit staging and a **local documentation-only commit** of this pair are authorized now. Everything below is a future RED/GREEN sequence, not a test result or permission to execute. Parent owns packages 1–8 and publication; do not close P7 or modify its ledger from A2. This freeze is not A1 quality approval.

- [x] Parent approved G0: constructor `normal_chat_mode='legacy'` with opt-in `decision_a2`; `decision_wire_mode='native'` with explicit `json` alternative; no automatic fallback or default activation; socket-only 15-minute waiting; chat-only 20-pair/16-KiB history; one lease with cancel/drain; whole-unsupported rejection; actual `/ws` and ACK tests required. This does not authorize implementation.
- [x] Parent approved the minimal revision direction: per-turn ownership at both real submit sites, reserve-before-submit/rollback, cross-thread propagation, full descendant drain plus executor join, unchanged no-owner calls, and exact history transmission using `Explain logP` then `解释分子生成的概念`. This remains design approval, not implementation release.
- [x] Parent reviewed and approved the revised spec sections 6.1–6.2, plan Tasks 5–6 and future allowlist, including action-root versus turn separation to avoid self-deadlock. Approval-status updates and an explicitly staged local two-document commit are authorized. Preserve the explicit difference with C: A2 does not promise finite-time UI termination under an uncooperative worker; C requires finite UI terminal with retained cleanup owner. Later C integration must reconcile it separately, not count this freeze as compatibility approval or scope expansion.
- [ ] Close A1 SPEC4 first using the four exact cases in Task 3: newline explanation+execution with tools false; molecular weight plus melting point; explicit `property_calculator` prohibition; **one** two-compound-count request with one supplied subject. Parent corrected its earlier summary: there is not a second reported count case. Record corrected commit SHA, exact regression names, independent SPEC4 approval and actual landed SHA; obtain separate parent release before A2 implementation. Observed `aed3571` is forbidden as a trusted dependency; historical GREEN totals do not override review failure.
- [ ] Pin the **landed** A1 revision, not just its local HEAD. Read its updated spec/plan and tests. Confirm `prepare_decision_request`/`PreparedDecision`, `context_value`, `config_generation`, exact tool names, reference revalidation and waiting revision. Include parent-reported Planner #71 `5db56b0` in the later integration-base comparison; do not merge it into this design-only task.
- [ ] Compare current app/presentation/reference/target contracts against the spec's source inventory. Parent approves the future file allowlist below and any unavoidable dependency conflicts.
- [ ] Confirm the resume refinement rule is supportable by landed A1: original kind/tools/metrics/targets/subjects stay authoritative; missing subject may receive one complete valid explicit molecule, not a new batch or request. If not, stop and revise with A1 owner; do not weaken requirements or write a parallel classifier.
- [ ] Agree the offline runner's pre-import isolation with parent. `src/web/app.py` constructs a global application at import; autouse fixtures alone are too late. Reuse the repository's approved isolated-run procedure after verifying it still applies. Do not inspect host env/key stores; inject only synthetic in-memory config and temporary test roots.

Because A1 is unlanded and may change, code sketches below define proposed interfaces/control flow, **not copy/paste production patches**. At G1 rebind signatures and concrete test fixture code to landed contracts before running the first RED. No placeholder behavior or invented implemented helper is implied.

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

No changes by default to A1 admission/bounds/inputs, session middleware, model adapters, key/config stores, reference service/controller/normalizer, `ToolRegistry`, domain tools/adapters, static workflow HTTP, requirements, deployment, launchers, templates or CSS. The two shared submit hooks and `decision_execution.py` above are the narrowly approved future exceptions, not permission to refactor other execution behavior. A failing test outside the allowlist is a parent revision request, not automatic scope expansion. Implementation occurs later on a separate authorized branch/worktree, never inside A1's active worktree.

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

- [ ] Write focused RED for both nested orders: adapter deadline returns before blocked invocation exits; workflow deadline returns before its adapter descendant exits. Capture separate signals `worker_entered`, `timeout_returned`, `worker_exited`, `executor_joined`, `owner_settled`; a timeout result must not set the latter three.
- [ ] Write RED for reserve-before-submit and rollback on executor creation/submission failure; a fast worker completing or registering a child before future attachment must not cause an empty-ledger pass. Verify existing invocation-slot release and no execution after failed submission.
- [ ] Write RED for explicit owner/action-root lineage propagation across the outer Session thread and both nested submit paths; delayed child registration after workflow timeout is still drained. Two turns sharing an adapter drain only their own descendants. Owner state never enters input payload/context/results/continuations.
- [ ] Write RED blocking executor join after callable/future completion: no `owner_settled` or lease release yet. Verify join runs off the event loop and outside the executor's own worker/done callback; repeated cancellation cannot abandon its join helper. No-owner calls retain original timeout latency, result/error and retry/slot behavior.
- [ ] Run this focused RED only after G1 release. Implement the small ownership ledger with action-root scopes. Reserve before submission, attach real future/executor or roll back, explicitly rebind/reset owner inside submitted callables. Keep the no-owner path unchanged; do not put per-request state on shared adapters or add a scheduler/store.
- [ ] Extend `settle_action` to retain its Session task and settle that action root's descendants, not await its still-running parent loop. Turn-wide settle seals further action roots after dispatch ends. Completion requires closed roots, zero in-progress reservations, all descendant futures settled and all executor joins completed; drain dynamically registered descendants, never only a snapshot. Consume cleanup failures without exposing raw worker diagnostics and retain unresolved ownership on incomplete cleanup.

- [ ] Write actual `/ws` RED: blocked model call → ping/pong → cancel matching server `turn_id` → one cancelled result/complete → new chat succeeds on same socket. Repeat after a resumed clarification, not just the initial turn.
- [ ] Write RED: stale/foreign cancel and second concurrent chat cannot cancel current work or add a second terminal. Cancellation before model dispatch releases queued admission safely.
- [ ] Write actual-route RED for both nested timeout orders, disconnect and repeated cancellation: before worker/join barriers release, the owner remains registered, reader lease stays held, writer publishes no replacement epoch, no model/tool closes and no complete is emitted. After release, assert settled journal, no replay, one terminal if writable and close-once. Late worker success cannot replace timeout/unknown/cancelled with scientific completion.
- [ ] Write RED for send timeout, event overflow, serialization failure, cancel-vs-finish race, cancellation during cleanup, and shutdown with active + waiting sockets. Settled writable paths get one complete; disconnected/unwritable sockets do not promise delivery. Block a worker throughout a bounded observation window to prove graceful shutdown remains pending/unresolved, then release it in test cleanup; do not leak a permanent test thread or claim a finite shutdown bound.
- [ ] Implement exactly one receiver, one active owner, one serialized deadline-aware sender. Keep control validation on receiver; async refresh/admission/lease/model work belongs to owner. No queued chats.
- [ ] Add optional bridge `cancel_event` watcher and server-only `worker_owner=None` forwarding. User cancellation stops dispatch and cancels the loop child once; transport cancellation also stops delivery. Both retain and drain watcher, provider child, Session tasks, real worker futures and executor-join helpers. Final result delivery follows settlement, not cancellation of an asyncio wrapper. Finishing latch decides completion-vs-late-cancel once; no result recreation after committed terminal.
- [ ] Track app-owned active owners so shutdown stops admission and drains before writer closure. Waiting state holds no lease/model/worker owner; clear it at shutdown. Never clear an unresolved owner or close a still-used resource to meet a deadline. If a thread never exits, graceful shutdown cannot complete. Pass focused GREEN, actual-route barriers and existing overflow/cleanup regressions; no claim that all legacy or tool-internal detached threads are covered.

Future command: `python -B -m pytest tests/agent/test_worker_ownership.py tests/agent/test_decision_loop.py tests/agent/test_web_decision_runtime_lifecycle.py tests/agent/test_decision_chat_transport.py -q -p no:cacheprovider`.

**C integration difference, not resolved here:** parent reports C's preparatory design requires a finite-time UI terminal while retaining a cleanup owner. A2 currently waits for owned settlement before its single terminal and does not promise finite-time termination under an uncooperative worker. This plan does not implement that terminal/cleanup split or claim C compatibility. Later C integration must separately review terminal ownership, late frames, retained leases/resources and shutdown; cancelling an asyncio task is never proof of drain.

## 6. Task: actual chat history and nonce-safe multi-turn resume

**Files:** new `decision_history.py`; loop/continuation/runtime; new history test and actual-route tests.

- [ ] Write RED history through actual `/ws` with first prompt `Explain logP`, second prompt `解释分子生成的概念`. Both are already listed in dirty A1's `tests/agent/test_web_decision_admission.py:63–68`; recheck the landed A1 at G1. Run real admission, loop and adapter with an in-memory provider transport. Let `A` be the first safe displayed response; assert the second adapter request contains the exact `user: Explain logP`, `assistant: A`, then `user: 解释分子生成的概念`, retaining existing decision/protocol system prefixes. No prefilled history, monkeypatched admission/loop or fixed runtime answer. Assert empty allowed/required tools and zero tool/retrieval calls for both turns. A different socket with the same cookie and a different user see neither retained pair; client-supplied history is rejected.
- [ ] Keep the acceptance claim narrow: exact pair transmission for admitted concepts/greetings, not arbitrary chat, label recall or real-model understanding. Do not add free-chat classification, bypass admission, or invent a waiting nonce for a rejected prompt. Any arbitrary-chat expansion requires separate design; it is not a condition to make this test GREEN.
- [ ] Write RED chat-only retention: scientific/partial/failed/cancelled/waiting outcomes are excluded; 20-pair/16-KiB bound evicts whole oldest pairs; sensitive/oversized pair omission is marked. Current input remains exact.
- [ ] Write RED prefix replay: prior ordinary assistant text starting with `{` is not counted as a decision; substituted history rejects before CAS. Empty memory preserves existing direct callers; incompatible old waiting revision rejects without execution.
- [ ] Write actual-route RED two clarification cycles: missing input → waiting → full clarified supported request → waiting → full clarified request, with original trace/requirements/history preserved and a fresh nonce after each wait. Then cancel during blocked resumed model work; next fresh turn succeeds. No waiting result is treated as completed scientific work.
- [ ] Write actual-route RED table: foreign cookie, same-user other socket, stale nonce, duplicate nonce, changed model epoch/tool schema/requirements, expired/abandoned handle, revoked reference and unsupported clarification. Count model/tool calls and compare durable waiting record before/after: invalid resume causes no CAS mutation or dispatch.
- [ ] Implement `decision_history.py` shared expected prefix from closed pairs. `run` seeds prefix; continuation validator compares it exactly, counts suffix actions and starts semantic replay at prefix length. Bump internal protocol revision after confirming landed A1; preserve public decision envelope/input_ref schema.
- [ ] Implement one socket waiting record with frozen initial context/obligations, 15-minute expiry and no loop/client reference. Resume validates A1-compatible complete clarified text and original obligations, but calls loop with **original** context/requirements plus `clarified_query`; current generation is used to reject stale state. Do not re-prepare original query from browser or replace constraints with fresh history.
- [ ] Implement `abandon` as local handle release, not durable cancellation. Disconnect/restart cannot reconstruct Web continuation from a client nonce alone. Run GREEN; do not relax core CAS or historical source checks to pass it.

Future command: `python -B -m pytest tests/agent/test_decision_history.py tests/agent/test_web_decision_runtime.py tests/agent/test_web_decision_runtime_lifecycle.py tests/agent/test_decision_continuation.py tests/agent/test_decision_continuation_store.py tests/agent/test_decision_protocol_recovery.py -q -p no:cacheprovider`.

## 7. Task: existing candidate projection and real reference HTTP boundary

**Files:** bridge, runtime-reference tests, transport tests. Service/controller/normalizer remain read-only.

- [ ] Write actual-application route RED: seed a clearly marked synthetic historical CandidateSet in a temporary real store using existing scientific-reference fixtures; obtain cookie and use existing protected restore/confirm HTTP. No fixture claims real generation. Then ordinary enabled `/ws` chat must retain confirmed selection; selected property follow-up must dispatch exact trusted canonical structure to real property tool.
- [ ] Write route RED source matrix: foreign owner, wrong revision/order/compound key, unconfirmed view, source revocation, invalid explicit replacement; no unauthorized calculation or silent use of a different molecule. Partial source retains warnings and source status; accepted specific-candidate analysis does not retroactively complete source generation.
- [ ] Add complementary bridge transport-order RED using a stored CandidateSet result fixture: events → result → candidate frames → complete, unchanged strict candidate schema. Projection/send failures have distinct reference-unavailable vs transport-failure behavior; never execute generator/tool again.
- [ ] Reuse `_send_reference_candidate_events`/`ScientificReferenceService.project` against settled legacy result, not redacted display envelope. Same owner/store and serialized sender, before complete. No new candidate-producing tool is admitted.
- [ ] Run GREEN actual-route ownership tests and existing reference contract tests. A bridge CandidateSet fixture alone does not satisfy the actual-route requirement or B generation acceptance.

Future command: `python -B -m pytest tests/agent/test_web_decision_runtime_references.py tests/agent/test_scientific_reference_web.py tests/agent/test_scientific_reference_execution.py tests/agent/test_scientific_reference_resilience.py tests/agent/test_decision_chat_transport.py -q -p no:cacheprovider`.

## 8. Task: existing home UI controls and persistent status

**Files:** `home/main.js`, new `tests/home_decision_runtime_test.js`; existing home scripts/tests read-only.

- [ ] Write Node RED with existing home script loading pattern: server-announced decision mode enables accessible Stop, Continue and Start new/abandon controls; default legacy mode does not change. First `request_accepted` sets turn ownership; Continue sends only the matching trace/nonce/full message; Stop sends only matching turn ID.
- [ ] Write RED all outcomes: complete ends stream but does not relabel waiting/partial/failed/rejected/cancelled as completed. Status stays attached to message instead of disappearing with tool-status timeout. Next turn and stale socket/turn frames cannot overwrite it. Disconnect clears pending control handle; no auto-resend.
- [ ] Write RED existing candidate mount workflow: candidate frames arrive before complete, exact mounted ordered keys produce confirm request, failed/partial mount produces no ACK, slow stale ACK cannot authorize new selection. Do not add `turn_id` to strict candidate JSON or treat a receive as mount.
- [ ] Implement controls through existing DOM patterns with text labels and `role='status'`, not a UI framework or redesign. Keep scientific numeric rendering and original/canonical candidate data unchanged. Explain RAG enabled-but-not-performed and explicitly blocked requests without inventing results. Remove raw outbound payload logging in touched send code; never log continuation/cookie/config/key values.
- [ ] Run Node GREEN, `node --check` on changed JS and existing home candidate/reference/task-panel tests. These are DOM contract tests, not live browser proof; normal-browser acceptance stays with parent/package 8.

Future commands:

```powershell
node --check src/web/static/js/home/main.js
node tests/home_decision_runtime_test.js
node tests/home_scientific_references_test.js
node tests/home_agent_task_panel_test.js
node tests/home_structured_molecule_render_test.js
node tests/home_workflow_completion_behavior_test.js
node tests/frontend_safe_render_test.js
```

These existing filenames were verified at the design baseline. Recheck them at G1 if presentation work has moved tests; do not invent a missing npm test task or install a framework.

## 9. Review evidence and stop criteria

- [ ] Verify future changed paths against the exact allowlist; only the named shared submit hooks/settlement helper are added execution scope. Other A1-owned files or HTTP/config/tool changes require explicit scope revision. Preserve user's unrelated changes. No direct `main` changes. The present local freeze commit must contain only the two approved documents.
- [ ] Record each focused RED/GREEN result honestly. Test doubles use in-memory HTTP transport and temporary stores; they prove protocol/lifecycle, not live external inference, target availability or trained activity predictions. Required skipped/failed cases block completion.
- [ ] Parent schedules full Agent/repository tests and source compile checks in the approved isolated implementation environment, plus all applicable existing Node regressions. Do not run live health/real acceptance or providers as a side effect of this plan.
- [ ] SPEC review checks default-off wire decision, no fallback, A1 authority, admitted-only history/nonce/socket isolation, cancellation/model-switch races, complete-vs-success distinction, scientific formatting and candidate ACK order. QUALITY review checks reserve/rollback, cross-thread propagation, action-root versus turn drain, nested futures/executor joins, no-owner compatibility, bounded queues, exact scope and absence of sensitive logging. Retain the pending C terminal/cleanup integration difference explicitly.
- [ ] Record exact future implementation branch/revision, dependency SHAs and any later parent-authorized implementation commit/PR separately. No publication command is included here. Present handoff is only the parent-approved local two-document freeze commit: no push, PR or implementation test claims. Report its hash and worktree status outside the committed documents.
- [ ] Parent decides if any package-8 real external model/science/browser acceptance is authorized next. Never count lab, fake generation, static HTTP workflow or local RDKit-only evidence as that acceptance.

## 10. Coverage-to-gate map

| Requirement | Planned evidence | Gate if unavailable |
|---|---|---|
| Actual `/ws` and legitimate server context | Tasks 2–3 actual app + middleware | A2 blocked; no lab substitute |
| Real adapter decision/observation-driven next action | Task 3 in-memory adapter transport + real loop/Session/tool | Offline contract only; live inference remains package 8 |
| One lease/current model/config epoch/cleanup | Tasks 4–5, both submit paths, reservation/descendant/join barriers and no-owner controls | A2 blocked on false drain/close/leak/deadlock; uncooperative worker has no finite shutdown guarantee |
| Same-user history/nonce/socket/multi-turn cancel | Task 6 admitted prompt pair, actual routes and suffix replay | Precise transmission only; not arbitrary chat or real-model understanding; isolated loop tests insufficient |
| C finite UI terminal with retained cleanup owner | Explicit difference in Task 5/spec 6.2 | Later C integration review required; no compatibility claim or C implementation here |
| RAG true ordinary chat vs explicit retrieve | Task 3 matrix | A1 revision gate for misclassification; actual RAG B remains blocked |
| Partial and scientific numeric truth | Tasks 3, 7, 8 | Never relabel as scientific completed |
| Candidates/ACK/restore/selected input | Tasks 7–8 route HTTP/WS + DOM boundary | No dynamic candidate-production or live mount claim |
| No default activation/static HTTP change | Tasks 2, 4, 8 | Parent must review any mode change |
| Broader generation/rank/ADMET/reverse/RAG bindings | Explicitly excluded | **P7-B blocked, not completed** |

Final handoff is the parent-approved revised spec/plan pair in a **local documentation-only commit** based on planning HEAD `ff02afc`. Parent approval covers the design text, not implementation execution or automatic compatibility with C; the C integration difference remains unchanged. Brainstorming's alternatives and writing-plans' future TDD decomposition inform the documents. A1 remains unlanded and G1 blocked pending corrected, approved and landed A1 plus separate parent release; implementation, merge, push and execution handoff remain withheld. This is not A1 quality approval or P7 completion.
