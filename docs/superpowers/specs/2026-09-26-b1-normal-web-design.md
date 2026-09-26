# B1 normal Web integration — design draft

Date: 2026-09-26. Status: **Parent and corrected independent written review approved; Task1 TDD preparation released. Later task and production-entry gates remain closed.**

Reviewed source pin: `a5189a5f854050e59c6fc8ed4f8a397313c09067`, branch
`codex/dynamic-bindings-b1`; worktree was clean at initial inspection. Parent has
since advanced HEAD to `f3e5f0ae3562e5b82ba126d3edf66d6be235153d`; local diff
confirms only `docs/handoff/remaining-through-step8.md` changed, no source/tests.
This task writes
only this document and the companion [plan](../plans/2026-09-26-b1-normal-web.md).
No imports, tests, network, environment/secret/asset inspection, staging or commit.
The previous plan's Task5 freeze records independent 2078 passed in 592.33s;
that is inherited offline core evidence, **not a run here or normal-Web proof**.
Parent owns PR84 RAG-focused CI/class-diagnostic investigation independently;
diagnostics remain pending. This task does not edit its files, rerun its tests,
infer a passed result or depend on a hypothetical diagnostic/code fix.

## 1. Decision and unchanged end state

Use the approved existing-engine approach: connect normal `/ws` semantic admission
to the seven-tool `ModelDecisionLoop`, `WorkflowRunSession`, revision8 journal,
source checks and publication barrier. The model chooses tool/clarify/finish after
each actual observation; admission supplies obligations, never an execution order.

| Sensible option | Trade-off | Choice |
|---|---|---|
| Extend existing async assembly, carry/exchange and B8 path together | Small opt-in integration; preserves one lease, owner, journal and budget | Recommended and selected under delegated choices |
| Build a separate B coordinator/endpoint around the same core | Easier local isolation, but duplicates socket history, nonce, ownership and admission logic | Not selected; cannot establish normal `/ws` by testing a lab route |

Do not expand `INITIAL_TOOLS`, invoke a static Planner/Supervisor workflow, or
silently fall back to original-four/chat after B admission failure. Defaults remain
`normal_chat_mode='legacy'`, `ordinary_chat_policy='a1_closed'`; no launcher,
environment activation, deployment or browser-selectable profile is added.

This closes the **next normal-entry B1 increment**, not packages P1–P8. The full
2026-09-25 dynamic-bindings design, especially §§4–11, remains binding: B2 still
requires targeted/seeded generation, one logical invocation with up to five
internal rounds, inclusive16 backend budget, token-bound proof, all-candidate
analysis/ranking and dynamic report/mount/ACK evidence. C still requires explicit
hashed docking consent, actual Vina/pose evidence and safe physical cleanup. P8
still requires repeated real provider/science/browser normal-entry acceptance;
E01–E12, B016/B017 and ordinary REAL-010/DIVERSE-015 positives are not replaced
with rejection tests. P7's end state includes ordinary normal chat and the actual
known12/20 case sets, not just a seven-tool allowlist; preserve every original
positive expectation. The ledger's 32 GOLD/DIVERSE ×3, separately named REAL-010
UI positive and16 legacy accounting remain P8 requirements, not new passes.
B2 generation/ranking, C and P8 are committed next increments, not optional scope
cuts. No pending milestone or full product-capability support is claimed here.

## 2. Verified source seams and actual gaps

All paths below are relative to the pinned repository. These are current APIs,
not promises that integration already exists.

| Source / symbol | Inspected fact and required delta |
|---|---|
| `src/web/app.py::create_async`, `_assemble`, `_build_ordinary_capability_base`, `project_ordinary_capabilities` | Async rollback and reader/writer ownership exist. Assembly/projection still hard-code original-four and identity feature names. Add server B1 selection and inverse canonical mapping. |
| `src/web/decision_request.py::validate_request_envelope`, `assess_whole_request`, `prepare_with_intent` | Bounded envelope, whole-query assessment and semantic intent exist. `_UNSUPPORTED`, v1 requirements and snapshot validation reject B science/catalog. Preserve legacy branches; add B-only obligation preparation. |
| `src/agent/contracts/ordinary_admission.py::loop_admission` | Only original profile/catalog/`whole-request-v1` accepted. Carry is a trusted-server projection, not browser authentication. Add one closed B triple. |
| `src/web/decision_runtime.py::_prepare_semantic`, `_execute_segment`, `_validate_resume` | One captured view/reader, segment, waiting record and new loop per turn exist. Loop construction omits `binding_profile`; resume reclassifies the reply as a full A1 request. |
| `src/agent/harness/decision_loop.py::_run`, `run` | B explicitly rejects any carry/exchange (lines269–280); B clock starts at loop entry. Configuration recheck omits admission binding. B outer drain exists but B never sets `AdmissionExchange.checkpoint`. Removing one rejection is insufficient. |
| `src/agent/harness/decision_binding_continuation.py::snapshot_payload`, `validate_continuation`, `_replay` | Actual SQLite B8 restoration authenticates complete observations, original input journal and sources. Exact snapshot key set currently has no ordinary admission/root fields; clarification replay lacks ordinary display revalidation. |
| `src/agent/harness/decision_continuation.py::configuration_digest`, `claim_continuation`, `validate_history` | Existing v6/v7 fingerprint, intent/counter reconciliation, display replay and pre-CAS credit checks are reusable rules; B deliberately uses its separate decoder. |
| `src/web/decision_chat.py::_event_frame`, `_deliver_result`; `src/web/static/js/home/main.js::resolveAgentEventPresentation` | Bridge streams terminal events while loop finalizes; home labels `task_completed` final without examining B publication flags. Both must honor pending publication. |
| `src/web/decision_runtime.py::_TurnSender.send_text` | Unconditionally overwrites `retrieval_performed=False`; this would misreport real B retrieval. |
| `src/web/decision_request.py`, `src/web/static/js/home/config.js` | Omitted request/default UI `rag_count` is5; `RetrievalRequirement.k` and actual B RAG are fixed3. Explicit count intent must not be silently discarded. |

Read with `AGENTS.md`, `docs/PROJECT_STANDARDS.md`, the full dynamic-bindings
design, `2026-09-26-b1-decision-execution-design.md`, and its plan's latest Task5
freeze/Task7. Preserve Task5's compare-before-fresh-seal restoration, immutable
failure correction and consumed-reference guards; no new recovery engine.

## 3. Proposed interfaces and smallest future allowlist

The following changes are **proposed**, not existing APIs:

- Add keyword-only `binding_profile=None` to `MolecularChatApp.create_async` and
  thread it through `_validate_chat_profile`/`_assemble`. Exact B1 ID requires
  `decision_a2 + semantic_v1`; None preserves defaults. B2/aliases/unknown values
  reject before resource construction. Synchronous construction stays non-B.
- Store the validated choice on the app; build B base with existing
  `build_capability_snapshot(binding_profile=B1_PROFILE_REVISION,
  original_four_profile=False, wired_names=B1_TOOLS, rag_enabled=True, ...)`.
  Wiring is assembly-reviewed adapter presence, not health. Missing required
  wiring fails B assembly; unavailable scientific assets do not disable chat.
- Extend `project_ordinary_capabilities(..., rag_enabled=False)` additively.
  Reconstruct canonical names using `TOOL_FEATURE_PAIRS` inverse (ADMET/reverse/RAG
  feature IDs differ); validate an unprojected B base before request projection.
  Keep original base serialization/digests exact and readiness `unknown`.
- Add `binding_profile=None` to existing envelope/assessment/preparation entry
  functions and their internal revalidation calls. New private B-only module
  `src/web/decision_binding_request.py` owns bounded whole-request reduction to
  existing `BindingRequirements`; it is not another classifier model or protocol.
  `PreparedDecision.requirements` accommodates v1 or v2; its original context
  ownership/getter semantics remain unchanged. Profile comes from server args.
- Extend `loop_admission(..., binding_profile=None)` with exact B1/profile,
  `ordinary-product-b-v1`, `whole-request-b-v1` triple. Existing signatures on
  `ModelDecisionLoop.run` and carry/exchange DTOs need no additional authority
  object. Extend B8 decoder with optional trusted `admission_carry` and
  `request_kind`, selected by caller, never by snapshot contents.
- Add `HomeAdvancedOptions.setDecisionRagCount(value)` accepting only3/null:
  connection-local supported-count policy, not coercion of saved/requested values.
  B ready frame advertises `capabilities.rag_count=3`. An untouched UI default
  (no saved count or user edit) visibly defaults to3 before a request exists.
  Treat any saved count, even5, or user edit as explicit: keep that value until
  the user actively selects the offered supported3 choice. Unsupported explicit
  values display a clear notice/"use3" action; never send3 while showing/requesting5.
  No browser profile field; server independently rejects unsupported requests.
  Keep persisted preference and connection-local effective default separate.
  In a B connection with no explicit RAG choice, unrelated temperature/molecule
  saves must omit that implicit count rather than persisting either the effective3
  or manufacturing an explicit5. Preserve any preexisting explicit count exactly.
  Outside the B policy, preserve existing default/save behavior. Load the real
  options module in regression tests; a stub cannot establish this invariant.

Future production write allowlist (no writes now):

1. `src/web/app.py`, `src/web/ordinary_capabilities.py`,
   `src/web/decision_request.py`, new `src/web/decision_binding_request.py`:
   assembly, canonical view and admission only.
2. `src/agent/contracts/ordinary_admission.py`,
   `src/agent/harness/decision_loop.py`,
   `src/agent/harness/decision_continuation.py`,
   `src/agent/harness/decision_binding_continuation.py`:
   composed carry, fingerprint, budget, replay and exchange release only.
3. `src/web/decision_runtime.py`, `src/web/decision_chat.py`:
   normal socket execution, resume and safe presentation only.
4. `src/web/static/js/home/main.js`, `src/web/static/js/home/advanced_options.js`:
   pending-event display and fixed B RAG count only.

No new routes/DB tables/executors. No changes to scientific producers, RAG core,
models, factory policies, Session, ledger, auth, references, report projector,
binding resolver/requirements schema or runtime defaults in this increment.
An actual need outside this list is a written amendment before code, not implicit
permission. Exact future test allowlist is in the plan.

## 4. Admission, permissions and scientific dataflow

Exactly `property_calculator`, `drug_likeness_assessment`, `activity_predictor`,
`target_database_search`, `admet_predictor`, `reverse_target_predictor`, `rag_search`.
Use `B1_TOOLS`; never resolve feature aliases into tools. Required tools come from
the complete request plus v2 groups; allowed tools equal that required set for
this bounded entry. No unrequested exploratory tool gets a slot. Preserve registry
owner, idempotence/side-effect checks and independent strict-bool permissions.

| Whole admitted request | Immutable representation / behavior |
|---|---|
| Existing properties/likeness | Same metrics, complete ordered molecular subjects/counts; v2 `molecular_results`. No silent fragment/subset. |
| Available-method ADMET; activity for user target | `AdmetRequirement(scope='available_methods')` / `ActivityRequirement`; exact subjects/count, authored target when supplied. Bare ADMET means this disclosed bounded readout, never all endpoints. Explicit unsupported toxicity/endpoint guarantees clarify. |
| Reverse prediction | One subject, fixed threshold0.6/top10. With known subject set `ReverseRequirement`; when missing, explicit required reverse tool retains core's single-subject restriction until clarification, without inventing SMILES or mutating original requirements. |
| Reverse then lookup a returned target | Require reverse + target; `TargetRequirement(input='reverse')`. Model selects a server-issued eligible row handle from actual observation. No highest-score/tie/organism policy inferred. |
| Direct target lookup | `TargetRequirement(input='user', query=<original or parsed authored target>)`; resolved/not-found remain distinct. Required resolved result only when explicitly admitted. |
| Explicit local molecular retrieval | `RetrievalRequirement(query=original_query, k=3, require_hits=False)`; original text goes unchanged to RAG. Explicit demand for a hit sets `require_hits=True`; numeric hit-count guarantees/external literature reject. |
| Conversation, capability or qualitative knowledge | Existing semantic intent/display path with empty v2 groups and empty allowed/required tools; no retrieval merely because RAG preference is true. |

B whole-request grammar extends the existing full-consumption approach, not just
the deny regex. Recognize ADMET/ADME available-method assessment, reverse target
prediction, local molecular RAG retrieval, existing four obligations, and explicit
reverse→returned-target linkage. Parse every clause, subject, target, count and
qualifier. B-only fixed examples include `评估 ADMET；SMILES: CCO`,
`反向寻靶并查询返回的一个靶点；SMILES: CCO`,
`使用 RAG 检索本地分子库中与乙醇相关的分子`, and a conjunction of supported analyses for
one shared explicit subject. Retrieval text is data, not instructions to other
tools. Preserve the whole-risk gate: an unconsumed scientific suffix cannot become
chat through an intent proposal. Negative clauses cannot delete mandatory work.
Unsupported generation/ranking/docking/web literature/custom controls block the
whole request. Maintain the four A1 counterexamples recorded in the parent spec.

For B envelopes only, omitted `rag_count` defaults3; an explicit non3 value
blocks an actual retrieval request with `request_clarification_required` and a
truthful supported-count notice, not ordinary chat. Never infer that an incoming5
was a default: on the wire it is explicit. Retain omission/value faithfully through
`_validated_envelope`. Reject prose count conflicts too. Choosing3 is a new
explicit user choice, not silent scientific request rewriting; UI can make that
choice without a broken first-use default, and reverts only connection policy on
disconnect (not explicit preference). Compared with a bounded variable-k extension,
fixed3 is smaller here: current RetrievalRequirement, query-only adapter/resolver,
tool source validation and receipt/replay all bind3. Changing only a widget or tool
argument would not preserve exact-request proof. Supporting5/other counts remains
a separately reviewed bounded extension, not a reason to weaken P7/P8 positives.
Keep envelope24KiB, context64KiB, requirements32KiB, arguments4KiB, observation64KiB
and entire snapshot512KiB. B query uses the existing UTF-8 content16KiB policy
consistently in envelope revalidation/binding digests; no legacy limit change.
Specifically, `build_admission_binding` currently hashes query with a16KiB JSON
bound; B must first enforce16KiB content and the64KiB whole-context ceiling, then
allow its bounded JSON query representation up to64KiB for that digest. Merely
changing Web envelope validation would leave escaped/non-ASCII B queries broken.
The semantic intent prefix needs the same treatment: current history_prefix
limits the query's JSON representation to16KiB too. In the B-only runtime branch,
validate the actual B query at its existing content/envelope/context boundaries,
use history_prefix with an empty current-query slot to validate/copy the unchanged
system and historical pairs, then place the exact validated query in that final
user slot before the real transport validates the complete messages. Never send
the empty placeholder or truncate the original query. Original-profile calls and
historical-pair16KiB bounds remain unchanged; no decision_history API/limit edit.
Tests must exercise actual native/JSON intent dispatch with escaped and Unicode
queries fitting the24KiB envelope but exceeding16KiB serialized-query size, plus
exact-content-boundary positives and one-byte-over negatives with zero dispatch.

Reuse reference resolve/confirm/ACK, never model/history SMILES. Properties→ADMET/
activity consume the whole sealed batch; reverse→target consumes exact raw-row
identity. RAG never becomes target or molecular authority. Current producer
`validate_current_observation` hooks must run through existing `WorkerOwner`
owned work, not on the event loop; no initialization/retrieval/prediction on replay.
Preserve `valid_hits`, validated `valid_empty` (possibly no FAISS dispatch),
`invalid_discard`, unavailable, partial/family-review and method limitations.
Only core's validated cited closure produces scientific final prose; model finish
text, missing endpoints and unverified reference rows never supply numbers.

## 5. One root budget, one lease, two release barriers

Start existing `ActiveSegment` before refresh/reader acquisition. B uses that same
clock/start/deadline from the first requirement/source check onward. Its local
loop start must not grant a second300s. On resume cap at the minimum of segment
deadline and **segment.started_at + saved_remaining**, before decode/verification;
Web's post-send cap can only reduce this. Charge admission, intent, repair,
source hashing, SQLite callbacks, waiting for adapter slots and cleanup.

`M=intent_requests+model_requests <= configured_main_limit <=16`, outer tool
reservations/attempts<=12. B1 has G=0, hence root debits M+G=M<=16; no separate
main allowance, free intent/repair/finish, or reset on new Loop. Add a B-semantic
fingerprint-only `budget_policy='b-root-inclusive-16-v1'`; persist existing exact
ordinary counters/receipt rather than introducing empty generator journals.
B2's generator debits/token ledger remain its later required extension.
Candidate intent needs two available slots, known chat one; resume never reruns
intent and rejects exhausted totals. Proven never-dispatched calls can retain
existing rollback semantics; uncertain dispatch is never refunded/replayed.

Core B8 tail stays exact: at snapshot start t, R=deadline−t>0,
F=min(30,R/4), saved_remaining=R−F; tail deadline=min(root,t+F). Snapshot building,
terminal/waiting callbacks, final source checks and physical owner drain all
consume F. No refund/rewrite/re-reserve loop. With R100/F25, a20s tail leaves at
most75; a30s tail releases no answer/nonce. Human think time is not active credit.

Store candidate checkpoint facts privately on `_BindingFinalization`. Only outer
`run`, after successful physical drain and final clock/invalidation check, sets
the set-once `AdmissionExchange.checkpoint` and returns a releasable result.
Failed/uncertain publication leaves it empty. Do not emit another positive store
callback after final source validation. Propagate unconfirmed physical cleanup
to the existing Web retained-owner path: an exception is not a joined worker.

Web bridge then performs existing bounded result/reference projection, while the
same model reader is retained; it is a separate owned async child after the loop
owner has sealed. Final `complete` follows physical settlement. Install socket
waiting authority only from successful complete-send callback, with
`min(checkpoint.remaining_seconds, remaining_credit(segment, now))` and TTL
`bridge_returned_at+900`. This clamp does not charge F twice or refund unused F.
Delayed/failed send, expired credit/TTL, disconnect or shutdown grants no waiting
handle. No extra source work after core owner sealing; freshness is established
at the core release boundary, not claimed atomic with future source replacement.

## 6. Authenticated continuation and compatibility

Protected `/ws` still uses `AgentEntrySessionMiddleware`, origin/TLS policy and
opaque session cookie. `PreparedDecision.context` binds user_id=session_id from
server scope, not `client_id` or JSON. Scientific/RAG flags express existing
request permission, not docking consent or authenticated account identity.

Retain `_Waiting` original context/prepared requirements/admission binding on
the same socket. A new Loop per segment is expected; a new/closed socket cannot
redeem the old nonce, even with the same cookie and SQLite trace. Do not rebuild
`_Waiting` from SQLite or browser history. No new reconnect/crash-recovery API.

B resume does not run A1 `_validate_resume` on the last reply. Check socket nonce,
TTL/current model+capability/profile/permissions and whole-reply scope first;
`SMILES: CCO` and `target: PDE5A` can refine the original activity request, whereas
new calculations, count/target/subject substitutions, permission switches and
unsupported suffixes cannot change it. Use a pure B reply-scope check, then the
existing owned B8 journal/resolver reduction, never concatenate a fake request
or authorize from `_Waiting.input_queries` alone.

Before CAS authenticate the existing waiting owner/configuration/store marker,
exact ordinary binding/intent receipt/counter sums and original history prefix,
then original journal/action records/seals/current source closure. Revalidate
all historical ordinary clarify text with the same display gate (like v7),
including intermediate questions, before any CAS. Reconstruct a detached trial
reply journal against the already authenticated staging projection and original
requirements; check monotonic constraints and scope without starting/installing
it. This is a narrow B8 decoder extension, not a public restore API. Missing
input may remain unresolved and clarify again; invalid supplied input is not
erased by later omission. Check TTL/deadline immediately before and after CAS.

On confirmed claim only: original live start/restore, full ordered native
observation comparison before new seals, trusted journal restore, original
records/closure, then admit new reply once. A post-CAS source swap, mutation or
commit-then-raise may consume nonce but grants no dispatch/result. Pre-CAS
rejection leaves SQLite waiting unchanged (Web elapsed credit still decreases).
`_check_retry_authority` must also reject the B invalidation marker; never infer
unconsumed authority from result status alone.

Compatibility selection is server-side:

| Caller | Revision / behavior |
|---|---|
| Existing no-profile/no-carry | v6 unchanged |
| Original-four semantic carry | v7 unchanged |
| Existing explicitly trusted B core, no carry/exchange | Preserve current offline B8 core contract; not normal-Web authority |
| B1 normal Web | Carry **and** empty exchange required, B8 exact extended fields/fingerprint |

This explicitly reconciles old dynamic-design §11.4's no-carry prohibition with
the now-frozen core API: Web remains strict; do not break approved direct core
tests to claim an ordinary admission boundary. No promotion of6/7, core-only8
or different B profile into Web8. Decoder expects semantic keys only when the
caller supplies validated carry; self-consistent checksums are not authentication.
The journal must be seeded **after** any trusted intent turn-id normalization,
and configuration rechecks must include the same admission binding as initial
fingerprint. Otherwise ordinary chat alone invalidates the B context/fingerprint.

## 7. Presentation, errors and rollback

Preserve B terminal summaries and `publication_stage='pending_source_check'`,
`answer_released=false` through bridge sanitization. Home displays these as
pending validation, never green/final; only verified `agent_result` followed by
matching `complete` ends the normal turn. Failure-only corrections remain visible.
Neither pending frames nor a failure result exposes a continuation, scientific
answer, report or candidate authority. Default legacy terminal rendering stays.

Stop overwriting B retrieval metadata. For B, derive `retrieval_performed` only
from released accepted RAG receipt/proof (valid-empty counts as retrieval;
`index_search_executed` remains its separate fact). False means no verified
retrieval, not fabricated evidence that no attempt occurred: failure/partial
observations retain actual attempt diagnostics. Preference alone remains false.
For B only, add a bounded presentation-only retrieval state derived from actual
Session/execution facts: verified, not_attempted, unverified, or unknown. Only
positive zero-dispatch evidence permits not_attempted; absent accepted evidence
alone yields unknown, never a claim that no attempt occurred. Home must not map
B retrieval_performed=false to "本轮未执行检索". Unverified/unknown use truthful
"未取得可验证的检索结果" wording; verified-empty remains verified. Preserve the
old-profile boolean presentation. No browser/model-authored state is authority.

Retain existing reference projection, exact candidate DTO/mount/ACK/restore and
owner checks; no turn_id added to strict reference/report DTOs. B1 does not turn
RAG rows into CandidateSet or claim G1 dynamic-report support. Ordinary chat with
incidental reference does no science; consumed invalid references reject, unused
ones cannot block independent RAG/target lookup. Include same-canonical/different
reference identity and tamper/revocation regressions.

Assembly failure/cancellation uses existing AsyncExitStack: close only acquired
owners once, tools borrow RAG/gmm, never close borrowed source in a tool check.
Partial tool-pool acquisition remains owned. Model/capability writer stages all
fields before publish and rolls back partially rebound main consumers; gmm stays
independent. Writer/shutdown waits for actual workers and reference projection,
even under repeated cancellation. Close errors remain unconfirmed, not success.
Core stale-source correction is irreversible/frozen/append-only with bounded
retries; storage failure suppresses success without claiming durability.

Rollback selects the old server profile for **new** admissions after normal
drain; never convert/replay B waiting work under7, delete evidence or reexecute
uncertain actions. Generation changes invalidate waiting. No deploy/runbook or
live switch is performed by this task.

## 8. Review questions and evidence limits

Selected resolutions above need parent + independent written acceptance, not
new user questions: preserve direct core8 while closing Web ingress; add semantic
fields conditionally to8; distinguish untouched UI defaults from explicit count
choices; stage reply validation before CAS without promoting transcript to authority.
These are proposed changes awaiting written review
to real gaps, not APIs claimed to exist today.

Independent Euclid written review identified three P2 gaps: semantic intent query
serialization bound, false no-attempt UI wording, and transient RAG-count leakage
through unrelated option saves. The amendments above and corresponding plan
probes address all three. Corrected Euclid written review approves design hash
8CDEC224D858950F93EC7D724D833864A12651E9DDF06F445007C9F104A8983D and plan hash
F69F35B5EBF94D203B006F7CCAEB818C032F0083CABAF8CA08895AA74BBE1BFB with no remaining
actionable findings. Parent accepts the written design under delegated choices.
Only Task1 preparation/behavioral RED is released now; no Web production switch.

Remaining source-dependent gates (待确认 before affected code/release):

- The existing RAG fixture `initialized_service` runs `asyncio.run`; initialize
  it outside the ASGI loop. `sources` also pre-executes a tool and must not seed
  normal-Web evidence. New Web fixture must attach an initialized actual service
  with zero prior retrieval and separately observe the first real tool call.
- Actual activity positive evidence cannot be produced by this no-assets offline
  integration. Use real unavailable behavior, not fabricated successful rows;
  retain positive real activity/ADMET readiness as P8 gate. If fixture isolation
  needs new producer injection APIs, stop for a named amendment, not fake tools.
- PR84 class diagnostics remain parent-owned/pending. Current `f3e5f0a` adds only
  ledger text over reviewed Task5 `a5189a5`. Reconcile any later actual source
  changes before release; no dependency on an unimplemented fix or CI pass.

The companion plan defines initial RED probes, actual mounted WebSocket/SQLite/
RAG tests, legacy unions and written review gates. None has been run here.
