# Literature Evidence Integration Plan

**Goal:** Let the configured MedChat model use a selected, session-bound publication abstract while keeping each displayed claim traceable to the retrieved source.

**Architecture:** Keep retrieval and selection server-owned. Store a bounded source record in the existing per-session scientific history, treat all article text as untrusted data, and use the configured model only through the existing request gate and bounded decision transport. Require exact source spans for generated claims; malformed, unsupported, ambiguous, missing, cancelled, and timed-out results fall back without fabricated conclusions. The server supplies the publication link.

**Tech stack:** FastAPI, existing Pi decision transport, Europe PMC client, pytest, and the MedChat browser UI.

### Work items

- [x] Specify the model response contract and evidence-span validation, including short quoted excerpts and missing abstracts.
- [x] Add tests for context ownership, source identity, exact supporting spans, tool requests from untrusted content, cancellation, and input limits.
- [x] Implement a reusable evidence preparation and validation boundary.
- [x] Integrate selected-article interpretation into the existing model gate and session lifecycle.
- [x] Run focused and affected regressions, compile checks, and a real front-end interaction with the configured model; stop and verify the temporary server.
- [ ] Extend from one selected article to research-question retrieval and multi-source comparison only after the single-source evidence boundary is proven.

### Natural-language research questions

- [ ] Test and implement a bounded model-proposed Europe PMC query with explicit query display and preserved user identifiers.
- [ ] Test and implement multi-source claim-to-abstract attribution; reject missing, altered, or invented source excerpts and numeric claims.
- [ ] Integrate read-only natural-language retrieval with the existing model gate, worker ownership, cancellation, and honest no-evidence fallback.
- [ ] Verify the original T790M question and mixed-action boundaries using focused tests and the real frontend.
