# P7-A2 Web Decision Runtime Integration Implementation Plan

> **For agentic workers:** G0 is approved; G1 remains blocked. Use `subagent-driven-development` (recommended) or `executing-plans` only after corrected A1 is approved and landed and parent separately releases A2. This is a documentation-only freeze; do not execute implementation steps in this task. Parallel read-only/test-design review is possible; app/handler/loop integration writes must be serialized against the pinned dependency revision.

**Goal:** Integrate the existing model decision loop into the actual, gated normal `/ws` with trusted admission, single-lease lifecycle, bounded socket history/continuation, honest outcomes and existing reference ACK behavior.

**Architecture:** One new Web runtime coordinator composes A1 admission with existing `ChatHandler.process_decision_message`, `ModelDecisionLoop`, dynamic `WorkflowRunSession`, shared registry/store and app request gate. Preserve static HTTP workflow routes and default legacy startup; no decision-to-legacy fallback.

**Tech Stack:** Existing Python 3.10/FastAPI/asyncio/Pydantic/SQLite/LangGraph/model adapters, Jinja2 and native JavaScript; pytest and direct Node scripts. No dependencies added or upgraded.

**Status:** **G0 parent-approved; G1 blocked.** Parent authorizes changes and a local commit of only this plan and its companion spec; no merge/push/implementation/test/compile/network/env/services. Base `bb11ded0bdb62d919ca64969730cfd2b85d77980`, branch `codex/web-decision-runtime-plan`. Read-only A1 snapshot `aed3571e9e1a25f8c757dc937969ad04a424b98c` is **SPEC4 returned for fixes, not completed/trusted/approved for integration**. Parent reports Planner #71 merged at `5db56b0`; this documentation freeze retains `bb11ded` as its base and performs no merge. Spec: `docs/superpowers/specs/2026-09-25-web-decision-runtime-integration-design.md`.

---

## 0. Authority and executable-plan gate

Only the spec and this plan are written and authorized for a local documentation commit now. Everything below is a future RED/GREEN sequence, not a test result. No implementation commit, merge or push is authorized. Parent owns packages 1–8 and publication; do not close P7 or modify its ledger from A2.

- [x] Parent approved G0: constructor `normal_chat_mode='legacy'` with opt-in `decision_a2`; `decision_wire_mode='native'` with explicit `json` alternative; no automatic fallback or default activation; socket-only 15-minute waiting; chat-only 20-pair/16-KiB history; one lease with cancel/drain; whole-unsupported rejection; actual `/ws` and ACK tests required. This does not authorize implementation.
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
- `tests/agent/test_web_decision_runtime.py`: reusable actual-application route fixture and admission/route matrix.
- `tests/agent/test_web_decision_runtime_lifecycle.py`: actual-route cancellation/configuration concurrency matrix.
- `tests/agent/test_web_decision_runtime_references.py`: actual reference HTTP and selected-input `/ws` coverage.
- `tests/agent/test_decision_history.py`: history and continuation prefix regressions.
- `tests/home_decision_runtime_test.js`: no-framework home DOM/socket contracts.

Modify only:

- `src/web/app.py`: mode/epoch/runtime assembly, shared registry helper if necessary, shutdown ordering.
- `src/web/chat_handler.py`: optional runtime early branch and additive bridge arguments.
- `src/web/decision_chat.py`: cancel event, serialized sends, projection and finalization.
- `src/agent/harness/decision_loop.py`: initial history prefix.
- `src/agent/harness/decision_continuation.py`: prefix-aware validation/replay and internal revision.
- `src/web/static/js/home/main.js`: server mode, turn controls, stable outcome UI, touched send-path payload log removal.
- `tests/agent/test_decision_chat_transport.py`: default-off assertion and transport regressions.
- `tests/test_model_request_lifecycle.py`, `tests/test_web_app_lifecycle.py`: existing lifecycle regression coverage.

No changes by default to A1 admission/bounds/inputs, session middleware, model adapters, key/config stores, reference service/controller/normalizer, domain tools, static workflow HTTP, requirements, deployment, launchers, templates or CSS. A failing test in these boundaries is a parent revision request, not automatic scope expansion. Implementation occurs later on a separate authorized branch/worktree, never inside A1's active worktree.

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
        await handler.process_decision_message(..., cancel_event=turn.cancel_event)
        on transport/owner cancellation: cancel-and-drain children before lease exit
```

Future command: `python -B -m pytest tests/agent/test_web_decision_runtime_lifecycle.py tests/test_model_request_lifecycle.py tests/test_web_app_lifecycle.py -q -p no:cacheprovider`.

## 5. Task: responsive receiver, cancel and bounded finalization

**Files:** `decision_runtime.py`, `decision_chat.py`, handler bridge; lifecycle/transport tests.

- [ ] Write actual `/ws` RED: blocked model call → ping/pong → cancel matching server `turn_id` → one cancelled result/complete → new chat succeeds on same socket. Repeat after a resumed clarification, not just the initial turn.
- [ ] Write RED: stale/foreign cancel and second concurrent chat cannot cancel current work or add a second terminal. Cancellation before model dispatch releases queued admission safely.
- [ ] Write RED: disconnect and repeated owner cancellation while synchronous tool or adapter cleanup is blocked retain lease; release worker barrier then assert settled journal, no replay and close-once. Unknown execution state stays unknown/failed/cancelled as supplied by core, never scientific completion.
- [ ] Write RED for send timeout, event overflow, serialization failure, cancel-vs-finish race, cancellation during cleanup, and shutdown with active + waiting sockets. Connected paths get one complete; disconnected/unwritable sockets do not promise delivery.
- [ ] Implement exactly one receiver, one active owner, one serialized deadline-aware sender. Keep control validation on receiver; async refresh/admission/lease/model work belongs to owner. No queued chats.
- [ ] Add optional bridge `cancel_event` watcher that cancels loop child once, drains it and preserves final cancelled result delivery. Always join watcher and child. Transport cancellation uses hard cancel-and-drain instead. Finishing latch decides completion-vs-late-cancel once; no result recreation after committed terminal.
- [ ] Track app-owned active owners so shutdown stops admission and drains before writer closure. Waiting state holds no lease/model; clear it at shutdown. Pass RED set and existing overflow/cleanup tests.

Future command: `python -B -m pytest tests/agent/test_web_decision_runtime_lifecycle.py tests/agent/test_decision_chat_transport.py -q -p no:cacheprovider`.

## 6. Task: actual chat history and nonce-safe multi-turn resume

**Files:** new `decision_history.py`; loop/continuation/runtime; new history test and actual-route tests.

- [ ] Write RED history through `/ws`: first ordinary turn supplies a harmless invented label, second asks what label was given; inspect actual adapter request messages for the server-retained pair. A different socket with same cookie and a different user see neither pair. Client-supplied history is rejected.
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

- [ ] Verify changed paths against the exact allowlist; A1-owned files or HTTP/config/tool changes require explicit scope revision. Preserve user's unrelated changes. No direct `main` changes.
- [ ] Record each focused RED/GREEN result honestly. Test doubles use in-memory HTTP transport and temporary stores; they prove protocol/lifecycle, not live external inference, target availability or trained activity predictions. Required skipped/failed cases block completion.
- [ ] Parent schedules full Agent/repository tests and source compile checks in the approved isolated implementation environment, plus all applicable existing Node regressions. Do not run live health/real acceptance or providers as a side effect of this plan.
- [ ] SPEC review checks default-off wire decision, no fallback, A1 authority, history/nonce/socket isolation, cancellation/model-switch races, complete-vs-success distinction, scientific formatting and candidate ACK order. QUALITY review checks cleanup/task references, bounded queues, exact file scope and absence of sensitive logging.
- [ ] Record exact future implementation branch/revision, dependency SHAs and any parent-authorized implementation commit/PR separately. No publication command is included here. This sidecar is authorized only for a local two-document freeze commit, with no PR and no implementation test claims.
- [ ] Parent decides if any package-8 real external model/science/browser acceptance is authorized next. Never count lab, fake generation, static HTTP workflow or local RDKit-only evidence as that acceptance.

## 10. Coverage-to-gate map

| Requirement | Planned evidence | Gate if unavailable |
|---|---|---|
| Actual `/ws` and legitimate server context | Tasks 2–3 actual app + middleware | A2 blocked; no lab substitute |
| Real adapter decision/observation-driven next action | Task 3 in-memory adapter transport + real loop/Session/tool | Offline contract only; live inference remains package 8 |
| One lease/current model/config epoch/cleanup | Tasks 4–5 route barriers and existing consumers | A2 blocked on close/leak/deadlock/uncertain settlement |
| Same-user history/nonce/socket/multi-turn cancel | Task 6 actual routes and suffix replay | A2 blocked, not fulfilled by isolated loop tests |
| RAG true ordinary chat vs explicit retrieve | Task 3 matrix | A1 revision gate for misclassification; actual RAG B remains blocked |
| Partial and scientific numeric truth | Tasks 3, 7, 8 | Never relabel as scientific completed |
| Candidates/ACK/restore/selected input | Tasks 7–8 route HTTP/WS + DOM boundary | No dynamic candidate-production or live mount claim |
| No default activation/static HTTP change | Tasks 2, 4, 8 | Parent must review any mode change |
| Broader generation/rank/ADMET/reverse/RAG bindings | Explicitly excluded | **P7-B blocked, not completed** |

Final handoff of this sidecar is the G0-approved pair of documents and their authorized local freeze commit. Brainstorming's alternatives and writing-plans' TDD decomposition inform them. G1 remains blocked pending corrected, approved and landed A1 plus separate parent release; implementation, merge, push and execution handoff remain withheld. P7 is not completed.
