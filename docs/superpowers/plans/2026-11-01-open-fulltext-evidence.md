# Open Full-Text Evidence Implementation Plan

**Goal:** Answer a targeted PMID full-text question from publicly available PMC passages while preserving source identity, experimental context, and uncertainty.

**Architecture:** Resolve the PMID with Europe PMC, require its validated PMCID and open-access metadata, then retrieve only that PMCID from the allowlisted NCBI BioC JSON service using the existing bounded, credential-free transport. Select a small, question-relevant set of Results and Methods passages using explicit identifiers, retain BioC offsets, and send them to the configured model as untrusted data. Bind any short quotation and numeric claim to one passage, classify it in a separate model call, and display source links and limitations; never treat approximate quotes or model classification as proof of entailment.

**Tech stack:** FastAPI, existing Pi model adapter and lifecycle, Europe PMC, NCBI PMC BioC, bounded JSON transport, pytest, native Web UI.

### Work items

- [x] Test and implement strict NCBI endpoint allowlisting and Europe PMC PMCID/open-access metadata validation.
- [x] Test and implement bounded BioC document identity, section/offset extraction, and question-relevant passage selection.
- [x] Test and implement evidence-bound model extraction, numerical provenance, classification, and unavailable-source behavior; display only original source excerpts after model misattribution was observed.
- [x] Connect explicit PMID full-text requests to the existing worker ownership, model gate, cancellation and session contracts.
- [x] Run focused and affected regressions, compile checks, and real frontend tests using the configured model; stop the temporary server.
