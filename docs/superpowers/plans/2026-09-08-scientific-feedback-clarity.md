# Scientific feedback clarity implementation plan

> Execute with subagent-driven-development and test-driven-development. User approved the bounded design on 2026-09-08; no production entry migration.

**Goal:** Explain invalid molecular inputs without exposing model-authored scientific claims, and constrain property reports to actual descriptors and screening rules.

**Architecture:** Reuse InputValidator for evidence-backed clarification text only after the model chooses clarify. Preserve continuation/state/event contracts, generic fallback for unsupported contexts, and chat behavior. PropertyCalculator retains its calculation/data schema; one shared rule assessment feeds its prose instead of contradictory partial implementations.

**Tech Stack:** Existing Python, RDKit, pytest, ModelDecisionLoop and Node lab regression. No dependencies or external configuration changes.

## Approved scope and alternatives

Selected: deterministic explanations backed by local validation, never unrestricted passthrough of model questions. Model-text passthrough would improve expressiveness but weaken the scientific anti-hallucination boundary. A general explanation framework or new planner is unnecessary for these two defects.

Only molecular-input tools use molecular clarification. A current invalid complete SMILES produces an explicit invalid-input message; absent input requests complete SMILES; unavailable validation must not be called invalid; valid input and unrelated contexts retain a neutral clarification. Do not echo raw input or add properties/alternative molecules. Resume must validate the corrected current input, not keep the old invalid diagnosis.

## Task 1 — clarification

- [x] Add `tests/agent/test_decision_clarification.py`: invalid input, missing input, valid input, unavailable validator, nonmolecular context, malicious model question, and invalid→valid same-owner continuation.
- [x] Observe RED with `python -B -m pytest tests/agent/test_decision_clarification.py -q -p no:cacheprovider`.
- [x] Add focused `src/agent/harness/decision_clarification.py`; wire its return into the scientific ClarifyDecision branch of `decision_loop.py`. Reuse InputValidator and MOLECULAR_INPUT_TOOLS; preserve generic fallback and avoid model question/raw input in the explanation.
- [x] Verify tests and existing continuation/chat/lab regressions.

Spec review added parser-level failure and Markdown-format ambiguity regressions: two InputValidator exception branches now retain the existing unavailable candidate status (still blocks execution), and formatting ambiguity receives a neutral format request rather than a false molecular diagnosis. Quality review added a 256 UTF-8 byte conservative parsing limit for clarification only; long inputs retain the neutral prompt to avoid blocking the async control path. Task 1 final: 21 passed.

Core acceptance assertion:

```python
assert 'SMILES 无效' in result.final_answer
assert not result.success and result.metadata['waiting_for_input']
assert not result.tool_results
assert 'pIC50=9' not in result.final_answer
```

## Task 2 — property prose (isolated worker)

- [x] Add `tests/agent/test_property_report_boundaries.py` using real RDKit CCO/CCN/aspirin and boundary props: numbers unchanged, no unsupported oral/efficacy/permeability/selectivity claims, all branches labelled descriptor/rule assessment.
- [x] Run RED, then change only `src/agent/tools/property_calculator.py` presentation/assessment helpers. Keep public methods and data keys. Distinguish Lipinski four criteria from TPSA/rotatable-bond checks, use the same assessment across reasoning and formatted output, and explicitly label incomplete rule inputs rather than claiming complete compliance.
- [x] Run GREEN and existing property tests. No model weights, thresholds for activity or production routes change. Task 2 final: 59 passed; each formatted report contains only one summary/disclaimer.

## Task 3 — review, regression, handoff

- [x] Independent review of both changes, fix reproduced issues with RED→GREEN tests; spec and quality reviews closed without unresolved findings.
- [x] Run `python -B -m pytest tests/agent tests/test_agent_decision_model.py tests/test_openai_compatible_model.py tests/test_admin_auth_routes.py -q -rs -p no:cacheprovider --tb=short` (final rerun: 2118 passed, 1 optional performance skip).
- [x] Run anti-hallucination/platform/real-check helper tests (53 passed), contract acceptance (34/34), `python -m compileall -q src scripts`, and `node tests/decision_lab_ui_test.js` (27 passed).
- [x] Record genuine results and limitations in `docs/handoff/scientific-feedback-clarity.md`; no real external-provider claims unless actually rerun. This task requires local real-RDKit validation, not a new paid full benchmark.
- [x] Exact-stage only task files and check staged diff and sensitive-content patterns; task-branch commit is the final repository closeout. No push, cumulative PR, main merge or production restart.
