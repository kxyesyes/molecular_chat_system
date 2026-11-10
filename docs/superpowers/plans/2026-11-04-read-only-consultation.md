# Model-evidence consultation implementation plan

**Goal:** Allow the reproduced AUC interpretation and calibration-documentation questions to reach the configured model without authorizing new scientific work.

**Architecture:** Extend the existing complete-clause concept contract with bounded model-evidence roles: an unverified metric premise, a source-missing statement, inference questions, and documentation-method questions. Require an explicit read-only clause and a substantive question. Every clause must match completely. Preserve the existing zero-step ordinary-chat route and session-bound candidate projection. Do not add model calls, tool permissions, API fields, dependencies or database changes.

**Scope:** These are specified question forms, not general natural-language intent classification. Unknown expressions return to the existing admission process. A complete single-sentence literal-repetition request is a text transformation; extra sentences are not ignored.

## Implementation and acceptance

- Add failing tests for observed questions and Chinese/English variations.
- Add negative tests for appended and embedded calculations, retrieval, docking, new values, external operations, modal requests and verb-free result questions.
- Verify the actual Web socket/provider path retains original candidate values and does not promote the user-supplied AUC into accepted evidence.
- Extend only the shared concept module, using complete bounded clause roles.
- Obtain independent review; run the focused concept, obligation, candidate-context and presentation tests sequentially.
- Run targeted compileall and diff checks.
- Start one server via the mandated safe launcher on port 6001. Record its session and PID. In the real front end, use the configured model to compare two molecules, then submit the AUC and calibration-documentation questions, literal repetition and a mixed-request negative case.
- Save a screenshot, close the owned temporary server, and verify process/listener exit.
- Record remaining language coverage and actual model wording separately from deterministic test results.
