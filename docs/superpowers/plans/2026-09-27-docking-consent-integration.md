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

## PR #104 publication verification — 2026-09-30

Publication authority now follows the user's remaining-through-step8 instruction.
Exact `ebb2093` was independently re-reviewed without actionable blockers, and
the isolated, network-disabled five-module command above passed 855 tests in
18.42s (exit0). PR #104 targets main; no model or docking execution was enabled.

Initial CI run36596556172 failed its unchanged credential scan: the new privacy
test contained a literal synthetic credential matching the scanner pattern.
Local reproduction identified only that test file. The fixture now constructs
the exact same synthetic value at runtime; all redaction assertions and the
scanner remain unchanged. AST comparison confirmed fixture-value equivalence.
The original scan now passes, `git diff --check` passes, and the same five-module
offline regression passed **855 tests in16.13s**, exit0 (terminal8b8da9).
No production file changed in this correction. CI on the amended head remains
required; the initial failed run is not represented as successful.

### HTTPX encoder compatibility correction

Run36597065439 on6da2018 completed failed: Agent had two surrogate-fixture
failures,12319passed,1skipped; task-runtime independently failed downloading the
Temporal test server with HTTP524 (1568passed,7skipped). Static scan passed.
The Agent failures reproduce with the repository-pinned HTTPX0.25.2 installed
only in an ignored scratch target: focused RED2failed4passed. No installed
environment or dependency pin was changed.

Minimal design: the real client encoder is an independent pre-call oracle.
HTTPX0.25.2 escapes the surrogate in valid JSON; local0.28.1 rejects its UTF-8
encoding. Keep ordinary success and non-ASCII-header rejection unconditional.
For a successful probe, require exact model round-trip and dispatched-body
equality, one attempt/POST and the full successful journal sequence. For failed
encoding, require zero dispatch/attempts, null IDs and the exact sanitized build
error. No version skip, altered serializer, weakened scanner, production change
or real-provider acceptance claim is introduced.

Implementation/verification: only the existing encoding test changed. Independent
source review found no blockers. The same five-module offline regression passed
on both actual versions: **0.25.2:855passed,1 anyio pytest-rewrite warning,15.88s;
0.28.1:855passed,16.23s**, session40577 terminal59d8f2 exit0. The scratch wheel is
not tracked. Updated-head CI is still required; retain both earlier CI failures.

## C1 prepare/identity — bounded TEST SOURCE preparation, NOT RUN

### Workspace and grant

This batch is in branch `codex/docking-consent-preparation`, local HEAD
`ebb2093bf817bf375ba77f69c403a223dff0dd99`. That is the reviewed C0b dependency,
not a claim it is merged or published. Parent will prepare later focused clean
main-based publication/rebase; no original worktree is changed here. The C0b
SOURCE/RED/GREEN/fresh receipts and historical assertions above remain intact.

Parent confirmed only the C1 prepare/identity scope in the C1 table and design
sections3.1/4.1-4.3/5. This grant permits the NEW
`tests/task_runtime/test_docking_consent.py` and this appended checkpoint only.
No production, C0b tests, spec, runner, assets, configuration, weights or training
files were edited. No Python/import/collection/compile/test/network, commit, push
or tree alignment occurred. Parent owns the ignored runner's REPO-only derivation
from C0b3761 and its validation/pin; no runner path/hash is invented here.

### Exact test-facing C1 interfaces (proposed, absent production APIs)

The following small internal interfaces make the accepted scope executable in
tests. They are not HTTP routes, a new scheduler, consent approval or model APIs.

1. New `src/task_runtime/docking_consent.py` exposes:
   - `DockingConsentError(reason_code)`: fixed-code exception with `.reason_code`;
     its string contains that code, not input bytes, paths or raw lower errors.
   - `DockingConsentPolicy`: immutable trusted server policy with explicit
     `tool_policy_digest`, `adapter_contract_version`, `execution_backend`,
     `policy_generation`, `runtime_generation`, `vina_limit_seconds`, and
     `operation_limit_seconds`. Tests pass synthetic identity labels/digest and
     local/300/420; these are offline policy fixtures, never real installation,
     executable-identity, OpenSandbox availability or consent evidence.
   - `DockingConsentPreview.to_dict()`: a detached closed public projection.
     Its repr excludes the nonce. The only nonce-returning surface is the READY
     prepare result, not a factual store getter or a repeated prepare.
2. Existing `TaskRuntime.__init__` gains optional trusted C-only injection seams:
   `docking_consent_policy`, `consent_wall_time_ms` and `consent_monotonic`.
   Missing policy means C preparation unavailable, not tool probing or fallback.
   Clock injections support server TTL tests only; the preparation response
   deadline still uses the actual event-loop30s budget. No browser/model clock,
   policy, timeout or backend selection is accepted.
3. Existing `TaskRuntime` gets precisely this prepare-only entry:

   ```text
   async prepare_docking_consent(*,
       preparation_id, owner_session_id, revision,
       receptor_name, receptor_bytes, ligand_name, ligand_bytes, parameters)
       -> DockingConsentPreview
   ```

   Identity/owner are trusted server arguments resolving an EXISTING durable
   draft, not client-supplied authority. Revision must equal that draft. The
   method must not generate another task ID, call submit_docking, create a tasks
   row/event, select a backend, check readiness, or call a model/tool/executor.
   No **kwargs/execution override surface is part of this API. The four scientific
   parameter fields are explicit; no stager defaults are inherited by omission.
4. Existing `TaskStore` adds narrow internal factual/transaction seams:

   ```text
   reserve_docking_consent_draft(*, identity, policy, now_ms, monotonic_now)
   get_docking_consent(preparation_id) -> detached private dict
   expire_docking_consents(*, now_ms, monotonic_now,
                          runtime_generation, policy_generation)
   ```

   Reservation writes only docking_consents in the existing DB, with preparation
   PK/task uniqueness and transactional owner/global capacity. It receives IDs
   assigned by the later trusted C admission seam; it is not standalone Web
   admission. The getter is PRIVATE store authority, not C2's owner-bound public
   view. Expiry invalidates unused records without deleting live files or freeing
   pending/unresolved cleanup reservations. Calls/readbacks never slide expiry.
   PREPARING/READY transitions can stay private store implementation details;
   tests observe their durable results, not a copied transition algorithm.

### Record, preview and digest assertions

The reserved `identity` has exactly preparation_id, task_id, owner_session_id,
trace_id, origin_turn_id, query_digest, refinement_revision, admission_revision
and source_refs. `source_refs` contains the trusted receptor_ref/ligand_ref
identities. C1 preserves these; C0c/C3 must later prove admission, source ownership
and original-query completeness. Model proposal syntax alone cannot supply them.

The factual private record exposes identity, state, version, binding,
binding_digest, approval_nonce_hash, manifest_locator, expires_at_ms and
cleanup_state (other private timestamps/receipt columns may be required by the
existing design). Initial state is AWAITING_INPUT, with no binding digest/nonce
hash and no runnable task. READY follows complete verified staging. Store only
the stager's logical `input_manifest.json` locator plus task identity; resolve
locally through the existing secure stager, not caller paths. The pending/settled/
unresolved cleanup fact is separate from consent state.

The binding's fixed closed fields are spelled out in test expected_binding:
schema DockingConsent@1; the complete identity above; receptor/ligand records
{name,size,sha256}; ligand_mode=file; center/size/exhaustiveness/num_modes;
energy_range3.0; config_hash/input_hash/request_digest; tool_name=molecular_docking;
adapter/policy/backend/generation fields; issued_at_ms/expires_at_ms and the two
execution budgets. Bind only that record, not an arbitrary request dictionary.

Tests independently compute copied-file SHA256 and:

- config_hash: SHA256 of normalized four-field config's sorted, compact,
  ASCII-escaped JSON, matching the existing staging algorithm;
- input_hash: SHA256 of receptor size/hash and ligand mode/size/hash JSON,
  matching existing docking_execution._input_hash/_manifest_input_hash;
- request_digest: SHA256 of b"medchat-task-submission-v1\\0" plus the existing
  config_hash/ligand_mode/ligand_sha256/receptor_sha256 canonical mapping;
- binding_digest: SHA256 of b"medchat-docking-consent-v1\\0" plus the exact binding
  mapping, sorted/compact/ASCII-escaped UTF-8, allow_nan=False. In test source the
  prefixes contain a NUL byte escape, not the printed backslash and digit.

Preview has exactly schema, preparation_id, task_id, trace_id, receptor, ligand,
parameters, tool_policy, binding_digest, expires_at_ms and approval_nonce.
parameters includes normalized four inputs and fixed energy_range3.0. tool_policy
has label=molecular_docking/local, digest, execution_backend,
adapter_contract_version, vina_limit_seconds and operation_limit_seconds. No
owner session ID or absolute path is public. Preview dictionaries are detached.
Use a fresh cryptographic32-byte nonce encoded as64 hex characters; store SHA256
of its ASCII encoding, never raw nonce. This selects an encoding for the approved
256-bit nonce, not a new entropy threshold. Hash/nonce are absent until READY;
repeat/changed preparation of READY is rejected without re-stage or nonce reissue.

Fixed tested error reasons: consent_not_found (same missing/foreign result),
consent_revision_conflict, consent_expired, consent_invalid_input,
consent_not_waiting, consent_policy_unavailable, consent_owner_busy,
consent_capacity_full, consent_identity_conflict, consent_preparation_timeout,
and consent_cleanup_unresolved. HTTP status mapping remains C3 work, not a new
runtime dependency on Web response classes.

### Reuse and lifecycle constraints

Use the existing database initialization transaction/WAL and TaskStore database,
not another store; no runnable tasks entry is created before C2 approval. Reuse
DockingInputStager.stage/load_verified_locator and its portable names, hashes,
owned-directory/lease/secure deletion semantics. Existing same-task immutable
staging behavior must not change. Legacy submit_docking and non-C callers retain
their behavior; a server-fixed local C policy never invokes the Temporal selector.

The existing _stage_verified drains its writer on caller cancellation, and
_projection_exists_sync currently only checks tasks. Those helpers alone do not
prove C1: the prepare caller must finish cancelled/timeout while the real writer
remains retained by the existing TaskRuntime, and runtime.close must not release
that owner early. Register the operation before launching I/O. Do not cancel only
an asyncio wrapper and lose its thread. No new executor pool/service/queue is
approved. On cancellation/30s deadline, consent becomes REVOKED/non-approvable;
cleanup stays pending until actual writer/file access settles, then settled or
unresolved. Cleanup failure must keep capacity and make close report the fixed
unresolved error instead of false success. No late READY/nonce publication.

One unexpired preparation per owner and16 pending across the C runtime are the
spec limits, transactionally reserved in the same table. Include pending and
unresolved I/O/cleanup even when consent is REVOKED/EXPIRED. A new explicit draft
may reuse capacity only after settlement, not by evicting an old owner. Global
one-execution admission/raw execution are C2, not exercised by these prepare tests.
AWAITING_INPUT and newly issued READY each have their own non-sliding15-minute
TTL; now==expiry rejects. Monotonic elapsed time caps wall-clock rollback;
runtime/policy generation change invalidates unused records without renewal.

Existing orphan cleanup must treat READY consent or unresolved C writer/cleanup
as authority despite no tasks row. After actual writer settlement, only its own
verified stage may be deleted/quarantined through the existing secure path.
Foreign task stages and unmarked neighboring files survive. Do not hide a failed
delete by dropping its reservation. Existing _discard_staging's task-only absence
predicate requires the narrow C-aware ownership seam, not a second cleaner.

### Coverage and honest execution boundary

New tests are16 functions /76 parameter cases by static source count, NOT an
executed collection count. API and behavioral claims remain separate:

- test_c1_prepare_api_present resolves new APIs only inside its body. It first
  requires async TaskRuntime.prepare_docking_consent, then the three store methods
  and new module/types. No new-module import happens during collection.
- Real temp SQLite and real stager verify durable draft identity across reopened
  connections, independently copied-file/config/input/request/binding hashes,
  READY-only nonce hash, private/public separation and detached snapshots.
- Strict field/type/filename/byte boundaries include missing grid/options,
  nonfinite/bool/string/tuple numerics, extra parameters and execution overrides,
  file-only inputs, real25MiB exact/over cases, inherited100-byte portable basename
  exact/over cases, and spec parameter endpoints. No invented product threshold.
- Separate real TaskStore instances race from two worker threads/connections for
  one owner and the sixteenth global reservation. Identity collisions reject;
  equal scientific input hashes across owners do not share binding/nonce/task.
- Controlled TTL clocks verify just-before/equal expiry, wall rollback capped by
  monotonic elapsed time, runtime/policy generations and non-sliding reads; READY
  starts its TTL at issuance, not draft creation. Generation tests do not try to
  reserve new work under an invalidated generation.
- An actual staged writer is held by threading.Event AFTER real copy/publication,
  then performs a real late read before exiting. Cancel/repeat-cancel/actual30s
  deadline must return non-success while the writer and its files remain owned;
  owner/global slots remain occupied, orphan sweep cannot delete, runtime.close
  cannot finish early, late result cannot become READY, and only owned files are
  cleaned after release. A failed-delete negative keeps unresolved capacity.
- The writer's40s event escape and5/6/10/35s test watchdogs bound fixtures only;
  no production deadline is shortened or enlarged. Release/join happens in
  finally. The delete-failure injection is removed ONLY for teardown after all
  unresolved assertions, never counted as successful cleanup evidence.
- The runtime uses a real LocalTaskBackend with instrumented forbidden methods,
  a no-execution handler, explicit local config and real store/stager. Model,
  HTTP send, process launch, selector, broker factory and raw execution sentinels
  record actual attempted boundary calls and must remain empty. No input digest
  or synthetic fixture claims real molecular validity or scientific performance.

Multipart accumulation/body framing tests require the later C3 HTTP adapter;
C1 tests the bytes/closed parameters that adapter must bound before this call.
OpenSandbox's smaller ligand bound remains its separate release/selected-backend
gate, not permission to enable it in these initial-local tests. C0c/C2/C3/C4-C8,
human consent, claim/raw CAS, full ordinary-chat entry/report/download and real
scientific release remain mandatory and unimplemented by C1 preparation.

### Freeze and next gate

Test SHA256:
`43593EB7AF0A6032643B04EFB44DAB62A3270109564FBBC835DEEA7A07AAE227`.

Unchanged production reference pins:

| File | SHA256 |
|---|---|
| task_runtime/runtime.py | `667665B975E0FE808E208AEC3DFBE5A6C059232E7DE23D589A0344B1D0A2EB33` |
| task_runtime/store.py | `4585CBCC762AD84FB89401644018F54DC9BF7DEB09638F8EE3F2EDCA921EEC4A` |
| task_runtime/database.py | `2AAD54CF582A8F9D1F89F505F3B554EE4F0B68D013A01E10939796BF7E1CB73E` |
| task_runtime/staging.py | `FA9171512B6322C4F2F71B548962850DB6D14B4371A39C86BAB8963088DE2FD1` |

No existing test assertion was edited. New production docking_consent.py is still
absent. Static whitespace/source checks only; no syntax compilation or execution.
Independent SOURCE must review these exact interfaces, lifecycle assertions and
fixture validity before parent grants the one initial API RED node:

`tests/task_runtime/test_docking_consent.py::test_c1_prepare_api_present`

Expected missing-API failure is TaskRuntime.prepare_docking_consent at call phase,
not collection/import failure. This expected result is NOT an actual RED receipt.
Parent supplies the approved new-tree MedChat -I -S -B runner and exact pin before
execution. Only after actual RED and a new bounded grant may production C1 work
start in its existing allowlist. No implementation/runner/commit grant is implied.

### Parent actual C1 API RED receipt — implementation HOLD

Parent-reported direct terminal `f73b83`: **1 failed in 0.99s, exit 1** for
`tests/task_runtime/test_docking_consent.py::test_c1_prepare_api_present`.
The failure reached the test call phase at the missing
`TaskRuntime.prepare_docking_consent` API; it was not a setup or collection
failure. This is the actual initial API RED receipt, not evidence that the
remaining behavioral tests have run or that production implementation is ready.

Parent confirmed all **7 before/after pins unchanged**: test
`43593EB7AF0A6032643B04EFB44DAB62A3270109564FBBC835DEEA7A07AAE227`,
pre-receipt plan
`754EE143213BF898D3739195263BBED06C0C0C3BB6B8FA2EECF42F230528E7A0`,
approved runner
`A99BB658B70EAC9DB91F6BA59557D3F54ED0B91134AB16682DA06FA9FC0980B2`,
and the four production reference pins listed above.

Parent reports the Python slot released. This worker did not execute Python;
the present grant is this plan-only receipt append, followed by freeze.
Zeno's independent full-SPEC review remains in progress. Production
implementation is NOT released: await that review and a separate explicit
bounded implementation grant. Tests, production and runner remain frozen.

### Bounded C1 implementation SOURCE handoff — NOT GREEN, self-review blocker

Parent subsequently accepted Zeno's full C1 SOURCE review of test43593/plan754EE
and the D5FB receipt append, then explicitly granted implementation ONLY in
runtime.py, store.py, database.py, new docking_consent.py and this plan. The
actual initial API RED remains f73b83 above; no behavioral GREEN is inferred.
This batch uses that grant, not an older slot or publication authority.

Implementation prepared in the existing worktree, with no commit or alignment:

- database.py adds docking_consents and its owner index in the existing WAL
  initialization transaction. No runnable tasks/event row, second DB or queue.
- store.py adds trusted draft reservation, detached private facts, expiry, and
  private token-bound PREPARING/READY/revocation/cleanup transitions. Reservation
  uses BEGIN IMMEDIATE for preparation/task uniqueness and one-owner/16-global
  capacity. Pending/unresolved file ownership remains occupied after expiry.
  Ready bindings remain historical facts; no reverse transition/nonce recovery.
- docking_consent.py supplies the immutable local-only policy and private-repr
  preview, closed native input checks, binding construction and nonce-free public
  metadata except the one prepare response's raw approval nonce. The existing
  stager validates portable names, bounds and normalized config unchanged; input
  and request hashes reuse the existing execution/runtime algorithms. Only the
  approval nonce hash is stored. The separate private operation token is writer
  ownership, not human approval or raw-dispatch authority.
- runtime.py adds prepare-only orchestration. Its retained owner covers actual
  to_thread staging and verification; no wrapper cancellation substitutes for
  physical completion. A cleanup owner waits that actual worker, then uses the
  stager's existing lease/owned-tree discard with a token-specific store predicate.
  The generic orphan predicate additionally protects C records. Runtime.close
  drains retained owners and reports consent_cleanup_unresolved on uncertain
  deletion/storage, rather than releasing the capacity. READY files stay protected;
  no C2 approve/cancel/view/claim/dispatch or public HTTP surface was introduced.
- The single preparation deadline is30s; checks occur before/after phases and
  after SQLite lock acquisition before sealing. Late results cannot be returned
  as a successful preview. The following *return-boundary gap* remains a blocker,
  rather than being relabeled a passed deadline contract.

**Self-review SOURCE blocker (not executed):**
runtime.py `_abort_consent_preparation` currently awaits the durable SQLite
revocation marker via `_await_task_outcome` before the prepare caller returns.
This supplies the frozen test's immediate REVOKED fact and retains the actual
writer, but a blocked SQLite operation can extend caller latency beyond the
already chosen30s failure deadline. It therefore does not yet establish the
specification's finite logical return independent of SQL settlement. Do not
call this full-SPEC READY or silently relax the deadline. Independent SOURCE
must resolve the logical revocation vs durable receipt boundary; a deterministic
blocked-SQL regression/contract clarification would require a new test grant
because test43593 is frozen. No staging edit or new client/service framework is
proposed. The current source is frozen for review, not released for GREEN by
this handoff. API RED alone proves none of this behavioral ownership.

Additional conservative behavior for review: when stage throws before returning
a verified owned locator, cleanup cannot certify absence and retains an
unresolved reservation; it never guesses from a missing path. Existing private
stager quarantine/delete behavior is reused, not rewritten. Unresolved cleanup
is not auto-retried or converted to success by a second close.

SOURCE freeze SHA256:

| File | SHA256 |
|---|---|
| src/task_runtime/runtime.py | `E089B580479013902F6020F70D672AE58A480EA6430D5805E2FA2F854EA0FF35` |
| src/task_runtime/store.py | `1033921B70B31B7ABE6ADD23F183FEECE22AF27642E744A17C906862A388A1EA` |
| src/task_runtime/database.py | `7043CBCF85A8B5262CF3B5F857F15B56A8450C4F4E1299AC3750C2114F58E12E` |
| src/task_runtime/docking_consent.py | `80BC2E0FF7A5749F533D420B6242A6DB4DC2FAFA120706B33D4DF9B14F09EB0D` |

Test43593 and stagingFA917 remain byte-identical. C0b transport041699 and
model7F36 remain unchanged. Runner was neither edited nor executed. Only shell
source/hash/whitespace inspection was performed; git diff --check passed.
No Python/import/collection/compile/tests/network/assets/training/configuration,
commit or push was performed. The full C1 module and legacy staging/local/runtime
regressions remain required after SOURCE clearance and a separate Python grant;
all C0c/C2-C8/real-execution and production activation gates remain mandatory.

### C1 blocked-revocation TEST-FIRST amendment — SOURCE only, NOT RUN

Parent accepted Zeno's boundary review and explicitly released only this test
amendment plus this plan append. Production remains frozen at runtimeE089 /
store1033 / database7043 / contract80BC from the preceding full hash table.
No fix is included. Historical test43593 remains the pre-amendment pin, not the
current amended test hash; its previous API RED f73b83 remains intact.

Test amendment:

- The existing interrupted-writer test retains its REVOKED/pending assertion,
  nonce/binding absence, owner/global capacity, orphan, real late file read,
  cleanup success/failure and shutdown assertions. Only the timing of the first
  REVOKED observation changes: a transparent wrapper sets an event after the
  real store revocation returns, and the assertion follows that event, after
  caller completion but before releasing the physical writer. Caller completion
  alone is no longer represented as proof of a durable SQL write.
- New test `test_c1_blocked_revocation_returns_before_receipt_and_retains_owners`
  has six combinations: cancel/repeat-cancel/actual30s deadline crossed with
  SQL-first/writer-first release. It uses the real temporary SQLite TaskStore,
  a second Store for independent connections, actual stager copies, an actual
  post-release read, and real secure deletion with an observation-only wrapper.
- The revocation wrapper is installed on TaskStore, not just one instance:
  every caller/settlement/shutdown invocation records entry and waits a finite
  barrier BEFORE delegating to the unchanged SQL method. Returned/exited events
  distinguish actual method completion from an asyncio wrapper. This is a held
  real-store call test, NOT a claim to hold a native SQLite write lock.
- Before the expected caller-return RED assertion, tests establish actual
  PREPARING/pending state, no binding/nonce, a still-live writer and an entered
  revocation with no returned receipt. Caller is observed via asyncio.wait:
  observation timeout never cancels it into a false pass. Cancellation must
  propagate CancelledError; deadline must return consent_preparation_timeout.
  While SQL is held, the independent store must still report PREPARING, not an
  invented REVOKED projection. Other-connection owner/global reservations remain
  bounded; orphan cleanup cannot delete this task's files.
- Exactly one actual revocation invocation is required across caller cleanup,
  settlement and shutdown, including repeated cancellation of a close awaiter.
  No test fixture deduplicates calls or substitutes a successful receipt. The
  proposed future fix must share one retained revoke operation itself.
- With only SQL released, tests observe actual REVOKED/pending while the writer
  remains alive. With only writer released, tests observe its real late read
  and unchanged PREPARING while SQL is blocked. In either case shutdown, deletion
  and capacity release remain blocked. After BOTH complete, require actual
  REVOKED/settled, no binding/nonce, zero seal attempts, one exited SQL invocation,
  deletion only after both returns, intact unowned neighbor and reusable capacity.
- `test_c1_blocked_revocation_fixture_preserves_normal_success` is the seventh
  case: the same wrappers permit real READY, independently recomputed binding
  digest and nonce hash, one actual seal and zero revocation/deletion calls.
  This is a positive fixture control, not authorization from fabricated consent.

All barrier release occurs at the start of finally, before joins/assertions.
Teardown gathers the caller/close awaiters, invokes the real runtime owner drain,
then checks actual writer/SQL-call exit events. asyncio.run additionally joins
the default executor's actual threads on exit. The60s barrier escapes and5/35s
observation watchdogs are fixture bounds only; the production30s deadline is
not patched, extended or represented as a35s contract. No barrier escape is
accepted as a passing result. These cleanup paths have been inspected, NOT run.

Approved semantic boundary for the later fix: existing local aborted latch is
sticky and chooses the existing exception outcome immediately. Durable receipt
is separate, with proposed internal pending/confirmed/unconfirmed status; a held
or uncertain write is never reported as committed REVOKED. No public view/HTTP
schema or cancellation exception payload is added here. The tests observe the
actual behavioral boundary, not a fake receipt/status algorithm. SQL failure or
commit ambiguity cannot release ownership merely because the caller returned.

Explicit exclusion: these tests interrupt while stage is held BEFORE sealing;
they do NOT solve or prove the seal-in-flight/commit-then-raise/other-process
READY claim race. C2's cross-process authorization and subsequent full-C gates
remain mandatory. Local revocation is not a substitute for a durable raw fence.

Amended test SHA256:
`B4FEA807B4BDCBB91C34402AD7CBF2C03DEC32CF4A89652C29FF72543E9D7B20`.

Exact proposed targeted selectors (6+1 parameter cases, static count only):

```text
tests/task_runtime/test_docking_consent.py::test_c1_blocked_revocation_returns_before_receipt_and_retains_owners
tests/task_runtime/test_docking_consent.py::test_c1_blocked_revocation_fixture_preserves_normal_success
```

Expected negative on frozen production: caller remains waiting for the held
revocation after real call/DB/writer facts are established. This is an expected
behavioral RED, not an actual terminal receipt; neither failures nor the positive
control are claimed executed. Independent TEST SOURCE comes next, then only a
separate parent slot grant can authorize targeted RED. No Python/import/compile/
collection/tests/network, runner/production/staging edit, commit or push occurred.

### Parent actual boundary RED and minimal retained-revoke SOURCE repair

Parent-reported direct terminal `e87933`: **1 failed, 1 passed in6.59s,
exit1**, eight before/after pins unchanged. The selected sql-first/cancel case
reached the actual held-revocation facts then failed test line865 at
`prepare caller awaited blocked durable revocation receipt`; the normal-success
control passed. This is an actual behavioral RED, not a missing API/collection
failure. The other five interrupted parameters were NOT reported executed.

Parent then granted the minimal runtime/store repair, with B4FE tests and
staging read-only. This amendment changes only those two production files and
this plan; database7043 and contract80BC did not need changes.

- `_abort_consent_preparation` sets the sticky local aborted latch and creates
  at most one retained revocation task plus one settlement task. It awaits no
  SQL/worker I/O before returning to the caller. Caller cancellation and the
  original30s timeout keep their existing exception outcomes; no deadline change.
- The retained revocation task owns/drains the real to_thread store invocation.
  It records internal pending/confirmed/unconfirmed status. Caller, repeated
  cancellation, settlement and shutdown reuse that task; no second revoke or
  retry is started. Physical drain remains a shutdown/settlement obligation.
- Store revocation returns a boolean only AFTER the existing connection context
  commits successfully. Confirmation requires exactly one matching preparation/
  operation-token row and an actual REVOKED or EXPIRED state with no approval
  nonce hash. EXPIRED remains EXPIRED; no terminal state is reopened. Zero-match
  returns false; exceptions/commit uncertainty never produce confirmation.
- Settlement waits BOTH the original worker and the shared revocation task.
  With matching ownership but no confirmed receipt it does not delete files or
  free capacity; pending/unresolved records continue to protect orphan cleanup.
  An independent post-worker lookup finding no owned row for a rejected request,
  with no stage ever started, can settle that empty local owner, but does NOT
  relabel its revocation receipt confirmed. This preserves rejected duplicate/
  foreign/missing-request behavior without touching another writer's token.

Source-only freeze SHA256:

| File | SHA256 |
|---|---|
| src/task_runtime/runtime.py | `1B6D6D74845086910B8DC957C5F799FD9AEF111E598B0C4D94ECF40F7A85DA46` |
| src/task_runtime/store.py | `105E265C37E7A4274FFB2C11CE3181736AFA27ABE26EF97BEBE66C3AC0BE04BD` |

The previous blocker is addressed in SOURCE, not proven GREEN. Tests remain
`B4FEA807B4BDCBB91C34402AD7CBF2C03DEC32CF4A89652C29FF72543E9D7B20`.
Only static inspection/hash checks and git diff --check were performed. No
Python/import/collection/compile/tests/network, runner/staging edit, commit or
push occurred. Exception/zero-match receipt branches are not claimed separately
fault-injection tested by the reported boundary RED. Any additional tests or
semantic expansion require parent grant before changing this freeze.

Independent SOURCE precedes parent-authorized selected/full-module GREEN and
legacy regressions. Seal-in-flight/commit-then-raise authorization races are NOT
claimed solved; no C2 approve/view/claim/dispatch surface or activation is added.

### Parent full C1 module terminal and read-only filename diagnosis

Parent actual terminal `50394/7e1596`: **82 passed, 1 failed in99.88s,
exit1**, eight before/after pins unchanged, no warnings reported. All seven
blocked-revocation boundary/control cases passed. The sole failure was
`test_c1_reuses_existing_portable_filename_byte_limit[100]`: preparation raised
consent_invalid_input at runtime line188, followed by conservative
consent_cleanup_unresolved at close line328. This is NOT full C1 GREEN or C1
completion. Receipt faults (revoke zero/pre/post-commit throw, finish pre/post-
commit throw and real deletion failure), begin outcomes and seal-in-flight
remain outstanding; none is inferred passed from this module run.

Keep the reviewer's receipt distinction: physical deletion confirmed followed
by finish COMMITTED as settled can legitimately free durable DB capacity even
if the local close later sees an exception. Do not require a new cross-process
receipt-equals-commit framework. Unconfirmed revocation may remain conservative.

Read-only source findings (NO diagnostic execution or fixture correction yet):

- stager basename validation allows100 UTF-8 bytes; a separate practical-path
  check at staging.py2405 applies the240-character ceiling unconditionally.
- `_stage_under_lease` checks both the final task input path (539-546) and the
  temporary `.stage-<16hex>/inputs/<name>` path (551-569). Neither limit can be
  relaxed or bypassed by a filename-boundary test.
- This fixture uses `stage_root = tmp_path / 'stage'`, task ID `c-task-1` (8
  characters), directory `inputs` (6), receptor name100 ASCII characters. If T
  is the character length of the resolved tmp_path, final receptor length is
  T+123; temporary receptor length is T+138. Therefore this positive test needs
  T<=102 for BOTH paths to satisfy240. T in103..117 could pass the final-path
  check yet fail the longer temporary-path check. This is source arithmetic,
  not evidence of the actual T in run50394.
- The frozen runner nests its ordinary-offline temporary root into child TEMP/
  TMP without --basetemp. Installed pytest tmpdir.py builds pytest-of-user /
  pytest-N and a30-character node-name prefix plus numbered suffix. Its actual
  run root length and the lower ManifestError reason were not captured here.
  Windows path length is a plausible cause, NOT a confirmed diagnosis.
- Runtime sanitizes the lower stage/verify exception to consent_invalid_input.
  If stage started without returning a verified locator, settlement deliberately
  cannot certify deletion/absence and close reports cleanup_unresolved. That
  secondary error does not identify the original callee or justify a cleanup
  behavior change from this failure alone.

Proposed minimal next diagnostic, requiring parent test-edit/execution grant:
instrument ONLY this100-byte case with transparent wrappers that call the real
stager `_assert_practical_path` and stage methods, re-raise their original
exceptions, and report fixed callee/phase labels, existing sanitized reason_code,
basename byte count and numeric full-path character/encoded-byte lengths. Print
no full path, raw content or raw exception. Keep current tmp_path, assertions,
limits, cleanup and production unchanged. This can distinguish final/temporary
path rejection from a different filesystem/manifest failure. If confirmed,
`tmp_path_factory.mktemp('c1-name')` is the proposed fixture-only short-root
correction, retaining100/101-byte cases and unmodified practical-path checks;
it must not be applied before evidence or treated as a production fix.

Only this receipt/source-note append changed in this diagnosis. Production
1B6D/105E/7043/80BC, testB4FE, staging and runner remain frozen. No Python,
import/compile/test, retry, fixture/path/config edit, network or commit occurred.

### Original100-byte case: transparent TEST diagnostic SOURCE freeze

Parent explicitly granted diagnostic-test-only wrappers plus this plan append;
no fixture correction or production repair. The original parametrized function,
tmp_path root,100/101 names, assertions, prepare and close calls are retained.
Only the100 branch installs wrappers around the actual stager
`_assert_practical_path` and the existing observed `stager.stage` callable.
Each wrapper delegates unchanged arguments, returns the original result and
uses bare raise for the exact original exception. It neither repairs names,
shortens paths, changes limits nor substitutes error/success results.

Collected output is ONLY `C1_NAME_DIAGNOSTIC` plus JSON records with these keys:
callee, phase, reason_code, basename_bytes, path_chars, path_bytes. Callee/phase
are fixed labels; reason is an existing ManifestError whitelist code or fixed
ok/non_manifest_error. Counts are numeric UTF-8 basename bytes, path characters
and os.fsencode path bytes. The path-check record describes the actual candidate;
the stage summary describes this original case's final receptor candidate.
No path/name string, raw exception/representation, input bytes or secret is
retained in the records or printed. Observation/printing failures cannot replace
the callee outcome. Output is emitted after asyncio.run teardown even when
close produces the secondary conservative cleanup exception.

Exact parent-only diagnostic node:

`tests/task_runtime/test_docking_consent.py::test_c1_reuses_existing_portable_filename_byte_limit[100]`

Test SHA256:
`0C4176E3B455534DC6DD09F9821A9C0760D7293AF2D2A66243C042E73B044DB9`.

SOURCE inspection only; diagnostic NOT executed. A missing/inconclusive record
would not establish root cause. Do not apply the proposed short-root fixture
correction until actual callee/reason/length evidence supports it. Production
1B6D/105E/7043/80BC, staging and runner remain frozen; no Python/import/compile/
collection/test/network, production edit, commit or push occurred.

### Parent actual filename diagnostic and minimal fixture correction

Parent diagnostic terminal `313448`: **1 failed in1.14s, exit1**, eight pins
unchanged. Exact nonsecret recorded facts: task_root133 characters with8-byte
basename passed; final_input241 characters with100-byte basename failed the
real `_assert_practical_path` with manifest_invalid_input_name; stager.stage
reported the same241-character final candidate and reason. This confirms the
original failure hit the existing240-character FULL-PATH ceiling, not the
100-byte basename ceiling. No temporary-path failure is claimed observed: the
earlier final-path check already rejected. The prior source-only hypothesis is
now supported by this actual diagnostic, with no raw path or exception disclosed.

Parent explicitly released fixture-only correction after that evidence. Removed
only the temporary diagnostic wrappers/output. Restored the B4FE function's
prepare/close flow and all original100/101-byte acceptance/rejection, binding
name and zero-execution assertions, then made these minimal additions:

- Only this parametrized test now requests tmp_path_factory and constructs its
  rig at `tmp_path_factory.mktemp('c1-name')`; no global basetemp/runner change.
- Before prepare in BOTH cases, assert numeric full-path length is at most the
  unchanged stager `_MAX_WINDOWS_PATH_CHARS` (240) for the final task candidate
  and the `.stage-` plus16-hex-character temporary candidate. The representative
  hex value affects no length and creates no path. This makes the independent
  path-size prerequisite explicit without weakening either stager limit.

Test SOURCE freeze SHA256:
`436EAD8C1584532FF11D22F3C4195A71031308E85D439703C4270C79D515AF19`.

Exact next parent verification nodes, after SOURCE clearance:

```text
tests/task_runtime/test_docking_consent.py::test_c1_reuses_existing_portable_filename_byte_limit[100]
tests/task_runtime/test_docking_consent.py::test_c1_reuses_existing_portable_filename_byte_limit[101]
```

No execution occurred in this amendment; no two-node GREEN or full-C1 completion
is claimed. Runtime1B6D, store105E, database7043, contract80BC, staging and runner
remain unchanged. Only test/plan edited with apply_patch; no Python/import/
compile/collection/test, production edit, limit change, network, commit or push.
All outstanding receipt/begin/cleanup/seal-in-flight and later C gates remain.

### Parent corrected-fixture verification receipts — NOT C1 completion

Parent actual two-node terminal `a8eb0a`: **2 passed in1.04s, exit0**, eight
before/after pins unchanged, for the original filename-boundary[100] and[101]
nodes after the fixture-only short-root correction.

Parent actual full corrected C1-module terminal `11878/0f5e81`: **83 passed
in99.17s, exit0, zero warnings and zero skips**, eight before/after pins
unchanged. This is parent execution evidence on test436E and the frozen
production, not an independent fresh QUALITY run by this worker.

Preserve all earlier receipts: f73b83 missing-API RED; e87933 boundary1failed/
1passed;50394/7e1596 full-module82passed/1failed;313448 filename diagnostic
1failed and its actual241-character path rejection. Those failures are not
erased or retrospectively relabeled by the subsequent GREEN runs.

This grant changes ONLY this plan receipt appendix. Production1B6D/105E/7043/
80BC, test436E, staging and runner remain frozen; this worker ran no Python and
made no production/test edit, commit or push. Receipt-fault coverage, begin/
cleanup outcomes, seal-in-flight, legacy regressions and independent fresh
review/QUALITY remain pending. C1 and full C are NOT declared complete. A next
minimal receipt-fault matrix may be proposed in prose only; no new tests or
implementation are authorized by these receipts.

### Parent legacy/staging regression receipts and legacy basename diagnostic

Parent actual legacy terminal `9076/7fc675`: **419 passed,2 skipped in35.93s,
zero warnings**, ten before/after pins unchanged. The two skips are Windows
symlink privilege and explicit-opt-in wall-clock performance coverage. This is
offline regression evidence, not scientific/live acceptance. As instructed,
plan200AC was kept frozen during the subsequent parent staging regression;
these receipts are appended only after its physical terminal.

Parent actual `30874/2121a3` physical terminal for test_staging.py plus
test_secure_snapshot_boundary.py: **1 failed,184 passed,11 skipped in43.76s,
exit1**, POSTFLIGHT_MATCH true, ten pins unchanged. Preserve skip categories:
six symlink-privilege, one open-file replacement and four POSIX-only cases.
The sole failure was the pre-existing
`test_portable_100_byte_input_basename_is_accepted` at test line998, through
stage line563 into practical-path line2408, with
ManifestError(manifest_invalid_input_name). It is not a full staging GREEN.

Source inspection: unlike the C1 fixture, this legacy test stages directly under
tmp_path with task ID `task-1` (6 characters). For tmp_path character length T,
its100-byte ASCII receptor yields final length T+115 and temporary length T+132.
Both must satisfy the unchanged240-character practical-path ceiling, requiring
T<=108. The reported stack points to the temporary-path check. Actual lengths
have NOT yet been captured for this run; do not substitute the C1 diagnostic's
241-character final path or assume identical roots for this separate fixture.

Parent granted only minimal diagnostic preparation, not a fixture or production
fix. Changed only this existing test to accept monkeypatch and transparently
wrap the real `_assert_practical_path` and this stager's real stage call. Root,
100-byte basename, task/input bytes/config, actual load_verified and its original
assertion remain unchanged. Wrappers forward original arguments/results and
bare-raise original exceptions. The finally output `STAGING_NAME_DIAGNOSTIC`
contains only fixed callee/phase labels, whitelisted ManifestError reason (or
fixed ok/non_manifest_error), basename-byte and full-path character/encoded-byte
counts. No raw path, filename, exception repr or input content is recorded.
Diagnostic bookkeeping/output failures do not replace the actual outcome.

Exact proposed parent-only diagnostic selector:

`tests/task_runtime/test_staging.py::test_portable_100_byte_input_basename_is_accepted`

Diagnostic test_staging.py SHA256:
`2C5E6A3684A8D8DDCCB69A6A7810999DE1DC8FAE93B8C75CF5819023309F08D0`.
Pre-diagnostic test_staging.py SHA256 was
`0933A13A3F8F779CBF50C82970ECA07220500BD48BE61807682A43BBCDA314BB`.
test_secure_snapshot_boundary.py remains
`2D6EE0008855A44F07A9928CB5A326D35B5DBD6C0E9A72A016A12C7F76C1F6A1`.

Diagnostic SOURCE only, NOT executed; no short-root correction yet. Runtime1B6D,
store105E, database7043, contract80BC, C1 test436E, production staging and runner
remain frozen. No Python/import/compile/collection/test/network, production
edit, limit relaxation, commit or push occurred. Receipt-fault tests await review;
other receipt/begin/cleanup/seal-in-flight/fresh QUALITY and full-C gates remain.

### Actual legacy basename diagnostic and fixture-only correction

Parent actual diagnostic terminal `8c67d5`: **1 failed in1.81s, exit1**, all
ten before/after pins unchanged. Recorded nonsecret facts: task root125
characters passed; final receptor233 characters/100-byte basename passed;
ligand143 characters passed; TEMPORARY receptor250 characters/100-byte basename
failed the real practical-path check with manifest_invalid_input_name. The
existing240-character full-path ceiling is the verified cause, not a100-byte
basename validation failure. Keep this diagnostic and the preceding regression
failure as historical receipts, not replaced by an assumed corrected GREEN.

Parent then granted only the minimal legacy positive-fixture correction.
Removed all temporary diagnostic wrappers/output and restored the original
stage/load_verified/assert flow. Only this test now uses
`tmp_path_factory.mktemp('stage-name')`. Before staging, numeric final and
temporary receptor path lengths must both be at most the unchanged existing
`_MAX_WINDOWS_PATH_CHARS` (240); the temporary candidate uses the real prefix
and16 representative hex characters. Actual stager code and path/name limits
are unchanged. Task ID,100-byte receptor basename, input bytes, config and the
original load_verified assertion are retained. All other tests, including
overlong101-byte-name negatives and path-limit negatives, remain untouched.

Corrected test_staging.py SHA256:
`14F92A304927B34E4973E00BC66F8A406A71907F421D4145A3A9A87E7D56F483`.

Exact positive node for parent verification after SOURCE:
`tests/task_runtime/test_staging.py::test_portable_100_byte_input_basename_is_accepted`.

This is SOURCE-only correction, not an executed pass. Only test_staging.py and
this plan changed; runtime1B6D/store105E/database7043/contract80BC, C1 test436E,
secure snapshot test, production staging and runner stay frozen. No Python,
imports/compile/collection/tests, production edit, limit relaxation, network,
commit or push. Six receipt-fault preparation remains on HOLD until parent
GREEN pins; no overlap or C1/full-C completion claim is made.

### Parent corrected legacy module-pair GREEN; receipt-fault TEST SOURCE batch

Parent actual physical terminal `12395/d37397`: **185 passed,11 skipped
in25.56s, exit0**, all ten before/after pins unchanged, for test_staging.py plus
test_secure_snapshot_boundary.py after the fixture-only correction. Retain the
11 skip reasons already recorded (six symlink-privilege, one open-file
replacement, four POSIX-only). Preserve30874/2121a3 and8c67d5 failures and
diagnostic facts; this GREEN does not erase them or establish live/scientific
acceptance. Parent then released the next TEST+plan-only batch below.

Added exactly one parametrized C1 test with six faults and one no-fault control:

`tests/task_runtime/test_docking_consent.py::test_c1_receipt_faults_preserve_actual_sql_and_cleanup_facts`

Exact parameter IDs:

```text
[revoke-pre-throw]
[revoke-post-commit-throw]
[revoke-zero-match]
[finish-pre-throw]
[finish-post-commit-throw]
[delete-failure]
[normal]
```

Each case reserves the real owner plus15 other-owner drafts in actual SQLite,
uses a separate Store for independent connections/readback, stages actual files,
holds the real writer after stage until cancellation has returned, and then
performs a real late receptor read before writer exit. No model/backend/tool
dispatch is allowed. The normal control is successful cancelled-preparation
revocation+cleanup WITHOUT injected faults; existing ordinary READY positives
remain unchanged.

Fault boundaries and observations:

- Revoke pre-throw: throw before calling the real method; DB remains PREPARING/
  pending, no delete, owner/global capacity retained.
- Revoke post-commit throw: first call the real method and observe its actual
  committed REVOKED/pending state through the independent connection, then
  throw. Runtime cannot infer a confirmed receipt from that exception; no file
  deletion, actual cleanup becomes unresolved and capacity remains occupied.
- Revoke zero-match: delegate the real SQL method with a deliberately different
  token (never change the stored/runtime token), require its actual False result
  and an unchanged independent DB record. No fake False return or mocked UPDATE.
- Finish pre-throw: require actual secure deletion completed successfully before
  finish entry, then throw before the real finish write. DB remains pending and
  capacity remains occupied even though files are physically gone.
- Finish post-commit throw: require actual deletion first, call the real finish,
  independently observe COMMITTED settled, then throw. Local close reports its
  fixed uncertainty error, but the DB must remain settled and actually admit a
  replacement owner into the now-free global slot. No fictional DB rollback or
  new cross-process receipt-equals-commit framework is introduced.
- Delete failure: always call the real `_safe_delete_owned_tree` and its real
  shutil.rmtree path. Inject PermissionError only at os.unlink of receptor.pdb
  on this thread inside the verified owned quarantine tree; all other unlinks
  delegate unchanged. Require actual unlink attempt, real False cleanup result,
  residual receptor/ownership marker and durable unresolved/capacity retention.
  The delete method itself is not replaced with fake False.

All cases observe exactly one revoke and one finish invocation, no late binding/
nonce, real writer/SQL exit events, intact non-owned neighbor, sanitized close
error, actual DB state and actual other-connection admission decisions. The
finally block first releases the20s finite fixture barrier, then gathers caller,
close awaiter and the real runtime.close drain (which may truthfully remain an
error). It checks physical exits and no additional invocation; asyncio.run joins
the real default-executor threads. No owner/receipt/state field is mutated by
teardown, no manual DB rollback and no injected success cleanup. Pytest retains
responsibility for its own temporary fixture directory after these assertions.

Previous test436E content is unchanged: removing the newly appended parameter
block and its separating blank lines reproduces SHA256436E exactly by a
read-only UTF-8 prefix hash check. No previous assertion or fixture was edited.
Current test SHA256:
`C0FE246B0753DD7686BEC07FBD88F4C1CA674E948C374E0853202DF2B6E3A232`.

SOURCE preparation only, no collection or execution; seven cases is a static
parameter count, not a pass claim. Runtime1B6D/store105E/database7043/contract80BC,
test_staging14F9, secure snapshot, production staging and runner remain frozen.
No Python/import/compile/test/network, production edit, commit or push. Parent
SOURCE and a separate slot grant precede execution; no production repair is
authorized without an actual relevant RED. Begin/cleanup/finite seal outcomes,
independent fresh QUALITY and later C gates remain incomplete; no C1 completion.

### Parent receipt-fault GREEN; begin/seal transaction TEST SOURCE gate

Parent reports actual physical terminal `67eb55`: **7 passed in4.47s, exit0**,
all ten before/after pins unchanged, for the six receipt faults plus normal
control above. No warning/skip count was supplied in this receipt; none is
inferred. This is parent execution evidence, not an independent fresh QUALITY
run or scientific/live acceptance. All earlier failures remain recorded.

Under the subsequent TEST+plan-only grant, append the approved13-case matrix;
do not change production, preceding tests, runner or the original30s preparation
deadline. The three exact selectors (relative to this worktree) are:

```text
tests/task_runtime/test_docking_consent.py::test_c1_transaction_boundary_interrupt_retains_actual_owner
tests/task_runtime/test_docking_consent.py::test_c1_transaction_boundary_normal_control
tests/task_runtime/test_docking_consent.py::test_c1_transaction_boundary_exception_preserves_real_commit_outcome
```

The first expands to8 cases: outcome cancel/deadline x boundary pre-commit/
post-commit x phase begin/seal (e.g. `[cancel-pre-commit-begin]`). The normal
control holds a real seal after its committed receipt, then releases a successful
READY preview. The last expands to4 cases: pre-commit/post-commit x begin/seal
(e.g. `[pre-commit-begin]`). These are static parameter counts, not collection or
pass claims. Imports and API access remain inside the test/helper bodies.

Transaction instrumentation is deliberately at the existing store connection
context, not a method-entry exception surrogate:

- Delegate every execute to the actual SQLite connection and return its actual
  cursor. Identify the target only after the real PREPARING or READY UPDATE has
  executed with rowcount1. Independently observe begin's preceding EXPIRED UPDATE
  transaction and its completion, but never hold or fault that expiry transaction.
- Delegate the original database.connection context manager unchanged. A
  pre-commit hold is after the real UPDATE and before that original context exits;
  a pre-commit exception therefore traverses its real rollback. A post-commit hold
  is only after the original context has actually committed and closed; throwing
  there cannot be described as a rollback of that committed transaction.
- Record only fixed COMMIT/ROLLBACK trace labels (not expanded SQL, tokens or
  payloads). Verify independent native SQLite readbacks before/after the boundary;
  explicitly close those connections. Do not use the instrumented connection
  for readback, or issue competing test writes while the target lock is held.
  The committed flag is set only after the original context returns, not at
  method entry or upon scheduling a worker.

For cancel/deadline cases the real caller must settle while the actual transaction
is still held, with CancelledError or the fixed preparation-timeout reason and
no preview. A concurrent real runtime.close must remain pending, with no early
deletion and (for seal) the staged receptor still readable. Release the boundary,
then require actual transaction exit, retained-owner drain, durable REVOKED with
nonce cleared and settled cleanup; seal files must be physically deleted while
an unrelated neighbor remains intact. No stager invocation is permitted after a
held begin is cancelled/timed out. Historical committed seal binding is observed
and retained, never erased from assertions to pretend a lost reply rolled back.

Exception cases require the fixed sanitized caller error. A begin pre-commit
exception leaves the independently observed original draft intact after actual
ROLLBACK and causes no staging. Other exception boundaries require confirmed
terminal revocation/cleanup; an actual seal commit-then-raise retains its
historical binding, whereas seal rollback has no committed binding. The normal
control checks the full expected copied-input/grid/options binding, canonical
digest and persisted hash of the returned nonce, using the existing helpers.
Every case checks zero runnable task/event/model/backend/tool activity.

The fixture barrier has a60s escape (reported as a fixture failure), not a new
production timeout. Deadline cases await the unmodified actual30s caller deadline
with a35s test observation bound; no clock advance or deadline patch is used.
Other joins use finite observation windows. Finally always releases the barrier
before gathering the real caller/close tasks and runtime drain, checks transaction
exit events, and lets asyncio.run join real default-executor threads. No owner,
receipt or DB state mutation, fabricated cleanup success or manual rollback.

Removing only this appended helper/test block and separator blank lines reproduces
the preceding test SHA256 exactly:
`C0FE246B0753DD7686BEC07FBD88F4C1CA674E948C374E0853202DF2B6E3A232`.
New test SHA256:
`76750AEB6F73A73E1E2A2CE8045F7F603F03DC0C4A632C4418A5F35F174A722C`.
Read-only hashing reverified runtime1B6D/store105E/database7043/contract80BC,
production stagingFA91, test_staging14F9, secure snapshot2D6E and runnerA99B
unchanged. No Python/import/compile/collection/test/network or production edit
was performed. Independent SOURCE review and parent slot authorization precede
execution; any implementation needs relevant actual RED and a separate grant.
Begin/seal in-flight outcomes are still an unexecuted C1 gate, not solved or
deferred to C2; this append does not declare C1 or full C complete.

### Parent actual begin/seal GREEN — independent C1 release gate pending

After Lovelace SOURCE GO for test7675/plan2C4F, parent first ran begin/pre-commit/
cancel plus the normal control: actual terminal `71d6e5`, **2 passed in1.80s**,
all ten pins unchanged. No warning/skip or exit-code value was supplied with
that two-node receipt, so none is inferred here. The batch was not presumed RED.

Parent then reports physical terminal `19685/5ec76b` for all three transaction
test functions: **13 passed in124.17s, zero warnings, zero skips, exit0**,
all ten before/after pins unchanged. The four deadline cases retained the actual
original30s preparation deadline. No automatic retry or production repair was
performed for this matrix. This replaces its preceding unexecuted status with
parent GREEN evidence only; it is not independent fresh QUALITY or proof of
uncovered interleavings. Historical API/boundary/filename failures remain intact.

This grant changes only this plan. Before append, read-only SHA256 checks matched
test7675, plan2C4F, runtime1B6D, store105E, database7043, contract80BC,
stagingFA91, legacy staging14F9, secure snapshot2D6E and runnerA99B. No test,
production or runner change; no Python/import/compile/test/network/commit/push.

Remaining C1 release gates, without adding another feature batch:

1. Independent fresh execution of the entire current C1 test module together
   with the already required C1 legacy selection, on one frozen source/test/
   runner set and after a separate sole-slot grant. The explicit C1 selection is:

   ```text
   tests/task_runtime/test_docking_consent.py
   tests/task_runtime/test_staging.py
   tests/task_runtime/test_secure_snapshot_boundary.py
   tests/task_runtime/test_task_store.py
   tests/task_runtime/test_local_backend.py
   tests/agent/test_docking_tool_contract.py
   tests/agent/test_docking_contract_integration.py
   ```

   This carries the C1 table's staging/store/typed legacy inputs, the spec's
   local-backend cancellation regressions, and the secure-snapshot module already
   included in this C1 validation sequence. It does not import the full C0-C8
   final legacy list or authorize execution now. The current C1 module includes
   all old cases, seven blocked-revocation cases, seven receipt-fault/control
   cases and these13 transaction cases. Do not sum earlier pass counts and call
   that a fresh full-module/combined run. Report actual collection, terminal
   counts, warnings, skips/reasons and complete before/after input hashes.
2. Independent combined SOURCE/QUALITY disposition against the approved C1
   contract: copied-file/config/input/request/binding identities and hash-only
   nonce; strict inherited bounds; durable same-task/owner identity without a
   runnable task or model/tool/backend dispatch; owner1/global16 capacity,
   non-sliding expiry/generations; retained actual staging/verification/SQL
   ownership, orphan protection and truthful cleanup/receipt outcomes. Include
   the new real transaction and six-fault evidence, preserve original deadlines,
   and distinguish local caller outcome from durable/physical settlement. A
   newly found gap requires a bounded test-first grant, not silent scope growth.
3. Before publication, the previously required clean focused integration/alignment
   and evidence review must resolve the unpublished dependency baseline and
   verify the actual publishable revision. Do not assume pre-alignment local
   receipts automatically prove a changed integrated tree; parent coordinates
   any required fresh validation/CI and publication authorization separately.

No additional C1 production change is authorized or inferred from GREEN. C1 is
not yet released. C0c ordinary entry, C2 approval/claim/raw dispatch, C3 HTTP/body
framing, C4-C8 ownership/UI/artifacts/full chat-to-report chain and real scientific
activation remain their existing later gates, not new requirements to implement
inside this prepare-only batch. These offline receipts grant no production
execution authority and do not establish full C completion.

### C1 verification-in-flight cancellation — one-case TEST SOURCE amendment

Parent released one narrowly scoped regression after Lovelace identified the
existing spec C1/physical-I/O requirement: cancellation after stage has returned,
but while runtime's subsequent load_verified_locator still owns an actual file
read. This adds no new product requirement or cancellation/deadline matrix.
Production and all preceding test assertions remain frozen.

New exact node:

```text
tests/task_runtime/test_docking_consent.py::test_c1_cancel_during_actual_verification_retains_reader_before_cleanup
```

Reuse make_rig, actual TaskStore/SQLite and stager. The stage observer delegates
and records its actual return. Only the subsequent load_verified_locator call
marks a thread-local verification scope; within that scope only the exact owned
receptor's existing _secure_read_file marks the read scope. A transparent os.read
wrapper holds the first actual descriptor read behind a40s finite fixture escape,
then delegates to the original os.read. All other reads delegate immediately.
Thus stage's own internal verification is not held, and the target descriptor
has already been opened/validated by the real secure reader. Original manifest,
size, hash, containment and descriptor checks run unchanged; no fake manifest,
digest, read bytes, ownership record or cleanup receipt is returned.

While that read is held, require caller cancellation to return, then observe the
real revoke method's completed receipt through an independent Store connection:
REVOKED/pending with no binding/nonce. Keep owner/global capacity occupied using
the existing1/16 bounds. A real runtime.close stays pending and both copied files
remain readable. Instrument discard_unprojected at its ENTRY before delegation,
not merely physical deletion after its lock: cleanup_entries must remain empty.
This prevents the stager lock from disguising an illegally early cleanup call.

After release, observe actual receptor read bytes through EOF and the actual
returned validated manifest with independently checked receptor/ligand SHA256.
Require verification exit before the sole cleanup entry, real secure deletion,
durable settled cleanup, released capacity and unchanged non-owned neighbor.
Observe the actual seal method entry as well: none is allowed before or after
release; caller remains cancelled with no preview, binding or nonce. Existing
no_execution verifies zero model/tool/backend/runnable-task activity. Finally
releases the real descriptor barrier before gathering caller/close and runtime
drain, and asyncio.run joins default-executor threads; no owner/receipt mutation.

Use the unchanged existing positive as the normal control, not a duplicate:

```text
tests/task_runtime/test_docking_consent.py::test_c1_real_sqlite_copies_hashes_and_seals_existing_identity
```

Read-only inverse-prefix SHA256 exactly reproduces preceding test
`76750AEB6F73A73E1E2A2CE8045F7F603F03DC0C4A632C4418A5F35F174A722C`.
New test SHA256:
`962E8A48B0FD37B54B2530875EBD07B00270069F947084F7C6E4A84FD6D73318`.
Plan F29E is the pre-amendment receipt/gate pin. No Python/import/compile/
collection/test/network/production or runner edit, commit or push. This case is
SOURCE-only and unexecuted, not predetermined RED. Independent SOURCE and a
separate parent slot grant precede its targeted validation; the independent
fresh whole-C1-plus-legacy gate above must include this appended case. No C1
release or broader C scope is implied.

### C1 independent SOURCE/fresh QUALITY accepted; local commit handoff

Parent reports actual verification-in-flight single-node terminal `e2af81`:
**1 passed in1.48s, exit0**, all ten before/after pins unchanged, for
test_c1_cancel_during_actual_verification_retains_reader_before_cleanup.
No warning/skip count was supplied for this individual receipt. Plan752F and
test962E stayed frozen during the subsequent independent run; this receipt was
not appended while execution pins were live.

Parent now confirms Lovelace independent **SOURCE + fresh QUALITY PASS**:
session `16128`, physical terminal `073569`, **990 passed,13 skipped, zero
warnings in272.49s, exit0**, for the complete C1 module plus the six legacy
modules explicitly listed above. Postflight `9eb175` confirms **14/14 inputs
unchanged**. This is independent execution evidence, not another author run.
The13 skips remain uncovered: seven symlink-privilege cases, one open-file
replacement case, four POSIX-only cases and one explicit-opt-in performance
case. Do not count them as passing coverage or infer scientific/live validation.

Executed pins include test962E, plan752F, runnerA99B and production
runtime1B6D/store105E/database7043/contract80BC, with their full SHA256 values
already recorded above. The present plan-only receipt append necessarily changes
the plan hash; it does not change any executed test or production byte. Preserve
all preceding RED/failure/diagnostic receipts and actual deadline boundaries.

Parent authorizes local commit of exactly seven paths:

```text
src/task_runtime/runtime.py
src/task_runtime/store.py
src/task_runtime/database.py
src/task_runtime/docking_consent.py
tests/task_runtime/test_docking_consent.py
tests/task_runtime/test_staging.py
docs/superpowers/plans/2026-09-27-docking-consent-integration.md
```

This accepts the bounded C1 prepare/identity offline SOURCE/QUALITY gate for
local handoff. It does not assert remote publication, latest-main integration,
CI/merge, real Vina availability or full C completion. No push/merge or C2 edits
are authorized. Parent will create a separate clean C2 worktree/branch and
release its test preparation independently. Current branch remains
codex/docking-consent-preparation on reviewed dependency ebb2093; no alignment
or dependency-merged claim is made in this receipt. Exact local commit/tree and
new plan SHA256 are reported after commit, outside this self-referential record.
