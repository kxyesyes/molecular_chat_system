# Agent real-model audit and bounded cleanup implementation plan

> **For agentic workers:** Use executing-plans, systematic-debugging and test-driven-development. User approved the bounded repair/cleanup scope during this task.

**Goal:** Verify real family weights through API/Agent/rendering, fix reproduced failures, and simplify duplicated logic without removing public compatibility.

**Architecture:** Keep Supervisor -> harness -> workflow -> tool adapter and existing scientific validators. Parse metric labels only outside explicit SMILES fields. Preserve structured failures and reuse the existing bounded, secret-safe Web failure formatter. No new agent framework, no model activation in the original registry, no retraining.

**Tech stack:** Python/FastAPI, PyTorch/PyG/RDKit, pytest, native JavaScript/Node.

## 1. Baseline and real weights

- [x] Create `codex/agent-real-model-audit` from the clean isolated asset worktree.
- [x] Copy only the trained model directory to a TemporaryDirectory; select both bundles there.
- [x] Exercise actual HTTP routes, tool adapter, Supervisor and production JS renderer on public CCO/CCN inputs, not held-out data. Assert original registry hash unchanged.
- [x] Reproduce natural-language `pIC50` incorrectly treated as an invalid SMILES; record baseline regression results.

## 2. Parsing regression

Completed: RED reproduced the metric-token failure; GREEN covers case variants and conservative explicit-input rejection.

Files: `src/agent/tools/activity_input.py`, `tests/agent/test_family_activity_tool.py`.

- [x] Parametrize bare metric labels `pIC50`/`IC50` and case variants: tool receives only `CCO` and the exact requested family. Assert malformed structures and explicit `SMILES: pIC50` remain rejected.
- [x] Run RED with `python -B -m pytest tests/agent/test_family_activity_tool.py -q -p no:cacheprovider`.
- [x] Filter a finite vocabulary of activity metric labels from unlabelled prose tokens only; do not repair, truncate or discard invalid explicit structures.
- [x] Run GREEN and the existing conservative input tests.

## 3. Error envelope and feedback

Files: `src/agent/supervisor.py`, `src/web/chat_handler.py`, `tests/agent/test_agent_audit_regressions.py`.

- [x] Reproduce invalid SMILES through real Supervisor and ActivityPredictorTool, asserting outer error/status retention and meaningful safe Web feedback.
- [x] Assert generic workflow failure never shadows a structured input error; sensitive details still produce the existing generic safe fallback.
- [x] Preserve status/error/warnings from AgentResult's existing serializer. Skip only known generic failure captions in the existing Web failure-selection logic; keep its privacy and length checks.
- [x] Run RED then GREEN; do not introduce a parallel formatting framework.

## 4. Independent audit and cleanup

Completed audit scope: checkpoint tool/model/adapter identity, artifact round-trip and corruption recovery, delegated state-store ownership, required-step stop policy, and abstaining-router call count. Shared the existing checkpoint predicate and step failure policy; removed only unreferenced private persistence helpers. Independent review approved after state-store ownership and acceptance wall-clock timeout regressions were corrected.

- [x] Verify review findings with minimal reproductions before editing.
- [x] Remove redundant work only with behavioral coverage, retaining ReAct/legacy imports still used by public entry points and tests.
- [x] Record large-module hotspots separately; line count alone does not prove dead code.

## 5. Verification and handoff

Completed 2026-09-08: final suite 7074 passed / 240 skipped / 9 warnings / 171 subtests passed; separately opted-in trained-weight acceptance 2 passed; contract 34 passed; four Node scripts, compileall and diff checks passed. See `docs/agent_real_model_audit_20260908.md` and `docs/handoff/agent-real-model-audit.md`.

- [x] Repeat real-weight API -> Supervisor -> JS renderer probes with repaired prompts, failure and mixed-batch paths. No production activation or external model needed.
- [x] Run `python -B -m pytest tests -q -rs -p no:cacheprovider --tb=short`, contract acceptance with a separate output file, focused Node scripts, compileall and diff checks.
- [x] Record actual failures/skips, audit boundaries, changed paths, branch and commits. Stage only named code/tests/docs, never weights/private data/runtime reports. No merge or restart.
