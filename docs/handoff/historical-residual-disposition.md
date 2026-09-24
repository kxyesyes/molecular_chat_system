# Historical residual disposition — package 6

Audit date: 2026-09-25. **Disposition audit complete; residual feature work is NOT complete.** In particular, an integrated Chinese target-design report and independently evidenced property cards remain pending. This document does not mark packages 1–8 complete, authorize deletion of dirty files, or authorize deployment.

## 1. Scope and frozen revisions

- Original checkout: `D:/MedChat/molecular_chat_system`, HEAD `f37744311b732514006d7932644d2d5a5e614fc1`, branch `codex/industrial-agent-platform-design`; read-only throughout.
- Documentation checkout: `D:/MedChat/molecular_chat_system_worktrees/historical-residual-disposition`, branch `codex/historical-residual-disposition`, HEAD/base `ecd6ccad5ffdd155f5944c4136107004e2ea00be`.
- The locally available `origin/main` equals `ecd6cca`; local `main` is older (`3b87853066069231891bad09efefd165ecf5af26`). “Current” below means the frozen `ecd6cca` source, not the stale local main branch. No fetch/network was performed, so remote freshness is not asserted.
- Reviewed PR #66 is compared at `37b66a127dc0a8c20a91f103a271065882b2b40e`; PR #67 at `8c3b6d4acd73d6551bf4819e37e643f1d3f52ba6`. Review/pending-CI status is supplied by the parent. Their exact local source is available, but neither is claimed merged or CI-green. Neither patch was applied to this worktree.
- Original porcelain inventory: nine modified files and four untracked entries, one of which is a directory. Expanding the two reporting source files yields **13 code/document/test files in scope**. The separately listed `data/molecular_faiss_index.index.manifest.json` is **excluded, not opened or hashed**. No other asset inspection was needed.
- Read both checkouts' AGENTS guidance, the original project standards and the current standards' changes (including scientific contracts and Temporal restrictions). The narrower instruction here wins: one new documentation file only; no source/test changes, no original staging, no staging/commit/push/PR/merge anywhere. Parent will integrate this document with a later functional batch.

Disposition labels mean: **superseded** = do not transplant the obsolete mechanism; **migrated** = equivalent intent exists in current code, not a change made by this audit; **pending** = still needs functional/test work; **intentionally retained** = preserve an explicit restriction or historical evidence. None means “delete the original file.” Hunk coordinates below refer to the original dirty file's new-side lines against `f377443`.

## 2. Every original residual

| Original path / hunk | Disposition | Exact current evidence and remaining obligation |
|---|---|---|
| `AGENTS.md:231`; removed old blanket prohibition near old line 254 | migrated; original retained | Current `AGENTS.md` §8 permits only specifically authorized, gated merging; equivalent policy intent, not identical wording. Original explicitly requires passing CI and no unresolved review comments. This audit grants no merge authority. |
| `src/agent/supervisor.py:22`, `172–176`, `332–336`, `344–352`: reporting import, execute/run hooks, `_present_workflow_result` | superseded mechanism; pending report intent | Current Supervisor consumes shared execution results; `RunSession._build_final_result` owns final observations/ledger ([E1](#e1-authoritative-candidates-and-evidence)). No current `src/agent/reporting` package. Do not reattach a mutable raw-result presenter after validation. Chinese presentation remains [G1](#g1-p1-chinese-report-and-independent-property-cards). |
| `src/agent/supervisor.py:190`: top-level presentation metadata export | superseded | Current `tool_result_sequence`, typed `agent_result`, Web projection and scientific-reference publication replace this transport. Current partial projection explicitly handles typed-result metadata when the envelope lacks top-level metadata; no need to restore the old export solely for cards. [E1](#e1-authoritative-candidates-and-evidence), [E2](#e2-current-cards-and-reference-ack). |
| `src/agent/tools/base_tool.py:38–57`: all-lines-valid early return/canonical dedupe | superseded for migrated analysis callers; pending remaining callers | Current properties/likeness/ADMET use complete-input parsing; reverse is separately addressed in PR #66. The old patch still falls through to fragment-mining regex if any line fails, strips punctuation before validation, and depends on Base's heuristic fallback when RDKit is missing. Not safe as a global repair. Full caller inventory in §4; [G2](#g2-p1-bound-the-remaining-extraction-callers). |
| `src/web/chat_handler.py:301`, `304–311`: `complete.molecules` payload | superseded | Strict `molecule_candidates` events precede completion, from accepted observations rather than `metadata.presentation`; current complete-frame signature remains content-only. [E1](#e1-authoritative-candidates-and-evidence), [E2](#e2-current-cards-and-reference-ack). |
| `src/web/chat_handler.py:407–447`: `_workflow_molecules` validates only flags and dedupes strings | superseded | `ChatHandler._candidate_event_from_observation:881` requires success, permitted status, `output_contract=CandidateSet@1`, and strict deserialization before/after sanitization. It is not equivalent to trusting `{valid: true, method: RDKit}` on arbitrary arrays. |
| `src/web/static/js/home/main.js:370`, `1594`, `1655`: second completion argument and renderer forwarding | superseded | Current socket handler `:473–513` and `completeLastMessage:1740` drain the correlated candidate lifecycle. Current Node task-panel/completion tests pass without the second argument. |
| Same file `:3717–3754`: remove regex mining; add `selectMoleculeCandidates` and structured renderer | migrated safety intent; superseded selector | No Markdown-to-card mining in current renderer; strict helper validates shape, identities, budgets and lifecycle. Current `tests/home_structured_molecule_render_test.js` validates rejection and actual rendering. Do not restore the weaker selector. |
| Same file `:3787–3788`, `3794–3795`, `3833–3834`, `3879–3881`: unique candidates, counts, structured rows and validation badge | migrated | Current `renderMoleculeCandidates:3591` uses validated canonical structures and candidate IDs, mounted collection ordering and reference selection. [E2](#e2-current-cards-and-reference-ack). |
| Same file `:3935–3946`: inline `molecule.properties` or fetch fallback | pending legitimate numeric-card desire; intentionally retained refusal | Current `main.js:3787–3800` deliberately shows that independently verified property data was not provided. It neither reads `candidate.metadata.properties` nor performs the old property fetch. Alignment is implemented, but card projection is missing: [E3](#e3-property-alignment-is-not-property-card-projection), [G1](#g1-p1-chinese-report-and-independent-property-cards). Do not delete this requirement or restore an unaudited fallback. |
| Same file `:3951–3960`: safe SMILES DOM text nodes | migrated | Current renderer `:3803` onward uses `textContent` for canonical SMILES; structured rendering tests cover hostile metadata and DOM construction. |
| `src/web/templates/index.html:2532`: old cache token | superseded | Current template `:2547–2555` loads candidate/reference modules and `main.js?v=20260925-terminal-labels-v1`; retains a cache-lineage comment. Current completion test checks the current token. Do not revert to `20260714-target-design-v2`. |
| `tests/agent/test_chat_handler_agent_events.py:277–328`: TargetDesign/EmptyTargetDesign fixtures | superseded fixture shape | Current same test file `:795` onward constructs strict CandidateSet events. `test_candidate_event_uses_only_tool_result_sequence`, terminal-status and external-main-model rejection tests cover the authoritative source boundary. Inspected, not rerun here. |
| Same file `:456–522`: two complete-frame candidate/empty-array tests | migrated intent; superseded assertions | Current tests cover successful/partial CandidateSet events, malformed observations and terminal failures. Empty/no-valid candidates must not manufacture text-derived cards; the old required `complete.molecules=[]` is not the current protocol. Frontend no-card/lifecycle checks were actually rerun. |
| `tests/home_agent_task_panel_test.js:32–33`, `49–51`: require two-argument signature | superseded | Current same file checks content-only completion plus task-panel behavior. Actual Node run passed. |
| `tests/home_workflow_completion_behavior_test.js:29`: old cache assertion | superseded | Current test checks terminal-label token; actual run passed. |
| Same file `:51–52`, `143–191`: selector extraction, dedupe/no-Markdown tests, old wiring assertions | migrated safety intent; superseded wiring | Current structured-render suite tests strict schema, canonical identity, no regex fallback, malformed metadata rejection and lifecycle. Current reference suite tests actual mount/ACK. Both actual runs passed. |
| Untracked `src/agent/reporting/__init__.py` (entire file) | superseded export; original retained | Exports only the old raw-array presenter. Do not activate it implicitly; future pure projection should consume the current evidence contract. [G1](#g1-p1-chinese-report-and-independent-property-cards). |
| Untracked `src/agent/reporting/target_design.py:17–67`: collect raw generator/property steps; set presentation and replace final answer | superseded mechanism; pending report | Current session validates/aligns results before ledger registration; Web projects without rewriting the scientific execution result. Old helper does not establish current ledger/source identity or independent property provenance. Never overwrite accepted status/count/evidence with this reconstructed presentation. |
| Same file `:70–145`: raw-array canonicalization, dedupe, truncate, property lookup | migrated validation/alignment intent; pending numeric projection | Current CandidateSet sanitizer and alignment supersede revalidating old arrays. Old property map checks neither observation success nor current evidence identity; `_candidate_items` is not a CandidateSet deserializer. Requested-count truncation can hide later rejection counts. [E1](#e1-authoritative-candidates-and-evidence), [E3](#e3-property-alignment-is-not-property-card-projection). |
| Same file `:147–237`: model/count summary, Chinese sections, MW/LogP/TPSA/QED table, warnings and interpretation boundary | pending integrated Chinese report | Current standalone property report and deterministic ranker exist, but do not establish the old end-to-end report desire as complete. Old report hard-codes “no docking performed” and must not replace actual tool evidence. Its test passes `docking_top_n=3`, but the helper never reads that key: **old Top-N reporting was not actually implemented**. Current Top-N computation exists separately. [E4](#e4-report-and-top-n-boundaries), [G1](#g1-p1-chinese-report-and-independent-property-cards). |
| Same file `:239–289`: tool lookup, status/warnings, numeric/table/count helpers | superseded implementation; intentionally retained safety intent | Keep truthful partial/unavailable, target-specific caveats and escaping. Do not reuse local completeness/status inference over authoritative observations; old `_display_number` accepts bool/nonfinite numeric types without strict checks. Use current validated data/provenance, not raw lookup. |
| Untracked `tests/agent/test_molecular_tool_smiles_extraction.py:4–21`: ten whole structures must not produce fragments | migrated functional intent; pending legacy Base behavior | Current `test_explicit_molecular_input.py` exercises actual property/likeness executions; `test_admet_whole_input.py` exercises ADMET. They pass. Old test directly calls inherited `extract_smiles`, which is no longer the actual analysis execution boundary. Do not imply Base itself was repaired. |
| Same file `:24–27`: canonicalize `CCO/OCC` before dedupe | migrated candidate identity intent; intentionally distinct input contract | Current CandidateSet/aligner canonicalize identity. Complete-input parser preserves valid input strings/batches rather than promising Base's exact canonical-deduped list. PR #66 also retains historical first-structure reverse behavior. Future caller tests should target actual entrypoints, not force all tools into this obsolete helper contract. |
| Untracked `tests/agent/test_target_design_presentation.py:5–128`: validated candidates, numbers, partials, Chinese report and limitations | split: validation migrated; report/card assertions pending | Current CandidateSet/alignment/property-report/ranker suites cover independent parts and were rerun, but not a unified Chinese report/card numeric path. Keep these requirements as [G1](#g1-p1-chinese-report-and-independent-property-cards); do not transplant old data shape or mark the whole test intent satisfied. |
| Same file `:131–156`: target-specific EGFR scientific caveat | pending integrated report assertion | Preserve target-specific language and avoid fixed PDE5A wording. New report must derive target and outcome from accepted execution, not infer experimental activity or docking. |

## 3. Current source/test evidence

### E1. Authoritative candidates and evidence

- [CandidateSet contracts](../../src/agent/contracts/candidates.py): `CandidateRecord.from_dict:197`, `CandidateSet:242`, `CandidateSet.from_dict:363`; [sanitizer](../../src/agent/validators/molecule_candidates.py): `sanitize_generated_candidates:36`. The current validated set is not an arbitrary `complete.molecules` array.
- [RunSession](../../src/agent/runtime/run_session.py): normalization/validation/alignment/ledger registration `:513–550`, `_build_final_result:1055`. It registers the accepted representation and retains ledger, claims, warnings, partial and skipped-step information. Replacing final text with old raw-array output risks disconnecting presentation from that evidence; do not transplant such overrides.
- [ChatHandler](../../src/web/chat_handler.py): `_candidate_event_from_observation:881`, `_send_reference_candidate_events:968`. [Scientific references](../../src/web/scientific_references.py): `project:177` compares complete observation/evidence with persisted source, then publishes/compares the returned view. A matching ID alone is insufficient. Candidate display can survive missing reference storage, but that does not grant confirmed reference authority.
- Existing tests: `test_candidate_contracts.py`, `test_generated_candidate_validation.py`, `test_candidate_alignment.py` (run); `test_chat_handler_agent_events.py::test_successful_agent_emits_strict_candidate_set_before_result`, `::test_candidate_event_uses_only_tool_result_sequence`, `::test_external_main_model_cannot_create_candidate_event` (source inspected, not run).

### E2. Current cards and reference ACK

[molecule_candidates.js](../../src/web/static/js/home/molecule_candidates.js) `normalize:487` and `createLifecycle:654` enforce shape, identity, budgets and correlated delivery. [main.js](../../src/web/static/js/home/main.js) handles events `:473`, completion `:1740`, rendering `:3591`, and mounted IDs `:4045`. [scientific_references.js](../../src/web/static/js/home/scientific_references.js) is exercised by the existing reference-controller suite: no ACK on non-mount, late ACK cannot undo clear, collection confirmations remain independent. Do not restore text/regex-derived selection or a weaker completed-array protocol.

Actual passing Node suites: `home_agent_task_panel_test.js`, `home_workflow_completion_behavior_test.js`, `home_structured_molecule_render_test.js`, `home_scientific_references_test.js`. In particular, structured rendering `:714–747` and `:984–1012` explicitly reject fetching unverified properties and rendering ordinary/malicious metadata values as verified science.

### E3. Property alignment is not property-card projection

[candidate_alignment.py](../../src/agent/validators/candidate_alignment.py) `align_candidate_results:16` preserves matched row fields including `properties`, adds canonical candidate IDs, drops foreign/duplicate rows, records missing/discarded rows and marks partial outcomes. It rewrites formatted aligned output as escaped JSON, **not a Chinese candidate table or card-property event**. `RunSession:519–527` invokes it when a step supplies `candidate_source`; [target-design step template](../../src/agent/planning/step_templates.py) supplies that metadata for properties/ADMET/activity.

`test_alignment_maps_canonical_smiles_and_discards_unknown_and_duplicate_records` and `test_orchestrator_aligns_before_events_and_final_answer` prove canonical matching and pre-event sanitization (run). They do not prove a property-to-card projection. Current card refusal at `main.js:3787–3800` is intentional and correct until independent-tool evidence is carried through. **Missing projection is a functional desire gap, not a reason to remove numeric cards from the user's requested end state.**

PR #67's exact `src/agent/tooling/analysis_contract.py` introduces strict query/raw/normalized observation validation for property, likeness and ADMET; preserves scientific data/extensions; bounds validation within the worker deadline. `factory.py` selects its adapters only for those three tools. The module explicitly defines validation views, not projections. `test_query_forwarded_exactly_without_structured_smiles_support`, `test_preserves_compat_observations_and_opaque_extensions`, `test_registry_real_producers_never_compute_invalid_whole_input`, and worker/slot tests are present at `8c3b6d4` (inspected, not run). This does not add independent-property cards or close G1; CI remains pending per parent.

### E4. Report and Top-N boundaries

- [PropertyCalculator](../../src/agent/tools/property_calculator.py) has genuine RDKit descriptor reporting and bounded heuristic explanations. Existing `test_property_report_boundaries.py` checks actual values, schema, separate Lipinski/Veber checks and unavailable input; run here. A standalone property report is not a complete target-design report.
- [CandidateRanker](../../src/agent/tools/candidate_ranker.py) `execute` requires valid positive integer Top-N, joins actual property/optional ADMET/activity evidence, records unrankable candidates, and returns `CandidateRanking@1`, `requested_top_n`, `top_candidates`, warnings and prioritization provenance. `test_candidate_ranker_returns_deterministic_top_n_without_docking_claims` and the full existing ranker file pass. Ranking is prioritization, not binding energy or experimentally validated potency.
- [target_design_steps](../../src/agent/planning/step_templates.py) `:91–163` binds validated candidates and aligned outputs into ranking. `RunSession._build_final_result` still joins successful tool-formatted outputs; current partial projection adds Chinese failure summaries, but neither constitutes an integrated, deterministic Chinese target-design report with independently evidenced numeric cards.
- The original report formats MW/LogP/TPSA/QED and target-specific Chinese caveats, but ignores `docking_top_n` even though its test passes it. Preserve the reporting goal and use the current ranking output; do not credit the old helper with Top-N functionality it lacks.

## 4. Remaining `extract_smiles` caller inventory

Search scope was current `src/**/*.py` at `ecd6cca`, then exact PR #66 diff. Eight call sites remain in five Base subclasses before PR #66; its change removes the reverse pair, leaving six in four subclasses. The Base definition and separate utility definition are not call sites.

| Current call site | Actual guard / exposure | Disposition |
|---|---|---|
| `llm_molecular_generator.py:105`, `should_use` | Canonical generation-intent grammar governs selection; extraction participates in analysis-vs-generation recognition, not an independently trusted scientific result. | pending bounded cleanup; do not globally activate stronger behavior via old patch. |
| `llm_molecular_generator.py:444`, `_analyze_generation_intent` | Optimization takes first extracted structure. Request grammar/count/temperature checks and generated-output canonicalization exist, but output validation does not prove the original optimization input was whole/unchanged. | pending P1 complete-input optimization regression/design; risk established by source path, no new failing reproduction claimed. |
| `molecular_docking.py:107`, `should_use` | Heuristic routing only. `execute:121` rejects unstructured requests; receptor/ligand/box are required and canonical workflow registration uses `DockingInput`/`DockingToolAdapter`. | intentionally retain execution gates; pending routing cleanup, not evidence that regex can execute docking. |
| `docking_tools.py:65`, `PrepareLigandTool.execute` | Both outcomes return `success=False`; no preparation is executed by this text path. Separate `run_from_smiles/run_from_file` explicitly require output paths. All four staged helpers lack standalone model capability in `factory.py`; they are registered/allowed in docking policy, not proven universally unreachable. | intentionally retained restricted compatibility boundary. Do not auto-enable staged helpers. |
| `reverse_target_tool.py:47,97`, selection/execution | At base, `_check_rdkit` precedes extraction and predictor initialization follows it, but that does not ensure whole input. | pending integration of reviewed PR #66, not a new global Base patch. |
| Same reverse call sites at `37b66a1` | Replaced with `parse_molecular_smiles`; invalid whole input fails before predictor construction/call; unavailable parser gets a distinct error; valid input is unchanged, historical first structure for batches retained. | migrated in reviewed PR only; not yet credited to main. Exact new tests: `test_invalid_whole_input_never_reaches_predictor`, `test_valid_structure_reaches_predictor_unchanged`, `test_valid_batch_keeps_historical_first_structure`, `test_invalid_input_does_not_initialize_predictor` (source inspected, not run). |
| `rxn_chemistry_agent.py:58,78`, selection/execution | Text extraction still feeds reaction/retrosynthesis/literature handlers. `factory.NON_WORKFLOW_TOOLS` explicitly excludes RXN as legacy chat-only with no workflow policy. This excludes the workflow/model capability path; it is not a claim that direct legacy calls are safe or absent. | intentionally retained non-workflow status; pending legacy-input hardening only if that entry is retained. No activation. |

Already migrated execution boundaries: properties `:53,83`, likeness `:54,75`, ADMET `:78,99` use `parse_molecular_smiles`; activity uses `activity_input.py` and that parser. Shared `molecular_input.py` disables RDKit name/CXSMILES acceptance and rejects malformed whole structures instead of repairing valid fragments. Existing whole-input analysis tests pass. The raw parser preserves input strings; candidate canonical identity/dedupe belongs to the validated result contract.

Separate `src/agent/utils/smiles_extractor.py::SMILESExtractor.extract_smiles:24` is still regex-based, but current `src` search finds only its definition and lazy export in `utils/__init__.py`, no call sites. Intentionally retain pending a separately scoped dead-code decision; do not activate it or claim runtime coverage. The old original `react_agent.py`/analysis-tool callers are not present as extraction callers in current source.

## 5. Recovery and historical findings

| Historical concern | Current exact evidence | Disposition / coverage limit |
|---|---|---|
| Hard-coded Supervisor run version versus custom orchestrator version | Current `runtime/run_session.py:261,285,966` writes `self.orchestrator.workflow_version`; `runtime/delegated_executor.py::prepare:158` binds that version and `SpecialistDispatch.claim_run:53` writes it. `orchestrators/workflow.py::_compatible_checkpoint:230` compares tool, workflow, input, tool/adapter/model versions. | migrated source behavior. Existing `test_delegated_session_lifecycle.py` version/authorization-change and registered-version tests plus `test_workflow_resume.py` pass. These do **not** specifically assert custom workflow-version parity in both run and checkpoint rows; retain a small permanent regression follow-up rather than inventing exact test coverage. |
| Restored artifacts alias nested checkpoint metadata | `contracts/domain.py::WorkflowArtifact.from_dict:18–28` deep-copies the mapping; `orchestrators/workflow.py::_result_from_checkpoint:260` validates the artifacts list and calls `from_dict` for each. | migrated implementation. Existing restore/result/state/evidence and malformed-artifact tests pass. Additional read-only in-memory double-restore/mutate probe passed here. A permanent exact nested-mutation regression is still pending, as the historical handoff noted. Do not generalize this to all `to_dict`/other fields being deep-copied. |
| Historical sandbox timing / `artifact_failed` finding | `docs/handoff/agent-recovery-audit-integration.md:34` retains the prior artifact failure. Parent's later diagnostic is recorded in the dated addendum below. | Historical exact cause remains unknown. Parent demonstrated that PR68 fixes the same-class ancestor-mtime false-rejection trigger on the actual broker path; this is no longer a separately reproduced business-code fix. No diagnostic rerun or scientific acceptance is claimed here. |

## 6. Linked safe gaps and minimal follow-up design

### G1. P1 Chinese report and independent property cards

Parent's later functional batch should add a pure projection of accepted execution data, not mutate `AgentResult`/CandidateSet counts/status, change ledger evidence, re-canonicalize old raw arrays, or re-enable `complete.molecules`.

Join accepted independent property observations to candidates by canonical identity and candidate ID **within the correct trace/step/source evidence**; preserve observation status, input/output digests, source version and units/method. Candidate identity and generator metadata alone are insufficient proof. Keep presentation of descriptors separate from heuristic rules, ADMET/activity and ranking. Missing, partial, nonfinite, mismatched, failed or stale evidence must remain explicitly unavailable/partial. Do not fetch unaudited replacement values.

For the Chinese report, render target, requested/actual/rejected counts from validated sources, actual step outcomes/warnings, model/method provenance, independent property values and the existing ranking's requested/actual Top-N, ties/limitations and unrankable reasons. Derive docking/activity assertions from actual evidence, never fixed prose. Preserve the reference publication/ACK/ordered-selection contract; define any additive/versioned presentation transport before implementation because the current frontend accepts exact field sets.

Minimum tests: complete/partial/empty sets, two targets including EGFR, canonical aliases/duplicates/foreign rows, malicious metadata properties, failed independent properties, stale/wrong-source evidence, valid numeric cards and report parity, Chinese Top-N reflecting actual ranker output, no fabricated docking/potency, and ACK/mount/restore compatibility. Existing numeric property reports and alignment are foundations, not completion evidence for this gap.

### G2. P1 Bound the remaining extraction callers

Integrate reviewed PR #66 only after the parent's exact-head CI/review gate; recheck on the eventual combined base with PR #67. Next use actual optimization entrypoints for RED tests on malformed mixed batches, suffixes, quotes, charged/isotopic/disconnected structures and unavailable RDKit. Reuse complete-input parsing locally where semantics fit, preserving intentional first-structure behavior or explicitly documenting any change. Keep selection separate from execution. Do not apply the old all-valid-lines shortcut globally. Staged helpers/RXN remain non-activated; route-only/direct-legacy coverage belongs in an explicitly bounded follow-up, not an authorization expansion.

### G3. P2 Regression closure and parent acceptance ledger

Add permanent custom-workflow-version parity and double-restore nested-artifact isolation regressions in a later authorized code/test batch, without changing correct recovery behavior to fit obsolete patches. Preserve the historical sandbox exact-cause-unknown record; the parent diagnostic below removes the same-class mtime trigger from separately pending business fixes. Parent must integrate G1/G2 outcomes and reconcile the current packages 1–8 ledger; package 6's **audit** can be recorded complete, its linked **features pending**. Packages 7/8 and real scientific acceptance cannot be inferred from this offline audit. Server deployment remains excluded.

## 7. Actual verification in this audit

### Later parent diagnostic — 2026-09-25 (reported, not rerun here)

Parent supplied evidence from ignored `recovery-parity-regressions/scratch/test_manifest_snapshot_probe.py`: it calls the actual `test_manifest_survives_service_reopen_and_tamper_and_cross_job_fail_closed` unchanged, using real broker/SQLite and its original FakeSDK. Only the artifact module's `read_file_snapshot` is injected. The matrix is committed old secure_io `1bba025` versus current PR68, each with deterministic sibling modification disabled/enabled: **4 passed in 3.16s**, per parent.

Old/no perturbation succeeds. Old/sibling write changes ancestor mtime, triggers `unsafe file snapshot`, then the original manifest `KeyError`; PR68 succeeds in both cases and executes the original cross-job/tamper rejection assertions. Target file identity/hash remain unchanged during every snapshot. This demonstrates PR68 fixes that same-class false-rejection trigger on the broker consumer path. It **does not uniquely attribute the historical natural `artifact_failed`**, whose low-level logs were not captured. Keep `historical exact cause unknown`; do not treat it as a separately reproduced business bug needing another code change. Probe remains ignored scratch, not submitted. No real Vina/QEMU/model execution or true scientific acceptance; G1 did not repeat the probe.

### Original audit verification (unchanged historical results)

All results below are newly executed on `ecd6cca`, not copied historical pass counts. Original tests/dirty code and PR #66/#67 branches were not executed.

### Python existing tests and additional probe

Interpreter: `C:/Users/xkx52/.conda/envs/MedChat/python.exe -B`. Reused the isolation pattern documented in [RAG extraction plan](../superpowers/plans/2026-09-24-rag-service-extraction.md): repo path replaced, temporary cwd/config/state paths, six non-secret OS launch variables only, real-service switches off, normal `tests/conftest.py`, no cache/bytecode. Used its documented in-process `pytest.main` variant. No fixture-copy step was needed. Added `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` and socket guards (only Windows asyncio `_fallback_socketpair` loopback setup allowed; other connect/connect_ex/create_connection calls fail). These are runner adaptations, not repository changes. Test-only temporary DB/config files are distinct from prohibited existing production DB/env/index/weight reads.

Exact pytest paths, all under `tests/agent/`:

```text
test_candidate_alignment.py
test_candidate_contracts.py
test_generated_candidate_validation.py
test_property_report_boundaries.py
test_explicit_molecular_input.py
test_admet_whole_input.py
test_candidate_ranker.py
test_workflow_resume.py
test_delegated_session_lifecycle.py
```

Arguments after absolute paths: `-q -p no:cacheprovider --tb=short -rs`.

Actual result: **425 passed in 8.62s**, process exit **0**, no skips/failures reported. This is not full-repository CI, current external CI, or real model/scientific-provider acceptance.

Additional in-memory probe used one synthetic checkpoint with `artifacts[0].metadata.nested.values=[1]`, restored twice through `WorkflowOrchestrator._result_from_checkpoint`, appended `2` to the first restored nested list, and asserted both the second result and original checkpoint remained `[1]`. Actual output: `ARTIFACT_DOUBLE_RESTORE_MUTATION_PROBE=PASS`. No new test file was written; do not count this as a permanent regression.

### Node

Each command actually printed its success message; combined shell exit 0:

```text
node tests/home_agent_task_panel_test.js
  Homepage agent task panel static checks passed
node tests/home_workflow_completion_behavior_test.js
  Homepage workflow completion behavior checks passed
node tests/home_structured_molecule_render_test.js
  Structured molecule candidate frontend checks passed
node tests/home_scientific_references_test.js
  scientific reference schema/controller/actual mount tests passed
```

No production JavaScript changed. No health check, compileall bytecode write, service start, real model/RAG/DB/index/weight access, sandbox acceptance, browser session, network call, staging or publication was performed. Read-only `git diff --check` passed before the document was added; final new-file whitespace and write-scope checks are recorded below.

## 8. Files read / evidence inventory

This is the task-relevant read inventory, not an assertion that every listed large file was read end-to-end. Diffs, symbol/line slices and focused searches were used; executed tests also import their normal source dependencies.

- Original: all 13 expanded paths in §2, plus `docs/PROJECT_STANDARDS.md`. No excluded manifest contents were read. Original tracked diffs were restricted to the nine named modified paths. Source-only `rg` additionally inventoried old extraction callers.
- Current policy/docs: `AGENTS.md`, `docs/PROJECT_STANDARDS.md` (comparison/additions); `docs/handoff/remaining-through-step8.md`, `rag-parity-integration.md`, `agent-recovery-audit-integration.md`; `docs/superpowers/plans/2026-09-24-rag-service-extraction.md`.
- Current backend evidence: `src/agent/supervisor.py`; `contracts/{candidates,domain}.py`; `validators/{molecule_candidates,candidate_alignment}.py`; `runtime/{run_session,workflow_executor,delegated_executor,task_state}.py`; `orchestrators/workflow.py`; `planning/step_templates.py`; `tooling/factory.py`; `workflows/catalog.py`; `capabilities/catalog.py`; `tools/{base_tool,molecular_input,property_calculator,drug_likeness_assessment,admet_predictor,activity_input,llm_molecular_generator,candidate_ranker,molecular_docking,docking_tools,reverse_target_tool,rxn_chemistry_agent}.py`; `utils/{__init__,smiles_extractor}.py`. Other `src/agent` hits were used only for recovery/deepcopy/caller symbol inventories, not to inspect runtime assets.
- Current Web: `src/web/{chat_handler,agent_result_presentation,scientific_references}.py`; `src/web/static/js/home/{main,molecule_candidates,scientific_references}.js`; `src/web/templates/index.html`.
- Current tests: the nine Python paths and four Node paths in §7; `tests/conftest.py`; source/name searches in `tests/agent/{test_chat_handler_agent_events,test_agent_audit_regressions,test_domain_contracts,test_contracts,test_decision_protocol_recovery,test_supervisor_runtime_integration}.py`. Additional `tests/agent` symbol searches checked whether permanent custom-version/nested-artifact regressions already existed; absence is not a claim about all possible behavioral coverage.
- Exact PR objects read using local Git: #66 `src/agent/tools/reverse_target_tool.py`, `tests/agent/test_reverse_target_complete_input.py`; #67 `src/agent/tooling/analysis_contract.py`, `src/agent/tooling/factory.py`, `tests/agent/test_analysis_contract.py`; commit/file-list metadata for both. No GitHub/CI network lookup.
- Process guidance: inspected the local `using-superpowers/SKILL.md`; no implementation skill workflow or code changes were undertaken for this read-only disposition audit.

## 9. Handoff / three remaining actions

1. **G1:** Implement and independently review the evidence-bound Chinese report + numeric property-card projection; retain partial/unavailable and current ACK semantics.
2. **G2:** Complete the exact-head #66/#67 integration gates, then narrowly harden remaining optimization/legacy extraction paths without enabling helpers/RXN or importing dirty patches wholesale.
3. **G3:** Add the two permanent recovery regressions, preserve the unresolved sandbox record, and update parent packages 1–8 acceptance against the final integrated revision. Do not substitute this audit for package-8 real acceptance or deploy.

New commit: none. New PR: none. Only this document is handed off, untracked and unstaged on `codex/historical-residual-disposition`; base remains `ecd6cca`.

Final read-only checks: all **13/13** scoped original SHA256 fingerprints, taken during the audit and rechecked after writing, are unchanged; original HEAD/status retain the original 13 porcelain entries. Both indexes are empty. Destination status contains only `?? docs/handoff/historical-residual-disposition.md`, HEAD unchanged. New-document trailing-whitespace count **0**, missing relative file-link count **0**; tracked `git diff --check` exit **0** (the separate whitespace check covers this untracked document). A later optional `git diff --no-index ... --stat` invocation had incorrect argument placement and printed usage; it was not a test failure or verification pass, and changed nothing. No historical sandbox result was rerun or relabeled.
