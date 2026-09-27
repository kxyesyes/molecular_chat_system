# P7-C Docking Consent Integration Implementation Plan

## C0b test preparation on isolated branch

Parent created `codex/docking-preparation-transport` in its own worktree at
46d70767bda6c9cd6041f56358b36c150d8ba71d, the exact C0a PR102 head. This is a
stacked preparation baseline, not proof PR102 landed; no C0b PR is published yet.
The approved C0b table below is the implementation scope. Only appended transport
tests and this checkpoint changed; C0a parser/tests remain an unchanged prefix.
No Python, collection, provider request or production implementation occurred.

The first missing-API RED will be `test_c_transport_api_present`, independently
SOURCE-reviewed before an explicit sole-slot grant. Subsequent tests use actual
HTTPX MockTransport through the real model wrapper and existing shared request
path, never replace that request with a proposal fixture. They require strict
native/JSON schema, request ID/attempt/stages, detached bounded journal, null ID
and zero posts on ALL pre-dispatch failures including payload/header construction,
post-dispatch invalid/timeout/cancel spent once, no retry/cross-profile fallback,
terminal journal immutability and invalid/reused journal rejection. Root durable
proposal counts, Web admission and scientific consent remain C0c/C3 requirements.

Implementation must preserve existing ordinary bind-before-validation behavior;
C binds only after options, whole history, required model configuration, payload
and headers are captured successfully. C prevalidation leaves stages created→failed,
not a fictitious validated/dispatch stage. The shared journal snapshot/size/sanitizer
mechanics may be reused, but ordinary semantics cannot change to accommodate C.
Retain the same five-module regression command; add actual concurrent three-profile
coverage and stronger journal boundary probes before final C0b release if SOURCE
identifies gaps. No C0b production code is currently authorized or implemented.

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

## C0b mandatory SOURCE-gap test preparation — 2026-09-27, NOT RUN

Parent explicitly released this reviewer to act as test-prep implementer ONLY:
append to `tests/agent/test_docking_preparation_protocol.py` and update this plan.
The current branch remains `codex/docking-preparation-transport`, HEAD
`46d70767bda6c9cd6041f56358b36c150d8ba71d`. Mencius owns the sole Python slot.
No Python, import, collection, compilation, test, production edit, runner edit,
real network request, staging or commit occurred. Initial API RED is still NOT RUN.

This checkpoint makes the earlier preparation's conditional "if SOURCE identifies
gaps" obligation unconditional: all following coverage is mandatory before final
C0b release. Authored assertions are not proof of behavior or independent approval.
Another reviewer must review this freeze; the author must not self-approve it.

Added twelve test functions (parameter counts remain uncollected), retaining all
previous tests and reusing the existing C helpers and ordinary transport's literal
`INTENT`/`DECISION`/`MESSAGES`, `wire` and journal helpers. No generic test framework:

- Valid C arguments with only the native function name wrong, separately from
  correct C function plus ordinary/decision envelope in both wires. Complete
  decision-native history is first accepted by the real decision history checker,
  then rejected by C with null ID and zero posts in both wires.
- Actual asyncio deadline and explicit cancellation, both wires, with cooperative
  and cancellation-resistant HTTPX MockTransport handlers. The latter returns a
  valid response only after real cancellation reaches it: no success/parsed stage,
  one spent attempt, timeout/cancelled terminal, and no subsequent parsed overwrite.
  Events coordinate entry/interruption/release; no fabricated ReadTimeout or clock.
  The existing 0.05s deadline/2s control bounds match the ordinary transport tests.
  Every handler/task is released and joined in finally; no worker/process is used.
- Three actual profiles share one model/MockTransport and real ModelRequestGate;
  separately release each request, assert distinct IDs/DTOs/schema routing/history,
  reader counts and writer exclusion until all three settle. Ordinary prevalidation
  retains its non-null bound ID; C retains null. Additional payload/header probes
  observe created/null *during* construction, not merely after failure cleanup.
- Real-request journal descriptor 256/257 boundaries, URL/credential redaction,
  ignored raw body/usage/header material, dependency log suppression with an
  outside-request positive control, and nested detached snapshot mutation.
- Invalid/skip/non-native stages, wrong request ID/status/error type and rebind
  leave records unchanged and errors sanitized. Foreign, ordinary and subclass C
  journals cannot dispatch or mutate; foreign methods are never invoked.
- Direct existing journal-mechanism seams `_bind`, `_replace`, `_record` exercise
  exactly 8 versus 9 entries and 8192 versus 8193 encoded bytes, atomically. The
  seeded history and padded record are defensive primitive tests, NOT legal
  seven-stage lifecycles, accepted oversized descriptors, or durable root evidence.

Test SHA256: `226C3BA667C8E992A55B926639F54E2192550A856942006C80B1D2CA76FD57F4`.
Read-only static checks: the original 582-line C0a prefix matches HEAD exactly;
the entire prior 825-line file prefix hashes to
`E4D607DB7E2189A283B4EF959EDFA7939A5D50F0A716A2948EA0D03131EF8061`.
This amendment adds494 lines after that prefix; `git diff --check` is clean.
Only test/plan differ from HEAD. No syntax, collection, RED, GREEN or pass count
is claimed. The initial `test_c_transport_api_present` remains the first proposed
single-node API RED after independent SOURCE and explicit sole-slot transfer.
Full five-module GREEN/fresh QUALITY, two-file implementation SOURCE, and parent
release remain separate gates. C0c/C3 durable proposal limits, admission/consent,
physical model lease integration and scientific execution are not proven here.

Parent subsequently reported ignored-runner preparation: REPO-only substitution
from6D490 with full forward/reverse equality at terminal366c1e. This amendment
independently verified runner SHA256
`3761E8976C9DA239340AF31675950ED81F3FE1BEBCE0B170B2B4A74FFF20A755`
without changing or executing it. Parent also reports PR102 merged at18ad4f9,
reviewed/landed tree751bd047 equal and nine CI checks passed: C0a evidence only,
not C0b. This editing tree stays at46d7076; no alignment occurred. Parent owns
later patch preservation/alignment and fresh input re-pinning before execution.

## C0b accepted API RED and two-file implementation SOURCE handoff — NOT GREEN

Parent reports the authorized exact initial API RED at direct terminal `d56fee`:
`test_docking_preparation_protocol.py::test_c_transport_api_present`, **1 failed
in2.79s**, exit1, call-phase missing `DockingPreparationResponse`. No live task;
parent released the slot. All7 pins were unchanged: test226C, plan7FDA, spec,
C0a contract, two preimplementation production files, and runner3761. This is
missing-API evidence only, not reached transport/deadline/journal behavior.

Parent subsequently authorized ONLY `src/agent/decision_transport.py`,
`src/agent/openai_compatible_model.py` and this checkpoint. This implementation
did not run Python/import/compile/collection/tests, make real requests, change
tests/C0a contract/runner, align the tree, stage, commit or push. HEAD stays46d7076.
G3 work and its independently granted slot do not authorize execution here.

- Explicit third enum/function/schema/parser selection and frozen response DTO;
  C uses the existing strict C0a parser and closed role/content history. Native C
  calls require exact id/type/function and name/arguments shapes. No retry/fallback,
  separate client, root store, Web admission, consent or scheduler was added.
- One thin model wrapper delegates through `request_docking_preparation` to the
  existing `_request_protocol -> _post -> _read_post` chain, default256 tokens/30s.
- C's exact journal/profile guard runs before use; foreign/subclass/reused records
  fail without mutation/dispatch. Options/history/config/build failures retain
  null ID, zero attempts and created→failed. Payload/headers are captured before
  C allocates/binds the UUID and records validated/dispatch_started. Ordinary's
  early UUID/bind and validated-before-build ordering remain in the legacy branch.
- C journal reuses IntentJournal snapshot/8-entry/8192-byte/transition mechanics
  and existing allowlisted metadata sanitizer; its identities, digest, revision,
  phase and protocol are C-specific. A C-only null-ID failed transition is allowed;
  terminal stages cannot advance/rebind. IntentJournal's fixed error string is
  now a class constant so C can use its own fixed error without duplicating logic.
- C alone rechecks its dispatch deadline after the actual shared HTTP operation
  and after parsing: cancellation-resistant late success cannot become parsed.
  Explicit cancellation still propagates with a spent/unknown journal, with no
  detached worker or timeout increase. This is source intent, not executed proof.

Implementation SHA256 freeze:

| File | SHA256 |
|---|---|
| `src/agent/decision_transport.py` | `802999F93316D0875E9110600464DD3FF49CD7D7E4E59F8E1174FDA42EC03BD0` |
| `src/agent/openai_compatible_model.py` | `7F36FF02A1237E1C704D9BB3EBE6C0E33ECA873E1C35EFB136C0201C777A8250` |
| Frozen tests | `226C3BA667C8E992A55B926639F54E2192550A856942006C80B1D2CA76FD57F4` |
| Unchanged C0a contract | `FD13F3ADC899F4D5B739DB0571AF051F68912768A0A6A6B7EFE36772BA29BE57` |
| Unchanged ignored runner | `3761E8976C9DA239340AF31675950ED81F3FE1BEBCE0B170B2B4A74FFF20A755` |

Static checks only: `git diff --check` clean; direct HEAD/source substring equality
for `_journal_metadata`, `_validate_options`, `_parse_response`, and the entire
`_read_post`/`_post` block. Removing only the new model wrapper in memory restores
the original model file exactly. Legacy payload instruction/description/schema
strings and model-payload construction order were reviewed without executing.
No GREEN or independent approval is claimed. Stop for independent implementation
SOURCE; parent alignment/re-pinning and separate exact five-module GREEN/fresh
QUALITY grants remain outstanding. C0c/C3 durable counters and consent/execution
activation remain separate, unimplemented gates.

## Wegener two-P2 regression preparation — SOURCE freeze, NOT RUN

Parent reports independent implementation SOURCE **NOT READY**, with two P2
hypotheses. Parent authorizes tests/checkpoint only before targeted RED; production
remains frozen at802999F/7F36FF. No Python, import, compilation, collection, test,
network call, production correction, tree alignment, commit or push occurred.
The earlier one-node missing-API RED does not reproduce either new issue.

Read-only inspection of the installed MedChat dependency sources establishes the
relevant paths, not runtime reproduction:

| Installed source | SHA256 | Relevant source behavior |
|---|---|---|
| `Lib/site-packages/httpx/_client.py` | `C43F941BAEFE58C91E96D00039E1868FE719D91453026D7DB1647194563BFF8D` | build_request:340-389 merges headers and constructs Request; AsyncClient.stream:1570-1583 builds before send |
| `Lib/site-packages/httpx/_models.py` | `E3FFC6BB2BF580BC6E6428708A6F247D036220E709F5A72B785392BABBD97E6B` | _normalize_header_value:74-82 encodes str headers with default ASCII |
| `Lib/site-packages/httpx/_content.py` | `2C61B3AC94D1DCEBCDE0C6F519554D2D7917247FBAA0A97002DB4EF69E70FF28` | encode_json:176-179 uses ensure_ascii=False followed by UTF-8 encoding |
| `Lib/asyncio/tasks.py` | `B3BCCB5346D370059D20191CFEC96E15BB07442C3AAE9CE9C717190A8D8920C7` | wait_for:432-435 catches external CancelledError then returns fut.result() if the child is already done |

The installed HTTPX version source declares0.28.1. MockTransport's
`handle_async_request`:29-43 awaits request.aread, invokes its handler and accepts
an immediate Response. No dependency or stdlib code is patched by these tests.
Current production binds at decision_transport:586 and increments attempts:595
before _post's local HTTPX encoding; wait_for is at:599 and parsed publication:627.

Appended two test functions, retaining the entire prior1319-line226C test prefix:

1. `test_c_actual_httpx_encoding_precedes_request_binding`, both wires, covers
   non-ASCII synthetic authorization and an unpaired surrogate in model_name,
   plus a normal request positive control. Real _payload/_headers must succeed;
   a separate real client.build_request probe must raise UnicodeEncodeError with
   the expected ASCII/UTF-8 encoding before any MockTransport post. The actual C
   model call must then return a sanitized failure, null request/journal ID,
   zero actual posts/attempts, created→failed and completion=not_started.
   No synthetic builder exception replaces the installed encoding seam.
2. `test_c_external_cancel_at_completed_http_child_is_not_lost`, both wires,
   schedules one external cancellation callback just before returning a valid
   immediate HTTPX response, plus a no-cancel positive control. Callback facts
   must demonstrate a completed, noncancelled child distinct from the pending
   outer awaiter and an accepted outer cancellation. Assertions run in the test,
   not silently inside a loop callback. The caller must receive CancelledError;
   exactly one spent attempt and terminal cancelled/unknown/interrupted remain,
   with no parsed stage or later parsed overwrite. Child and outer tasks are
   retained/joined in finally. No sleeps, patched wait_for or fabricated journal
   transition is used to create the race; the postterminal negative assertion is
   separate from the actual response/cancellation observation.

Test SHA256: `D2EF41A76F765DEC2A320AFE0F7440EE4FE7C4D9B010ADE4A11D90DF7037F8BC`.
Static prefix SHA remains
`226C3BA667C8E992A55B926639F54E2192550A856942006C80B1D2CA76FD57F4`;
production hashes remain802999F/7F36FF; git diff --check is clean. These are
unexecuted desired-contract tests, not confirmed failures or an implementation fix.
Ordinary/decision semantics and tests are unchanged. SOURCE review of this freeze
must precede a parent-granted targeted RED selecting exactly the two new functions;
no module-wide run or production edit is authorized by this checkpoint. Subsequent
fix authority remains limited to the two approved production files and own plan,
only after parent accepts actual reproduction. No C0a contract change is needed
or made for these two hypotheses.

## C0b two-P2 correction — implementation SOURCE handoff, NOT GREEN

Parent reports actual targeted RED, direct terminal `324328`, exit1:
**6 failed, 4 passed in1.58s; 658 before/after pins unchanged**. This is parent
execution evidence, not a fresh run by this implementer. Both named regression
functions ran (10 parameter cases); the actual encoder/race prerequisites passed:

- Four encoding-fault cases (two faults, both wires) passed real HTTPX
  UnicodeEncodeError/no-POST checks, then failed at test:1370 because actual
  request_attempts was1 rather than0.
- Two external-cancel cases (both wires) passed every completed-child/callback
  race fact, then failed at:1424 because the outcome was a successful
  DockingPreparationResponse, not CancelledError.
- Four positive controls passed. Parent reports terminal slot release.

The subsequent implementation grant permits only decision_transport.py and this
checkpoint. No Python, import, compile, collection, test, network call, alignment,
commit or push was performed here. No extra test or production module was edited.

Source correction, C profile only:

1. The existing shared _post/_read_post path selects a private C stream context.
   It builds the actual HTTPX Request with the same client, headers, JSON,
   timeout and identity encoding. Only after build_request succeeds does the
   transport-local synchronous dispatch closure bind the ID, record validated
   and spend the one attempt. client.send sends that same Request once, with
   stream=True/follow_redirects=False; there is no probe/rebuild or second POST.
   Pre-dispatch errors retain null ID/zero attempts and fixed sanitized reasons.
2. C retains an explicitly owned HTTP task and waits with asyncio.wait against
   one unchanged absolute deadline. External cancellation cannot return a done
   child's value. Cancellation/expiry is latched before cancellation and joining
   of the actual child; late values and child failures are consumed, not parsed.
   Repeated outer cancellation is retained without repeatedly cancelling or
   detaching that child. Cancellation during an expiry drain still propagates.
3. Returned live HTTPX response streams delegate iteration unchanged and protect
   their underlying asynchronous close with an owned, joined cleanup task.
   This also covers HTTPX's implicit EOF close: HTTPX sets is_closed before
   awaiting stream.aclose, so merely retrying response.aclose after interruption
   is insufficient. Buffered/closed responses add no cleanup scheduling point.
   The same small join helper protects closure of a C-owned client on build,
   send, read and cancellation exits; a borrowed client is never closed here.
   HTTPX retains its native send-error cleanup before a response is returned.
4. Terminal cancellation/timeout is recorded only after the owned HTTP task and
   its cleanup settle. A pre-dispatch interruption records failed/not_started
   with null ID rather than inventing a spent attempt; after dispatch it records
   cancelled/timeout and unknown completion. The fixed deadline is checked
   before binding, after HTTP settlement and after parsing. Physical cleanup is
   joined, not claimed to finish within the logical request deadline.
5. Ordinary/decision paths keep their original bind/dispatch order, client.stream,
   asyncio.wait_for, timeout and cleanup behavior. All profiles still use one
   shared HTTP status/encoding/byte-bound validator and one response parser;
   no public callback API, consent authority, scheduler or fallback is added.

SOURCE SHA256 freeze:

| File | SHA256 |
|---|---|
| `src/agent/decision_transport.py` | `1B4AFC38C69B2D1BA484DBBE552219BE4941F53F189C864E3A77C2A59FFBE19A` |
| Unchanged `src/agent/openai_compatible_model.py` | `7F36FF02A1237E1C704D9BB3EBE6C0E33ECA873E1C35EFB136C0201C777A8250` |
| Unchanged frozen tests | `D2EF41A76F765DEC2A320AFE0F7440EE4FE7C4D9B010ADE4A11D90DF7037F8BC` |
| Unchanged C0a contract | `FD13F3ADC899F4D5B739DB0571AF051F68912768A0A6A6B7EFE36772BA29BE57` |
| Unchanged approved spec | `97B61082201E2703BE460C3C592212ABA8E8E348B8FA3DD58FC04C5EB82FE0EF` |
| Unchanged ignored runner | `3761E8976C9DA239340AF31675950ED81F3FE1BEBCE0B170B2B4A74FFF20A755` |

Static checks only: git diff --check is clean; in-memory normalized text
comparisons against HEAD confirm _journal_metadata, _validate_options,
_parse_response and the shared _read_post response-validation body are unchanged.
Protected-file hashes above were rechecked. Branch remains
codex/docking-preparation-transport, HEAD46d70767bda6c9cd6041f56358b36c150d8ba71d.
The 658-pin execution manifest is parent-reported, not independently re-run here.

This is ready for independent implementation SOURCE review, not execution or
release approval. Existing pending/late and completed-child regression tests
remain frozen. Repeated cancellation during real body/client cleanup is a source
reasoning check here, not claimed executed coverage. No GREEN, fresh QUALITY,
new timeout guarantee or production activation is inferred. Await parent review
and a separate exact execution grant before any Python.

## C0b construction-time guard contract — TEST/DESIGN SOURCE, NOT IMPLEMENTED

### Actual pre-return-close RED and authority boundary

Independent Lovelace SOURCE found a remaining P2 in transport1B4A: the response
stream is protected only after client.send returns, but HTTPX response-hook or
redirect-processing errors can call response.aclose before that return. Installed
HTTPX _client.py:1693-1715 invokes response hooks and catches their exceptions to
close; _models.py:1065-1076 sets is_closed before awaiting the underlying stream.
Cancelling/joining the HTTP child therefore need not finish the physical close.

This is now reproduced, not merely a source hypothesis. Parent reports direct
terminal `cf929d`, exit1: **8 failed, 2 passed in2.95s; 5 pins unchanged**, against
test `C5D13C7453E0EDD20DA37D366D6A827C2AEE04E0B4DE85782F7DFB7FA6276CFB`.
Both no-interruption hook-error controls passed. Every interruption case passed
the actual HTTPX pre-return facts, then failed at old test:1522 because the outer
request settled before physical cleanup. The live stream re-raises cancellation;
it does not simulate a successful close. Earlier324328 six-failure RED remains
valid history and is not superseded by this different defect.

Parent approved the following bounded compatibility design and TEST+ownplan
preparation only. Production transport1B4A and model7F36FF remain unchanged.
No Python/import/compile/collection/test/network, implementation, commit or push
occurred in this preparation. Parent owns all execution grants.

### Exact interface and ownership contract

The only new public production symbol proposed is:

```python
async def docking_preparation_response_guard(response: httpx.Response) -> None:
    ...
```

This is a construction-time HTTPX response hook, not an installer accepting a
live client and not an authorization token. The existing model method signature,
wire schemas, pure C0a module, ordinary profiles and chat APIs remain unchanged.
Only decision_transport.py needs future production changes; no client subclass,
new scheduler, replacement response parser or transport framework is proposed.

| Boundary | Required behavior |
|---|---|
| C-owned client | Construct the existing real AsyncClient with the exact guard as its first response hook; retain its existing timeout/follow_redirects options and close ownership. |
| Borrowed client | Creator explicitly passes event_hooks={"response": [guard, *existing_response_hooks]} when constructing its real AsyncClient. Existing hooks retain identity and order; request hooks/transport/auth/options are not silently replaced. |
| Borrowed admission | C checks exact callable identity at response-hook index0, without changing the client. Absent or misordered guard yields INTERNAL_ERROR with details exactly {"reason": "docking_preparation_client_not_opted_in"}, null request ID, attempt0, zero POST and created->failed/not_started. Do not close the borrowed client. |
| Ordinary/decision/chat | No guard-admission requirement. Their existing request/bind/wait_for/error/cleanup behavior is retained. A registered guard must be a no-op outside the matching C request. |
| Request-local owner | A private ContextVar carries a per-call owner tied to the exact built Request identity, not a global active flag/model field. The guard must not adopt a foreign Request in a nested hook even if it inherits the same context/task. Reset the token after all owned work is joined. |
| First response guard | Before its first suspension, associate/protect the live response stream for that matching owner, before any later user hook can fail/read/close it. Do not inspect/copy/log the body or alter status, headers, decoding or validation. |
| Cancellation/expiry | Continue to cancel the actual HTTP operation under the same absolute deadline; do not shield the entire send. Protect and retain actual close tasks, consume failures, and join them under repeated cancellation before outer completion. A flag or cancelled task is not physical close success. |
| Publication/accounting | Same actual build-before-bind Request, one spent attempt after dispatch, no refund/retry/additional POST, no late parsed value. Preserve null-ID predispatch and sticky spent/unknown cancellation/timeout journal semantics. |

Concrete borrowed construction example (not production added in this checkpoint):

```python
client = httpx.AsyncClient(
    transport=existing_transport,
    event_hooks={
        "request": existing_request_hooks,
        "response": [docking_preparation_response_guard, *existing_response_hooks],
    },
)
model = OpenAICompatibleModel(api_key, model_name, base_url, client=client)
```

Compatibility is intentionally tightened for this new C feature: an arbitrary
existing borrowed client is no longer silently accepted for C. It remains usable
unchanged by ordinary/chat. The default Web model constructor at src/web/app.py:494
does not inject a borrowed client and uses the owned branch. No migration of all
chat clients or request-time insert/pop of shared hooks is allowed.

The creator must keep the guard first and unchanged throughout client use; later
hooks must not remove/reorder it, replace the protected stream or launch detached
close work. Custom auth/transport/hooks must preserve the one-request contract
and normal HTTPX ownership. This is not support for arbitrary subclasses or
extensions that fail/leak before the first response hook. A first-hook check is
not a sandbox or proof about arbitrary caller code. No-hook borrowed clients
also require opt-in: HTTPX may process a redirect location even when following
redirects is disabled. Public auth runs after response hooks and cannot replace
this boundary. Cloning a borrowed client or shielding the whole send is rejected.

### Test preparation and preservation of unsafe RED

The test-local c_opted_in_client helper constructs the same real httpx.AsyncClient,
using the supplied transport/options unchanged, and passes a copied hook mapping
with the exact guard first and existing response hooks behind it. It does not
patch any request-time code or supply an ownership algorithm. c_guard_api looks
up the new symbol only when a test/helper is called, so missing API is a call-phase
assertion, not an import/collection failure.

Exactly17 existing C client constructor calls were mechanically changed from
httpx.AsyncClient to this explicit helper. Reversing those names in memory and
excluding the appended new definitions restores **all74081 original C5D13 bytes**
and its SHA256. This includes every original C0a/D2EF assertion and all ten
pre-return cleanup parameters. No event, timeout, close barrier, race prerequisite,
terminal assertion or teardown was changed. The migrated cleanup test must now
reach held physical close through an opted-in real client; early refusal cannot
pass its close_entered/actual-request prerequisites. The old C5D13 unsafe-client
RED is retained above as historical evidence, not represented as a passing test.

New test sources (13 parameter cases by static count; not collected):

- test_c_construction_response_guard_api_present: async public hook exists at call
  phase; exact initial API RED node below (one case).
- test_c_uninstrumented_borrowed_client_refuses_without_changing_ordinary: absent
  and misordered guard, both wires; exact null/zero/refusal details, no hook or
  POST before refusal; same original client then successfully handles ordinary,
  decision and chat with unchanged hook list identity/order (four cases).
- test_c_constructor_guard_three_profiles_keep_request_local_close_ownership:
  three concurrent real HTTPX requests, two preserved later hooks, same transport,
  distinct request IDs; real EOF close in an owned task for C but original HTTP
  task for both legacy profiles; client/hooks unchanged (both wires).
- test_c_guard_does_not_adopt_foreign_request_inside_response_hook: actual nested
  GET in a C user response hook shares its task/context but not Request identity;
  its physical close stays unwrapped. Also directly checks a no-context no-op
  without reading/replacing its stream (both wires).
- test_c_owned_client_installs_guard_at_actual_construction_and_closes: constructor
  observation requires production to provide the guard itself; observer adds
  only a MockTransport, delegates to the actual HTTPX constructor and confirms
  one POST/one constructed-and-closed owned client (both wires).
- test_c_two_opted_in_requests_do_not_share_cleanup_owner: two concurrent C hook
  errors reach separate held closes; repeated cancellation of left cannot detach
  it or block/steal right's cleanup; independent spent terminal records, actual
  task joins, no parsed stages (both wires).

The constructor observer replaces only decision_transport's HTTPX dependency
reference with the real attributes plus the observing constructor. It never
globally patches HTTPX or installs a missing guard. The nested foreign GET is
test-hook behavior, not a new production request or fallback.

### Ordered gates; no execution granted here

- [x] Record actual cf929d evidence and explicit borrowed compatibility boundary.
- [x] Prepare opt-in fixtures and tests while production1B4A/model7F36FF remain frozen.
- [ ] Independent SOURCE review of this test/design freeze.
- [ ] Parent grants/runs exactly the new call-phase API RED node:
  `MedChat python -I -S -B scratch/ordinary_chat_offline_runner.py tests/agent/test_docking_preparation_protocol.py::test_c_construction_response_guard_api_present`.
  Desired failure is missing docking_preparation_response_guard, not collection
  or a fixture/setup error. Record the actual handle/result/pins; do not infer it.
- [ ] Only after accepted API RED and new implementation authority: minimal
  decision_transport-only guard/owner/admission correction, retaining shared
  parser/privacy and fixed deadline; independent implementation SOURCE handoff.
- [ ] Separate parent-granted GREEN must include migrated physical-cleanup tests,
  new opt-in/refusal/isolation cases and previous encoding/completed-child races.
  A refusal pass is not cleanup proof; full five-module/fresh QUALITY remain
  separately authorized gates. No runtime grant, consent or release follows here.

Current test SHA256:
`4E42157BA756C80B591703E3C6E17FE746E1C3AF4042BC5DD3DDBD68093601D0`.
Production remains
`1B4AFC38C69B2D1BA484DBBE552219BE4941F53F189C864E3A77C2A59FFBE19A`;
model remains
`7F36FF02A1237E1C704D9BB3EBE6C0E33ECA873E1C35EFB136C0201C777A8250`.
Static inverse-prefix verification and git diff --check passed. No executed
GREEN, collection count, independent QUALITY or production support is claimed.

## C0b construction guard implementation — SOURCE handoff, NOT GREEN

Parent accepted Lovelace SOURCE READY for tests4E42/design2F0684 and reports
actual API RED, direct terminal `2a5f0c`, exit1: **1 failed in1.65s; 5 pins
unchanged**. The exact guard-present node reached the missing callable guard
assertion at call phase, not a collection failure. This is parent-run evidence,
not a local execution by this implementer. The earlier cf929d physical-cleanup
RED and324328 encoding/cancellation RED remain unchanged historical evidence.

After that RED, parent authorized implementation ONLY in decision_transport.py
and this checkpoint. The interrupted turn was resumed under the same bounded
grant; no additional execution authority was inferred. No Python, import,
compilation, collection, test, network, commit, push or alignment occurred.

Implemented source changes:

- Public async docking_preparation_response_guard is installed as response hook0
  when the C-owned real HTTPX client is constructed. Borrowed C clients must
  already have that exact callable at index0. The C predispatch check reads the
  existing hook list only; absent/misordered/unreadable registration returns the
  approved fixed INTERNAL_ERROR reason docking_preparation_client_not_opted_in
  with null ID/attempt0, without creating an HTTP task, POST or closing the client.
- A private ContextVar holds a fresh _DockingResponseOwner for each C HTTP child.
  The actual built Request is attached before synchronous bind/dispatch. The
  guard compares response.request by identity with this Request; foreign nested
  requests and non-C profiles are no-ops, even with inherited task context.
- The matching guard installs the protected live stream synchronously, before
  any later user response hook runs. Post-send installation was removed. Native
  HTTPX hook/redirect-error cleanup therefore reaches the protected stream even
  when client.send raises without returning a response to _docking_stream.
- Each protected stream retains one actual close task. Repeated close entry joins
  the same task and checks its result; it neither retries nor treats is_closed
  as success. The existing cancellation-resistant join loop remains the only
  drain mechanism. A failed close stays a failed result even if a user hook
  caught its first exception. No entire-send shield was added.
- C _post finally checks/joins its retained stream, then closes only its owned
  client, then resets the ContextVar token using nested finally blocks. Thus
  stream failure does not skip owned-client closure or token reset. HTTP child
  interruption remains latched by _await_docking_post, with actual child drain
  before the caller records cancelled/timeout; late values cannot become parsed.
- Ordinary/decision client construction, bind order, stream and wait_for paths
  are unchanged. No shared hooks are inserted, removed or reordered during calls;
  no model/global active-request field, alternate parser, retry or scheduler was
  added. The approved custom-hook/extension compatibility boundary above still
  applies: guard order/protected stream must not be changed and work not detached.

Static verification, not execution:

- git diff --check is clean.
- In-memory normalized text comparisons against frozen1B4A confirm unchanged
  _journal_metadata, _parse_response, _await_docking_post, all of _read_post, and
  the complete _request_protocol tail from its fixed-deadline assignment onward.
  This includes its original absolute deadline checks, single-attempt dispatch,
  ordinary wait_for, error sanitization and terminal parsing publication.
- Model/tests/C0a contract/spec/runner hashes were rechecked unchanged; only the
  authorized production file and own plan were edited for this implementation.

| SOURCE freeze | SHA256 |
|---|---|
| decision_transport.py | `041699FCEFEB303E49F6B721EC2468BDAFB04559F2CB45AA47F4384F2F5FC7D2` |
| Unchanged model | `7F36FF02A1237E1C704D9BB3EBE6C0E33ECA873E1C35EFB136C0201C777A8250` |
| Unchanged tests | `4E42157BA756C80B591703E3C6E17FE746E1C3AF4042BC5DD3DDBD68093601D0` |
| Unchanged C0a contract | `FD13F3ADC899F4D5B739DB0571AF051F68912768A0A6A6B7EFE36772BA29BE57` |
| Unchanged spec | `97B61082201E2703BE460C3C592212ABA8E8E348B8FA3DD58FC04C5EB82FE0EF` |
| Unchanged ignored runner | `3761E8976C9DA239340AF31675950ED81F3FE1BEBCE0B170B2B4A74FFF20A755` |

Ready for independent implementation SOURCE review only. The opt-in physical
cleanup regression must still actually reach and finish held closure; refusal
tests are separate and do not prove cleanup. Parent-selected GREEN, five-module
regression and independent fresh QUALITY remain unexecuted gates here. No
production activation, scientific execution, timeout guarantee or final release
approval is claimed. Stop at this freeze pending SOURCE review and parent grants.

## C0b offline verification receipts — parent GREEN and independent fresh QUALITY

Parent reports independent Lovelace implementation **SOURCE READY** for the
041699FC transport freeze, unchanged model7F36FF/tests4E42, and plan4F3F82.
The following terminal receipts advance the preceding pending verification gates;
earlier RED and SOURCE records remain intact.

| Verification | Actual session / terminal | Result | Frozen inputs |
|---|---|---|---|
| Parent five-module GREEN | `33969 / 1a553e` | exit0; **855 passed in13.32s**; no warnings/skips reported | 9 before/after pins unchanged |
| Independent fresh QUALITY, Lovelace | `95857 / 97deb4` | exit0; **855 passed in13.02s**; no warnings/skips | 9 before/after pins unchanged; slot released |

These are exact execution receipts supplied by parent, including the independent
reviewer's fresh result; this plan-only update did not itself execute or rerun
either suite. Parent GREEN and independent QUALITY are separate runs and are not
interchangeable evidence.

Preserved preflight record: parent terminal `2204c6` stopped **before Python**
because the preflight used the wrong model path `web/models/ollama_model`.
The approved `src/agent/openai_compatible_model.py` matched its unchanged hash.
This was a parent preflight path typo, not a code/test failure or a Python run.

Scope: SOURCE and the two five-module offline verification runs support this
bounded C0b transport/preparation contract, including constructor opt-in and
cleanup regression coverage. They are **not full C acceptance**, real-provider
or scientific execution evidence, consent authorization, docking activation,
end-to-end ordinary-chat-to-report acceptance, or a physical-cleanup time bound.
C0c/C1-C8 and production activation remain separately gated.

This checkpoint changes ONLY own plan. Production transport remains
`041699FCEFEB303E49F6B721EC2468BDAFB04559F2CB45AA47F4384F2F5FC7D2`,
model remains
`7F36FF02A1237E1C704D9BB3EBE6C0E33ECA873E1C35EFB136C0201C777A8250`,
and tests remain
`4E42157BA756C80B591703E3C6E17FE746E1C3AF4042BC5DD3DDBD68093601D0`.
No Python, network, production/test edit, commit, push or alignment was performed.
Freeze these inputs for parent to prepare focused clean main-based integration;
no integration publication or widened C scope is authorized by this receipt.
