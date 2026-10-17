# PR 209 final-review remediation implementation plan

> **For agentic workers:** Use subagent-driven-development for bounded tasks, then independent spec and quality review. Follow test-driven-development for every production change.

**Goal:** Close the approved one P1 and four P2 findings without changing docking science, approval requirements, or merging PR 209.

**Architecture:** Keep constructor-bound tool identity and the existing source compiler, target lookup, request parser and constraint-report interfaces. Reject conflicting evidence; preserve unknown/missing chemistry honestly. Explicit numeric property bounds must be evaluated or reported unverified, never silently discarded. Do not change the already-approved immutable artifact snapshot handoff.

**Tech Stack:** Python 3.10, pytest, RDKit, Gemmi; existing FastAPI and native browser code unchanged.

**Baseline:** `ff1d3c06d7b4c5dea9de39cbaa06e5a5155eda70`, branch `codex/browser-agent-real-acceptance`. Maintainer authorized these fixes, related tests, independent review, commits/push and PR update; explicitly no merge.

## Task 1: Pinned toolchain before ligand dispatch

- [x] Add synthetic tests in `tests/test_chat_ligand_toolchain_fence.py`. Mutate the pinned ligand command during the ligand-preparation phase callback; assert no adapter command is invoked, for scoped/unscoped and file/SMILES entry paths. Also cover direct preparation and unchanged/legacy controls.
- [x] Run focused tests and record the expected missing-fence failures before touching production.
- [x] Add the existing `_require_chat_toolchain()` check at the real ligand command boundary in `src/docking/molecular_docking_service.py`, retaining the later Vina check and command ownership.
- [x] Run toolchain, receptor-preservation, ligand-preparation and cancellation consumers. This is a pre-dispatch fence, not an OS executable lock or transitive package attestation.

## Task 2: Formal charge consistency

- [x] Extend `tests/test_rcsb_source.py` with conflicting known charges, matching signed charges and explicit missing/unknown cases; demonstrate known-conflict acceptance before repair.
- [x] In `src/docking/rcsb_source.py`, compare known integer `_chem_comp_atom.charge` values from archive entry and CCD. Missing/unknown values are not zero and must have an explicit compatibility policy. Reject malformed charge text and known conflicts without changing coordinates or inventing chemistry.
- [x] Run source, source-consumer and projection regressions.

## Task 3: Ordinary target-name/accession consistency

- [x] Add tests alongside target database/exact-PDB tests for `EGFR (UniProt P06276)` without PDB; a synthetic BCHE service response must not be accepted. Include matching identity, name-only and accession-only controls.
- [x] Extend existing identity conflict checks in `src/agent/tools/target_database_tool.py` to ordinary lookup without changing service query contracts or exact-PDB behavior.
- [x] Run target tools, parser and planner regressions.

## Task 4: Explicit property bounds

- [x] Test the newly admitted request `优化 CCO，降低 logP；以 PDE 为靶点，要求 TPSA < 20`, including a synthetic TPSA=100 candidate and missing-value controls. First show the constraint disappears.
- [x] Minimally extend `src/agent/property_constraints.py` and existing verifier consumers as needed so TPSA/QED/HBA/HBD numeric bounds are checked against genuine supplied metrics, or explicitly marked unverified when unavailable. Preserve existing MW/logP behavior and scientific-claim scope.
- [x] Run property constraints, generation, optimization and target-role planner tests; synthetic values are fixtures, not scientific results.

## Task 5: Negation with SMILES dots

- [x] Add request parser/planner tests for `比较 CCO.Cl 和 CCN 的性质并排序；靶点: BuChE；无需 CCO.Cl 的活性`. Assert no activity step. Include decimal and normal sentence punctuation controls.
- [x] Update `src/agent/planning/request_parsing.py` to preserve token-internal dots while retaining sentence-level negation boundaries.
- [x] Run request parsing, router/planner and tool-policy consumers.

## Integration and handoff

- [x] Review only explicit task files; preserve unrelated work and never stage runtime assets or secrets.
- [x] Run the affected regression union, `python -m compileall -q src scripts`, `git diff --check`, and applicable contract tests. No JS/deployment/data assets are planned for modification; no new real Vina run is implied by offline tests.
- [x] Independent fresh spec review, followed by fresh quality review; close any important findings and re-review changed scope before publishing.
- [ ] Update the existing acceptance/handoff documentation with RED/GREEN evidence, exact commands, limitations and review outcome. Explicitly stage task files, commit and push the existing branch, then update PR 209.
- [ ] Check CI for the exact new head until a final outcome. Keep Open/Ready/unmerged; do not enable auto-merge or submit a fabricated human approval.

Pre-publication checkpoint: all five fixes, regression and independent SPEC/QUALITY are complete. The last two publishing/CI steps necessarily follow this saved commit; their exact-head outcomes are recorded in PR 209's latest status, without a self-referential documentation-commit loop.
