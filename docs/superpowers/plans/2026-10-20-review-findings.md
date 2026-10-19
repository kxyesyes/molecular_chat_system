# Independent review fixes

**Goal:** Resolve the eight confirmed review findings with regression evidence, preserve unrelated local changes, and create one draft PR to main without merging.

**Architecture:** Keep FastAPI/Jinja/native JavaScript and existing scientific tools. Parse objective directions locally and fail closed on ambiguous requests; validate activity evidence against its actual provenance; propagate cancellation through the existing workflow session; retain resource ownership until physical work exits; reject stale UI responses before mutation.

**Tech stack:** Python 3.10, FastAPI, RDKit, pytest, native JavaScript and Node contract tests.

## Task 1 — Scientific goal integrity (findings 1, 2, 8)

- [x] Add failing tests for negation/cross-metric directions, nested activity provenance and threshold rounding.
- [x] Associate each metric with its own direction; ambiguous/unsupported constraints remain unverified.
- [x] Accept current family model provenance only for the matching successful, non-demo, non-fallback endpoint; retain legacy compatibility.
- [x] Evaluate numeric goals with raw values while preserving display rounding.
- [x] Run focused tests, then independent specification and quality reviews.

## Task 2 — Cancellation and physical resource lifetime (findings 3, 4)

- [x] Add controlled route-level regressions: cancellation stops downstream calls; timeout retains ownership while the underlying tool is alive.
- [x] Connect task cancellation to workflow sessions and track physical worker lifetime through reset/shutdown gates.
- [x] Preserve terminal state/event contracts and existing cancellation semantics.
- [x] Run task/runtime/model lifecycle regressions and independent specification review.
- [x] Complete independent quality and integration review, including the routing-worker lease issue found during review.

## Task 3 — Web boundaries (findings 5, 6, 7)

- [x] Reproduce old task polling mutating a newer task, blocking structure preparation, and raw SMILES logging.
- [x] Check poll identity before applying responses; offload synchronous preparation; remove private molecule content from route logs.
- [x] Run Node syntax/contracts and Python route regressions, then independent specification and quality reviews.

## Delivery

- [x] Run affected regressions, attempt the full offline Python suite, compileall and supported Node contracts; explicitly report the incomplete full-suite run.
- [x] Inspect diff and submission boundaries, update handoff, complete independent overall review.
- [ ] Explicitly stage task files, commit on the isolated branch, push and create a draft PR targeting main. Do not merge.

Validation commands follow `docs/PROJECT_STANDARDS.md` section 11. Controlled test doubles demonstrate contracts only; they are not real scientific predictions or external service acceptance.
