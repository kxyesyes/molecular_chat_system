# G1 static evidence report — freeze for parent SPEC → QUALITY

Date: 2026-09-25. Current status: **G1 `a90581b` dual-approved per parent; authorized local PR73 + PR75 main integration and the single isolated full Agent run are GREEN.** Full snapshot is `fcb84bd4bf207593078d8c4871f49539b3641e93`; heavy slot released. Parent retains final integration/publication authority. This is not closure of all package 6 features or packages 1–8, not a dynamic-entry release, and not true scientific/model acceptance. Earlier review-pending restrictions/results below are historical and superseded by the integration record immediately below.

## Authorized main integration and one full Agent run — GREEN

Parent reports **SPEC Faraday APPROVE (407 + 5 Node)** and **QUALITY Descartes APPROVE (413 + 5 Node + 3 SQLite/race probes)** for `a90581b90cbef9b7dad4be98a7a450f086d9b6e9`, with 20 hashes unchanged. These are attributed review results, not tests rerun by this task. The approved G1 source/test bytes remain unchanged through both integrations and the full run.

### Exact integration lineage / scope

- Starting branch `codex/historical-residual-disposition` was clean at `a90581b`.
- Local `origin/main` exactly matched authorized PR73 commit `0295e9960b15c850bb212e434da66824181ef546`. Merged without conflicts as **`5086b86376282937e2c660ba05118cca4fffc77c`** (tree `488a719aa8e204576fca961b2f8ca899337ff9b1`). PR69–73 changed 34 files relative to G1's previous main `e173d43`; zero overlap with the 20 G1 paths.
- While the first **focus**, not full, was running, parent authorized PR75 inventory test/docs delta `7b5611fed039aa7aebde62063f45e45e965cf23a`. Local ref, commit and tree **`6f42e12156d80c88738ec2eba42caed94994d6cd`** matched the supplied values. Waited for that focus to finish, then merged without conflicts as **`fcb84bd4bf207593078d8c4871f49539b3641e93`** (tree **`6c2ffb2ee666c1d99f9bec3df3c2abb60d1080aa`**).
- PR75 added `docs/AGENT_TOOL_CONTRACTS.md`, its plan and `tests/agent/test_tool_contract_inventory.py`, and updated the parent's `remaining-through-step8.md`; these four upstream changes were merged, not edited here. Zero overlap with G1's 20 paths; inventory contributes 16 tests.
- No production conflict, manual resolution, fixture relaxation or G1 implementation edit. After full and before documentation, HEAD/tree/clean state and all 20 byte fingerprints matched the pre-full lock. Local `origin/main` was still exact `7b5611f`, with **zero new main delta**; no fetch/network/extra merge needed. ADMET PR74 was not included. No original dirty-tree/asset access or edits, push, PR, deployment or model/server activation.

### Actual integration verification

| Stage / exact snapshot | Result |
|---|---|
| Original 20 focus + 13 collision files at `5086b86`, session 19387 | **2421 passed, 7 warnings, 98.48s**, exit 0 |
| Rerun original 20 focus + 14 collision files (including PR75 inventory) at `fcb84bd`, session 73704 | **2437 passed, 7 warnings, 100.47s**, exit 0; no skips |
| G1 key minimum web-dependency profile at `fcb84bd`, session 44932; version assertions **Pydantic 2.5.0 / FastAPI 0.104.1** | **634 passed, 10 warnings, 50.57s**, exit 0; no skips |
| Five relevant Node scripts on both merged snapshots | All exit 0; task panel, completion, structured molecule, scientific references, evidence report |
| **Only full `tests/agent` run**, host profile, `fcb84bd`, **session 62470** | **6914 passed, 2 skipped, 7 warnings, 333.83s**, exit 0; **zero failures** |

Host warnings are existing SWIG/FastAPI deprecations. Minimum profile additionally emitted the existing Pydantic protected-namespace warning and two pytest AnyIO assertion-rewrite warnings from importing/verifying the profile before pytest. Nothing suppressed. No dependency installation or alteration.

The 20 original focus paths are enumerated in the initial-freeze section below. Collision additions (all complete files under `tests/agent/`, no deselection): `test_generation_ranking_contract.py`, `test_generation_ranking_contract_integration.py`, `test_target_tool_contract.py`, `test_generator_optimization_input.py`, `test_planner_responsibilities.py`, `test_task_planner.py`, `test_planner_step_templates.py`, `test_planner_template_execution.py`, `test_supervisor_agent.py`, `test_supervisor_runtime_integration.py`, `test_tool_registry.py`, `test_workflow_resume.py`, `test_generation_temperature_transport.py`, `test_tool_contract_inventory.py`.

Minimum-profile paths: the three `test_evidence_report_{contract,snapshot,frames}.py` files, `test_scientific_reference_web.py`, `test_analysis_contract.py`, `test_generation_ranking_contract_integration.py`. These include actual standard planner/typed registry → local RDKit/SQLite → capture → actualMain DOM → reference API, four-or-none metrics, Chinese sections/reasons/units, real partial rows, failed-step consistency and report-failure isolation. They are deterministic offline execution/transport evidence, not real molecular generation or efficacy acceptance.

All Python invocations use the **existing RAG isolation runner verbatim with only its repo cwd replaced**; minimum profile prepends the already-retained CI web profile and asserts its versions as documented in the merged 4C plan. Runner clears inherited non-allowlisted environment, uses temporary cwd/config/runtime DB paths, disables real-service switches, copies/hash-checks only the three tracked evaluation JSONL fixtures, invokes normal child pytest and cleans up. This is the normal runner (not the earlier custom in-process/plugin-disabled G1 focus recipe). Full's **sole path argument was `tests/agent`**, with child options `-q -p no:cacheprovider --tb=short -rs`. No root full or second Agent full was run. JS syntax and whitespace checks passed.

### Full lock / skips / slot release

- Full started **2026-09-24 22:37:51 UTC / 2026-09-25 06:37:51 Asia/Shanghai**, session **62470**, clean HEAD/tree as above. No merges, edits or other heavy runs occurred while it ran. Slot was announced released immediately on its exit-0 result; it is available to parent for P8/A1, not retained for documentation.
- Post-run lock verification completed **2026-09-24 22:43:38 UTC**, still clean and unchanged. Only G1 documentation is updated afterward.
- Exact skips: `tests/agent/test_decision_chat_acceptance.py:149` — **directory symlinks unavailable**; `tests/agent/test_harness_shadow.py:277` — **performance test disabled**. No added skip or assertion weakening.
- Full result is tied to **`fcb84bd`**, not future main/ADMET/P7 revisions. Historical natural `artifact_failed` exact cause remains unknown; full GREEN does not retroactively attribute that incident or turn the parent's deterministic broker probe into scientific acceptance.

### Twenty-file impact / byte SHA-256 at full lock

Paths are the exact `git diff --name-only 6b9e5c0..a90581b` set. Each file was unchanged from approved `a90581b` through both merges and before/after full. Aggregate recipe: concatenate sorted `path + " " + lowercase_sha256 + LF`, encode UTF-8 without BOM, then SHA-256. All-20 aggregate **`5037d3d7bbf8c28a42d5c3cea48649e26061a0617db286df986aa13bbe27ec91`**; 16 non-doc source/test files **`2c48070b69bff63d1bbfcb252c2ffbc709e62e5a5131ae16d2f548584205d2c4`**.

| Path | Byte SHA-256 at tested freeze | Integration impact |
|---|---|---|
| `docs/handoff/2026-09-25-g1-evidence-report-freeze.md` | `eb55f013b945b35da7f3db853e32b175d19915a9734f53d62ccc196895fb1dc1` | unchanged / no conflict |
| `docs/handoff/historical-residual-disposition.md` | `14852f20c3202e7c2a5db4293218b66784b83d04e0edf48db7a8611b3db9b5a5` | unchanged / no conflict |
| `docs/superpowers/plans/2026-09-25-evidence-bound-report-cards.md` | `c06513a2a1e4293fdd0bd113fd7825ebe2caa1007013400e7a04aad921c79785` | unchanged / no conflict |
| `docs/superpowers/specs/2026-09-25-evidence-bound-report-cards-design.md` | `94a070c6a273c11f404f818bab45eda4ffe12ff65d1a0c8ff936c2c974dce527` | unchanged / no conflict |
| `src/agent/contracts/scientific_report.py` | `d1850c6d7f110ffe3a1794c654207b92f6f86da710fba274ab8d3ac3244d0ea8` | unchanged / no conflict |
| `src/agent/persistence/scientific_references.py` | `e7df9cf139b0ccbd221df7ed42ef7f0fb66ea36c78c26ca604d0d89092fc17ca` | unchanged / no conflict |
| `src/agent/persistence/sqlite_store.py` | `09396ed474ecc5c3ac31bc845813d1fcc6a9decb4c624e7605a11df98b67b024` | unchanged / no conflict |
| `src/agent/presentation/__init__.py` | `84d0c14a388b7cd348725381fc2812f3be0364d5739f9a3d60fa1c0bceee6237` | unchanged / no conflict |
| `src/agent/presentation/evidence_report.py` | `d07d536f6c9b5bb11f28278c49a03f734e185399eef801a39d34ea41ee959624` | unchanged / no conflict |
| `src/web/chat_handler.py` | `7b8cb0956f52763d3fa31e0c64729be58caa4312d11a2e3be4cb39d86d2ada4a` | unchanged / no conflict |
| `src/web/scientific_report.py` | `1c77af2c730d6d661a4fc92639f66242671eb1975debf12e68d5f5b3c1ae48bd` | unchanged / no conflict |
| `src/web/static/js/home/evidence_report.js` | `62d3d463b3e886caef38dee27e390920987a75b17cf6b2a023df58291bd7a67b` | unchanged / no conflict |
| `src/web/static/js/home/main.js` | `66564a79982c6a10e9e760a49d34d21df3bb1e1c39c67d22894ff2dfad46e25d` | unchanged / no conflict |
| `src/web/templates/index.html` | `6c11b7cfab4bffff1bbf56c28fc4ea9b573cb50d612a9d797e475108f4bd0470` | unchanged / no conflict |
| `tests/agent/evidence_report_fixture.py` | `9cf6019589cea426c14586a068600632cbbe5c843205dcb63cdc04da92a6986e` | unchanged / no conflict |
| `tests/agent/test_evidence_report_contract.py` | `9e281164155e1846257a3af16d879af65bba85adeb741682b392e1cf11e350ec` | unchanged / no conflict |
| `tests/agent/test_evidence_report_frames.py` | `d18c3fd0efadaf12eaf49bdfcdfe1217ba2b14d485bb045926dd88aed15af4a4` | unchanged / no conflict |
| `tests/agent/test_evidence_report_snapshot.py` | `ac7cd136c98caaec26c028633604bc1dc0d71cf83990b659d9984461048b8e48` | unchanged / no conflict |
| `tests/home_evidence_report_test.js` | `66eb699f531543ac4fa8d14febfea5f3db523bdb0d4100e2aa93fb7a1ff76270` | unchanged / no conflict |
| `tests/home_workflow_completion_behavior_test.js` | `a40e25e8d6b68ac75d308defc4526527df5d2458029b9182c9b6de5f9b52b382` | unchanged / no conflict |

The table records the **tested pre-documentation snapshot**, not a recursive hash of this updated document. Only this handoff and the G1 plan are edited/explicitly committed afterward; the 16 source/test aggregate remains unchanged. Parent owns subsequent publication/integration; no new code review or PR approval is inferred from full GREEN.

## SPEC2 revision — four-or-none descriptors and Chinese UI

Parent-reported reviewer **348 passed** is independent review evidence, not a locally repeated result. Original step-consistency/weight findings are closed per parent; their fixes and regression tests remain included in this freeze from `bf4a2cf`. New changes still touch only the same two production files, two tests, this handoff and the plan. No snapshot/projector/ranker/result/ledger/CandidateSet/ACK/restore changes, no original dirty tree/assets, network, full Agent, model/server activation or push.

1. **SPEC §6:** Both DTO boundaries previously accepted resealed `partial/partial_source` rows with one or more null metrics and displayed remaining numbers. Both now require **all four finite validated values** for `available` or `partial`; malformed whole events are rejected, never stripped/repaired. Rejection leaves the existing no-independent-properties cards and exact ACK path intact. Genuine partial property sources with all four metrics on their accepted row still pass; missing candidate rows retain four nulls and explicit unavailable state. The producer was not changed: any malformed positive row reaching final validation suppresses the sidecar, not the old terminal/candidates.
2. **SPEC §§5–8:** One controlled `REASONS` map now supplies Chinese reason labels to property summaries, ranking and step displays. A real capture with an unreconstructable stored query displays `input_unverifiable` as “无法验证排序或性质输入” with no ranking score, rather than generic unprovided alone. Existing DOM now has exactly the six approved section headings and “本次结果快照”; MW uses Da, TPSA Å², LogP/QED no units. Re-review also aligned the exact report title, missing version “未记录”, “展示已截断” marker and fixed scientific-boundary sentence. Only text nodes/unchanged DOM primitives; no CSS redesign, raw reason markup, model prose, recomputation or invented defaults. Actual ranker weights remain displayed.

### Actual SPEC2 RED → GREEN and focus

- In-memory isolated MedChat Python runner, adapted from the existing RAG isolation recipe: temporary cwd/config/SQLite paths, cleared non-allowlisted environment, plugin autoload off, `-B`, no pytest cache, sockets denied except Windows asyncio's internal loopback socketpair. No source fixture copies or production assets needed for this selection. Normal conftest retained.
- `test_evidence_report_frames.py -k "spec_four or spec_chinese"`: **33 failed, 26 passed, 11 deselected, 25.90s** before fixes → **59 passed, 11 deselected, 26.23s** after fixes. RED comprises 28 Python acceptance failures (14 incomplete nonempty-value subsets × succeeded/real-partial captures), two actual DOM numeric-leak failures and three missing-six-section failures. All-null, missing-key, bool/string negative cases already rejected and remain covered.
- New matrix: 15 nonempty null-subsets of four descriptors + each descriptor individually absent/bool/string (27 cases), derived from each of actual succeeded and real partial captures. Python reseals before validation; Node receives the same rejected variants via `--spec-rows-stdin` and exercises actualMain, card mount and ACK. No handwritten positive report replaces the real capture. All four values on the original partial row continue to render.
- `--spec-chinese-stdin` is driven by three real RDKit/SQLite captures: actual standard plan, partial properties, and only temporary stored-query modification that makes the real projector produce `input_unverifiable`. Checks exact section labels, snapshot marker, units, reason display, caveat, missing versions and no score when unavailable; confirms unchanged AgentResult, no model call and exact ACK. Additional explicitly negative DTO derivatives cover all 14 controlled reason labels, hostile reason rejection and truncation text; these are not claimed as 14 real tool executions.
- **1373 passed, 7 warnings, 88.37s; no skip/xfail** across the same 20 mandatory focused Python files listed under the initial freeze below, using `-q -p no:cacheprovider --tb=short -rs`. This is an enumerated focus, **not full `tests/agent`**. Seven warnings are existing SWIG/FastAPI deprecations.
- Five relevant Node scripts passed: task panel, workflow completion, structured molecule, scientific references and evidence report. Both changed JS files passed `node --check`; both changed Python files passed in-memory `compile(..., 'exec')`; `git diff --check` passed. Prior 279/1312 counts are historical stages, not this run.

### Whole approved SPEC re-review (not independent approval)

| SPEC | Rechecked source/test evidence and disposition |
|---|---|
| §§1–3 scope/compatibility | Static live-only sidecar; no old raw-array protocol, property fetch, model rewrite, G2/P7 activation or asset migration. Scope unchanged. |
| §4 ownership/snapshot | `report_snapshot` reuses owned `_source` transaction and selected view TTL/order/version checks; `prepare_report_event` rechecks version/presentations. Snapshot ownership/current/read-only and frame race tests pass. |
| §5 observation/input/row proof | `_accepted`, `_property_rows`, `_ranking` still require full live/checkpoint match, ledger/evidence/input/digest proof and exact static bindings. Contract mismatch/tamper/read-only tests pass. Four-or-none DTO fix adds no producer repair. Units and unknown version labels now match UI requirements. |
| §6 DTO/bounds/states | Strict exact shape, detached JSON bounds, enum/cross-reference/digest guards remain. New missing-metric matrix and original step status/source/reason matrix pass both boundaries. No accepting malformed events by deleting fields. |
| §7 report/ranking/outcomes | Six sections, exact title/caveat, controlled unavailable reasons, actual Top-N/order/scores/weights, source counts and original partial/failure labels verified by actual capture→DOM and existing ranker tests. No new ADMET/activity/docking numbers. |
| §8 lifecycle/DOM/ACK | Snapshot marker added; existing no-store, late/conflicting/failed-terminal, report/property rendering failure, card identity/ACK/restore and nonmutation tests pass. DOM-only enrichment remains live-only; refresh restores no numeric report. No browser screenshot or active pagination feature claim. |
| §9 package 7 | Static-only stays explicit; dynamic markers do not admit positive mapping. P7 integration fixture/admission/binding remains separately owned, not completed by a flag. |
| §§10–11 acceptance/history | All 20 named mandatory Python suites rerun as focus, including real local RDKit/ranker/SQLite→frames→JS→ASGI confirm/restore. Historical design/audit evidence remains historical; full Agent, SPEC/QUALITY approval and integrated-main validation remain parent gates. |

No further confirmed implementation gap was identified in this bounded source/test re-review. This is **not** exhaustive proof of every theoretical permutation: prior declared limits (concurrent-writer timing beyond controlled recheck, exhaustive multi-generator ambiguity/budget permutations, active pagination and visual browser acceptance) remain disclosed below. Parent/SPEC owns acceptance of those limits and the current fixes.

## Faraday SPEC revision — uncommitted diff from `bf4a2cf`

Historical first revision: parent subsequently reported these two P2 findings closed; its then-current commit restriction and results below are retained as history, superseded by the SPEC2 authorization/status above.

Parent requested verification and minimal fixes only; **no full Agent (4C has the next slot), no push, no commit until parent review**. Both findings reproduced against actual `execute_standard_plan` → `capture` frames:

1. Changing the failed ADMET step's status alone to `succeeded`, leaving null source and `source_failed`, then resealing its digest was accepted by Python/JS and reached the report DOM. Both DTO boundaries now require the following exact step/source/reason consistency. No producer/result/ledger/observation state is rewritten.

| Step status | Required source | Allowed reason |
|---|---|---|
| `succeeded`, `partial` | Existing accepted source with identical `step_id`, `tool_name`, **and status** | `none` (source accepted; does not promote a partial observation) |
| `failed`, `rejected`, `cancelled` | null | `source_failed` |
| `skipped` | null | `source_unavailable` |
| `unknown` | null | `source_mismatch`, `source_unavailable`, or `source_failed` (unverifiable source does not establish a positive status) |

2. Actual `ranking_evidence.weights_used` was transported but absent from the DOM. Each Top-N entry now renders a text-only Chinese line from the recorded values, e.g. **实际排序权重：性质 1；ADMET 未提供；活性 未提供**. Null is not a fabricated zero. No score/weight normalization, recomputation or model rewriting. Tests compare report weights with the real ranker output, reject hostile weight strings, and assert frames/results remain unchanged after rendering.

Production changes are only `src/agent/contracts/scientific_report.py` and `src/web/static/js/home/evidence_report.js`. Regression additions are only `tests/agent/test_evidence_report_frames.py` and `tests/home_evidence_report_test.js`; this handoff and the plan record the revision. CandidateSet/ACK/restore, snapshot/full-observation checks, post-candidate delivery, no-store/partial/error bypasses and static-only scope remain unchanged. Original dirty tree/assets and the parent's sandbox probe were not touched or rerun.

### Actual revision RED → GREEN

- `test_evidence_report_frames.py -k "spec_step or same_rdkit"`: **6 failed, 5 deselected, 5.77s** before production edits (two consistency failures; four actual frame→DOM weight failures), then **6 passed, 5 deselected, 5.95s** after the minimal fixes.
- Explicit DOM-first check of the resealed ADMET forgery: **1 failed, 10 deselected, 2.76s** before production edits; it rendered a rejected/inconsistent report. The final Node path now refuses all 14 resealed inconsistent cases while preserving exact baseline candidate ACK, and keeps valid failure/rejected/cancelled/skipped plus real partial/unknown labels.
- Final isolated focused run: **279 passed, 7 warnings, 34.92s; no skip/xfail**. Same runner/isolation as the initial freeze, with these paths and `-q -p no:cacheprovider --tb=short -rs`:
  `tests/agent/test_evidence_report_contract.py`, `test_evidence_report_snapshot.py`, `test_evidence_report_frames.py`, `test_candidate_ranker.py`, `test_chat_handler_agent_events.py`, `test_chat_handler_partial_results.py`, `test_scientific_reference_web.py` (all under `tests/agent/`). Warnings remain SWIG/FastAPI deprecations.
- All five relevant Node scripts passed: `home_agent_task_panel_test.js`, `home_workflow_completion_behavior_test.js`, `home_structured_molecule_render_test.js`, `home_scientific_references_test.js`, `home_evidence_report_test.js`. The Python driver runs the actual positive frame/ACK path and the new `--spec-steps-stdin` path; standalone report script remains the hostile preflight check.
- `node --check` passed for changed helper and Node test; in-memory `compile(..., 'exec')` with `-B` passed for both changed Python files; `git diff --check` passed. Nothing staged or committed. Prior **1312**/13-Node results below belong to the initial freeze, not a newly repeated full focused run.

SPEC retains approval ownership. Remaining sequence: parent/SPEC re-review of this working diff → QUALITY after SPEC approval → commit/integration only when authorized; full Agent remains in the parent queue.

## Exact scope and Git lineage

- Branch/worktree: `codex/historical-residual-disposition` / `historical-residual-disposition`.
- Accepted audit commit `1581f3e`; prior main merge `e0d73d1`.
- Approved design/plan committed **before implementation** as `72c1ad4`.
- Requested reviewed main **`e173d432f767fe76e1c1f101d9cd8824ffee612d`**, containing PR66/67/68, merged locally as **`6b9e5c0da50cfd70ec5be3da9596eea822a1fe22`**. Functional review range starts at `6b9e5c0`; this handoff is included in the local freeze commit.
- Parent subsequently reports PR69/main `16b9157`. It is **not merged/tested in this worktree**. No network/fetch/push/PR/server/model activation. Parent controls integration and required CI.
- All writes/staging/local commit are scoped to this worktree. Original dirty checkout and its 13 paths/assets are not edited, staged, restored, copied or rehashed in this implementation turn. Prior audit's 13/13 fingerprint result is historical, not a newly repeated measurement. Excluded manifest asset was not read.
- No changes to result/ledger/runtime/analysis/ranking algorithms, CandidateSet contract, reference controller, ACK/restore route or DB schema. No new report persistence, property fetching, model rewriting, generic report framework or decision activation.

## Implemented seams and review focus

| Files | Boundary |
|---|---|
| `src/agent/contracts/scientific_report.py` | Exact versioned DTO, bounded plain JSON, finite/null descriptors, source/key/state correlations, projection digest, detached return. |
| `src/agent/persistence/scientific_references.py`, `sqlite_store.py` | Narrow owned snapshot facade; reuses `_source` transaction/source-version/budgets, checks live presentation TTL/identity/order. No writes. |
| `src/agent/presentation/__init__.py`, `evidence_report.py` | Full stored/live observation match (only transport `step_id` removed), detached evidence ID verification and live ledger comparison, input/output/observation hashes, existing CandidateSet + PR67 validation, static binding reconstruction, exact row/canonical identity/alignment, actual ranker prefix. |
| `src/web/scientific_report.py`, `chat_handler.py` | One optional `scientific_report` after already-sent candidate events, before existing terminal; owned source/version/presentation recheck off-loop. Missing store, projector errors and report-only send errors preserve old completion. |
| `src/web/static/js/home/evidence_report.js`, `main.js`, `templates/index.html` | Strict detached/frozen DTO; active trace learned from accepted candidate event, live lifecycle, Chinese safe-DOM report and independent property slots on existing cards. Exact reference/generator/candidate/structure join; mounted keys/ACK unchanged. Failed terminal clears pending report/candidates. No numeric restore. Cache token advanced. |
| `tests/agent/evidence_report_fixture.py`, `test_evidence_report_contract.py`, `test_evidence_report_snapshot.py`, `test_evidence_report_frames.py`, `tests/home_evidence_report_test.js` | Real offline runtime/SQLite/RDKit/ranker fixtures, tamper/ownership/readonly/partial/failure tests, same-frame JS mount and ASGI ACK/restore. |
| `tests/home_workflow_completion_behavior_test.js` | Only expected main-script cache-token literal changes; original substantive assertions retained. |
| Design/plan headers, residual audit, this handoff | Current approval/freeze state plus explicitly attributed parent diagnostic; original audit results remain historical. |

Four descriptors are copied from accepted independent property rows: molecular weight, LogP, TPSA, QED. No evidence means no number; generator `metadata.properties` is never a fallback. Partial property rows remain partial, missing rows stay unprovided, failed/unknown sources do not become successful observations. Sources expose original tool/model versions or null; property method is RDKit descriptors, not experimental validation. Generation counts come from the validated CandidateSet, not card count. Ranking uses recorded ranker Top-N/order/score/missing-evidence weights, never recalculation or model recommendations.

Static checkpoints without a recorded output digest remain eligible only through full checkpoint/live equality plus owned source snapshot, evidence identity and exact static input hash proof. DTO labels that origin `checkpoint_snapshot`; it does not fill missing provenance or mutate the ledger. Dynamic request-binding markers do **not** enable numeric projection. P7 must separately establish admitted references and explicit verified bindings.

## Initial freeze RED → GREEN and offline verification (`bf4a2cf`, historical)

Runner: existing RAG isolation recipe adapted **in memory**, repository cwd replaced by a fresh temporary directory; normal conftest retained, `-B`, plugin autoload disabled, pytest cache disabled. Runtime config/state/DB/cache paths are temporary/nonexistent placeholders; real-service switches are zero. Python sockets denied except Windows asyncio's internal loopback socketpair. No production env/credentials/log/DB/index/weight reads. Node consumes synthetic frames via stdin; no fixture artifacts written into the original tree. No installed dependencies changed.

Initial runner attempt explicitly requested absent `pytest_asyncio.plugin` and failed before collection. Removing that unnecessary runner option yielded the baseline below. This is not a feature RED or a resolved sandbox failure. Fixture development also corrected a missing transport trace, string versus enum error code, and invalid synthetic target source ID; these were fixture issues, not production fixes.

| Actual run | Result |
|---|---|
| Preimplementation focused baseline: alignment/contracts/generated-candidates/property boundaries/ranker/analysis/chat events/partial/reference web/store/resilience | **927 passed, 7 warnings, 36.47s** |
| New contract/snapshot RED | Missing presentation/contract modules and missing store facade; later corrected fixture produced exact facade `AttributeError`. |
| Initial report frame RED | **2 failed, 14 passed**: `scientific_report` absent from actual frames. |
| Initial JS RED | New helper file absent; actual Node assertion failure. |
| Failure-isolation RED | **3 failed, 4 passed**: report-only send exception changed terminal; pending report displayed at failed terminal. Narrow hook/lifecycle fixes returned GREEN. |
| Evidence-flag RED | **2 failed, 15 passed**: contradictory quality demo/fallback flags could pass. Explicit fail-closed checks fixed it. |
| Mandatory final focused set below | **1312 passed, 7 warnings, 67.32s; no skip/xfail** |
| All 13 current `tests/*test.js` scripts | All exit **0**. New script's standalone mode tests hostile preflight; its positive same-frame tests are run by the Python driver below. Existing 12 Node scripts retain their substantive checks. |
| `node --check` on `home/evidence_report.js` and `home/main.js`; `git diff --check` | Exit **0** |
| Changed/new Python in-memory `compile(..., 'exec')`, `-B` | **11 files passed**, no bytecode writes |

Seven Python warnings are existing SWIG and FastAPI `on_event` deprecations. No stale sandbox flake was rerun or described as resolved by these counts. Full `tests/agent` was **not run**: parent queue is Planner → 4C → G2 → ADMET → G1.

Final focused invocation uses the isolated runner with `-q -p no:cacheprovider --tb=short -rs` and these actual paths:

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

### Same-frame acceptance, not separate handcrafted positive DTOs

`test_same_rdkit_sqlite_frames_actual_js_mount_then_reference_api` passes for completed, optional activity failure, partial property coverage, and **actual standard planner/compiler/executor/session** via Supervisor + existing tool registry. Target and generator are explicitly deterministic offline fixtures; independent descriptors are from local real RDKit and prioritization from real `CandidateRanker`. Their checkpoints are persisted in real temporary SQLite.

Actual ChatHandler frames pass through stdin to `node tests/home_evidence_report_test.js --frames-stdin`. It runs the current candidate normalizer, report helper, actual main completion/display/card functions and reference controller. It checks Chinese report, MW **46.07**, LogP **-0.001**, TPSA **20.23**, QED **0.407**, real Top-N, exact card IDs/order, partial labels, no numeric storage, stale/late/repeated/conflicting traces, inert hostile DOM text and failure isolation. Returned exact mounted ACK bodies are submitted to the unchanged reference-confirm ASGI route: correct owner succeeds, wrong owner gets 404. Restore retains exact original keys and candidate events, contains neither report event nor restored numbers. No live browser/server/model, Vina or other scientific provider was started. This is offline deterministic UI/protocol acceptance, **not real molecular generation or efficacy validation**.

### Deliberate limits / deviations for review

- Static-only live sidecar. No dynamic reference admission, binding implementation, numeric refresh/recovery, normal decision entry or package7 flag counted as completion.
- Required property/ranker bindings use current finite static forms. Missing/redacted/unreconstructable inputs suppress numbers; duplicate output providers do not get guessed. Over-budget reports/sources may be suppressed entirely instead of weakening proof.
- The new JS DTO verifies digest syntax and correlations; it does not reproduce Python float JSON hashing or claim browser cryptographic authority. Server verifies hashes.
- Absent/invalid independent property sources conservatively use unprovided/source-unavailable or input-unverifiable states. No attempt to repair alignment, infer backend versions or complete every future reason-code branch.
- Snapshot transaction/source recheck and mutation refusal are tested. A dedicated concurrent-writer timing test beyond controlled recheck mutation, exhaustive multi-generator ambiguity fixtures, and every theoretical hostile/budget permutation from the original checklist are **not claimed** complete.
- Current candidate UI displays all accepted cards in its carousel (pagination code is retained but inactive). The actual card loop is tested; an active paginated UI is not claimed newly implemented.
- Legacy final-answer text is unchanged. New Chinese report is additive, never replacing historical prose or rewriting it with a model. No full visual browser screenshot acceptance is claimed.

## Files inspected

Read/inspected relevant ranges: `AGENTS.md`, `docs/PROJECT_STANDARDS.md`; accepted residual audit and the G1 design/plan; RAG isolation recipe; `contracts/{result,domain,scientific_references,generation_request}.py`; `evidence/ledger.py`; `persistence/{scientific_references,sqlite_store,redaction}.py`; `orchestrators/{__init__,base,workflow}.py`; `runtime/{run_session,workflow_executor}.py`; `planning/{bindings,step_templates,task_planner}.py`; `validators/{candidate_alignment,semantic_inputs,result_validator}.py`; `tooling/{analysis_contract,factory,registry}.py`; `tools/{property_calculator,candidate_ranker}.py`; `supervisor.py`; `web/{chat_handler,scientific_references}.py`; `web/static/js/home/{main,molecule_candidates,scientific_references}.js`; `web/templates/index.html`. Newly written files were reviewed in this worktree.

Inspected existing test fixtures/assertions: `test_candidate_alignment`, `test_chat_handler_agent_events`, `test_scientific_reference_store`, `test_scientific_reference_web`, `test_scientific_reference_browser_lab`, `test_target_driven_design_workflow`; the four original home candidate/completion/reference/task-panel Node suites, plus source inspection of the remaining Node suites for offline execution. The final focused list above records **executed** tests, not a claim to have read every line of each. No original dirty reporting module/test was transplanted.

## Parent sandbox diagnostic (do not rerun)

Per parent's 2026-09-25 report, ignored recovery worktree `scratch/test_manifest_snapshot_probe.py` calls the original manifest reopen/tamper/cross-job test, with real broker/SQLite + original FakeSDK and only artifact `read_file_snapshot` injection. Old committed secure_io `1bba025` versus PR68, crossed with deterministic sibling writes disabled/enabled: **4 passed in 3.16s**. Old/no perturbation succeeds; old/sibling ancestor-mtime perturbation yields unsafe snapshot then manifest KeyError; PR68 succeeds in both and retains original tamper/cross-job rejection. Target file identity/hash stay stable. This proves the same-class trigger is fixed on that consumer path, **not unique attribution of the historical naturally occurring artifact_failed**. Historical exact cause stays unknown. No separately reproduced business fix remains, no scratch copy/commit or repeated test here, no true scientific acceptance. Also recorded in the [residual audit addendum](historical-residual-disposition.md#later-parent-diagnostic--2026-09-25-reported-not-rerun-here).

## Three remaining actions

1. Parent consumes the dual-approved G1 plus exact PR73/75 integration/full evidence above and decides final functional-batch integration/publication. No new independent review or approval is invented for future changes.
2. Heavy slot is released to parent for P8/A1. Any subsequently authorized main delta gets only its bounded light compatibility check; no second full is authorized here and the `fcb84bd` full result must not be relabelled.
3. Parent updates the package acceptance ledger. G2/recovery remain separately owned; P7A1 admission/dynamic bindings require separate explicit design/implementation. No push/PR/deploy is authorized by this freeze.
