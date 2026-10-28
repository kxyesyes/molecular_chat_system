# Candidate presentation implementation plan

**Goal:** Make candidate results readable without exposing technical IDs by default or hiding decision-relevant risk.

**Architecture:** Keep the validated event/reference contracts untouched. Render user labels separately from structured candidate keys; separate visible scientific summary from an accessible audit disclosure. Enrich only from the same accepted property-row/source binding used today.

**Tech Stack:** Vanilla JavaScript, existing DOM and scientific report modules, Node contract tests, Python/Pi Web integration, real browser.

- [ ] Write failing DOM tests for visible labels, retained audit metadata, real status/risks, multi-collection identity, selection and XSS.
- [ ] Update card/report presentation without changing scientific payload or selection authority.
- [ ] Run all relevant Node suites, syntax checks, Python transport/reference/report regressions, and review.
- [ ] Run real generation and candidate follow-up in the started Web app; verify UI at practical viewport sizes.
