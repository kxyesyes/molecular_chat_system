# Evidence-bound report and property cards Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking. **Do not execute until the parent approves the companion design and separately authorizes implementation.**

**Goal:** Add a deterministic Chinese report and independently evidenced property values to current candidate cards without changing scientific results, candidate transport or reference ACK authority.

**Architecture:** Read an owner-bound consistent source snapshot, join accepted independent observations using exact checkpoint/trace/input/row proof, and build a detached ScientificReport@1. Deliver one additive WebSocket event and render via safe DOM alongside unchanged legacy text and strict candidate events. No new ledger, database schema, provider call or decision-entry activation.

**Tech Stack:** Existing Python/FastAPI/Pydantic/RDKit/SQLite, Jinja2, vanilla JavaScript, pytest and Node VM/DOM harnesses. No new npm/build/frontend framework.

---

## Review state and baseline

### PR77 root CI correction — uncommitted QUALITY freeze at `400beaf`

Fixed baseline **`400beaf066620aeaf504b4f48a83050599d250dd`**, branch `codex/historical-residual-disposition`. Parent reports PR77 root failure in [run 36069659921 / job 107867398126](https://github.com/kxyesyes/molecular_chat_system/actions/runs/36069659921/job/107867398126): `tests/test_static_placeholder_cleanup.py::test_current_template_script_order_and_local_urls[home]`. Preserve this as a genuine CI RED. Main PR76 `5ad08ac` is not merged; no commit, staging, push, full/root-full or model/server activation in this correction.

The actual isolated whole root module reproduced **1 failed, 6 passed in 18.13s**, exit 1, session 51112. The failing child assertion is line 56; line 128 is the parent process forwarding its exit status. Home returned HTTP 200. Actual script basenames have 12 entries versus 11 in `PAGE_SCRIPTS`: only `evidence_report.js` after `scientific_references.js` was missing from the literal expected list. Removing that single name in a read-only sequence comparison leaves identical order. Query/cache parameters are stripped by `urlsplit` and are not the cause. The helper exports `HomeEvidenceReport` before `main.js:30` consumes it; production ordering is correct.

Parent authorized exactly one test-literal insertion at line 38, leaving every assertion and production file untouched. `apply_patch` inserted `'evidence_report.js'` between `'scientific_references.js'` and `'formatters.js'`. All test function/class ASTs match HEAD; there was no skip, deletion, relaxed comparison, URL exception, fixture change or production adjustment.

**GREEN:** identical isolated whole-module invocation, session **34100**: **7 passed in 17.89s**, exit 0, no warning/skip/failure. All five existing G1 Node scripts pass; the original root-level Node cache test is `tests/home_workflow_completion_behavior_test.js` (main-script token assertion), already one of those five, not a sixth distinct test. The standalone evidence-report Node run checks hostile preflight; no new Python-driven frame suite or Agent full was run in this bounded correction. In-memory compile and `git diff --check` pass.

Reproducible command (run from this worktree; normal conftest, sanitized temporary runtime cwd/config, original worker isolation retained):

```powershell
$plan = Get-Content docs/superpowers/plans/2026-09-24-rag-service-extraction.md -Raw
$m = [regex]::Match($plan, '(?s)\$runner = @''\r?\n(.*?)\r?\n''@')
if (-not $m.Success) { throw 'Isolation runner missing' }
$runner = $m.Groups[1].Value.Replace('D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr', 'D:/MedChat/molecular_chat_system_worktrees/historical-residual-disposition')
$runner | & 'C:/Users/xkx52/.conda/envs/MedChat/python.exe' -B -c "import sys; exec(sys.stdin.read())" tests/test_static_placeholder_cleanup.py

node tests/home_agent_task_panel_test.js
node tests/home_workflow_completion_behavior_test.js
node tests/home_structured_molecule_render_test.js
node tests/home_scientific_references_test.js
node tests/home_evidence_report_test.js
```

The runner adds `-q -p no:cacheprovider --tb=short -rs`; the same root invocation records both RED and GREEN. Old full **6914 passed / 2 skipped** is `tests/agent`-only evidence on `fcb84bd`, and did not exercise this root module; it does not negate this CI failure. The 16 existing G1 source/test files retain aggregate SHA-256 **`2c48070b69bff63d1bbfcb252c2ffbc709e62e5a5131ae16d2f548584205d2c4`** (the handoff's path-plus-byte-hash recipe).

- [x] One-literal patch only in `tests/test_static_placeholder_cleanup.py`; original G1 16 files unchanged.
- [x] Whole root module 7 GREEN, existing five Node/cache checks GREEN; preserve original CI and local RED evidence.
- [x] Update only this plan and the companion G1 design; freeze **three uncommitted files** for original QUALITY review.
- [ ] Original QUALITY approval for this **one-test + two-doc** increment; later commit/push/CI rerun requires parent instruction. Local GREEN is not a claim that PR77 CI passed or is approved to merge.

### Post-full PR74 main delta — light GREEN at `b884465`

- [x] Full session 62470 completed/released before any PR74 merge. Commit the finished full record as `7724310`; verify local authorized main **`c3195f96c4a80aac40ac958d351ecea6b12f3c31`** and tree **`765c9edf9afc5b1c616de5df56d1c3f212c111fc`**.
- [x] Merge PR74 without conflicts as **`b88446591ec1c4f929bafdbe8f24cfea13828e7a`** (tree `b9c2bb56c11e16105426b2baa60b15d12a4c8247`); no overlap with the 20 G1 files or local implementation/test change.
- [x] Eleven-file G1 + ADMET status/URL/route cross-focus, session **13524**: **1316 passed, 62.89s**, no warning/skip/failure. Five Node scripts passed again. Exact paths and source aggregate are recorded in the [post-full delta handoff](../../handoff/2026-09-25-g1-evidence-report-freeze.md#post-full-pr74-delta--light-cross-focus-only).
- [x] No second full or heavy-slot reacquisition. Full **6914/2 skips** and minimum-profile **634** remain evidence for earlier `fcb84bd`, not this new merged snapshot. Only the two G1 docs are updated/explicitly committed afterward.
- [ ] Parent final integration/publication decision; no push/PR authorization.

### Current authorized integration/full gate — GREEN at `fcb84bd`

Parent reports Faraday SPEC **APPROVE (407 + 5 Node)** and Descartes QUALITY **APPROVE (413 + 5 Node + 3 SQLite/race probes)** for `a90581b`, 20 hashes unchanged. These are independent parent-supplied review results, not local retests. No G1 implementation/test changes after that approval.

- [x] Verify clean branch and local exact PR73 main `0295e9960b15c850bb212e434da66824181ef546`; merge as `5086b86376282937e2c660ba05118cca4fffc77c`, no conflicts/20-path overlap. Original 20 focus + 13 collision files: **2421 passed, 7 warnings**; five Node passed.
- [x] Before full started, verify parent-authorized PR75 `7b5611fed039aa7aebde62063f45e45e965cf23a` and tree `6f42e12156d80c88738ec2eba42caed94994d6cd`; merge as **`fcb84bd4bf207593078d8c4871f49539b3641e93`**, no conflicts or change to the 20 G1 files. Rerun 20 + 14 collision files including inventory: **2437 passed, 7 warnings**; five Node passed. ADMET74 not merged.
- [x] Reuse retained lowest web profile, assert **Pydantic 2.5.0 / FastAPI 0.104.1**; six G1/boundary key files **634 passed, 10 warnings**, including actual capture→DOM/ACK and partial/unavailable cases. No install/network/model activation.
- [x] Lock clean HEAD above/tree **`6c2ffb2ee666c1d99f9bec3df3c2abb60d1080aa`**; run exactly one isolated **`tests/agent`** full, session **62470**: **6914 passed, 2 skipped, 7 warnings, 333.83s, exit 0**. Skips: Windows directory symlinks unavailable (`test_decision_chat_acceptance.py:149`) and disabled performance test (`test_harness_shadow.py:277`). No root full or second Agent full.
- [x] No merges/edits during full. Release heavy slot immediately on completion to parent P8/A1 scheduling. Recheck unchanged HEAD/tree/clean/20 byte hashes before doc writes; local main still exact PR75, zero new delta. Full is not relabelled onto a later revision.
- [x] Record exact source hashes, commands, 20-path impact and attributed approvals in the [integration handoff](../../handoff/2026-09-25-g1-evidence-report-freeze.md#authorized-main-integration-and-one-full-agent-run--green). Only this plan and that G1 handoff are edited after full; explicit local doc commit is allowed. Source/test aggregate remains `2c48070b69bff63d1bbfcb252c2ffbc709e62e5a5131ae16d2f548584205d2c4`.
- [ ] Parent's publication/final integration decision. No push/PR authorized; future main deltas need their own light checks. Static/live-only scope, G2/recovery and P7 ownership unchanged; historical sandbox exact cause remains unknown.

### Current SPEC2 freeze — revision base `bf4a2cf`

Historical pre-integration revision record; later dual approval/full authorization and results above supersede its then-pending gates.

Parent reports the original two P2 findings closed, then authorized minimal TDD fixes for two more approved-spec gaps and a local commit after focused freeze. No full Agent/network/push; parent retains SPEC/QUALITY approval and integration authority. Reviewer-reported 348 passes are not counted as a local run.

- [x] Task 1/6: actual capture-derived four-metric missing matrix (15 null subsets plus four descriptors × absent/bool/string), each from succeeded and genuine partial sources. Reseal Python DTOs; pass the same variants to actualMain/Node. Both boundaries reject incomplete positive rows; genuine partial rows with all four values still pass and bad sidecars preserve baseline ACK/cards.
- [x] Task 4/6: controlled Chinese `REASONS`, six exact section headings, snapshot marker and fixed MW Da/TPSA Å² units; no rescore/invented values. Actual standard-plan/partial/unverifiable-input captures exercise DOM, not only handcrafted DTOs.
- [x] Revisit whole approved SPEC. Also align exact title/caveat, absent version label and truncation marker within the same UI-only patch. Per-section evidence/remaining coverage limits are in the [SPEC2 freeze](../../handoff/2026-09-25-g1-evidence-report-freeze.md#spec2-revision--four-or-none-descriptors-and-chinese-ui).
- [x] RED **33 failed / 26 passed** → GREEN **59 passed**; all 20 mandatory focused suites **1373 passed, 7 existing warnings, no skip/xfail**. Five Node scripts, changed JS syntax, in-memory Python compilation and whitespace checks passed. Full Agent was not run.
- [ ] Parent/SPEC re-review followed by QUALITY; local freeze commit is authorized but not an approval or permission to push.
- [ ] Parent-allocated full Agent and later integrated-main verification; G2/recovery and P7 remain separate.

### Prior SPEC revision state — HEAD `bf4a2cf` (historical, original findings now closed)

Faraday SPEC **not approved**, two P2 findings. Parent authorized minimal TDD fixes but **no commit until parent review, no push, no full Agent** (4C next slot). Current review target is the uncommitted diff from `bf4a2cf55ffa34e96c3821d4e0c6ca5bdcf05422`, not an approved replacement commit.

- [x] Task 1/6: reproduce the resealed failed ADMET → `succeeded` forgery using actual standard-plan/capture frames. Require matching accepted source `step_id`/tool/status plus `none` reason for succeeded/partial; require null source and status-specific nonpositive reason for failed/rejected/cancelled/skipped/unknown, in both Python and JS. Preserve source outcomes without rewriting observations.
- [x] Task 4/6/7: display actual `ranking_evidence.weights_used` in Chinese safe text for each Top-N entry; null is 未提供, no rescore/default values. Python checks copied real ranker weights; actual-main Node tests check DOM values, immutable frames and hostile strings.
- [x] RED → GREEN: six regression failures became six passes; final narrow focus **279 passed, 7 existing warnings**, five related Node scripts plus syntax/in-memory compile/whitespace checks passed. Actual partial/unknown frames and 14 contradictory resealed step cases covered. See [revision evidence](../../handoff/2026-09-25-g1-evidence-report-freeze.md#faraday-spec-revision--uncommitted-diff-from-bf4a2cf).
- [ ] Parent/SPEC re-review; SPEC owns remaining approval. QUALITY follows SPEC. No staging/commit in this revision turn.
- [ ] Full Agent when parent allocates G1's slot; no claim that this narrow rerun repeats the initial 1312-test focused set or tests later main integrations.

No implementation changes outside the two DTO/JS files, two test files and freeze/plan documentation. All original dirty paths/assets, CandidateSet/ACK/restore, source snapshot, full observation match, post-candidate delivery and static-only/P7 boundaries remain intact.

**2026-09-25 execution update:** Parent approved the design and static-only implementation with local commits, no push before double review. Approved docs were committed in `72c1ad4` before merging requested main `e173d43` as `6b9e5c0`. The [freeze handoff](../../handoff/2026-09-25-g1-evidence-report-freeze.md) is the current implementation/verification record. The original planning text and unchecked checklist below are retained as review requirements, not an assertion that implementation is still unauthorized or that every checklist case is complete. Full Agent is queued by parent after Planner → 4C → G2 → ADMET; SPEC → QUALITY must precede publication. Static only; P7A1 reference admission/dynamic binding is not implemented.

**PLAN ONLY, not implementation authorization.** [Design](../specs/2026-09-25-evidence-bound-report-cards-design.md) is the normative exact v1 schema/algorithm. Both docs share `e0d73d1` (audit `1581f3e` + requested main `3a68264`). PR #66 is now on that base; PR #67 candidate `9f3ce84` is CI-pending per parent, not merged here. Do not cherry-pick it opportunistically. Parent controls approved dependency head and functional-batch PR.

No boxes below are complete: they describe future work. G2 optimization/recovery tests and package 7 activation are separate. Do not run, edit, stage or migrate files from the original dirty checkout; retain all 13 original paths. No real model/secret/env/log/production DB/index/weight access or deployment.

## Proposed file map and public seams

| Path | Future responsibility |
|---|---|
| `src/agent/contracts/scientific_report.py` (new) | strict bounded ScientificReport@1, reason/status constants and validation; wire DTO only |
| `src/agent/presentation/__init__.py` (new, minimal) | export pure builder, no import side effects |
| `src/agent/presentation/evidence_report.py` (new) | snapshot/execution proof, static property/ranking binding and read-only projection |
| `src/agent/persistence/scientific_references.py` (narrow edit) | read-only `report_snapshot` beside existing source reader, one consistent transaction |
| `src/agent/persistence/sqlite_store.py` (one facade) | `get_scientific_report_snapshot(trace_id, *, session_id, references)` |
| `src/web/scientific_report.py` (new) | off-loop snapshot/build/source-version recheck; returns event or None |
| `src/web/chat_handler.py` (narrow hooks) | capture already-generated candidate events and send report before unchanged terminal frame |
| `src/web/static/js/home/evidence_report.js` (new) | strict normalization, bounded lifecycle, safe report/card rendering |
| `src/web/static/js/home/main.js` (narrow hooks) | request/event/terminal/clear/pagination integration, no scientific logic |
| `src/web/templates/index.html` | new helper include before main, new cache token with existing lineage retained |
| `tests/agent/test_evidence_report_contract.py` (new) | schema/budget/mutation/provenance/input/row/ranker tests |
| `tests/agent/test_evidence_report_snapshot.py` (new) | temporary-store ownership/latest/source-version/transaction/read-only tests |
| `tests/agent/test_evidence_report_frames.py` (new) | actual local workflow -> ChatHandler -> reference API -> captured frame chain |
| `tests/home_evidence_report_test.js` (new) | strict schema/lifecycle/DOM/card pagination and existing ACK compatibility |
| Existing candidate/reference/chat/Node suites (preserve substantive assertions; minimal additions and cache-token expectation update only) | protocol regression and old functional intent, never relaxed to permit metadata properties |

New public signatures (contract design, not code already installed):

```python
# persistence/scientific_references.py
def report_snapshot(store, trace_id: str, *, session_id: str,
                    references: list[dict]) -> dict | None: ...
# agent/presentation/evidence_report.py
def build_evidence_report(snapshot: dict, execution: dict,
                          candidate_events: list[dict]) -> dict | None: ...
# web/scientific_report.py
async def prepare_report_event(store, execution: dict,
                               candidate_events: list[dict], *,
                               session_id: str) -> dict | None: ...
```

The ellipses above declare interfaces, not deferred business rules: algorithms and exact data fields are in design §§4–7. Implement no generic JSONPath, report DSL, protocol negotiation framework, arbitrary schema slots or scientific recalculation.

Frontend helper API, fixed across tasks:

```text
HomeEvidenceReport.normalize(payload) -> detached report | null
HomeEvidenceReport.createLifecycle() ->
  startRequest(), observeTrace(traceId), enqueue(report), take(traceId), clear()
HomeEvidenceReport.renderReport(container, report) -> {mounted: boolean}
HomeEvidenceReport.findPropertyRow(report, reference, generatorId, candidate) -> row | null
HomeEvidenceReport.renderProperties(container, row, source) -> boolean
```

`take` consumes and closes the active buffer; it never changes candidate/reference controller state. Unknown/rejected report data must not reach any renderer.

## Task 0 — Confirm authority and isolated baseline

- [ ] Obtain parent approval for design choices in §10: live-only sidecar, additive report block, checkpoint proof and no numeric restore. Confirm implementation scope and the exact subsequently approved PR #67/main combination.
- [ ] In this worktree or parent's explicitly authorized functional worktree, record `git status --short`, branch/HEAD and scoped file fingerprints. Stop on overlapping dirty changes; never reset/overwrite. Integrate dependency only with explicit parent direction, then inspect `analysis_contract.py`/factory against `9f3ce84` for relevant changes.
- [ ] Reuse the isolated RAG runner documented in `docs/superpowers/plans/2026-09-24-rag-service-extraction.md`, replacing repo path and choosing the existing in-process variant for socket blocking. Keep normal conftest, `-B`, no pytest cache, temporary cwd/config/state and real-service switches 0. Guard network except Windows asyncio's internal socketpair; do not copy unnecessary fixtures or print environment values.
- [ ] Run the existing focused set listed under Task 7 before new tests; record actual counts/skips/errors and immutable base. Do not carry over audit numbers or call pending CI green.

## Task 1 — Freeze the v1 contract and hostile-input tests (RED first)

Files: new `contracts/scientific_report.py`, `tests/agent/test_evidence_report_contract.py`.

- [ ] Add a synthetic fixture constructing a real one-candidate CandidateSet (CCO), independent property row (recorded MW 46.07, LogP -0.001, TPSA 20.23, QED 0.407), bound checkpoint identities and digests. State that these are fixtures, not model outputs. Generate hashes through real helpers, never repeated dummy strings presented as verified proof.
- [ ] Add parameterized exact-key/version/budget/null/state tests before DTO implementation. Example assertions:

```python
@pytest.mark.parametrize('value', [True, '46.07', float('nan'), float('inf')])
def test_report_rejects_non_scientific_numbers(valid_report, value):
    payload = deepcopy(valid_report)
    payload['property_rows'][0]['values']['molecular_weight'] = value
    with pytest.raises(ValueError):
        validate_report(payload)

def test_unavailable_row_cannot_carry_positive_values(valid_report):
    payload = deepcopy(valid_report)
    payload['property_rows'][0]['state'] = 'unavailable'
    with pytest.raises(ValueError):
        validate_report(payload)
```

Define `validate_report(value) -> dict` in the new contract module as a strict detached-value validator (returns deep-independent plain JSON or raises ValueError). Test callable/getter-like Python objects, cycles, duplicate sources/rows and unbound source references without invoking their hooks.
- [ ] Run `pytest <absolute tests/agent/test_evidence_report_contract.py> -q -p no:cacheprovider --tb=short -rs` through the runner. Expected RED: missing module/API or violated assertion, not environment/provider failure.
- [ ] Implement exact schema in design §6 with 128 KiB/depth12/nodes8192 limits before copies/hashes. No additional arbitrary fields. Validate projection digest and referential integrity. Keep source digest algorithm distinct from checkpoint input hash.
- [ ] Run the same tests to GREEN. Assert returned DTO mutation cannot change the input fixture; record command/result. No ledger/result/provenance writes.

## Task 2 — Read-only owned snapshot (RED first)

Files: `persistence/scientific_references.py`, `sqlite_store.py`, new `test_evidence_report_snapshot.py`.

- [ ] Seed temporary SQLite with accepted generator/property/ranking checkpoints using real workflow fixtures; call current candidate publication normally. Save rows/metadata before snapshot calls, assert byte-for-byte equality afterwards. Test other session, missing owner, failed run, stale/latest replacement, workflow-version mismatch, expired/wrong pointer and size limits.
- [ ] Test consistent reading while a controlled second connection changes checkpoints: snapshot must be internally consistent or refused; later emission recheck must detect changed `source_version`. Do not return property rows from one generation and references from another transaction.
- [ ] Run the new snapshot file; expect RED for missing facade/behavior.
- [ ] Add only the following read flow: validate server pointer list -> transaction + existing `_source` -> select current owned matching presentation views -> parse bounded latest checkpoint outputs/metadata -> detach internal snapshot -> close transaction. No `UPDATE`, `INSERT`, publish/confirm or unowned fallback. The facade delegates to that function.
- [ ] Rerun snapshot and existing scientific-reference store/web/resilience tests. Check confirm/restore serialized key sets and source-version algorithm unchanged. Record actual GREEN; no DB schema changes.

## Task 3 — Pure source/property projection (RED first)

Files: new `presentation/__init__.py`, `presentation/evidence_report.py`, expanded contract tests.

- [ ] Add mutations of each proof component independently: wrong trace/tool/step/version/input hash, live-vs-checkpoint data/evidence/status difference, copied evidence ID with changed row, provided output digest mismatch, missing provenance, demo/fallback, ambiguous generator/output key, stale presentation, wrong row ID/canonical structure/index, missing/inconsistent alignment and duplicate/foreign rows.
- [ ] Add a **positive real static workflow fixture with recorded output_digest absent**, proving snapshot equality + static input proof permits values only with `output_digest_origin='checkpoint_snapshot'`; changing the accepted row without changing checkpoint must fail. A computed hash alone must never pass. Add positive recorded-digest and negative mismatch variants.
- [ ] Add partial property source and missing independent source cases; metadata-only properties yield no positive values. Use current `parse`/CandidateSet validation and actual RDKit rows, not replacement scalar assertions. Snapshot/ledger/result bytes must remain identical after build, after rejection and after mutation of returned report.
- [ ] Run tests to RED. Implement in this fixed order:

```text
preflight bounds -> match owned snapshot/execution outcome
-> map exact checkpoint/live observation identities (no tool-name collapse)
-> prove accepted source/provenance/evidence on detached copies
-> recompute/check output + full observation digests with explicit origin
-> validate generator data unchanged
-> resolve one static candidate_source / reconstruct real input binding
-> compare checkpoint input hash using WorkflowOrchestrator._input_hash
-> verify alignment counts and exact compound row identities
-> validate four descriptor leaves / produce unavailable reason otherwise
-> assemble DTO / final strict validation / return detached data
```

- [ ] Never call `verify_observation_integrity` on live objects; it rejects by mutating them. Do not add data to `candidate.metadata`, modify the ledger or call `property_calculator`/RDKit descriptors again. RDKit structure validation only is allowed here.
- [ ] Rerun contract + alignment + generation/property-report tests to GREEN. Ensure failed/invalid sources are explicitly nonpositive, not coerced into empty successful observations.

## Task 4 — Chinese semantics, generations, outcomes and actual ranker (RED first)

Files: same builder/contract and tests; no ranker/runtime edits.

- [ ] Add PDE5A/EGFR fixtures asserting target-specific report data, actual requested/valid/unique/invalid/duplicate/display counts, real provenance fields or null, all tool statuses and skipped/unknown steps. If generation has no accepted set, counts remain unknown; do not invent a successful zero set.
- [ ] Produce ranking via real `CandidateRanker.execute` on valid fixture evidence; persist/execute through current validation/session before projection. Assert output prefix/order/score/weights/missing evidence and requested-vs-actual Top-N match exactly; mutate order/counts/input hash to prove fail-closed behavior. Test absent ADMET/activity optional `[]`, unrankable molecules and oversized display subsets.
- [ ] Run to RED, then implement only copying/verification of accepted ranker output. Reconstruct its `BindingResolver` input using query, checkpoint metadata allowlists, original full accepted outputs and optional [] behavior. Do not recalculate ranking or read arbitrary model text. If reconstructing an exact input is impossible, set `input_unverifiable`.
- [ ] Represent run outcome separately from display coverage; overall partial remains partial. Successful ranker with limited evidence may have partial display coverage, but its recorded tool status is unchanged. No docking energies/activity numbers are introduced in v1.
- [ ] Run ranker + property-report + new contract tests to GREEN; assert no synthetic score/default positive, no model/provider call, and literal output data unchanged.

## Task 5 — Additive Web delivery, not a replacement completion protocol (RED first)

Files: new `src/web/scientific_report.py`, narrow `chat_handler.py`, new frame tests.

- [ ] Build an async capture-WebSocket test using the real ChatHandler plus real temporary-store workflow execution; do not replace the projector with a fake dict. Assert event ordering and byte-level old-frame compatibility (apart from deliberate new event insertion):

```python
types = [frame['type'] for frame in socket.frames]
assert types.index('molecule_candidates') < types.index('scientific_report')
assert types.index('scientific_report') < types.index('complete')
assert set(socket.frames[-1]) == {'type', 'content'}
assert 'molecules' not in socket.frames[-1]
assert before_result == serialize_execution_after()
```

`socket.frames`, `before_result` and `serialize_execution_after` are test fixture captures around actual `_process_message`, with a recursive plain-JSON snapshot of the typed result, provenance, ledger and persistence rows. Define these test helpers in `test_evidence_report_frames.py`, not production code.
- [ ] Parameterize completed and partial: partial ends with existing `message` and preserves failure warnings; total failure/rejected/cancelled emits no positive report/cards. Unknown/non-target workflow keeps previous behavior. Snapshot/projector exceptions must leave old completion/candidate delivery/ACK reachable, with no model fallback.
- [ ] Run to RED. Let `_send_reference_candidate_events` return the exact event list it already sends (without a second project/publish call). Immediately afterwards, use `prepare_report_event` in a worker/off-loop path, then send at most one new frame before the current terminal path. Use authenticated session scope, never browser-supplied owner. Keep current complete/partial text and authoritative result untouched.
- [ ] Gate target-design source from the server-produced execution envelope; reject contradictory trace/outcome and unknown proof. Recheck source version/current views just before emission; discard on race. Do not hold SQLite transaction across socket awaits.
- [ ] Rerun existing ChatHandler event/partial/prompt-boundary tests and scientific-reference web tests to GREEN. No changes to ACK/restore route signatures or shape.

## Task 6 — Strict JS report and numeric-card rendering (RED first)

Files: new `evidence_report.js`, narrow `main.js`, `index.html`, new Node test.

- [ ] Add Node VM cases for every schema/status/budget boundary, accessor/prototype poisoning, invalid Unicode, duplicate/conflicting projection, wrong trace and late/new-request events. Use exact same event fixtures generated by Python for valid cases; forged getters must not run during rejection.
- [ ] Server tests verify exact canonical hashes. JS verifies digest syntax/correlation, not a fresh serialization/hash of floats or raw rows it does not receive. Add a cross-language fixture containing both integer-valued floats and non-integer descriptor values to prevent a Python/JS numeric-encoding false rejection. No extra cryptographic dependency is required.
- [ ] Add DOM harness assertions: Chinese headings/target/partial warnings/actual Top-N; property text equals source values; hostile tags/markdown/URLs are inert text; no `innerHTML`, raw HTML parsing, metadata lookup, property fetch or card creation from report data. Numeric zero/negative LogP remain numeric, missing is unavailable not zero.
- [ ] Add reference-join matrix: wrong presentation/revision/order/generator/candidate/canonical structure gets no values. Same candidate ID in another collection never aliases. Pagination retains only that mounted collection's values. Report renderer throws => existing candidate mount result/ACK keys unchanged; report-only mount cannot ACK. ACK failure disables selection but does not relabel valid values as experimental evidence.
- [ ] Run `node tests/home_evidence_report_test.js`; expect RED for missing helper, not an altered/relaxed original assertion.
- [ ] Implement the fixed helper API above and design §§6–8. Mount a Chinese report block using text nodes alongside existing content; decorate already-mounted card property containers through exact row lookup. Do not change candidate event normalization, introduce a `properties` field into CandidateSet, restore `selectMoleculeCandidates` or change complete signature.
- [ ] Wire lifecycle start/trace observation/report enqueue/terminal take/clear/disconnect and pagination. Retain baseline candidate renderer's mount result even if optional enrichment fails. No report storage; refresh restores only structures using unchanged reference protocol and explicit no-independent-property message.
- [ ] Add helper include before main, cache-bust only relevant helper/main assets. Update only the literal current-main token expectation in `tests/home_workflow_completion_behavior_test.js` to the new approved token; preserve all completion/terminal-label behavior assertions. Keep old lineage comments needed by current tests; do not relax substantive existing Node trust assertions. Run new Node suite and all four original Node suites to GREEN, plus `node --check` for changed/new scripts.
- [ ] Whenever a helper script is added, update the exact corresponding `PAGE_SCRIPTS` literal in `tests/test_static_placeholder_cleanup.py` and run the entire isolated root module. Keep strict order, local URLs, forbidden/placeholder assets and cleanup assertions; Agent-only full/Node cache checks cannot replace this gate. PR77's correction and actual RED/GREEN are recorded above.

## Task 7 — Same-frame end-to-end and required original regressions

- [ ] Complete `test_evidence_report_frames.py`: real local executor/session, synthetic deterministic target/generator, real RDKit PropertyCalculator/CandidateRanker, temporary SQLite and actual ChatHandler. No generator/provider network. Verify positive values/digests and partial failure semantics from captured frames, not handcrafted copies of desired output.
- [ ] Feed JSON-serialized **same captured frames through stdin** to `node tests/home_evidence_report_test.js --frames-stdin`; the harness must invoke actual helper and main completion/mount wiring. Assert displayed Chinese labels/values, unchanged canonical IDs/order and exact ACK payloads. A Python driver may own the Node child; use UTF-8 and a bounded timeout. No fixture/runtime output written in the original tree or repository.
- [ ] Submit captured ACK bodies to the existing FastAPI reference-confirm route using in-process ASGI and the temporary store. Test success, wrong-owner denial, ACK failure, clear/late ACK, restore unchanged exact keys and absence of restored numeric cache. Transport/JS failures cannot create reference authority.
- [ ] Run this mandatory focused Python set with normal isolated runner (prefix all paths with worktree absolute path):

```text
tests/agent/test_candidate_contracts.py
tests/agent/test_generated_candidate_validation.py
tests/agent/test_candidate_alignment.py
tests/agent/test_candidate_ranker.py
tests/agent/test_property_report_boundaries.py
tests/agent/test_explicit_molecular_input.py
tests/agent/test_admet_whole_input.py
tests/agent/test_reverse_target_complete_input.py
tests/agent/test_chat_handler_agent_events.py
tests/agent/test_chat_handler_partial_results.py
tests/agent/test_analysis_contract.py
tests/agent/test_scientific_reference_contracts.py
tests/agent/test_scientific_reference_store.py
tests/agent/test_scientific_reference_web.py
tests/agent/test_scientific_reference_execution.py
tests/agent/test_scientific_reference_resilience.py
tests/agent/test_scientific_reference_browser_lab.py
tests/agent/test_evidence_report_contract.py
tests/agent/test_evidence_report_snapshot.py
tests/agent/test_evidence_report_frames.py
```

Use `-q -p no:cacheprovider --tb=short -rs`. `test_analysis_contract.py` depends on the approved integration gate; its absence is a blocked dependency, not a skip masquerading as success. Browser-lab/environment skips are reported exactly; do not start a live service or read production assets to remove them silently.

- [ ] Run the complete isolated root module `tests/test_static_placeholder_cleanup.py` with the same options (command above), independently of the Agent suite; all seven tests must pass. Keep the original homepage cache-token assertion in `tests/home_workflow_completion_behavior_test.js` below.
- [ ] Run all existing Node suites with their substantive assertions preserved (only the Task 6 cache-token literal adapts) plus the new report suite; required named core:

```text
node tests/home_agent_task_panel_test.js
node tests/home_workflow_completion_behavior_test.js
node tests/home_structured_molecule_render_test.js
node tests/home_scientific_references_test.js
node tests/home_evidence_report_test.js
node --check src/web/static/js/home/evidence_report.js
node --check src/web/static/js/home/main.js
```

- [ ] Run isolated full `tests/agent` regression on the eventual integrated revision, then changed-Python in-memory compile and `git diff --check`. No bytecode/compileall writes unless separately allowed. Check scope, original fingerprints, no secrets/assets, no new skip/xfail and no unrelated edits. No health/deployment/real-acceptance command is implied by this plan.

## Task 8 — Parent review/handoff gate, not automatic publication

- [ ] Self-review and obtain independent SPEC then QUALITY review of the exact functional diff: source/data integrity, additive transport, immutable results, current/legacy frame compatibility, mandatory tests and disclosure of missing/dynamic/restore functionality. Fix only authorized scope with reproductions first.
- [ ] Update the G1 handoff with exact base/dependency heads, files, RED/GREEN commands/results, actual end-to-end captured-frame evidence, count/skips, known limits and original-path preservation. Record package 6 audit complete and G1 implemented only when those gates pass. Do not close package 7/8 or historical sandbox/recovery/G2 work.
- [ ] Stop for parent's integration instruction. Explicitly stage only approved files if a later commit is authorized; do not `git add -A`, push/create/merge a PR or deploy based on this plan. The present turn commits only the separately accepted audit and authorized main merge; these new design documents are for review.

## Spec-to-task self-review matrix

| Design requirement | Plan coverage |
|---|---|
| Frozen base, #66 merged, #67 pending, G1-only | Task 0, 8 |
| Exact additive bounded/versioned v1; no old protocol | Task 1, 5, 6 |
| Independent row/source/trace/input/output/observation identity | Task 2, 3 |
| No metadata trust, no ledger/result mutation, static missing digest honesty | Task 1–3 |
| Chinese counts/provenance/status and actual ranker Top-N | Task 4, 6, 7 |
| Safe DOM, failure isolation, original ACK and exact restore | Task 5–7 |
| Original test intentions plus current mandatory tests and same-frame chain | Task 3–7 |
| Future package 7 consumes same seam; no activation/fake dynamic proof | Task 3's unsupported-binding cases, Task 5 gating, Task 8 |

### Original residual test-intent mapping (read-only source, no transplant)

| Original dirty test / assertion | Required current-protocol acceptance |
|---|---|
| `test_target_design_presentation_filters_canonical_candidates_and_reports_boundaries` | Tasks 3/4/6/7: deduped CandidateSet identity, independent property numbers, request/actual counts, partial ADMET/activity outcomes, Chinese report and no invented energy/potency. No raw-array presenter or final-answer rewrite. |
| `test_target_design_report_uses_requested_target_in_scientific_boundary` | Task 4 + Node/frame tests: separate PDE5A/EGFR fixtures, target-specific report text, no fixed PDE5A claim for EGFR. |
| `test_chat_handler_sends_only_structured_target_design_candidates_on_complete` / empty-structured counterpart | Task 5/7 plus existing event suite: strict separate candidate events, empty/no-valid candidate handling and unchanged completion; do not resurrect the obsolete completion payload shape. |
| Original task-panel/completion Node changes | Task 6/7 reruns the current original-named Node suites and strict-render/reference suites without weakening their metadata, signature, mount or ACK assertions. |
| Original whole-line Base extraction tests | Retain current whole-input regression suites in Task 7; global extractor/optimization-input implementation remains parent-owned G2, not a G1 code change. |

No application code or tests were written/run in the design turn. The audit's former passing counts are not this plan's implementation evidence. Parent review is the next action.
