# G1 static evidence report — freeze for parent SPEC → QUALITY

Date: 2026-09-25. Status: **local implementation and focused offline acceptance complete; independent reviews, full Agent slot, and integration pending.** This is not closure of all package 6 features or packages 1–8, not a dynamic-entry release, and not true scientific/model acceptance.

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

## Actual RED → GREEN and offline verification

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

1. Parent performs **SPEC → QUALITY** review against exact local freeze diff from `6b9e5c0`; this self-check is not independent double review. Fix only approved findings and rerun affected focus/frame/Node tests before publication.
2. When parent's queue reaches G1, run the isolated **full Agent** suite on the agreed integrated revision (including later main changes only when authorized); record exact count/skips/failures. Do not claim current focus verifies PR69 or other parallel batches.
3. Parent integrates approved G1 with its functional batch and updates the package acceptance ledger. G2/recovery remain parent-owned; P7A1 admission/dynamic bindings require separate explicit design/implementation. No push/PR/deploy is authorized by this freeze.
