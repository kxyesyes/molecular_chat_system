# Activity Evidence Inspection Implementation Plan

> **For agentic workers:** Use subagent-driven-development for independent specification review followed by quality review. Parent owns tightly coupled parser/rendering work; a test worker may own the new regression file.

**Goal:** Answer existing-result exclusion/value questions without re-executing scientific tools.

**Architecture:** Add an activity-inspection intent/view module, integrate it through existing `evidence_followup.followup_answer`, and retain only revalidated detached evidence in `WebTurnProjection` for consecutive read-only views.

**Tech Stack:** Python, pytest, existing Pi/Web runtime and real browser.

## Tasks

- [ ] Create `tests/agent/test_activity_evidence_inspection.py`. Reuse explicit synthetic activity fixtures and full-Web helpers from `test_browser_evidence_followup.py`. Test exact real prompt, English/Chinese variants, candidate selection, unchanged raw values, no added calls, missing/cross-session/intervening/prose-only evidence, unsupported clauses with no-recalculation, and chained read-only questions. Core assertion:

```python
history = []
turn(handler, source_query, history)
before = deepcopy(handler.web_results[-1].to_legacy_dict())
complete, frames = turn(handler, inspection_query, history)
assert "0.42" in complete["content"] and "5.01" in complete["content"]
assert len(activity.calls) == 1
assert handler.web_results[0].to_legacy_dict() == before
```

- [ ] Run `python -m pytest tests/agent/test_activity_evidence_inspection.py -q --tb=short` in the existing scientific environment. Record assertion RED before production edits.
- [ ] Create `src/agent/activity_evidence_inspection.py`: complete-clause Chinese/English inspection grammar and pure renderer; no model/network/storage/tool execution. Return `None` outside inspection, `not_calculated` for unsupported read-only requests, and an accepted evidence report otherwise. Use existing canonicalization, boundedness and acceptance functions via the evidence-followup integration; no duplicated source-of-truth validators.
- [ ] Update `src/agent/evidence_followup.py` to dispatch inspection before ordinary science and return a detached source snapshot only for accepted read-only views. Update `src/web/web_business.py` to preserve that snapshot in the next socket-local entry:

```python
if view.get('activity_evidence') is not None:
    self.entry['activity_evidence'] = deepcopy(view['activity_evidence'])
```

- [ ] Run focused GREEN, existing evidence-followup/comparison/results transport/runtime regression, targeted `compileall`, relevant home Node suites and `git diff --check`. Test unknown inputs do not silently become executable or successful.
- [ ] Independent spec review, then quality review; fix findings with failing regressions before acceptance.
- [ ] Confirm live server process and idle/waiting state. End pending UI request, stop/restart only owned server. Real frontend: comparison, exact follow-up, second read-only question, missing-evidence new page, unsupported extra metric. Inspect actual returned numbers/source status and absence of new scientific execution cards.
- [ ] Record accurate commands/counts, screenshots, limitations and unchanged branch/commit state. No staged changes or PR.

## Validation record

Implementation, specification review, quality review and final-code real-browser acceptance are complete. This validation record supersedes the unchecked execution-plan markers above. Counts below are overlapping runs, not additive.

- Initial test-worker RED: **21 assertion failures**, zero setup errors, 67.23 seconds. Parent independently reproduced the missing inspection branch. Temporary-directory sandbox setup failures were rerun with normal temporary-directory access, not treated as code failures.
- Existing follow-up/delivery baseline: **34 passed**. Test worker changed only the new regression file; parent then took ownership.
- Additional RED-first cases cover generic-domain isolation, common/extended no-recalculation wording, malformed numeric/provenance rows, all-candidate selectors, failed-row containers, and malformed ranking carry. Independent spec review identified gaps; each was reproduced and fixed before the final specification PASS.
- Final specification review PASS: **31 direct cases passed**, plus independent valid/invalid ranking-chain probes and unrelated-turn invalidation. Reviewer closed.
- Quality review found that valid consistency-only and exclusion-only clauses could miss a secondary keyword gate. Added two direct failing regressions and four full-Web legacy/summary cases. Supported inspection is now derived from parsed clause type and explicit nongeneric field semantics; generic result requests remain outside the domain. Final quality review PASS: **33 direct cases passed** plus 14 independent semantic/isolation/immutability probes. Both reviewers are closed.
- Final focused full-Web/inspection/ranking/delivery command: `python -m pytest tests/agent/test_activity_evidence_inspection.py tests/agent/test_browser_evidence_followup.py tests/agent/test_result_followup_delivery.py -q --tb=short`: **83 passed**, 81.84 seconds. This includes all final changes. The preceding 77-test run passed before the six additional quality-review regressions; counts overlap.
- Expanded 29-file runs: initially **1,954 passed**, seven existing SWIG/FastAPI warnings, 408.18 seconds; subsequently **1,969 passed**, seven existing warnings, 423.02 seconds. The latter preceded the final attached-prohibition and typed-clause corrections; the final 83-case run above verifies those changes. Intermediate eight-file full-Web run: **428 passed**, seven existing warnings, 345.28 seconds. These are related regression evidence, not a claim that the entire repository or all real services passed on final code.
- The 29-file command is the 25-file command documented in [comparison display clauses](2026-10-26-comparison-display-clauses.md), plus `tests/agent/test_activity_evidence_inspection.py`, `tests/agent/test_web_decision_runtime.py`, `tests/agent/test_pi_web_profile_runtime.py`, and `tests/agent/test_pi_full_web_entry.py`, with `-q --tb=short`.
- Relevant Node suites passed: `node tests/home_comparison_table_test.js`, `node tests/home_result_details_test.js` (38 cases), `node tests/home_decision_runtime_test.js` (91 cases). No JavaScript source changed in this continuation.
- Real intermediate-code browser check: exact original follow-up now displays CCN probability `0.4225565493106842`, pIC50 `5.007589340209961`, original class, false consistency and exclusion explanation. New independent chat reports missing evidence with zero task panels and no values. Final-code repeat is still required below.
- Separate header investigation: `状态未知` reflects no independent readiness probe, not an offline Agent. Using the existing real connection-test button with the saved `deepseek-v4-pro` configuration succeeded and changed the label to `已就绪`; no header or model-settings change was needed.
- Final targeted `python -m compileall -q src/agent/activity_evidence_inspection.py src/agent/evidence_followup.py src/web/web_business.py tests/agent/test_activity_evidence_inspection.py` and `git diff --check` passed. Existing unrelated controller CRLF warning only. Full repository/real-service suite and strict deployment health check were not run; no deployment/data changes were made.
- Final incremental production scope is the new activity-inspection module and the two follow-up integration points above; tests are isolated to the new regression file. No science values, algorithms, API/schema, model settings or data assets changed. Branch `codex/feat/pi-baseline-migrate-worktree`, HEAD `95c29f8b`; staging empty, no commit/PR/merge. Reverse-target data remains absent and is not claimed fixed.

### Final real frontend acceptance

- Confirmed the owned process/session live and all test requests terminal, then restarted only that local test instance with the existing environment. Fresh final-code browser used the unchanged frontend-configured `deepseek-v4-pro`; generator configuration untouched.
- Real CCO/CCN property + BuChE comparison produced the same actual partial activity results. The exact original Chinese follow-up immediately returned CCN probability `0.4225565493106842`, pIC50 `5.007589340209961`, original inactive class, false consistency, exclusion reason and contract-based explanation. Model predictions remain explicitly non-experimental.
- Consecutive English exact follow-up also reused those values. A subsequent explicit all-candidate query displayed both CCO (`0.34439727663993835`, `4.353786468505859`) and CCN, without silently narrowing to CCN. Pure `Show exclusion reasons. Do not recalculate.` and Chinese consistency-only questions then returned the same source-backed report.
- Unknown `CCC` selector returned no values and explicitly said it was not replaced with another candidate. `Show its original activity probability and clearance. Do not recalculate.` returned not-calculated without claiming a clearance value or starting science.
- Separate fresh chat with no accepted source returned missing-evidence/not-calculated for the exact original numerical question, with zero task-execution cards and no numerical claims. No data was injected into browser responses.
- DOM proof after the full sequence: exactly **one** task-execution card (the initial comparison), serialized original card unchanged, zero streaming messages. This complements full-Web tests that assert zero extra model/scientific calls and immutable stored/source observations.
- Screenshot: `outputs/ui-verification/20261026-activity-evidence-inspection-after.png`, ignored runtime artifact. Temporary capture viewport was restored. Intermediate test tabs were closed; final result chat remains open on the running local test instance.
- Scope remains deliberately bounded to validated same-session activity evidence and the documented inspection grammar. This is not arbitrary historic-result retrieval or a claim of general conversational completeness. A new session or intervening unrelated/refused request cannot supply old evidence automatically.
