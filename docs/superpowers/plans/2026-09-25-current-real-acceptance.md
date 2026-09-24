# Package 8 Current Real Acceptance — Design and Inventory Plan

> **For agentic workers:** After parent approval only, use subagent-driven-development or executing-plans for separately authorized implementation. This document is a design proposal, not permission to execute it.

**Goal:** Establish honest, repeatable real end-to-end evidence on the latest integrated packages 1–8 revision; exclude package 9/server deployment.

**Architecture:** Keep contract, replay, isolated real tools, isolated real decisions, and normal Web integrated acceptance as distinct evidence layers. Reuse existing tools, ModelDecisionLoop, Session/ownership, scientific references and validators; a bounded test process must exercise ordinary HTTP/session → `/ws` → decisions → real scientific tools → frontend/artifacts.

**Tech stack:** Existing FastAPI/Jinja/native JS, pytest/Node, SQLite, RDKit, PyTorch/PyG RG-MPNN, Ollama generation, configured decision provider, Vina/preparation tools.

## 1. Scope and revision gate

- Inventory base: `e173d432f767fe76e1c1f101d9cd8824ffee612d`, branch `codex/current-real-acceptance`; initial worktree clean. Latest local commits: #68 snapshot sibling-write fix, #67 analysis contracts, #66 reverse-target whole-input validation.
- Read repository `AGENTS.md`, `docs/PROJECT_STANDARDS.md`, `docs/handoff/remaining-through-step8.md`, relevant source/tests and registry metadata. No imports, tests, service/network probes, environment inspection, actual datasets/weights/structures, credentials, runtime user store, or historical report/log contents were read/run.
- Only this document is written. No implementation, test/case file, separate manifest, commit, push, model start, production configuration change or deployment.
- Handoff ledger is historical context, not a fresh PR/CI audit. Packages 4B/4C/4D, planner integration, residual/G1/ADMET work and package 7 need final integration confirmation. G1/A1 labels are parent-supplied; do not invent their absent test filenames or infer completion from related older tests. **A1 is not full acceptance. Package 7 is still implementation work.**
- Final acceptance must freeze the *then-current integrated code and tree*, not reuse this baseline as final proof. Record package→reviewed revision→integrated revision→test evidence. Later relevant changes invalidate affected evidence; final required matrix must refer to one revision and asset identity set.

## 2. Actual existing entry points and limitations

| Existing entry/source | What it proves | What it does not prove |
|---|---|---|
| `scripts/run_agent_acceptance.py --mode contract` | Router/planner/schema/failure contracts; includes fake ContractTool/ProbeTool | Real generation, provider decisions, real Vina or Web integration; contract mode is not guaranteed side-effect-free |
| Same script `--mode replay --replay-input ...` | Rechecks stored structured truth/provenance assertions | Current live execution; old artifacts/provider availability; fresh revision compatibility |
| Same script `--mode real --case-set all-real --repeat 3` | Real tool probes plus golden/diverse ScientificAcceptanceRunner workflows | Normal HTTP/WS entry or model-driven decisions; external main-model probe is optional in its required gate |
| `scripts/run_decision_chat_acceptance.py --mode native\|json` | Real configured provider + RDKit through isolated ChatHandler/ModelDecisionLoop: chat, two molecules, clarification, invalid input | Ordinary Web app/session, family/generation/Vina/RAG, browser UI. Four cases, no repeat flag; max 4 model requests/2 tool attempts, 90-second loop |
| `scripts/run_decision_browser_lab.py --port 6012 --mode native\|json` | Explicit loopback lab, real provider/RDKit, `/decision-lab/` and `/decision-lab/ws` | Ordinary homepage `/ws`, full scientific matrix, automatic pass report or externally bounded lifetime |
| `tests/test_activity_family_real_acceptance.py::test_trained_family_acceptance` | Opt-in real trained PDE/BuChE bundles, CPU baseline→API→tool→decision→isolated WS→Node DOM; source digests and cleanup | External decisions: `decision_model_kind=scripted`; `/isolated-family` is not normal `/ws`; Node DOM is not full browser integration |
| `tests/scientific_reference_browser_lab.py` | Homepage/session + ordinary route setup `/ws`, candidate confirmation/restore, real RDKit | Generator and chat model are offline fixtures; not real generation or real decision-provider evidence |
| `tests/agent/test_main_integration_acceptance.py` | Bounded offline HTTP/session→WS, actual RDKit, partial/error/numeric presentation | Full MolecularChatApp or live provider; built on offline reference lab |

Family helpers actually live in `tests/family_real_acceptance_support.py`, `tests/family_acceptance_chain_support.py`, `tests/family_acceptance_process_support.py`; there is no family-weight CLI to invent. The opt-in pytest item writes a unique `outputs/agent_evaluation/family_acceptance_*.json`. Its parent runs each family in an owned child (120-second maximum), separately verifies process release/cleanup, snapshots only selected bundles, and never activates the source production selection. Preserve these boundaries.

### Evaluation inventory (filenames only; dataset contents not read)

`data/agent_evals/` contains `routing_cases.jsonl`, `workflow_cases.jsonl`, `failure_recovery_cases.jsonl`, `real_agent_cases.jsonl`, `golden_scientific_cases.jsonl`, `diverse_scientific_cases.jsonl`, `chemistry_quality_cases.jsonl`, `architecture_review_cases.jsonl`.

Source `src/agent/evaluation/{models,runner,scientific}.py` defines `EvaluationCase`, `load_cases`, router scoring, ScientificAcceptanceRunner and replay. `run_contract()` uses routing/workflow/failure/real_agent sets; `all-real` selects golden + diverse. Existing tests *expect* 16 REAL cases, 12 golden and 20 diverse, with no mocks in golden/diverse. These are test assertions, not a freshly validated dataset inventory/count. Other two sets are not automatically included in this CLI's final matrix. Revalidate approved case contents/IDs/hashes after asset-read authorization.

### Normal Web path on this base

`src/web/app.py` registers `/ws` → `ChatHandler.handle_websocket` → `_process_message` → routing/Supervisor execution. `src/web/routes/websocket_routes.py` separately delegates `/ws` to the same handler. `ChatHandler.process_decision_message` is an explicit bridge, not evidence that the ordinary path selects it. Changing `AGENT_HARNESS_MODE` alone cannot establish package 7.

Ordinary HTTP/session ownership, `/api/agent/workflows/references/confirm` and `/restore`, model lifecycle and `/ws` must be exercised together after package 7 lands. Do not invent `/api/chat`. `src/web/app.py` normally reads YAML, checkout env and the user LLM store, initializes scientific services, and starts background work; unguarded `main.py`/ASGI import is not the proposed isolation mechanism.

## 3. Recommended approach and missing instrumentation

Options: (1) reuse existing runners alone—smallest effort but insufficient; (2) extend layered evidence with a bounded ordinary-entry harness—**recommended**; (3) run against the existing port-6001 instance—unacceptable user-state/configuration risk and out of scope.

Needed after approval, not implemented here:

1. **Integrated safe launch seam:** package-7-approved ordinary app/session/router initialization with injected in-memory provider settings, isolated writable paths and real tool adapters. No production config read/write, no replacement scientific framework, no fake decision shortcut. Prove ordinary wiring parity; a second dedicated lab is not package-7 completion. No verified existing command currently supplies all these guarantees.
2. **Strict manifest/report aggregator:** separate assertion status, scientific result status and evidence class; inspect every case and every iteration. `run_agent_acceptance.py` returns 0 for `partial`, omits the external probe from required checks, and scientific `run()` derives top status from the last iteration. Stability `success_rate`/completion includes partial. None of these is the final pass predicate. `--mode all` is contract+real, not replay.
3. **Dependency/service injection:** ScientificAcceptanceRunner creates `RAGSearchTool()` without an injected RAG service; current tool returns unavailable without it. Its activity probe uses legacy `ActivityPredictor`, not both selected family bundles. Real-provider decision chat registers only PropertyCalculator. Reuse/inject approved services; do not hide these gaps through case deletion, demo models or fixtures.
4. **Stronger truth evidence:** current Vina probes check energy/pose existence but do not alone establish finite numeric value, real executable provenance and pose hashes. Some RDKit checks only verify tool success. Strengthen independent numeric/artifact checks; false values, NaN/Inf and stale artifacts must fail. Existing input-failure checks can pass on absence of successful tools: also prove expected validation reason, healthy control, zero unauthorized work, not an unrelated provider outage.
5. **Normal browser recorder:** bounded real browser driver on homepage/ordinary WS; capture sanitized event IDs/statuses, rendered values and artifact identifiers, not provider messages/session cookies/raw user text. Check candidate ACK/restore, owner isolation, continuation, cancellation, switching and partial terminal labels. Existing Node DOM assertions remain supplemental.
6. **Retention/isolation audit:** existing redaction is not proof that arbitrary exception strings/raw prompts are absent. Use closed public projection before persistence; no unrestricted stdout/stderr dump. Run compile/tests only in future isolated execution; record first failures/timeouts rather than replacing them with rerun successes.

## 4. Real dependencies and safe runtime inputs (all availability unverified)

Only `data/REGISTRY.md` metadata was inspected. Listed filenames/default paths do **not** imply presence, compatibility, licensing, readiness or successful execution.

| Dependency | Required safe configuration / evidence after approval |
|---|---|
| Main decision provider | Explicit `OPENAI_COMPATIBLE_API_KEY`, `OPENAI_COMPATIBLE_BASE_URL`, `OPENAI_COMPATIBLE_MODEL` in child environment/memory; endpoint allowlist, no credential URL/query, no redirects. Report only API-key-presence boolean, public provider alias and mode; never values/raw response. Native required; JSON separately tested if supported/advertised |
| Local generation | Operator-approved Ollama endpoint (`OLLAMA_BASE_URL`), loaded `gmm-llama:latest`; `MOLECULAR_GENERATOR_MODEL` used by CLI probe and `OLLAMA_MODEL` by scientific runner must both pin generation to that model. Neither is a main-chat-model switch. Record runtime model identity/digest, valid canonical candidates and dedup; no automatic model download/start |
| RDKit/ADMET | Approved Python scientific environment, actual installed versions; RDKit properties independently recomputed. Identify `adme_py` versus `rdkit_rules` and endpoint support per field. Unsupported ADMET/toxicity labels remain unavailable; rule descriptors are not validated pharmacokinetic/clinical predictions |
| Both family RG-MPNN bundles | `MEDCHAT_RUN_FAMILY_REAL_ACCEPTANCE=1`, `MEDCHAT_FAMILY_ACCEPTANCE_MODELS_DIR`, `MEDCHAT_FAMILY_ACCEPTANCE_PDE_BUNDLE_ID`, `MEDCHAT_FAMILY_ACCEPTANCE_BUCHE_BUNDLE_ID`; approved trusted source, sealed classification+regression cards/weights for each family, hashes/schema/units/thresholds. `ACTIVITY_MODEL_DIR` points only at private snapshots; no training or production activation |
| Target/reverse-target | Authorized copied target DB/cache (`TARGET_DB_PATH`, `TARGET_CACHE_DIR`), reverse dataset/fingerprints (`REVERSE_TARGET_DATA_DIR`, optional `CHEMBL_DB_PATH`); source/version/record identities. SQLite WAL/locks and structure downloads confined to private copy; no implicit seed/download against source |
| RAG | Authorized source/index/manifest and compatible embedding model/runtime. Registry mentions `RAG_INDEX_PATH`; actual RAG service injection/config mapping must be verified, not assumed from that name. Pin source/index/embedding hashes; unavailable index is not an explicit no-hit |
| Vina | Approved Vina, ADFR receptor preparation, Meeko ligand preparation (OpenBabel only if required by actual selected path); `MOLECULAR_DOCKING_VINA`, `MOLECULAR_DOCKING_ADFR_BIN` or `MOLECULAR_DOCKING_PREPARE_RECEPTOR`, `MOLECULAR_DOCKING_PREPARE_LIGAND`, optional `MOLECULAR_DOCKING_ROOT`, finite `MOLECULAR_DOCKING_VINA_TIMEOUT_SECONDS` |
| Scientific sample | Registry declares `data/samples/MAGL_5zun.pdb` + `data/samples/5.sdf`. CLI uses center `[5.99,3.01,17.345]`, box `[20,20,20]`, exhaustiveness 4, num_modes 3. Verify authorized input hashes/preparation and new true pose/energy output, not filenames or a hard-coded expected affinity |
| Runtime/browser | Python scientific dependencies, Node for current DOM/static tests, real browser for integrated evidence. Versions/readiness only checked later. No dependency installation/service startup is authorized now |

Use fresh child-only `HOME/USERPROFILE/APPDATA/LOCALAPPDATA/TEMP/TMP` and `MEDCHAT_USER_CONFIG_DIR`, `MEDCHAT_LLM_LOCK_DIR`, `MEDCHAT_AGENT_SESSION_DB`, `AGENT_STATE_DB`, `MEDCHAT_TASK_DB_PATH`. Existing offline isolation uses `MOLECULAR_CHAT_CONFIG`/`MEDCHAT_ENV_FILE` paths to nonexistent private files; this alone does not configure a safe *real* ordinary app. New safe launch must avoid persisting credentials even into temporary `.env`/LLM config. Never enumerate ambient environment; pass an explicit OS/runtime allowlist plus only approved dependency inputs. Keep task backend local, Temporal canary 0 and sandbox opt-ins off. No production model-setting HTTP writes.

## 5. Proposed final matrix (new IDs, not existing cases)

All required integrated rows run **three times**, against the same revision/assets. Component checks do not substitute for ordinary-entry coverage.

| Proposed row | Path and independently checked outcome |
|---|---|
| E01 Chat + authorization | Homepage HTTP/session → ordinary `/ws` → real decision provider; chat calls no scientific tool; tool-disabled/forbidden request cannot execute one; trace/one terminal per turn |
| E02 RDKit / clarification | Exact molecule count/order and independently recomputed descriptors/units; missing input asks, owned continuation resumes; cross-owner/reused continuation denied |
| E03 Whole invalid input | Invalid/mixed complete SMILES rejected without silently extracting valid fragments; expected diagnostic, no invented metrics/artifacts; unrelated dependency failure is not a pass |
| E04 ADMET truth | Real supported endpoints with backend/version and method label; unsupported outputs explicitly absent/unavailable; partial visible in API/events/UI. Pending ADMET fixes required before judging final scope |
| E05 PDE + BuChE | Both families, each classification/regression real RG-MPNN (`demo_mode=false`, `fallback_used=false`), approved bundle identities; direct CPU baseline vs API/tool/real-decision/WS/rendered numbers (initial tolerance 1e-6 absolute/relative). Unknown target fails closed; no source activation |
| E06 Generation + consumption | Real `gmm-llama:latest`, exact requested count of valid canonical-unique candidates; stable candidate ID↔SMILES mapping. Actual downstream property/activity/ADMET/ranker inputs consume versioned generated output with hashes, not merely matching plan order |
| E07 Target + reverse + RAG | Real source-backed target records/structures, reverse-target IDs consumed by lookup and subsequent design; no target evidence blocks generation. RAG cites verified retrieved source or explicit genuine no-hit; unavailable backend stays unavailable |
| E08 Vina positive | Approved MAGL sample inputs/preparation → actual Vina run → finite kcal/mol value parsed independently from new output, valid nonempty pose with SHA-256, consistent API/WS/artifact/UI values. Docking score is computational, not experimental affinity |
| E09 Docking negatives | Missing receptor/ligand/grid and deliberately absent executable in private test configuration: correct rejection/unavailable reason, no fabricated energy/pose, no uncontrolled fallback; do not remove or change real binaries |
| E10 Multi-step + references | Target-driven design / hit-to-lead and generated candidate selection: runtime upstream→downstream input/output digests and identities; ACK/confirm/restore/reconnect preserve owner/version. Tamper/stale/cross-owner refs reject; no regeneration substituted for selected candidate |
| E11 Lifecycle + partial | Bounded cancellation/disconnect, controlled provider timeout/malformed reply, bounded recovery, model switch via approved in-memory test seam, no stale model or duplicate costly calls; partial/not-completed remains visible with preserved numerical evidence |
| E12 Stability + cleanup | 3/3 required repetitions with fixed scope, per-case latency and request/tool counts, every failure retained, fresh trace/job IDs; owned children/clients/DB/ports drained and private state removed or explicitly retained on uncertain ownership |

E11 injected faults are **fault-injection evidence**, not real provider availability proof; pair with healthy real control. Scripted family checks and offline reference lab retain their own evidence class. Expected-negative assertions may pass while the scientific result remains invalid/unavailable. A legitimate positive scientific partial can pass a *preservation* assertion but cannot be relabeled full scientific success (including family classification/regression inconsistency).

## 6. Manifest proposal (embedded only; no manifest/cases created)

Recommended future private output: `outputs/agent_evaluation/current-real/<run-id>/manifest.json`, plus closed-schema per-iteration summaries and approved scientific artifacts. This path/schema is **proposed**, not an existing runner interface. Raw requests, model responses, user text, credentials and absolute source paths are excluded.

| Proposed fields | Required meaning |
|---|---|
| `schema_version`, `run_id`, `inventory_base`, `integrated_commit`, `tree_sha`, `package_revisions`, `scope_excludes` | Pin latest integrated packages 1–8; explicitly exclude 9; record code/runner hash and dirty-scope status |
| `case_manifest_sha256`, `cases[].id`, `required`, `expected_outcome`, `entry_path`, `evidence_class`, `decision_source`, `tool_sources` | Keep contract/replay/real-tool/scripted/real-decision/fault-injection distinct; exact expected count prevents empty/skipped suite pass |
| `dependencies[]` | Logical asset/provider ID, version, trusted origin/license, approved-use scope, input/card/weights/index hashes, validation status; no availability inference from name |
| `iterations[]` | Exactly 1,2,3; per-case validation status, unmodified scientific status, public failure code, latency, invocation counts, trace/decision/tool IDs, mode, main/generator identity separation |
| `truth_checks[]`, `consumption_edges[]` | Independent oracle/version/tolerance, finite/unit/identity checks; producer output-version/digest → consumer actual input-digest with subset/order assertion and evidence ID |
| `artifacts[]` | Safe run-relative path, media type, size, SHA-256, producer trace/tool/version, input digests, demo/fallback flags, validated pose/content status; verify existence/content/hash before cleanup |
| `api_key_present`, `privacy_check`, `source_unchanged`, `cleanup`, `overall_status` | Presence boolean only; pre-persistence projection verification; source selection unchanged without reading production user stores; process ownership, released port/state or retained reason; strict final aggregation |

Pass gate: complete expected case set × 3; required positive scientific results fully successful and all truth/provenance/consumption checks true; required negative/lifecycle assertions true; real provider actually decides in ordinary entry; both real families and real generation/Vina covered; no stubs in required real rows; privacy/cleanup gates passed. Any false assertion/fabrication is failed; any missing/pending/skipped/unavailable/positive-partial evidence blocks complete acceptance. Report failed/partial honestly, with a nonzero final gate; never use existing exit 0, average score, completion rate or LLM self-judgment as authority. Report all rounds, not only the best/latest. Provider stochasticity does not require identical generated molecules; canonical validity/count/identity/consumption contracts must hold each round.

## 7. Existing commands for later use — NOT executed or safe-launch recipes

Run only after parent authorizes implementation/execution and the bounded child isolation is verified. Relative paths below are existing repository entry points; `$RunRoot` denotes a fresh approved private output directory in that future launcher, not an existing variable/asset. Do not paste these into an ambient production-configured shell.

```powershell
python -B -m pytest tests/agent/test_real_acceptance_checks.py tests/agent/test_evaluation_runner.py tests/agent/test_decision_chat_acceptance.py tests/agent/test_main_integration_acceptance.py -q
python -B -m pytest tests/agent/test_analysis_contract.py tests/agent/test_admet_whole_input.py tests/test_admet_predictor_fallback.py -q
python -B -m pytest tests/test_activity_family_acceptance_support.py tests/test_activity_family_acceptance_process.py tests/test_activity_family_acceptance_chain.py -q
python scripts/run_agent_acceptance.py --mode contract --output "$RunRoot/contract.json"
python scripts/run_agent_acceptance.py --mode real --case-set all-real --repeat 3 --output "$RunRoot/scientific-real.json"
python scripts/run_agent_acceptance.py --mode replay --replay-input "$RunRoot/scientific-real.json" --output "$RunRoot/replay.json"
python scripts/run_decision_chat_acceptance.py --mode native --output "$RunRoot/decision-native-1.json"
python scripts/run_decision_chat_acceptance.py --mode json --output "$RunRoot/decision-json-1.json"
python -B -m pytest tests/test_activity_family_real_acceptance.py::test_trained_family_acceptance -q
python scripts/run_decision_browser_lab.py --port 6012 --mode native
python -B -m tests.scientific_reference_browser_lab --state-dir "$RunRoot/reference-lab" --port 6017
node tests/activity_family_results_test.js
node tests/home_scientific_references_test.js
node tests/decision_lab_ui_test.js
```

Decision scripts/family item have no `--repeat`: the approved orchestrator must run three independently bounded invocations with unique outputs. The family helper invokes `activity_family_acceptance_dom.js --input` with its actual private API-summary file; no such input was read/created here. Browser labs need an external lifetime deadline and browser assertions, not merely a listening port. Final ordinary-entry real command is pending package 7 and approved harness; none is claimed above. General regression/compileall/health checks from repository guidance remain future checks after isolation review, not evidence obtained here.

## 8. Bounded execution and parent approval sequence

- [ ] **Parent approves design and scope.** Freeze final required rows, native/JSON support and explicit real asset/provider access. This approval does not authorize production activation/deployment. Recommended choices above need no additional design brainstorming.
- [ ] **Re-inventory integrated dependencies.** Read final handoffs/diff for 4B/4C/4D/G1/ADMET/P7 and planner/residual work. Discover actual added test names with `rg --files`; attach their exact commands/results. Existing 4A/ADMET whole-input tests do not prove pending work complete.
- [ ] **Authorize bounded implementation separately.** Candidate new runner `scripts/run_current_real_acceptance.py` and focused tests `tests/agent/test_current_real_acceptance.py` are proposed, absent/unimplemented here. Keep scientific truth checks reusable in existing evaluation module; reuse family process ownership helpers where compatible. Establish failing regressions for exit-0 partial, first-round failure hidden by last-round success, scripted-as-real, missing consumption, malformed numeric/pose evidence and cleanup uncertainty before changes.
- [ ] **Prove safe launch offline first.** Fresh process/cwd/private stores; loopback unused non-6001 port (recommend 6018 for ordinary entry, 6012/6017 only for separate labs), one scientific case at a time. Disable autoreload/watchers not needed by test, external preloads/downloads and unrelated backends. No existing listener is stopped. Docking currently writes `cwd/temp_docking`, so private cwd is mandatory, not an assumed output env override.
- [ ] **Pin budgets before real run.** Recommend 90 s decision loop, <=4 model requests and <=2 tool attempts where fitting the row; explicit case-specific bounded limits for approved multi-step workflows. Keep family child <=120 s; Vina <=300 s and docking case <=420 s. Whole run <=60 min, graceful shutdown <=5 s followed by <=10 s owned-tree cleanup; budget exhaustion is retained failure/partial, not auto-extension. If assets/workload need larger limits, parent revises manifest before execution. Use Windows owned Job/process-tree containment (or POSIX owned process group); hiding a Start-Process window alone is not containment.
- [ ] **Authorized preflight then three real rounds.** Verify trusted asset snapshots/hashes, real tool versions/readiness and provider configuration in child only. No source model activation, no dataset training, no service/model auto-start. Run supplemental contract/replay/isolated suites with honest labels; run complete ordinary-entry matrix on final revision. Stage-scoped failures block dependent claims without fabricating completion.
- [ ] **Verify, clean, hand off.** Retain only approved sanitized summaries/scientific outputs needed for review; close sockets/clients/SQLite and terminate only owned descendants. Before deletion, verify absolute paths stay under owned root, no links/reparse traversal and ownership released. Uncertainty retains private state with cleanup failure; never broad delete caches/assets or kill by process name. Parent receives matrix of passes/failures/partials/pending dependencies, all 3 rounds and exact revision, not a deployment claim.

**Inventory-stage result (before P8-A below):** Design/inventory complete only. All real availability, actual latest integrated execution, package-7 ordinary-entry coverage and final scientific acceptance remained unverified/pending. At that stage no test/compile/health command was run and no commit or PR was created. The subsequent approved P8-A work is recorded below; it does not establish real acceptance.

## P8-A implementation / review-freeze ledger (2026-09-25)

Parent approved the precise offline design and TDD. This completes **P8-A report consistency only**, not package 8 real acceptance or package 7. Scope: new `src/agent/evaluation/aggregation.py`, new `tests/agent/test_evaluation_aggregation.py`, this plan and the approved small design. No existing scientific runner/CLI/models/Web code changed. Two existing pure scientific helpers (tool-order and provenance completeness) are reused.

### Evidence from this batch

| Stage | Actual result |
|---|---|
| Initial subprocess command including recursive cleanup | Tool policy rejected command before execution; no RED evidence |
| Initial over-broad read guard | 1 collection error: guard blocked RDKit's installed built-in fragment rules; narrowed guard to project assets, not scientific models/data. Not counted as behavioral RED |
| Callable positive control | RED 1 assertion failure (`aggregation callable missing`), then GREEN 1 passed |
| Exact case×round + existing truth/provenance behavior | RED 23 failed / 1 passed; GREEN 24 passed |
| Builtin/type/size/depth boundary + missing strict policy | RED 24 failed / 24 passed; GREEN 48 passed |
| Source/cleanup record linkage | RED 25 failed / 49 passed; GREEN 74 passed |
| Pose and expected rejection/partial | RED 17 failed / 80 passed; implementation run 1 failed / 96 passed due to a test assertion accidentally moved into wrong function (NameError). Restored assertion to original test; GREEN 97 passed |
| Consumed-leaf/partial-link/known-contradiction checks | RED 22 failed / 99 passed (including malformed values raising rather than returning fixed failure); GREEN 121 passed |
| Tuple compatibility and existing result authority | RED 4 failed / 125 passed; GREEN 129 passed |
| First related-regression + two-file in-memory compile | 136 passed, 0 skipped, 0 failed; compile passed |
| Final null-pose/event-status/error/demo-type regressions | RED 4 failed / 129 passed; final combined GREEN **140 passed, 0 skipped, 0 failed**, 2.10 s pytest, exit 0; two-file in-memory compile passed |

Final combined set = **133 new focused tests + 7 existing pure regression tests**. These are synthetic in-memory consistency tests; none is real scientific/provider evidence. Some added defensive examples already passed from earlier groups; counts above preserve that fact instead of claiming every new test independently went RED.

Existing related node IDs (no dataset tests selected):

- `tests/agent/test_evaluation_runner.py::test_scientific_stability_summary_reports_latency_and_failure_types`
- `tests/agent/test_evaluation_runner.py::test_replay_never_promotes_original_failure_to_passed`
- `tests/agent/test_evaluation_runner.py::test_replay_fails_skill_tool_and_forbidden_tool_contract_violations`
- `tests/agent/test_evaluation_runner.py::test_replay_reports_strict_status_rates_separately_from_completion_rate`
- `tests/agent/test_evaluation_runner.py::test_workflow_score_penalizes_order_and_fabrication`
- `tests/agent/test_real_acceptance_checks.py::test_structured_provenance_score_requires_input_hash_summaries_and_event_trace`
- `tests/agent/test_real_acceptance_checks.py::test_tool_provenance_uses_persisted_execution_input_and_hash`

### Isolation / exact final command

Used the installed MedChat Python path already documented in the earlier RAG extraction plan. The following was an inline test command, **not a new checked-in launcher or live service**. Child environment is cleared before explicit nonsecret test values are supplied; no ambient secret values enumerated/read. No conftest is loaded, no plugin autoload, no model or service starts. Imported library code includes bundled RDKit rule tables, not project datasets/weights. Network and project data/report/log opens are guarded. Tests consume only their in-memory fixtures and source code.

Private synthetic test directories were intentionally **retained**, not claimed cleaned: the initial recursive-cleanup command was policy-rejected; subsequent commands omitted deletion. Test child exited and was waited/disposed. This is not production cleanup evidence. No broad cleanup, hidden deletion fallback or user-asset operation was attempted.

```powershell
$root = 'D:/MedChat/molecular_chat_system_worktrees/current-real-acceptance'
$base = 'C:/Users/xkx52/AppData/Local/Temp'
$private = [IO.Path]::GetFullPath((Join-Path $base ('p8a-' + [guid]::NewGuid().ToString('N'))))
[IO.Directory]::CreateDirectory($private) | Out-Null
$p = [Diagnostics.ProcessStartInfo]::new()
$p.FileName = 'C:/Users/xkx52/.conda/envs/MedChat/python.exe'
$p.WorkingDirectory = $private
$p.UseShellExecute = $false
$p.CreateNoWindow = $true
$p.RedirectStandardOutput = $true
$p.RedirectStandardError = $true
$p.Environment.Clear()
$p.Environment['SystemRoot'] = 'C:/Windows'
$p.Environment['WINDIR'] = 'C:/Windows'
$p.Environment['PATH'] = 'C:/Users/xkx52/.conda/envs/MedChat;C:/Users/xkx52/.conda/envs/MedChat/Library/bin;C:/Windows/System32'
foreach ($key in @('TEMP','TMP','HOME','USERPROFILE','APPDATA','LOCALAPPDATA')) { $p.Environment[$key] = $private }
$p.Environment['PYTHONPATH'] = $root
$p.Environment['PYTEST_DISABLE_PLUGIN_AUTOLOAD'] = '1'
$p.Environment['PYTHONDONTWRITEBYTECODE'] = '1'
$p.Environment['MEDCHAT_USER_CONFIG_DIR'] = (Join-Path $private 'user-config')
$p.Environment['MEDCHAT_ENV_FILE'] = (Join-Path $private 'unused.env')
$p.Environment['MOLECULAR_CHAT_CONFIG'] = (Join-Path $private 'unused.yaml')
$p.Environment['AGENT_STATE_DB'] = (Join-Path $private 'agent.sqlite')
$p.Environment['MEDCHAT_TASK_DB_PATH'] = (Join-Path $private 'tasks.sqlite')
$p.Environment['TARGET_DB_PATH'] = (Join-Path $private 'targets.sqlite')
$p.Environment['TARGET_CACHE_DIR'] = (Join-Path $private 'target-cache')
$p.Environment['ACTIVITY_MODEL_DIR'] = (Join-Path $private 'models')
$p.Environment['REVERSE_TARGET_DATA_DIR'] = (Join-Path $private 'reverse')
$p.Environment['AGENT_HARNESS_MODE'] = 'legacy'
$p.Environment['MEDCHAT_TASK_BACKEND'] = 'local'
$p.Environment['MEDCHAT_TEMPORAL_CANARY_PERCENT'] = '0'
$code = @'
import sys
from pathlib import Path
repo = Path(sys.argv[1])
def audit(event, args):
    if event in ('socket.connect', 'socket.getaddrinfo'):
        raise RuntimeError('network forbidden in offline P8-A')
    if event == 'open' and isinstance(args[0], (str, bytes)):
        name = str(args[0]).replace('\\', '/').lower()
        if name.startswith(str(repo).replace('\\', '/').lower() + '/') and any('/' + part + '/' in name for part in ('data', 'outputs', 'logs')):
            raise RuntimeError('asset read forbidden in offline P8-A')
sys.addaudithook(audit)
for relative in ('src/agent/evaluation/aggregation.py', 'tests/agent/test_evaluation_aggregation.py'):
    compile((repo / relative).read_text(encoding='utf-8'), relative, 'exec')
print('Two-file in-memory compile: passed')
import pytest
raise SystemExit(pytest.main([str(repo / 'tests/agent/test_evaluation_aggregation.py'),
    str(repo / 'tests/agent/test_evaluation_runner.py') + '::test_scientific_stability_summary_reports_latency_and_failure_types',
    str(repo / 'tests/agent/test_evaluation_runner.py') + '::test_replay_never_promotes_original_failure_to_passed',
    str(repo / 'tests/agent/test_evaluation_runner.py') + '::test_replay_fails_skill_tool_and_forbidden_tool_contract_violations',
    str(repo / 'tests/agent/test_evaluation_runner.py') + '::test_replay_reports_strict_status_rates_separately_from_completion_rate',
    str(repo / 'tests/agent/test_evaluation_runner.py') + '::test_workflow_score_penalizes_order_and_fabrication',
    str(repo / 'tests/agent/test_real_acceptance_checks.py') + '::test_structured_provenance_score_requires_input_hash_summaries_and_event_trace',
    str(repo / 'tests/agent/test_real_acceptance_checks.py') + '::test_tool_provenance_uses_persisted_execution_input_and_hash',
    '--noconftest', '-c', str(repo / 'pytest.ini'), '-q', '-p', 'no:cacheprovider', '--tb=short']))
'@
foreach ($arg in @('-B','-s','-c',$code,$root)) { $p.ArgumentList.Add($arg) }
$process = [Diagnostics.Process]::Start($p)
$out = $process.StandardOutput.ReadToEndAsync()
$err = $process.StandardError.ReadToEndAsync()
if (-not $process.WaitForExit(120000)) { $process.Kill($true); $process.WaitForExit(); throw 'P8-A focused deadline exceeded; private directory retained' }
$out.GetAwaiter().GetResult()
$err.GetAwaiter().GetResult()
$code = $process.ExitCode
$process.Dispose()
if (-not $private.StartsWith([IO.Path]::GetFullPath($base) + [IO.Path]::DirectorySeparatorChar)) { throw 'Cleanup path outside owned base' }
Write-Output 'Private synthetic test directory retained (no recursive deletion requested).'
exit $code
```

### Review handoff

- Immutable output limitations: `live_execution_verified=false`, `final_acceptance=false`, `scope=offline_report_validation` even on gate passed.
- Supplemental IDs/hashes are checked only for internal consistency, not authenticity or filesystem reality. Prospective provider-request/tool-execution linkage remains collector work; current reports without it cannot silently pass the strict gate.
- `details.scientific_status` preserves the existing source **case status**, which must not be confused with raw underlying scientific tool outcome. No final scientific success claim is made.
- Known batch limits: one required pose observation per case×round; two fixed negative truth-check reasons; no JSON CLI/report adapters, artifact reader, source authenticator or normal WS launcher.
- Full pytest/compileall/health/live acceptance not run; full needs parent's queue, live needs a separately authorized batch.
- Freeze for two independent parent-arranged reviews (spec/quality). No review approval claimed, no push/PR/merge-to-main.

## P8-A Euclid SPEC correction ledger

Parent returned four P2 findings; prior SPEC was not approved. Starting tree clean at `3309dad` (implementation `6ca2c5c`). Minimal corrections touch only the same aggregator, its test file and these two design/plan documents. No commit/staging/push; HEAD stays unchanged. Code/test content hashes and precise corrected invariants are recorded in `docs/superpowers/specs/2026-09-25-current-real-acceptance-aggregate-design.md` for the same SPEC reviewer.

Reused the inline isolated command above, with only the pytest selection changed for each RED/GREEN pair: select `tests/agent/test_evaluation_aggregation.py`, add `-k spec_p1`, `-k spec_p2`, `-k spec_p3` or `-k spec_p4`, omit the seven related node IDs during each pair. Same `--noconftest -c pytest.ini -q -p no:cacheprovider --tb=short`, cleared child environment, private cwd, network/project-asset guard, 120-second deadline and two-file in-memory compile. The final combined run used the exact original inline command above including all seven existing node IDs. No actual asset/report inputs were used; fixture records are in memory. Private test directories retained, not claimed cleaned.

| Group | Actual RED | Actual GREEN |
|---|---|---|
| P1 terminal/provenance association (distinct and repeated tools) | 2 failed, 2 passed, 133 deselected; incorrect `passed` instead of `failed` | 4 passed, 133 deselected |
| P2 terminal name vs success, including unknown outcome | 3 failed, 2 passed, 137 deselected; incorrect `passed` instead of `failed`/`partial` | 5 passed, 137 deselected |
| P3 observed provider evidence under none policy | 3 failed, 2 passed, 142 deselected; incorrect `passed` instead of `failed` | 5 passed, 142 deselected |
| P4 positive claim / unknown check inside expected rejection | 7 failed, 3 passed, 147 deselected; incorrect `passed` instead of `failed`/`partial` | 10 passed, 147 deselected |
| Combined aggregator + seven existing pure regression nodes | — | **164 passed**, 0 failed, 0 skipped; pytest 1.96 s, exit 0 |

All four behavioral REDs used the actual aggregator and in-memory records; no mocked aggregate return. Final two-file in-memory compile and `git diff --check` passed. No producer changes, authentication claims, live/full execution or external coordination. `live_execution_verified=false` / `final_acceptance=false` are unchanged. Work is content-frozen awaiting the same SPEC re-review, not declared approved.

## P8-A Socrates QUALITY correction ledger

Starting state: uncommitted Euclid correction freeze above, 164-pass baseline, HEAD still `3309dad`; parent reports four QUALITY P2 findings and no approval. Scope stays aggregator + its tests + these two docs. The implementation uses one observed-record verification pass before supplementary completeness checks; pose truth facts are checked before artifact completeness. Public API/scientific models remain unchanged. No four-case blacklist, new framework, I/O or producer changes.

TDD sequence: tools/association (QUALITY findings 1 and 4 share the same gating defect), provider observations (finding 2), pose flags (finding 3), then the same independence invariant's adjacent duplicate/outcome/scripted cases. Each group first ran against the pre-fix implementation, then received its minimal fix; neighboring correct and partial controls were retained.

Commands: reuse the exact isolated inline PowerShell command under `Isolation / exact final command` above. For grouped runs omit its seven related node-ID lines, select the same aggregator test file, and append `'-k', 'quality_tools'`, `'quality_provider'`, `'quality_pose'` or `'quality_independent'` respectively to the existing pytest arguments. Both combined runs used the unchanged exact command including all seven related nodes. Same cleared child environment, private cwd, network/project-asset guard, `--noconftest`, disabled cache/bytecode/plugin autoload, 120-second deadline and two-file in-memory compilation. Test inputs are synthetic in-memory reports, never real report/assets/user text.

| Group | Actual RED | Actual GREEN |
|---|---|---|
| Tools: orphan completed/failed terminals; swapped IDs/outcome/tool/trace × absent source/trace/policy/IDs; valid/partial neighbors | 20 failed, 13 passed, 157 deselected | 33 passed, 157 deselected |
| Provider: orphan request on planning/other event, absent trace/policy/revision, observed duplicate without source, missing counterpart and complete/static controls | 13 failed, 3 passed, 190 deselected | 16 passed, 190 deselected |
| Pose: true/false/absent/malformed flag × absent artifact/source/hash, invalid reported energy without artifact | 35 failed, 4 passed, 206 deselected | 39 passed, 206 deselected |
| First combined checkpoint | — | 252 passed, pytest 2.23 s, exit 0 |
| Independence neighbors: duplicate execution IDs, terminal name vs payload with unknown provenance, scripted model vs policy without source, missing IDs vs independent trace facts | 7 failed, 7 passed, 245 deselected | 14 passed, 245 deselected |
| Final combined aggregator + seven existing pure nodes | — | **266 passed**, pytest 1.95 s, exit 0; no failures/skips |

REDs were actual aggregator assertions, not mocked returns or collection errors. Some assertion failures intentionally exposed missing contradiction/missing-observation reason codes when another independent failure/partial already controlled the verdict; the provider group also reproduced a false failure for a fully declared orphan whose missing decision should be partial. Thus the fixes preserve partial semantics as well as reject contradictions. Final in-memory compilation and diff whitespace check passed.

Freeze identity and invariant details are in the design's `Socrates QUALITY P2 corrections` section. No full/live, actual assets/env/userstores, staging/commit/push or PR. No source/config changes outside the approved files. Test process exited; private synthetic directories are retained under the existing wrapper and are not presented as verified cleanup. Both immutable live/final flags remain false. Stop implementation for parent-arranged **same QUALITY recheck**, not a claim of approval or real scientific acceptance.

## P8-A second QUALITY: cross-slot identity ownership

Parent reports four previous findings closed but one further P2: missing supplementary source hides observed cross-slot reuse. Starting content is the 266-pass freeze above; HEAD remains `3309dad`. Same file scope and offline restrictions. The minimal correction replaces separate global source/decision/artifact occurrence scans with one typed identity-to-case×round ownership check over all supplied declarations and observations, before completeness gates. Existing within-slot same-role duplicate rejection remains intact.

First write/run 42 tests: 32-case matrix of identity kind (request/execution/trace/artifact) × cross-case/cross-round × source present/missing × observed repeat/nonrepeat; two same-slot declared+provenance+tool_started/tool_completed controls; eight source/provenance/started-event/artifact identity-origin comparisons against incomplete pose records. Actual RED: **20 failed, 22 passed, 259 deselected**. Failures included the reported partial-instead-of-failed result plus missing global conflict reasons where a separate local contradiction already failed. Artifact-ID preservation and correct/partial controls passed before the fix. No collection errors or mocked aggregator returns.

After adding the unified ownership function: **42 passed, 259 deselected**; combined exact focused command: **308 passed**, pytest 2.12 s, exit 0. Then add 12 neighboring regression assertions for all sources absent, orphan markers including decision IDs, missing artifact association, cross-namespace text equality/equal pose hashes, and declared-only versus observed-only ownership. They all passed without further production changes; this supplemental run is not represented as another RED→GREEN cycle. Ownership selection: **54 passed, 259 deselected**.

Final combined result: **320 passed**, pytest 2.42 s, exit 0, no failures/skips; original 266 tests unchanged. Two-file in-memory compilation and `git diff --check` passed. Commands reuse `Isolation / exact final command` above unchanged for combined runs; ownership-only runs omit the seven related node-ID lines and append `'-k', 'ownership'` to the same pytest arguments. Same isolated cleared-env private child, 120-second deadline, no conftest/plugin/cache/bytecode, no network/project assets. Fixtures are wholly synthetic in-memory records.

All existing globally compared identity kinds were audited: source request/execution/trace, observed decision IDs, and strict pose artifact IDs. Ownership also includes corresponding event/provenance/pose trace and execution observations; no generic path parser or new identity metadata was added. Missing source/step/case observation cannot mask a known cross-slot conflict. Same-slot corroboration is not double-counted; equal content hashes and shared run/revision remain valid. Global failures coexist with per-slot partial reasons rather than rewriting raw scientific case status.

New code/test hashes are in the design's `Socrates second QUALITY` section. Exactly the original two Python files and two docs remain modified and unstaged; no commit/push/full/live, actual assets/env/userstores or external coordination. Private synthetic test directories retained, not claimed cleaned. Freeze for **the original QUALITY recheck**; no approval or actual execution proof asserted, immutable live/final flags stay false.

## Parent-approved local commit and integration checkpoint

Parent reports Euclid SPEC APPROVE and Socrates QUALITY APPROVE after the final same-spec invariant correction. Socrates's independent evidence is parent-reported **320 baseline + 37 identity + 1168 malformed-input checks**; this worker does not claim those independent runs as its own. Prior failed freezes and complete RED/GREEN ledgers remain preserved above. This worker's last actual focused result is 320 passed.

Authorized sequence: commit exactly the two Python files and two existing P8 docs; merge already-fetched `origin/main` commit `7b5611fed039aa7aebde62063f45e45e965cf23a` (PR75, 4D inventory complete); stop and report any manual source conflict. Inspect newly integrated evaluation-related tests/imports for secrets/network/real execution before selecting offline collision focus. Rerun the original isolated 320 command, then the inspected related focus, retaining source/test hash equivalence. Local documentation commits are authorized. No other worktree, real report/assets, full/live, push or PR. Parent says G1 occupies the heavy full slot and PR74 ADMET is still in CI/unmerged; this worker will not alter that coordination state.

After focused verification, freeze locally for a full slot or parent's CI-only decision. P8-A offline review/integration does not complete Package8; `live_execution_verified=false` and `final_acceptance=false` remain unchanged.
