# P7-C Docking Consent Integration Implementation Plan

## C0a publication baseline (parent evidence, 2026-09-27)

The pure-protocol four-file slice is published separately on
`codex/docking-preparation-protocol`. Parent fast-forwarded from108df5d to actual
mainc90c180c341c1f3da86281f2bd2b7feb08285353 after PR98/101 passed their nine
checks and reviewed/landed tree equality. All four owned file SHA256s remained
unchanged across the fast-forward; this paragraph is a later evidence-only append.
The eight incoming paths include bounds/generator contracts and test cleanup,
none of the four publication files. Author/fresh733-pass results below remain
108df5d evidence; latest-head full CI is still required before merge. No claim of
rerunning them on c90c180, complete C integration, deployment or real docking.

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` for separately released batches; use `superpowers:test-driven-development` within each batch. Stop for independent SOURCE/QUALITY between batches.

**Goal:** Ordinary Agent chat → server clarification/preparation → explicit human consent → one existing-runtime docking task → traceable response and owner-authorized artifacts in the original conversation.

**Architecture:** Reuse TaskRuntime/LocalTaskBackend, staging, DockingExecution, completion verification and existing process ownership. Add only the approved consent/entry/receipt seams; no second scheduler, model execution action or substitute manual form.

**Tech Stack:** Python/FastAPI/Pydantic, existing SQLite runtime, pytest/pytest-asyncio, vanilla JavaScript and existing Node test style.

## Baseline and contract

Actual local HEAD: `108df5d8acbdc1881a8f09c31b8b450acdfb247b`, branch `codex/docking-consent-integration`; parent performed the alignment. The authoritative contract is [the approved design](../specs/2026-09-27-docking-consent-integration-design.md), especially §§3.1–3.2, 4–6 and the §7 file allowlist. This plan references those requirements rather than redefining them.

Socrates accepted design SHA-256 `9D0B630F45E4060C3088EE4C487395C29A92D00D7FC7842AEABAB82061FFA0F4`. The sole subsequent spec edit is §3.2 `six fields` → `five fields`; current SHA-256 is `97B61082201E2703BE460C3C592212ABA8E8E348B8FA3DD58FC04C5EB82FE0EF`. Its historical c612 source anchors remain historical; this plan pins execution preparation to landed 108df5d.

The six incoming publication paths are `docs/superpowers/plans/2026-09-27-b1-publication-integration.md`, `src/agent/harness/decision_execution.py`, `src/agent/harness/decision_loop.py`, `src/agent/runtime/event_bus.py`, `src/agent/runtime/run_session.py`, and `tests/agent/test_decision_binding_publication.py`. Preserve their source-validation, frozen publication and cancellation protections. None is a C0 edit target.

Original preparation was documents only; Socrates accepted plan `3AB9AAD8F725FCDB491860BF1D82A7F5D73FE8A0DB5F731CCE0F993449EC2F87`, then test-first freeze `E4C8…` / plan `9A38…`. After the actual single-node missing-feature RED, parent released only the C0a production module plus this plan, followed by separately authorized author/fresh five-module runs recorded below. Current authorization is **this plan only**, recording those results and preparing publication scope; source/tests/spec remain frozen. Other commands remain future commands. No Python, runner edit, service/model/configuration probe, Git alignment, commit or push in this turn. Each future execution requires a separate slot grant and before/after input freeze, actual handle-to-terminal tracking and honest failure/skip reporting without silent retries.

## C0a — first code batch: pure proposal DTO only

**Exact edit scope (two new files):**

- `src/agent/contracts/docking_preparation.py`
- `tests/agent/test_docking_preparation_protocol.py`

Public API:

```python
class DockingPreparationProposal(BaseModel): ...
class DockingPreparationEnvelope(BaseModel): ...  # exactly {"proposal": ...}
def docking_preparation_json_schema() -> dict: ...
def parse_docking_preparation_json(raw: str) -> DockingPreparationProposal: ...
```

Use strict, extra-forbidden DTOs, the five proposal fields and closed nested value/span shapes from spec §3.2. Reuse `decode_protocol_json` from `src/agent/contracts/decision.py` with 8192-byte limit; add the stricter C depth/node bounds before Pydantic validation. Fix the counting convention in tests: envelope root depth 0; child values increase depth by one; each container/scalar is one node, object keys additionally count one node each. Reject depth >8 or nodes >256. No owner resolution, persistence, transport, model call, file access, execution method or approval capability in this module.

Structural range/index/type checks belong here; actual source existence, text bounds, owner/generation, whole-request obligations and live refinement/history checks belong to C0c/C3. DTO validity must never imply admission. Public parse failures use sanitized `DecisionProtocolError("invalid_docking_preparation")`, with no raw provider input or chained validation traceback.

- [x] Add the missing-feature test first, with a literal independent fixture (not generated by the new schema/validator):

```python
import importlib
import pytest

def contract():
    name = "src.agent.contracts.docking_preparation"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        pytest.fail("C0a docking preparation contract is absent", pytrace=False)

def test_minimal_proposal_is_not_execution_authority():
    api = contract()
    raw = ('{"proposal":{"version":"1","kind":"docking_request",'
           '"unresolved":true,"source_spans":[],"fields":{}}}')
    proposal = api.parse_docking_preparation_json(raw)
    assert set(proposal.model_dump()) == {
        "version", "kind", "unresolved", "source_spans", "fields"
    }
    assert proposal.kind == "docking_request"
    assert proposal.fields.model_dump(exclude_unset=True) == {}
    assert not any(hasattr(proposal, name) for name in (
        "execute", "approve", "approval_nonce", "owner", "task_id"
    ))
```

- [x] Observe **missing-feature RED**, before creating the production module:

```powershell
python -m pytest tests/agent/test_docking_preparation_protocol.py::test_minimal_proposal_is_not_execution_authority -q
```

Expected: explicit absent-contract failure, not skip, unrelated dependency error or a deliberately introduced behavioral defect. `python` in this plan means the parent-approved interpreter, not permission to discover or substitute an environment.

- [x] Prepare independent literal valid/mutated fixtures covering spec §3.2: all six kinds; missing/extra/duplicate keys at every level; version/native bool; absent versus null fields; field-specific numeric limits and bool/string rejection; structural span ranges/roles (actual source-text/code-point upper bounds remain C3); index uniqueness and reference bounds; kind/fields restrictions; finite floats, overflow and surrogates; UTF-8 bytes, depth and nodes; full-document parsing (no suffix/fence/repair). Assert schema closure separately. Boundary tests observe and delegate to real model validation: a valid positive control must reach it, at-limit schema-invalid shapes must reach it, and over-limit inputs must not. A later schema rejection alone is not guard proof. Public exception formatting must not contain a sentinel secret. Preparation is complete; execution is not.
- [x] Implement only the two DTOs, private closed nested DTOs, schema function and bounded parser; no imports of runtime/Web/tools. `fields` is a strict nested DTO whose omitted members stay omitted; direct construction also rejects invalid native types. Native-arguments and JSON-content fixtures use the same parser, but label this **payload parity**, not real transport proof. Source prepared after observed RED, then SOURCE accepted and tested as recorded below.
- [x] Observe focused GREEN and unchanged protocol regressions, in this order:

```powershell
python -m pytest tests/agent/test_docking_preparation_protocol.py tests/agent/test_ordinary_intent_protocol.py tests/agent/test_ordinary_intent_transport.py tests/agent/test_decision_transport_boundaries.py tests/agent/test_decision_protocol_recovery.py -q
```

Expected: all selected assertions pass; publish actual totals, warnings/skips and hashes only after terminal. C0a GREEN establishes pure proposal parsing only. Stop for independent review; it does not authorize C0b or establish working C.

### Existing transport seam inventory — read-only in C0a

| Exact file / symbol at 108df5d | C0b change after separate release |
|---|---|
| `src/agent/decision_transport.py:51`, `ProtocolProfile`, `_function_name`, `_parse_protocol` | Add explicit third profile `DOCKING_PREPARATION_V1`, function `docking_preparation`, parser dispatch; never fall through to decision schema. |
| Same file:214/296, `_snapshot_messages`, `_payload` | Closed C role/content messages; spec native/JSON envelope, one native call, no retry/fallback. Replace binary intent-versus-decision branching with explicit profile selection. |
| Same file:98/421, `IntentJournal`, `_request_protocol` | Add `DockingPreparationJournal` and `DockingPreparationResponse`, exact journal/profile guards and actual request-ID binding. Preserve ordinary journal semantics; C pre-dispatch failures retain null ID. |
| Same file:415, adjacent public entry | `request_docking_preparation(model, messages, *, mode="native", max_tokens=256, timeout_seconds=30.0, _journal=None)` delegates once to existing `_request_protocol(..., journal=_journal)`; no independent client. |
| `src/agent/openai_compatible_model.py:273`, adjacent to `propose_ordinary_intent` | Thin `propose_docking_preparation(..., _journal=None)` wrapper, same request options. No app route registration. |

**C0b edit scope:** these two existing production files and `tests/agent/test_docking_preparation_protocol.py`; C0a contract file only if a reviewed test demonstrates a needed protocol correction. Use real `httpx.MockTransport` at the lowest HTTP seam, not a stubbed proposal/request function. Observe missing-profile/API RED, then implement and run the same five-file GREEN command above. Test both wires, dispatch counter/actual journal ID/stages, pre-dispatch zero, post-dispatch invalid/timeout spent, terminal journal immutability, wrong profile, parallel calls, bounded detached records and no second call. Root durable four-proposal enforcement is **not** proven until C0c/C3; do not counterfeit it with an in-memory journal test.

Next-batch read-only preparation identifies these concrete future test nodes in that same test file (none added or run now): `test_c_transport_api_present`; `test_c_actual_request_and_journal`; `test_c_prevalidation_failure_has_null_id_and_zero_posts`; `test_c_rejects_wrong_profile_parallel_calls_and_fallback`; `test_c_interruption_keeps_spent_attempt_and_terminal_journal`; `test_c_journal_bounds_privacy_and_reuse`; `test_three_profiles_preserve_existing_transport_behavior`. The C response carries `proposal`, `error`, `tool_call_id`, `metadata`. One critical seam is C's pre-validation failure with null request ID versus the existing ordinary journal's bind-before-validation behavior: preserve ordinary semantics and explicitly branch C binding/stages, rather than relabeling IntentJournal. Reuse `_request_protocol -> _post -> _read_post`; no second transport, store, scheduler or Web/durable-root wiring is authorized or completed by C0b.

## C1–C8 — separately released batches

For every row: add the named desired-behavior regressions first → observe and classify RED → implement only that row's seams → focused GREEN plus listed legacy inputs → independent SOURCE/QUALITY. A new missing API is valid feature RED. Existing safeguards may already pass; never introduce a donor defect just to manufacture behavioral RED. Test files named below are exact; production paths are relative to repository root.

Safe order: **C0a → C0b → C1 → C2 → C4a → C0c/C3 → C4b → C5 → C6 → C7 → C8**. C2 uses injected raw execution only. C HTTP activation/public success remains disabled until C4a/C4b/C5 are all accepted; C0c/C3 tests use isolated constructor-enabled apps.

| Batch / dependency | Production scope (spec §7 is the ceiling) | Focused tests and acceptance |
|---|---|---|
| **C1 prepare/identity**, after C0b | New `src/task_runtime/docking_consent.py`; `src/task_runtime/database.py`, `store.py`, `runtime.py`. Add consent table in existing DB and `prepare_docking_consent`; reuse stager, no new task queue. | New `tests/task_runtime/test_docking_consent.py`: copied input hashes independently computed, explicit grid/options, fixed request/task/owner identity, zero model/backend/tool calls, body/file/type/path limits, expiry/capacity reservations, cancelled staging retains writer then cleans owned files only. Legacy: `tests/task_runtime/test_staging.py`, `test_task_store.py`; `tests/agent/test_docking_tool_contract.py`, `test_docking_contract_integration.py`. |
| **C2 one-use execution**, after C1 | Same consent/store/runtime files; `src/task_runtime/docking_execution.py`. `approve_docking_consent`, `cancel_docking_consent`, `get_docking_consent_view`; claim CAS and durable raw-dispatch reservation inside existing execution lease. | Extend consent tests: two actual SQLite connections race → one submit/at most one raw call; nonce/digest/owner/input/policy/backend/generation/expiry mismatch; claim commit-then-raise, accepted-start-then-raise, reserved-before-call crash, lost response, repeat approval and pre-task-row cancel latch. First observed error remains authoritative; ambiguity never restores READY or retries. Legacy: `tests/task_runtime/test_local_backend.py`, `test_docking_execution.py`. |
| **C4a physical ownership P1**, after C2, before execution exposure | `src/docking/adapters/base.py`, `src/agent/tools/molecular_docking.py`, `src/docking/molecular_docking_service.py`, `src/task_runtime/docking_execution.py` (runtime scope injection if needed within approved seam). Implement spec §5.2 receipt from command reservation through late-spawn settlement; consume before service deletion and lease exit. | Extend `tests/test_docking_command_cancellation.py` and consent tests. Block spawn before handle return; cancel/timeout and surface failure while receipt pending: **no deletion/quarantine/history removal/input lease release**. Late suspended process never resumes; worker/process/readers/handles settle before deferred own-job cleanup exactly once. Cover refresh retaining scope, fast attachment race, POSIX pending spawn, Windows assign failure, cleanup/join failure and repeated shutdown. Failed settlement remains unresolved; not just a returned tool result or worker finally flag. Real platform skips are explicit. |
| **C0c/C3 ordinary entry**, after C0b/C1/C2/C4a | New `src/web/docking_request.py`, `src/web/routes/docking_consent_routes.py`; `src/web/decision_runtime.py`, `ordinary_capabilities.py`, `app.py`; consent/store/runtime seams from C1–C2. | New `tests/agent/test_web_docking_consent.py`: real ordinary `/ws` chat → AWAITING_INPUT draft → `docking_refine`/owned upload → same-task READY → explicit same-origin human approval. No standalone preparation without chat draft. Recheck whole query, independent numeric spans, owner/source/generation/revision; mixed/omitted obligations cannot execute. Persist/spend root proposal slots before dispatch, fifth proposal denied, missing durable counters fail closed; model work drains before actionable card, no model lease across human wait. Text “yes”/model proposal is never consent. C disabled, tools disabled, B1 and four-helper prohibition remain unchanged. Legacy admission/session selection below. |
| **C4b bounded feedback P2**, after C3 | New `src/web/docking_consent.py`; `src/web/decision_runtime.py`; existing runtime/binding owner retention seam only. `src/web/decision_chat.py` stays unchanged. | Extend Web consent tests: cancellation-resistant physical send **and separate close** barriers; single absolute 5s logical deadline returns undeliverable without draining either. Retain actual task/lock/resource owners; queued expired send does zero I/O; late completion cannot grant/publish/call success/release ownership. Close once only after send settles, no added close-wait budget. Cover deadline equality, receiver exit/repeated cancel/shutdown; release barriers in finally. Existing A2 drain tests must remain unchanged. |
| **C5 evidence/download**, after C2/C4a/C4b | Consent/runtime/execution and C Web route/projection files above; `src/task_runtime/routes.py`, `src/web/routes/docking_routes.py`, `docking_report_routes.py`; trusted tool/service private output-root injection from C4a. Reuse completion/secure-I/O unchanged. | New `tests/test_docking_consent_artifacts.py`: independently parse synthetic PDBQT finite energy/count/hash; deny forged/cross-job/tampered/nonfinite results, TOCTOU/link escapes, foreign/expired/old-generation grants. Owner authorization before refresh/open/cancel; generic task and legacy result/report/conversion/history paths cannot bypass C authority. Reader close/disconnect exactly once. Cancel/UI-timeout-before-late-success never upgrades; consent/writer/lease/reader/unresolved cleanup prevents orphan deletion. Synthetic evidence is not real Vina evidence. |
| **C6 inline UI**, after C3/C4b/C5 | New `src/web/static/js/home/docking_consent.js`; `src/web/static/js/home/main.js`, `src/web/templates/index.html`; C projection file only for actual route/DOM contract alignment. | New `tests/home_docking_consent_test.js`, extend Web consent tests. Feed actual route events to DOM assertions: original message owns clarification/card/progress/report; drained segment_complete releases composer, not scientific terminal. Explicit click only, escaped text, no persisted nonce, sticky cancel/timeout and pending-cleanup state, same-task reconnect and owner-bound download. No separate manual form counts as C entry. |
| **C7 optional backend preservation**, after C5 | Read/reuse `src/docking/sandbox_runner.py` and `src/sandbox_broker/*`; no SDK/service rewrite or activation. | Existing offline sandbox selection below: fixed backend, bounds before broker call, late create/destroy uncertainty keeps owner, no local fallback. Offline mocks do not establish physical remote cleanup. OpenSandbox activation/real reconciliation has a separate grant and release gate; initial local C cannot claim it. |
| **C8 required complete chain**, after C0–C7 | Tests only unless a failure receives a narrow corrective batch; no automatic scope expansion. | Web consent + artifact + DOM tests: ordinary request missing files/grid → clarification → verified sealed preview → human approval → one raw invocation → verified completion → original-message report → same-owner download → follow-up observation with zero new invocation. Root/query/task stable; input hashes absent before verification, then stable through READY/report/download. Unsupported upstream multistep obligations remain visibly waiting/unavailable. Manual HTTP positive alone fails this gate. |

## Validation commands and final gates

Future focused commands (run a row only under that batch's execution grant; append its legacy inputs from the table):

```powershell
python -m pytest tests/task_runtime/test_docking_consent.py -q
python -m pytest tests/test_docking_command_cancellation.py tests/task_runtime/test_docking_consent.py -q
python -m pytest tests/agent/test_web_docking_consent.py -q
python -m pytest tests/test_docking_consent_artifacts.py -q
node --check src/web/static/js/home/docking_consent.js
node --check src/web/static/js/home/main.js
node tests/home_docking_consent_test.js
```

Final **ordered legacy selection**, alongside all new focused tests, is C-specific, not a borrowed ASCII 49/B1 61 count. Freeze these exact inputs on the integrated head before the separately authorized run:

```powershell
$legacy = @(
  'tests/agent/test_ordinary_intent_protocol.py',
  'tests/agent/test_ordinary_intent_transport.py',
  'tests/agent/test_decision_transport_boundaries.py',
  'tests/agent/test_decision_protocol_recovery.py',
  'tests/agent/test_ordinary_semantic_admission.py',
  'tests/agent/test_ordinary_admission_budget.py',
  'tests/agent/test_ordinary_continuation.py',
  'tests/agent/test_ordinary_chat_policy.py',
  'tests/agent/test_ordinary_capabilities.py',
  'tests/agent/test_ordinary_app_assembly.py',
  'tests/agent/test_ordinary_web_runtime.py',
  'tests/agent/test_ordinary_web_lifecycle.py',
  'tests/agent/test_web_decision_runtime.py',
  'tests/agent/test_web_decision_runtime_lifecycle.py',
  'tests/agent/test_web_decision_runtime_references.py',
  'tests/agent/test_decision_chat_transport.py',
  'tests/agent/test_worker_ownership.py',
  'tests/agent/test_decision_binding_publication.py',
  'tests/agent/test_scientific_reference_store.py',
  'tests/test_agent_session.py',
  'tests/test_agent_session_entrypoints.py',
  'tests/agent/test_docking_tool_contract.py',
  'tests/agent/test_docking_contract_integration.py',
  'tests/task_runtime/test_staging.py',
  'tests/task_runtime/test_task_store.py',
  'tests/task_runtime/test_local_backend.py',
  'tests/task_runtime/test_docking_execution.py',
  'tests/test_docking_command_cancellation.py',
  'tests/test_temporal_docking_routes.py',
  'tests/test_docking_history_index.py',
  'tests/test_docking_pdbqt_viewer.py',
  'tests/sandbox_broker/test_service.py',
  'tests/sandbox_broker/test_worker_runner.py',
  'tests/sandbox_broker/test_api.py',
  'tests/sandbox_broker/test_validation_artifacts.py',
  'tests/sandbox_broker/test_security_contract.py'
)
python -m pytest @legacy -q
node tests/home_decision_runtime_test.js
node tests/home_agent_task_panel_test.js
node tests/home_workflow_completion_behavior_test.js
node tests/home_scientific_references_test.js
node tests/home_evidence_report_test.js
```

- [ ] Independent SOURCE and fresh QUALITY on the final integrated revision; required full CI at that same latest head. No earlier-head pass substitutes for it.
- [ ] Separately authorized real E08/E09/E11/E12 using spec §8's approved sample/backend/budgets and private roots: ordinary chat and human consent produce new real Vina evidence, independently parsed finite kcal/mol/pose hash, matching original-request UI/report/download; unavailable executable/input negatives; cancellation and physical cleanup proof. Required rows repeat three times on the same revision/assets only under that grant. No asset/config probing or real execution in this document task.
- [ ] Report C completion only when the required ordinary-chat chain and actual cleanup gates pass. C0a pure GREEN, C-exec-only GREEN, cooperative mocks, manual cards, backend-only runs and late cleanup claims cannot replace them. Preserve real failures and unresolved ownership as such.

**Actual C0a RED:** parent authorized exactly one node on branch `codex/docking-consent-integration`, HEAD `108df5d8acbdc1881a8f09c31b8b450acdfb247b`, before the production module existed:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py 'tests/agent/test_docking_preparation_protocol.py::test_minimal_proposal_is_not_execution_authority'
```

Actual result chunk `c70277`: `1 failed in 5.34s`, `ORDINARY_PYTEST_EXIT=1`, process exit 1; the call returned terminal directly, with no live session ID. Failure was `C0a docking preparation contract is absent` (missing-feature RED, not a reproduced behavioral vulnerability). Slot was explicitly RELEASED at terminal; no rerun or expansion. Before/after read-only checks found the same branch/HEAD, production absent, and all four pins unchanged:

| Input | SHA-256 before = after RED |
|---|---|
| `tests/agent/test_docking_preparation_protocol.py` | `E4C8A60DA67F6C37B9F11657B4E0BCA67C52948AC83551E2B2016F6DAEBAEC8E` |
| This plan at test-first freeze | `9A38ACD969CA9960E5A1BA7667AA7DB952089B5EB61D327B076BC36BB3C1E942` |
| Approved design | `97B61082201E2703BE460C3C592212ABA8E8E348B8FA3DD58FC04C5EB82FE0EF` |
| Ignored `scratch/ordinary_chat_offline_runner.py` | `4AE31180E73E9B4E49F676111CC8EE42EC1E632BAAC9D5285570493ECABF36EB` |

Runner bytes/target were read-only verified before this authorized invocation; parent supplied its earlier REPO-only forward/reverse byte comparison `6bbc46`. No runner was edited here. Only the selected node ran in this RED invocation; subsequent broader evidence is recorded separately below.

**Production SOURCE handoff:** new `src/agent/contracts/docking_preparation.py` SHA-256 `FD13F3ADC899F4D5B739DB0571AF051F68912768A0A6A6B7EFE36772BA29BE57`. It reuses `decode_protocol_json`, checks C depth/node bounds before model validation, provides the four reviewed APIs, and fixes public parse failures to the sanitized C error. Native closed DTOs perform structural checks only; no owner/source-text resolution or execution authority. Omitted fields remain absent in dumps and the schema admits neither explicit null nor inferred defaults. Parent independently read source/spec/test boundaries and accepted SOURCE before author GREEN. Tests `E4C8…` and spec `97B610…` remain unchanged.

### Actual five-module GREEN and independent QUALITY

Both runs used the frozen `4AE311…` runner and MedChat `-I -S -B`, with exactly this ordered selection:

```powershell
& 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -I -S -B scratch/ordinary_chat_offline_runner.py 'tests/agent/test_docking_preparation_protocol.py' 'tests/agent/test_ordinary_intent_protocol.py' 'tests/agent/test_ordinary_intent_transport.py' 'tests/agent/test_decision_transport_boundaries.py' 'tests/agent/test_decision_protocol_recovery.py'
```

| Evidence | Actual handle / terminal | Result | Input freeze |
|---|---|---|---|
| Author, directly observed | session `18256`, initial chunk `f5f8ea`, terminal `fe8a6e` | `733 passed in 16.46s`; runner/process exit 0; no warnings/skips reported; RELEASED | 498 files identical before/after, no retry or expansion |
| Independent Mencius fresh QUALITY, confirmed by parent | `79902` / terminal `49fe72` | `733 passed in 15.79s`, zero warnings/skips; PASS | Same 498 pins confirmed unchanged |

The 498-file freeze covered all tracked Python under `src/` and `tests/agent/`, the new DTO/test, test bootstrap/config, own plan/spec, runner and its three tracked synthetic evaluation fixtures. Hash coverage was broader than execution; only the five named modules were selected. Both runs concern baseline `108df5d8acbdc1881a8f09c31b8b450acdfb247b`, DTO `FD13…`, test `E4C8…`, spec `97B610…`, and plan **`C2BDF365CCDA6EE974A7BFB8220C3032F5D09FBF3D0148D6AA904F6726CCFF24` before this result-recording edit**. This updated plan does not retain that hash. Fresh QUALITY is parent-confirmed independent evidence, not a second author invocation.

**Meaning:** C0a pure DTO/protocol parsing plus these legacy regressions is accepted. No C transport/journal, durable root accounting, ordinary-chat admission, consent CAS, execution, Web/download wiring or physical-cleanup integration is implemented or proven here. No real model/service or scientific availability/latency claim; this is not C completion.

### Four-file publication preparation

Publish only these new files, subject to parent alignment and publication gates:

1. `src/agent/contracts/docking_preparation.py`
2. `tests/agent/test_docking_preparation_protocol.py`
3. `docs/superpowers/specs/2026-09-27-docking-consent-integration-design.md`
4. `docs/superpowers/plans/2026-09-27-docking-consent-integration.md`

Ignored runner/scratch outputs are excluded. Parent plans to align this tree from 108df5d to actual main `c90c180` (including PR98/101); this worker has not performed or verified that alignment. Re-pin the actual resulting head and incoming overlap before publication, and require latest-head CI; these 108df5d passes are not evidence of a run on c90c180. C0b remains a future separately released test-first batch, with no code changes in this turn.
