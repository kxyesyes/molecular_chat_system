# Invalid-SMILES Decision Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task.

**Goal:** Prevent malformed scientific completion after invalid molecular input without relaxing validation.

**Architecture:** Keep the existing Pi runtime, strict decision contract, and real property tool. Add the missing guidance to the shared initial decision prompt, with deterministic wire-level regression tests and separate real-provider validation.

**Tech Stack:** Python, pytest, HTTPX test transport, RDKit, official Pi runtime.

## Task 1: Regression first

- [x] Create `tests/agent/test_invalid_smiles_decision.py`.
- [x] Assert initial prompt guidance across default, B1, and Web binding profiles.
- [x] Exercise invalid RDKit input through native and JSON protocol fixtures; assert the clarification rule reaches the first request, one real tool failure, no fabricated data, and zero protocol repairs.
- [x] Assert scientific finish with empty evidence remains invalid and a repaired schema failure still fails strict acceptance.
- [x] Run the new test file; verify missing initial instructions cause failures before production changes.

## Task 2: Minimal fix

- [x] Edit only initial instructions in `src/agent/scientific_session/decision_policy.py`.
- [x] Explain nonempty unique observed usable evidence and clarification after invalid input/tool failure without sufficient evidence. Preserve tool-first execution for executable requests, chat-only chat finish, nonempty finish text, and the existing review-only exception.
- [x] Run the new tests and affected decision, binding, recovery, transport, chat-acceptance and security tests; run `python -m compileall -q src scripts`.

## Task 3: Real validation and delivery

- [x] Run unchanged four-case `run_acceptance` with real DeepSeek and RDKit; retain sanitized reports only under ignored outputs.
- [x] Repeat the suite to sample intermittent failures; report all runs and usage counters.
- [x] Review the diff, verify no validator/acceptance/security changes, record validation and limitations.

Delivery procedure: explicitly stage only task files, commit, and create a draft PR to main. Do not merge or deploy. The final handoff records the resulting commit and PR.

Rollback: revert the prompt-only change. No migration, data, API or schema changes.
