# Docking Typed Contract Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or executing-plans. Keep production and integration-test write sets disjoint.

**Goal:** Complete the approved molecular_docking-only contract without replacing scientific validation or resource lifecycle.

**Architecture:** One specialized LegacyPythonToolAdapter subclass and non-projecting Pydantic transport views. Factory selects it only for molecular_docking. Existing execute_tool_compat, DockingResultValidator and rejected docking cleanup remain authoritative.

**Tech Stack:** Python 3.10, Pydantic 2.5+, pytest, existing tool registry and specialists.

## Task 1 — Actual integration RED (controller)

- [x] Create `tests/agent/test_docking_contract_integration.py`: real MolecularDocking, Registry, DockingAgent and SpecialistDispatch. Inject only the service; use synthetic temporary receptor/ligand/pose files.
- [x] Assert direct structured, query-plus-structured and delegated wrapped requests reach the same service exactly once, retain box/SMILES/config and non-scientific extensions, emit valid source information. Missing box must never call the service; text must reach the domain refusal.

```python
result = registry.resolve("molecular_docking").execute(payload)
assert result.success
assert len(service_calls) == 1
assert service_calls[0]["config"].manual_center is True
assert result.data["best_pose"]["pose_file"] == str(synthetic_pose)
```

- [x] Run new integration file with the isolated runner below before implementation; retain actual failures. Existing wrapped paths may already pass and are compatibility baselines, not new RED claims.

## Task 2 — Contract RED/GREEN (implementer)

- [x] Create `tests/agent/test_docking_tool_contract.py` against actual factory adapters, first establishing failed behavior for unsupported input forms and unvalidated raw/canonical outputs.
- [x] Create `src/agent/tooling/docking_contract.py`: `DockingInput`, `DockingOutput`, `DockingToolAdapter`; precise transport shape, one wrapper, explicit box/ligand, finite numeric strings preserved. No dataclass defaults or execution control promotion.
- [x] In `src/agent/tooling/factory.py`, choose these three only when canonical name equals molecular_docking. Preserve every ToolSpec policy field and all other tool registrations.
- [x] Validate pre-compat and post-compat observations including partial/failure/raw_result snapshots; use existing scientific validator and rejected-result scrub, bounded known carriers, no projection or reexecution. Fixed safe validation errors only.
- [x] Cover owner/aliases/readiness, original input unchanged, once-only execution, failed/partial preservation, malformed scientific claims, local/OpenSandbox proof, timeout slot retention, caller raw_validator, and close. Test bad payload errors contain no original input.
- [x] Run both new files and the seven-file baseline from the approved design. Correct any noncompliant synthetic fixtures only with evidence and retain all original assertions; do not broaden production write set.

## Task 3 — Independent review and regression (controller)

- [x] Request separate SPEC then QUALITY reviews of actual diff. Reproduce findings before fixing; preserve all failed attempts in handoff. One unreproduced intermediate assertion remains explicitly unexplained, not claimed fixed.
- [x] Run tests/agent plus the seven-file docking baseline, relevant Web/model/session lifecycle files listed in previous activity handoff, and offline contract with sockets blocked. No real model/Vina/Temporal/OpenSandbox execution.
- [x] Compile src/scripts in memory, run git diff --check and scoped credential scan; run existing Node scripts if the broader contract gate requires them. Record skips rather than call them successes.
- [ ] Write `docs/handoff/docking-typed-contract.md` with exact commands, RED/GREEN, independent review and pending items. Precisely stage this batch, commit, push draft PR, attach it, verify current-head CI. Merge only after specific PR authorization and fresh gates.

## Isolated command template

Use MedChat Python and the existing runner in `docs/superpowers/plans/2026-09-24-rag-service-extraction.md`, replacing only the worktree path with this docking worktree. It clears non-whitelisted environment, uses temporary cwd/config/DBs, disables real/canary, and invokes normal subprocess pytest with conftest:

```powershell
$python='C:/Users/xkx52/.conda/envs/MedChat/python.exe'
$plan=Get-Content docs/superpowers/plans/2026-09-24-rag-service-extraction.md -Raw
$m=[regex]::Match($plan,'(?s)\$runner = @''\r?\n(.*?)\r?\n''@')
$runner=$m.Groups[1].Value.Replace('D:/MedChat/molecular_chat_system_worktrees/rag-service-extraction-pr','D:/MedChat/molecular_chat_system_worktrees/docking-typed-contract-pr')
$runner | & $python -B -c "import sys; exec(sys.stdin.read())" tests/agent/test_docking_contract_integration.py
```

Do not apply the global target-path override wrapper to the full root tests: prior target tests own their own roots. Full CI and focused local results are distinct evidence. The original project, real models/data and production configuration remain untouched.
