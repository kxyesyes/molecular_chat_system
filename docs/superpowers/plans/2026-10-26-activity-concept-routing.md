# Activity Concept Routing Implementation Plan

**Goal:** Restore real model-based explanations without triggering predictions.

**Architecture:** Shared complete concept recognition; same-session validated
activity inspection takes precedence, otherwise pure concepts reach ordinary chat.

**Tech Stack:** Existing Python/FastAPI/Pi/plain browser stack.

## Execution

- [ ] Add `tests/agent/test_activity_concept_routing.py`: test fresh Chinese and
  English concepts through `followup_answer`, `compile_web_requirements` and the
  full Web runtime. Assert no scientific steps/calls, actual model invocation,
  original query preservation and no generated activity snapshot. Include mixed
  requests and missing prior-result negatives. Run pytest and record RED.
- [ ] Add `src/agent/contracts/concept_request.py` with
  `is_concept_explanation(query)`: complete clause matching only; existing topics
  plus activity endpoint/conflict concepts and read-only qualifiers.
- [ ] Replace the private implementation in
  `src/agent/scientific_session/web_requirements.py` with a shared import alias.
  In `src/agent/evidence_followup.py`, allow pure concepts to return `None` only
  outside valid contextual inspection, while preserving numerical refusals.
- [ ] Run the new suite plus `test_activity_evidence_inspection.py`,
  `test_browser_evidence_followup.py`, `test_result_followup_delivery.py`,
  `test_web_profile_requirements.py`; run compileall and diff check.
- [ ] Independent review of only this incremental delta; fix verified findings
  using RED-first regressions. Preserve unrelated working-tree edits.
- [ ] Confirm live owned server, end pending test turn through UI, restart only
  that instance after changes. Real fresh concept queries, existing-result
  inspection and missing-result requests; save final screenshot.
- [ ] Record exact validation counts/commands and branch state. Do not stage the
  mixed tree, commit unrelated files, create a PR or claim overall goal complete.

## Validation

- Initial new suite: 11 failed and 7 passed, reproducing the two routing bugs.
- Added NaN/infinity corrupt-source cases: 2 failed before correction. A later
  independent quality review found a multi-clause variant; 3 new assertions
  failed before its correction. Current focused concept suite: **40 passed**.
- After threshold-applicability grammar was added, concept + Web requirements
  passed **82 cases** before the last three quality-review cases. A broader
  nine-file run passed **355 cases**, seven existing dependency warnings, before
  the final threshold and quality corrections. Final-code nine-file rerun:
  **373 passed**, seven existing dependency warnings, 268.84 seconds.
- Real browser with frontend-configured `deepseek-v4-pro`: a fresh concept
  question and the threshold-applicability question produced explanatory chat
  without scientific task cards. Another fresh pIC50 explanation incorrectly
  conflated stronger inhibition with drug effectiveness; this remains an open
  scientific-quality issue, not a verified clinical conclusion.
- A real mixed question `What is pIC50? What was my previous pIC50?` started
  activity prediction and then requested SMILES despite lacking previous data.
  This separate inspection-grammar gap is reproducible and remains unresolved.
- Local reverse-target dataset was found in a separate worktree. A 24/24 strict
  health check passed with a temporary environment override. The live instance
  is performing its complete 831,120-row source verification; this is not yet
  an accepted scientific result. During prewarm a real UI request correctly
  reported no prediction, but the summary also showed a contradictory generic
  result line and internal warning token; this presentation issue is unresolved.
