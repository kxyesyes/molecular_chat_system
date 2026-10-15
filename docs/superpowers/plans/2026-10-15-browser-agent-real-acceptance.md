# MedChat Real-Browser Agent Acceptance and Repair Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Evaluate the actual chat agent through a real browser, fix reproducible application defects, and rerun the originating and related scenarios without substituting API calls or synthetic scientific outputs for acceptance.

**Architecture:** Exercise the standard production chat entry on the current task branch. Reuse real local science assets and the existing saved main-model configuration, with separate acceptance session/task databases. Business actions originate only from browser controls; source, sanitized logs and persisted tool observations are diagnostic evidence, not replacement acceptance paths.

**Tech Stack:** FastAPI, Jinja2, vanilla JavaScript, browser WebSocket UI, existing DeepSeek-compatible chat configuration, Ollama, RDKit, ADMET-AI, RG-MPNN, target/reverse-target assets and AutoDock Vina when available.

## Boundaries and grading

- Baseline: merged PR #208, commit `5168da5f`; task branch `codex/browser-agent-real-acceptance`.
- The existing service on port 6003 predates this baseline. Preserve it and use a task-owned loopback service on port 6001 for current-code acceptance.
- Preserve existing tracked/untracked work, credentials, saved settings and data. Do not commit logs, databases, raw transcripts, screenshots or scientific output assets.
- Use only benign public example molecules. Initial generation is at most three candidates; optimization at most two; docking is one ligand against one selected structure before any expansion.
- Never read or print API keys. The app may use its existing local private configuration; no new key is saved. No real key is put in a report or command.
- Pass requires correct intent, parameters, identity/constraints, actual tool outcome, and a faithful final answer. A reply or a rendered page is not a pass.
- Statuses: PASS, FAIL (application defect), BLOCKED (missing external service/data/model), and NOT RUN. Correctly reporting a blocked computation does not make the scientific task pass.
- Unit tests may use explicit fixtures to reproduce bugs, but browser acceptance uses real services and results only.
- Observe each scene's expected behavior below before submitting it. Record every turn, visible task state, trace/run identity when available, actual tool inputs/outcomes, elapsed time and whether final claims match evidence. Persist only a redacted summary in the tracked report.

## Representative scenarios and expected behavior

Each independent scenario starts in a fresh browser chat. Follow-ups within a scenario remain in the same chat. Browser reload is not treated as proof that server-side conversation identity was reset.

| ID | UI request / follow-up | Expected behavior before execution |
| --- | --- | --- |
| S01 | `请计算阿司匹林 CC(=O)Oc1ccccc1C(=O)O 的分子性质和 ADMET。给出数值、单位与计算来源，区分计算值和模型预测。` | Correct aspirin input reaches real property and ADMET tools; numerical results, model/version/provenance and limitations agree with outputs; no experimental claims. |
| S02 | In S01 chat: `把分析对象改为咖啡因 CN1C(=O)N(C)c2ncn(C)c2C1=O，只比较它和刚才阿司匹林的分子量与 logP，不要再运行 ADMET。` | Correctly replace active molecule, retain the previous aspirin for comparison, respect the explicit no-ADMET constraint, and compare matching endpoints/units. |
| S03 | `查找人源丁酰胆碱酯酶 BuChE（UniProt P06276），列出可用于对接的实验结构，说明物种、结构编号和共晶配体信息。` | Real target lookup; exact human identity; structures and ligand claims are backed by returned records. Missing structure data are explicit. |
| S04 | `以 PDE 为靶点设计三个候选分子，要求分子量小于 450、logP 小于 3，返回 SMILES 并逐项检查约束。` | Recognize target-driven design, request three real generated candidates, validate SMILES/count and calculate constraints. Unsupported specificity or unavailable generation is reported honestly; no invented candidates or guaranteed inhibition. |
| S05 | In S04 chat after actual candidates: `对第 2 个候选做两种优化，优先降低 logP，仍保留分子量小于 450 的约束。` If S04 blocks, use explicit aspirin in a separate optimization request and record that original reference-follow-up was blocked. | Correct candidate reference and target/constraint carry; two distinct real optimization outputs or explicit inability; genuine before/after property comparison, no fabricated improvement. |
| S06 | `分别预测阿司匹林 CC(=O)Oc1ccccc1C(=O)O、咖啡因 CN1C(=O)N(C)c2ncn(C)c2C1=O 和对乙酰氨基酚 CC(=O)Nc1ccc(O)cc1 对 BuChE 的活性，保留每个候选的原始预测和复核提示。` | Correct three identities and family; real deployed models; preserve raw pIC50/classification values, per-row status and conflicts. Do not relabel incomplete/conflicting batches as wholly successful or erase valid rows. |
| S07 | In S06 chat: `只保留通过一致性检查的候选，按活性证据排序；解释另外的候选为何没有参与排序，不要改动原始预测。` | Reuse prior candidates/target/results, exclude conflicts with explicit reasons, retain raw values and do not silently reweight absent evidence. |
| S08 | `对咖啡因 CN1C(=O)N(C)c2ncn(C)c2C1=O 做反向寻靶，给出前 5 个候选靶点、依据和局限。` | Real reverse-target source; no unsupported targets/scores. Specific safe failure stage/reason if blocked; no success claim after validation failure. |
| S09 | In S08 chat: `查找排名第一的靶点结构，并说明能否继续对接。` | Resolve reference only from real prior result; pass exact target identity downstream. If reverse-target failed, clarify/block dependency without pretending structure retrieval ran. |
| S10 | In S03 chat, choose a structure actually returned by UI: `用阿司匹林 CC(=O)Oc1ccccc1C(=O)O 与上述 [已返回结构编号] 做一次对接。使用共晶配体定义口袋；缺少该信息先说明，不要猜坐标。` | Correct real protein/ligand, valid preparation and consent where required, real Vina output and reproducible artifacts. No fabricated affinity or guessed grid; missing executable/structure/ligand is BLOCKED. |
| S11 | `比较阿司匹林 CC(=O)Oc1ccccc1C(=O)O 和对乙酰氨基酚 CC(=O)Nc1ccc(O)cc1 的性质、ADMET 和 BuChE 活性，按可用且通过检查的证据排序，保留所有原始结果与排除原因。` | Actual cross-tool fan-out and candidate-aligned merge; correct parameter binding, no row mixing, no unsupported ranking or concealed failures. Final output answers the full comparison with source evidence. |
| S12 | Fresh chat: `帮我把它和那个靶点做分子对接。` | Ask for missing ligand/target, do not reuse another chat's state or invent inputs; no docking dispatch before adequate identity and required approval. |
| S13 | `计算 SMILES C1CC 的性质。` Then `更正为 CC(=O)Oc1ccccc1C(=O)O，请继续。` | Reject invalid ring closure truthfully without science claims, then recover on explicit corrected molecule without stale failed input or unrelated tools. |
| S14 | `找一下 PDE 的资料，不要生成分子，也不要运行对接；如果具体亚型影响答案，请先澄清。` | Respect negated operations, choose lookup/clarification rather than generation/docking; describe family vs subtype scope correctly. |

S10's bracketed structure is selected only from actual S03 UI evidence; it is not a preselected or guessed identifier. S05's referenced candidate similarly depends on actual S04 output.

## Execution and repair checkpoints

- [x] Inspect branch, existing service age, browser availability and local asset presence without reading credentials.
- [x] Start the standard current-code app, verify its browser UI and model configuration presence, and record the exact profile. Do not use the synthetic decision lab.
- [x] Execute S01–S14 from the browser, preserving multi-turn continuity within each group and recording pass/fail/blocked evidence.
- [x] For each reproducible defect, trace browser evidence through source/logs to a concrete cause; append a bounded file/test repair entry before changing production code.
- [x] Observe a failing regression, implement the smallest in-scope change, run related tests, restart only the task-owned server and reload the browser.
- [x] Rerun the original UI scene and related scenes with real tools. A unit test alone does not close a UI defect.
- [x] Review scientific truthfulness and cross-tool/cross-turn identity boundaries independently before publication.
- [x] Prepare the named source/tests/redacted report files for one draft PR; keep all runtime assets and unrelated work out. Full-suite blockers prevent merge readiness; do not merge without new authorization.
- [x] Prepare the scenario matrix, key failure evidence, fixes, blocked dependencies, limits and actual agent capability assessment in the validation report.

## Evidence and implementation locations

- Plan: this file.
- Redacted report: `docs/superpowers/validation/2026-10-15-browser-agent-real-acceptance.md`.
- Raw local-only diagnostics/screenshots/databases: ignored `outputs/browser-agent-real-acceptance/`; session identity database must reside outside a Git checkout under the existing private user configuration boundary.
- Production/test changes are selected only after a real UI failure is reproduced; no architecture/profile migration, model retraining, threshold change or broad refactor is pre-authorized by this plan.

## Browser failure repair entries

- S02/S03/S08/S12/S13/S14: bounded positive-intent routing, explicit missing-input clarification, socket-local validated property comparison and correction recovery. Scope: routing/planning, chat handler, focused property-context helper and regression tests; no model or shared-session state substitution.
- S04 first failure: new generation output counts were mistaken for a prior candidate reference. Scope: `src/web/scientific_references.py` and focused reference tests; preserve confirmed-view, ordinal and session boundaries.
- S04 second failure after the reference repair: the role-target parser treated the complete MW/logP constraints and SMILES output instruction as an unknown target. Scope: bounded non-target tail grammar in `src/agent/contracts/target_request.py`, plus regression tests proving unknown suffixes and target switches remain rejected.
- S03 supplemental search: the tool sent the entire natural-language request as a provider identifier (`UniProt:invalid_identifier`). Scope: explicit-accession parsing in `src/agent/tools/target_database_tool.py`; malformed, partly invalid and negated identifiers must not silently become a valid shorter query.
- S06: the initial character in the Chinese molecule name for paracetamol was mistaken for a target preposition. Scope: `src/agent/tools/activity_input.py` and named-molecule list tests, retaining full-structure and target-conflict validation.
- S05 supplemental optimization: the requested two novel structures became a count of one and the unchanged input seed was reported as a generated candidate. Scope: generation count contract, generator seed exclusion and focused generation tests. Real optimization improvement is not guaranteed and must remain independently verified.
- S02 visible comparison formatting: the browser displays raw pipe-delimited Markdown rather than aligned table cells. Scope: the existing escaped home-chat renderer and a standalone Node regression, retaining URL filtering and literal fallback for malformed tables.
- S04 third-pass integration: real generation returned three structures, but downstream activity rejected the explicit role-form PDE target. Scope: activity input interpretation of the already validated target-role design clause, preserving rejection of unknown/conflicting targets.
- S04/S05 constraint verification: numeric MW/logP bounds were present in the request but not checked explicitly in the generated-candidate summary; the optimization verifier checked relative logP only. Scope: bounded descriptor-threshold checks against real property rows, a visible per-candidate check table, and regression tests; no new scientific calculations or threshold changes.
- S04/S11 real ADMET evidence: all requested models ran, but known scientific-boundary notices caused the generic validator to set `validated=false`; the structured design report also rejected the post-alignment source representation. Scope: distinguish only the exact known ADMET advisory notices from validation failures, verify source hashes across server-owned alignment, and retain all notices and strict malformed/forged-evidence rejection. Do not relax the evidence report to hide upstream inconsistencies.
- Independent regression review: constrain imperative negation so `分别` remains affirmative; prevent descriptor-only formatting from replacing activity comparisons; reflect missing historical comparison evidence in the terminal status. Preserve source-pronoun/ACK, target-list, malformed-unit, failed-row and per-cell Markdown boundaries with focused failing tests before repair.
