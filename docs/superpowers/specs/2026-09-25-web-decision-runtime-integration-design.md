# P7-A2 normal Web decision runtime integration — review design

Date: 2026-09-25. Status: **G0 approved by parent; G1 blocked pending corrected, approved and landed A1 plus separate parent release. Documentation-only freeze authorized; not implementation authorization, not P7 completion.**

Planning branch: `codex/web-decision-runtime-plan`; inspected base: `bb11ded0bdb62d919ca64969730cfd2b85d77980`. The worktree was initially clean. Read-only A1 snapshot: branch `codex/web-model-decision-entry`, HEAD `aed3571e9e1a25f8c757dc937969ad04a424b98c`, clean when inspected. **Parent subsequently reported A1 SPEC4 returned for intent-gap fixes. This snapshot is not completed, trusted or an acceptable integration dependency.** Its source is an interface observation only; previous A1 test totals/freeze notes do not override that failed review.

Parent update: Planner #71 is merged into main at `5db56b0`. This is parent-supplied integration context, not a revision merged or executed here. This documentation-only freeze retains `bb11ded` as its base; no merge/rebase/implementation race is needed. Later A2 must pin reviewed A1 fixes and the then-current approved integration base, including Planner #71 compatibility.

Authority: repository `AGENTS.md`, `docs/PROJECT_STANDARDS.md`, and this task's narrower instructions. Only this spec and `docs/superpowers/plans/2026-09-25-web-decision-runtime-integration.md` are written and explicitly authorized for a local documentation commit. No production/test changes, imports, tests, compilation, network, environment/credential inspection, services, merge or push are authorized in this freeze. Packages 1–8 remain the parent's goal; deployment is excluded. No parent goal/ledger status is changed here.

## 1. Recommendation and bounded alternatives

| Option | Cost and behavior | Decision |
|---|---|---|
| Reuse normal `/ws`, handler bridge, decision loop, Session and app model gate | One small runtime coordinator; targeted history/transport changes; existing home UI gains cancellation/continuation/status handling | **Recommend.** Proves the intended normal entry without changing scientific tool scope |
| Put runtime state and task ownership directly in `ChatHandler` | Fewer files, but grows the already large legacy handler and couples its shielded lease to cancellation | Bounded alternative, not preferred |
| Persist Web conversation/resume reconstruction across sockets/restarts now | Requires a new retention/recovery contract and wider storage work | Defer. A2 resumes only the still-owned socket's waiting request; no reconstructed browser authority |

No new `/api/chat`, test-only chat endpoint, framework, dependency upgrade, planner replacement, generic tool DSL or wholesale handler refactor. Static `/api/agent/workflows/plan` and `/run` retain their current meanings. Explicit legacy mode is compatibility, **never fallback from an admitted decision-mode request**.

## 2. Source-grounded starting points

Paths are repository-relative and symbols are the anchors; line numbers will drift when A1 lands.

| Source | Verified behavior and A2 consequence |
|---|---|
| `src/web/app.py::_setup_routes`, `ChatHandler.handle_websocket` | Actual `/ws` refreshes config and delegates to handler. Handler accepts and reads one frame, then awaits all of `_process_message`; no live cancel/receive concurrency |
| `chat_handler.py::_process_message` | Uses the legacy Supervisor or generation/RAG path under `@model_request`. Socket-local history exists, but standalone fallback uses `self.conversation_history`. Decision mode must branch before this method |
| `chat_handler.py::process_decision_message`, `decision_chat.py::process_decision_message` | Existing server-only bridge calls the loop and emits bounded events, result and complete. It has transport cleanup, but no normal admission, candidate projection or user-cancel channel |
| `model_lifecycle.py::ModelRequestGate`, `model_request`, `finish_on_cancel` | Writer drains readers; decorator shields its child. It is wrong to wrap the new runtime in this decorator or acquire a nested reader. Reuse gate directly and actively cancel/settle child first |
| `app.py::_apply_llm_config/_apply_and_close_llm/_refresh_llm_config_from_env/_shutdown` | App owns main client replacement/close. Generator is separately created from Ollama config. No nonsecret configuration-generation epoch exists at this base |
| `app.py::_create_supervisor_agent` | Lazily builds app-owned registry from existing tool instances and audits registration. Reuse its assembly/registry authority, not a second `get_all_tools` pool or a Supervisor execution |
| `agent_session.py::AgentSessionMiddleware`, `agent_session_config.py` | Server anonymous cookie session, origin/transport validation and `scope['agent_session_id']`. This is bearer-session isolation, **not an authenticated account identity**. References are under the protected workflow prefix |
| `decision_loop.py::ModelDecisionLoop.run` | Owns dynamic `WorkflowRunSession`, model decisions, obligations, evidence and action settlement. Fresh message history is just system + current query; `context.memory` is not consumed |
| `decision_continuation.py::claim_continuation/validate_history` | Owner/fingerprint/history checks precede single-use CAS. Replay validation assumes a two-message prefix. Public metadata has a nonce but not enough trusted Web request reconstruction |
| `scientific_references.py::project/resolve/confirm/restore` | Owner/source/manifest/ACK validation already exists; projection consumes legacy result dictionaries, and supports successful observations in partial sources without promoting source status |
| `home/main.js`, `home/scientific_references.js`, `home/molecule_candidates.js` | Candidate normalization/queue/mount/exact-key ACK and restoration already exist. `complete` ends display; `agent_result` currently gives transient text, not durable outcome/continuation state |
| `tests/agent/test_decision_chat_transport.py` | Contains an explicit no-normal-dispatch source assertion and a real FastAPI **isolated** endpoint test. Neither is an A2 normal-route acceptance test |

A1's `src/web/decision_request.py` is present only in the inspected A1 branch: `prepare_decision_request(payload, *, session_id, trace_id, references=None, config_generation=None)` returns `PreparedDecision` with fresh `.context`, frozen allowed/required tool sets, immutable requirements and generation. Its intended ceiling is the required subset of `property_calculator`, `drug_likeness_assessment`, `activity_predictor`, `target_database_search`; **SPEC4 means its current intent classification cannot be relied on to enforce whole-request obligations.** It does not assemble the app or receive WebSocket frames. A1 also adds trusted context projection/direct reference binding and currently advances the internal waiting protocol from revision 4 to 5. All these interfaces need reinspection at the corrected reviewed revision.

### Mandatory SPEC4 repair dependency

| Exact parent-confirmed SPEC4 case | Required A2 integration regression after reviewed repair |
|---|---|
| `解释 logP\n对接这个分子`, with `enable_tools=false` | Here `\n` denotes an actual newline. Full request remains execution when any clause executes; explanation prefix/newline cannot turn disabled/unsupported docking into unrestricted chat |
| `计算 CCO 的分子量和熔点` | Supported molecular weight cannot erase unsupported melting point in the same request; clarify/reject the whole request, never finish the known subset |
| `计算性质（禁用 property_calculator）；SMILES: CCO` | Explicit tool prohibition cannot be dropped or re-enabled; conflicting execution clarifies/rejects |
| `分析两种化合物的性质；SMILES: CCO` | This is the **one reported count case**. Preserve requested two-compound intent despite one supplied subject; clarify/reject, never substitute UI `mol_count` defaults or complete the one-subject subset |

Parent corrected the earlier count summary: SPEC4 contains exactly the four cases above, including **one count case**, not two. Additional quantifier controls may be planned as new regression coverage but are not reported SPEC4 findings. G1 must match these exact cases to A1's repair/review artifacts and record their test names, corrected SHA, SPEC4 approval and landed SHA. Until all four findings are closed and parent separately releases A2, **A2 integration is blocked**. Adding A2 wiring, an isolated test, or a new regex adapter cannot compensate for failed A1 admission. New/resume requests share this whole-unsupported rule; no silent workflow fallback or known-subset finish.

## 3. Mode wiring — explicit parent decision, no activation now

G0-approved design interface: keyword-only `MolecularChatApp(..., normal_chat_mode='legacy', decision_wire_mode='native')`. Closed sets are `legacy | decision_a2` and `native | json`; invalid values fail assembly. These are **planned new server constructor options**, not existing configuration keys. Module-level app construction and launchers continue to omit them. Do not add environment variables, settings UI, provider probes, YAML changes or production enablement in A2. Implementation remains blocked at G1.

The actual `/ws` handler gets an optional app-owned `WebDecisionRuntime`. Absent runtime means current legacy route behavior. In `decision_a2`, **every chat frame** passes A1 admission, then this runtime, never legacy routing. Announce mode and supported capability names in additive `connection_ready` metadata; frontend controls are enabled only from this server announcement. Browser fields cannot choose mode, native/JSON transport, model, endpoint, backend, generation, identity, requirements or allowed tools.

`native` is the approved initial explicit wire selection; an operator/test may explicitly select `json` through the same server constructor. A provider rejecting the selected protocol yields failure, not an automatic protocol/client/legacy retry. Existing bounded schema repair in the loop is unchanged. Parent approved this exact bounded wiring at G0; both default-off and enabled-route tests remain required evidence before a later implementation freeze. Changing the default or exposing deployment configuration is a separate decision, not implied by passing A2 tests.

## 4. Minimal assembly and authority flow

```text
existing session/origin middleware → actual /ws → ChatHandler.handle_websocket
  → optional WebDecisionRuntime (one socket receiver; one active turn)
  → refreshed configuration → one app reader lease → captured model + generation
  → A1 admission → existing handler.process_decision_message
  → ModelDecisionLoop → existing dynamic WorkflowRunSession / registry / store
  → bounded events → result + candidate projection → exactly one connected complete
```

The runtime is a Web boundary coordinator, not another planner/session engine. App supplies the existing registry, Agent state store, reference service and model gate. Registry, event bus and references use the same store authority. A short app helper may factor registry initialization from `_create_supervisor_agent`; do not call Supervisor plan/run/execute to implement dynamic requests. Registration errors or missing store/registry fail decision availability explicitly. Reuse tool owners and close them only at app shutdown.

After middleware validation, derive `user_id=session_id=scope['agent_session_id']` as A1 expects. Generate trace IDs on the server. No client cookies/nonce/history become model context directly. Session identity must exist before admission or model dispatch; body identity and capability fields are rejected, not merged. Cookie tokens never enter `AgentContext`, logs or run records. Existing HTTP model settings are not a new per-browser credential mechanism and remain unchanged.

Validate frame byte limit (24 KiB) before JSON parsing with the existing bounded strict decoder; reject duplicate keys, nonfinite numbers, non-object frames and unknown fields. A1 retains its 16 KiB query and strict options limits. Control-frame validation belongs to runtime, not an expanded A1 tool admission allowlist. `timestamp`/`client_id` remain non-authoritative compatibility data and are not logged.

## 5. Request and socket protocol

One receive task per socket, one request-owner task at most, no unbounded request queue. Outbound writes share a socket-local deadline-aware send lock; the existing event buffer remains capped at 128 and sends at 30 seconds. Ping remains receivable while provider/lease/tool is blocked; a blocked network send cannot guarantee delivery of pong. Backpressure fails explicitly and settles work.

| Frame | Exact role in decision mode |
|---|---|
| Existing `type:'chat'` + A1 fields | Start a new request if no active turn. Allocate server trace and turn ID; invalidate any previous local waiting handle only after this new frame is validated for acceptance |
| `type:'ping'`, optional timestamp | Existing pong, no admission/model/lease |
| `type:'resume', trace_id, continuation_id, message` | Complete clarified request text, not an arbitrary patch to original authority. Requires matching socket-owned waiting record and A1-compatible validation described below |
| `type:'cancel', turn_id` | Cancel only the matching active turn on this socket; no trace-global or cross-socket task control |
| `type:'abandon', trace_id, continuation_id` | Drop matching socket waiting handle, with `continuation_abandoned` control acknowledgment. No execution, no new scientific terminal and no claim that durable waiting state was cancelled |

Control IDs are bounded strings in the corresponding server-issued format. Resume accepts exactly the listed fields; options/selection/requirements changes require a new chat. Invalid/foreign/stale controls return fixed non-disclosing `error` codes, no CAS, no model and no extra `complete` for the active turn. A second start/resume while active returns `request_in_progress`. Runtime sends additive `request_accepted` with `turn_id` and `trace_id` before model work. Preserve the original per-trace evidence IDs; use `turn_id` solely for per-socket delivery/control.

Accepted starts/resumes, including admission rejection and pre-model cancellation, have exactly one `agent_result` and one `complete` while connected. Errors for malformed/unaccepted frames are not accepted turns. Add the same turn ID to decision-owned event/result/complete envelopes for stale-turn filtering. **Do not add fields to strict `molecule_candidates` envelopes**: those retain their existing exact schema, source trace and reference pointer, ordered before the enclosing turn's complete. Ignore frames from old sockets; ignore non-candidate frames for stale turns.

`complete` means the response stream ended, not scientific completion. Preserve `waiting_for_input`, `partial`, `failed`, `rejected`, `cancelled`, `completed` in result and persistent DOM status. An unsupported request is rejected with a fixed reason (`unsupported_scientific_request` or A1's reviewed clarification code); no fabricated success text, obsolete planner invocation or numeric LLM rewrite.

## 6. Model lease, configuration generation and shutdown

1. Accepted-turn owner invokes existing config refresh **outside** the reader lease. It may wait for a writer, while receiver continues to handle controls. Handle config-invalid state with fixed failure/closure; never silently keep using revoked settings.
2. Acquire exactly one `gate.request()`; capture main model, generation and wire mode inside that lease, then create a fresh `ModelDecisionLoop` with that captured model. Check `decide` support and approved external-adapter family; unsupported/local-only client yields explicit unavailable failure. Do not call `generate_for_chat`, synthesize a greeting, or replace it with local generation.
3. App creates an opaque random UUID generation with initial model creation. Rotate it on **every committed runtime client/config replacement**, including credential-only replacement, reset/removal and replacement at the same model/URL. No key digest, credential comparison or model `__dict__` fingerprint. Unchanged observed config does not rotate; constructor/build failure does not publish a new model/epoch. Stage then publish client/config/epoch within the existing writer boundary; close retired client there after old readers have settled.
4. Fresh prepared contexts use captured generation, and loop receives A1's `config_generation`. Resume uses the original context but **current captured generation**; equality is required before dispatch/CAS. Never supply the old saved generation to pretend a replaced client matches.
5. User cancel signals an `asyncio.Event` to the bridge, whose owned watcher cancels the loop child once and lets it settle into its real cancelled outcome. Do **not** cancel the transport owner for an ordinary cancel: it must deliver the final result. Before the loop exists, owner observes the signal and emits cancelled without model/tool execution. Once finalization is committed, completion wins the race; late cancel cannot relabel/reopen it.
6. Disconnect/send timeout/overflow/server teardown cancel-and-drain all owned children; repeated cancellation cannot truncate cleanup. `settle_action` already shields synchronous Session work until settlement. A cancelled await is not proof a worker stopped: retain the lease until all model consumers settle; preserve uncertain tool status and no replay. An uncooperative worker blocks replacement safely and must be reported as unresolved, never detached to produce a false pass.
7. Waiting turns release the lease after terminal delivery; their continuation record contains context/obligations but **no model/loop/client/task object**. Old clients may then close. Shutdown first stops new runtime admissions and cancels/drains active owners; then existing app writer closes unique model clients and registry owners. Do not hold writer while awaiting a request that still needs a reader. Repeated shutdown retains close-once behavior.

The shared `@model_request` implementation and existing HTTP background lease transfer need not change. Main chat changes never mutate `molecular_generator_model` or its `gmm-llama:latest` configuration. No A2 read/write of user key stores, `.env`, key values or new credential persistence is part of implementation or tests; test configuration boundaries with synthetic in-memory injected providers.

## 7. Multi-turn history and owner-bound continuation

### Conversational history

Keep a detached socket-local history, never `handler.conversation_history`, browser-supplied messages, a process-global map keyed only by user, or a new persistent chat store. Two sockets with the same cookie intentionally have independent histories. Only completed, safely displayed **ordinary chat** user/assistant pairs enter history. Exclude scientific outputs, partial/failure/cancel/waiting content, tool messages and model-authored authority. This prevents unsupported scientific follow-ups from treating remembered numbers as evidence. Scientific anaphora still requires A1 and confirmed references, not conversational history.

Use `AgentContext.memory` only for the server's bounded ordinary-chat pairs: at most 20 pairs, at most 16 KiB serialized total; evict oldest whole pairs. An individually oversized or sensitive pair is omitted with an explicit history-omission display marker, not silently retained or summarized by another LLM. Never alter the authoritative current query or scientific requirements to pack history.

Add a small `decision_history.py` helper with closed `{user, assistant}` string-pair validation, bounded plain JSON and secret rejection. Build messages as existing system instruction + alternating historic user/assistant text + authoritative current user query. Prior conversation is data, never `system`, tool/function calls or evidence. For scientific requests the history prefix is empty. Teach continuation validation to compare the **exact expected prefix**, count actions only in the decision suffix (an ordinary assistant answer may begin with `{`), and replay from the prefix end. Fingerprint includes the frozen memory through A1's context projection. Increment the internal waiting revision beyond landed A1 if these semantics change; never accept incompatible snapshots by guessing an offset. Empty-memory direct callers preserve their message shape.

### Waiting continuation

Retain at most one pending record per socket for at most 15 minutes: original prepared obligations, frozen starting context/history, nonce and trace, generation, mode and created time. Store no live loop/client. New waiting results replace the prior nonce; terminal non-waiting results erase the handle. New request, explicit abandon, socket disconnect, shutdown or expiry erases it. Durable Agent records remain audit state; no reconnect/restart recovery, cancellation mutation or automatic execution of them is promised.

For resume, validate socket owner and handle before querying storage; reconstruct from this server record, never browser original query/options or current mutable history. Call A1 on the complete clarified text with the original flags to reject unsupported/disabled/mixed new tasks, then compare original kind, allowed/required obligations, metrics, explicit targets and existing subject constraints. Do not replace original requirements with the newly admitted object. A missing molecular subject may be filled by one valid explicit molecule; changing an already required subject, adding a batch/count/metric/tool or overriding a target requires a new request. In particular, a selected molecule already sealed into original subject requirements is not replaceable within this bounded resume. New chat still honors A1's explicit-input-over-old-selection rule, including rejection of invalid explicit input; never resurrect stale selection on later turns. Parent must approve this narrower Web resume policy at G1 rather than assuming every core continuation is exposed.

Use the original context/query/history and requirements for fingerprinting, and `clarified_query` for the new complete text in `loop.run`. Existing owner/source revocation/nonce/history validation and CAS stay authoritative. Changed generation/spec/requirements, foreign owner, repeated nonce or expired socket handle reject before model/tool calls and do not consume a valid waiting record. CAS uncertainty means stop, not retry model execution. Any A1 review change to clarification/refinement semantics is a **revision gate**, not permission to weaken these comparisons.

Tests must exercise at least two clarification cycles and a subsequent cancellation/new turn through actual `/ws`. A one-shot loop test cannot demonstrate this reconstruction contract.

## 8. RAG, scientific status and candidates

`enable_rag` is a strict user permission/preference, **not proof that retrieval occurred**. Existing home defaults true and sends it on ordinary chat. In A2:

| Input | Required behavior |
|---|---|
| `你好`, `enable_rag=true` or omitted | Genuine external model decision chat; no retrieval; metadata distinguishes requested/enabled preference from `retrieval_performed=false` |
| Explain logP / explain what RAG is | Ordinary explanatory answer if A1 admits it; no generated measurements or claim of retrieved sources |
| Calculate supported properties with RAG true | Only admitted initial tools; no background RAG injection into model context |
| Retrieve papers / search knowledge base / retrieval plus properties | Whole request blocked/rejected pending B, never no-retrieval scientific completion |
| Tools disabled + scientific execution | Reject/clarify, never promote to unrestricted chat |
| Ambiguous new action, generation/ranking/ADMET/reverse/docking compound task | Explicit A1 rejection/clarification; no successful supported subset |

Do not force `enable_rag=false` before A1 to evade policy, and do not automatically call the legacy RAG service because it is true. If A1's final classifier misclassifies an explicit retrieval request, route-level RED blocks A2 and returns the issue to A1; do not duplicate a keyword classifier in runtime. Ordinary external text is not a validated scientific result. Scientific numeric text remains generated by the existing evidence-based loop formatter; no post-loop model paraphrase.

Extend the existing bridge's finalization with the existing `_send_reference_candidate_events` projection against `result.to_legacy_dict()` and the shared service **before complete**, after the scientific result is settled. Projection reads trusted stored sources, not sanitized/truncated display metadata as authority. Use the same serialized deadline-aware sender. Projection/ACK failure makes references unavailable with a safe display warning; does not rerun tools or alter accepted numeric evidence into new scientific success. Transport failure still ends delivery and drains ownership.

Ordering: `agent_event* → agent_result → molecule_candidates* → complete`. Existing home completion drains candidate queue into mounted cards; existing controller ACKs only exact ordered keys after mount through `/api/agent/workflows/references/confirm`. Server-send is not ACK. Restore uses existing protected HTTP restore and never executes anything. Keep candidate original/canonical structures and compound IDs intact, false/zero numeric values intact and partial-source warnings visible. No candidate event from failed/rejected/cancelled observations; reuse existing partial-source eligibility.

**Four A1 tools do not generate CandidateSets.** A2 does not claim new candidate production. Required normal-route evidence is: restore/confirm an existing owner-bound source, ordinary chat without erasing confirmed selection, then selected follow-up through enabled `/ws`; stale/foreign/unmounted inputs rejected. A separate transport-order contract can use an explicitly marked stored CandidateSet fixture. It is not evidence of dynamic generation or a live browser ACK. B adds candidate generation/ranking bindings later.

## 9. Precise future write scope and TDD seams

This is the proposed **later implementation** allowlist, not files changed by this sidecar:

| File | Only intended responsibility |
|---|---|
| `src/web/app.py` | Default-off constructor mode, runtime injection/shared registry, nonsecret model epoch under existing writer, cancel/drain before shutdown |
| `src/web/chat_handler.py` | Early decision-mode delegation before legacy lease; bridge optional cancel/turn forwarding; reuse existing presentation methods |
| **New** `src/web/decision_runtime.py` | Closed frame validation, socket state, request ownership, admission composition, model snapshot, bounded history and waiting handle |
| `src/web/decision_chat.py` | Cooperative user cancellation vs transport cancellation, serialized output integration, single terminal delivery and pre-complete candidate projection |
| **New** `src/agent/harness/decision_history.py` | Closed bounded history-prefix construction shared by run and continuation validation |
| `src/agent/harness/decision_loop.py` | Seed admitted conversational history, no policy/tool expansion |
| `src/agent/harness/decision_continuation.py` | Exact prefix validation/replay offset and internal revision gate |
| `src/web/static/js/home/main.js` | Decision announcement/turn guard, cancel/resume/abandon controls, durable status labels and remove raw outbound payload logging in touched send path |
| **New** `tests/agent/test_web_decision_runtime.py` | Actual app `/ws`, real middleware/admission/bridge/loop/Session/store; in-memory external transport double |
| **New** `tests/agent/test_web_decision_runtime_lifecycle.py` | Same actual route with deterministic model/worker barriers, switch, repeated cancellation, shutdown |
| **New** `tests/agent/test_web_decision_runtime_references.py` | Actual protected reference HTTP plus actual decision `/ws`, ownership/mount-ACK contract boundary |
| **New** `tests/agent/test_decision_history.py` | Prefix shape, bounded retention and semantic replay regression |
| **New** `tests/home_decision_runtime_test.js` | Existing home scripts with DOM/socket fixtures; exact controls, stale socket/turn, persistent status, candidate completion flow |
| `tests/agent/test_decision_chat_transport.py` | Replace blanket no-entry source assertion with default-off/enabled behavior coverage; extend bounded finalization regressions |
| `tests/test_model_request_lifecycle.py`, `tests/test_web_app_lifecycle.py` | Non-regression of existing reader/writer consumers and repeated shutdown |

Read-only dependencies by default: A1 `decision_request.py`, `decision_bounds.py`, `decision_inputs.py`; `model_lifecycle.py`; session/auth modules; model adapters and config/key-store modules; scientific references, candidate normalization/controller, tool policies/adapters and HTTP workflow routes. If tests require changes there, parent must revise scope first. No production/test edits on the A1 worktree. No template/CSS redesign: create small accessible controls using the current home UI's DOM construction pattern.

Tests must construct `MolecularChatApp` and traverse its registered `/ws` behind real `AgentEntrySessionMiddleware`, not copy the route into a test app or invoke handler/bridge directly as acceptance. Existing app import constructs a global app and `tests/conftest.py` redirects some config roots; future runner must isolate collection **before import**, deny provider networking and redirect all Agent/target/cache roots. Do not read the host environment to discover credentials. Inject synthetic in-memory config and adapter HTTP transport; keep real admission, registry ownership, loop, Session, store and auth middleware. No monkeypatched identity scope/loop success in normal-route acceptance. Isolated helpers are supplementary only.

## 10. Revision and completion gates

- **G0 Parent design review (approved):** constructor-only `legacy` default / `decision_a2` opt-in; explicit native/JSON with no automatic fallback; socket-only 15-minute waiting; chat-only history capped at 20 pairs / 16 KiB; one lease with cancel/drain; whole-unsupported rejection; actual `/ws` and ACK tests required. Approval authorizes this documentation freeze only, not activation or implementation.
- **G1 Landed A1 (blocked):** `aed3571e9e1a25f8c757dc937969ad04a424b98c` is explicitly not accepted. Require corrected commit SHA + SPEC4 approval covering the four exact cases above (one count case) + actual landed SHA + separate parent release for A2; then compare A1 spec/plan, admission signature/limits, immutable requirements, explicit-input/selected-reference rules, generation validation and waiting revision. These future SHAs are pending evidence, not assumed. Rebase/merge only in a later authorized implementation task; account for parent-reported Planner #71 `5db56b0` without merging this sidecar. Re-run relevant contracts there. Current A1's claimed test totals are not A2 evidence.
- **G2 Cross-contract revisions (pending):** target/property/candidate/reference or presentation changes after `bb11ded` require renewed scope review. History prefix changes must update run/fingerprint/replay/revision together. No widening A1 or B to make a route test pass.
- **G3 Offline A2 review (not executed):** all planned actual-route RED/GREEN, lifecycle, Node and compatibility tests; code quality/spec review; clear failure/skip accounting. Contract doubles prove routing/lifecycle, not real external inference/science.
- **G4 Parent acceptance/publication (not authorized here):** parent coordinates CI and any authorized package-8 real model/science/normal-browser acceptance. Missing real evidence stays blocked/partial. Deployment remains excluded.

A2 completion, when reviewed and implemented later, means gated normal-route integration of the four-tool foundation, **not P7 completion**. P7-B generation, ranking, ADMET, reverse-target and actual RAG input/evidence bindings remain explicitly blocked pending their own designs and reviewed prerequisites. Dynamic docking approval/side-effect lifecycle is also not enabled. No test fixture, legacy static workflow or lab report closes those gaps.
