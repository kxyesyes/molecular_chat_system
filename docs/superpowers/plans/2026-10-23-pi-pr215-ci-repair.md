# PR #215 CI Repair Plan

> **For agentic workers:** Use subagent-driven-development for the independent acceptance-chain slice; preserve failure-first verification and separate spec/quality review.

**Goal:** Repair verified failures on PR #215, update the same draft, and retain live-model/deployment limitations without merging.

**Architecture:** Keep the official Pi decision entry and existing scientific contracts. Diagnose CI failures against the actual runtime; migrate stale test observation contracts only when production behavior is proven correct. Do not replace Pi with mocks, drop business safety assertions, or increase production timing limits to hide failures.

**Tech Stack:** Existing Python 3.10/FastAPI/pytest, Node/Pi 1.4.0, GitHub Actions.

## Evidence at baseline 8141b755

Quality run 40049269829: eight successful jobs, agent/root-activity failures, and failed aggregate. Review submissions, inline threads and conversation comments were empty at inspection. Agent collected 15215 tests, exposed six failures in `test_browser_quality_boundaries.py`, then exceeded its 1200-second command deadline at 24%. Root-activity: 1958 passed, 6 skipped, one missing-Node error-classification failure.

## Task 1: Browser quality boundaries (parent)

- [x] Reproduce `python -m pytest tests/agent/test_browser_quality_boundaries.py -q -p no:cacheprovider` with isolated state.
- [x] Compare fixtures with `tests/agent/pi_web_support.py`, `test_browser_evidence_followup.py`, `test_browser_s02_context.py` and the actual Web result/status/history boundaries.
- [x] Retain molecular values, accepted evidence, session isolation, tool call counts and partial-result safety assertions. No production defect was demonstrated in this slice; correct only the stale terminal expectations and cleanup fixture.
- [x] Run the browser evidence/context/quality combination and obtain independent spec then quality review.

## Task 2: Missing-Node classification (independent worker)

- [x] Reproduce `python -m pytest tests/test_activity_family_acceptance_chain.py::test_settings_and_caches_restore_even_when_node_is_missing -q -p no:cacheprovider`.
- [x] Trace `tests/family_acceptance_chain_support.py` dependency detection through the newly required Pi path; preserve cleanup and actual failure evidence.
- [x] Add focused cases proving dependency absence is `dependency_unavailable`, while an available dependency with bad execution is not misclassified.
- [x] Apply the minimal helper correction; keep assertions for environment, Torch settings and prediction caches. Run related acceptance-chain tests, spec review, then quality review.

## Task 3: Agent CI deadline (parent after reproduction)

- [x] Analyze timestamped CI progress to distinguish a stalled test from cumulative real-Pi startup cost.
- [x] Inspect `.github/workflows/quality.yml` and `tests/test_quality_workflow_contract.py`; reproduce proposed partition coverage checks before changing CI.
- [x] Apply approved splitting with exact, disjoint coverage of the original collected node IDs, the existing command time bounds and all assertion paths; no failing cases excluded to obtain green.
- [x] Verify configuration tests and actual repository collection.
- [ ] Check actual CI on the new head after publication; record results in PR #215 without treating the old head as evidence for a new commit.

## Task 4: Review, update and handoff

- [x] Run affected regression, compileall and diff/sensitive-artifact checks. No JavaScript changed; baseline remote static-quality already passed.
- [x] Record exact RED/GREEN and baseline remote results in the existing validation/handoff docs. Preserve cloud DNS rejection, local protocol failures and 21/24 health limitations.
- [ ] Explicitly stage only this task's files, commit/push the existing branch, update PR #215 and inspect new-head CI/review. No merge, deployment, secrets or production DB changes.
