# Concept and prior activity inspection implementation plan

**Goal:** Answer concept-plus-history questions without inventing data or running a new prediction.

**Architecture:** Extend the existing complete-clause inspection grammar and session-local evidence boundary. A mixed request separates only fully recognized concept clauses from fully recognized historical inspection clauses, uses the existing Pi ordinary-chat model for concepts, and separately attaches the validated source-bound inspection view. Preserve original history and fail closed on additional or uncertain operations.

**Tech Stack:** FastAPI, Python Pi Web runtime, existing evidence contracts, pytest and real browser verification.

## Tasks

- [x] Add RED-first tests for isolated historical questions, mixed Chinese/English requests, session isolation, invalid source, multiple candidates, and extra requests.
- [x] Implement full-clause classification and read-only evidence projection without enlarging model authority or altering raw scientific values.
- [x] Integrate mixed concept execution through the existing Web lifecycle; preserve original conversation input, terminal ordering, cancellation and eligible evidence.
- [x] Run focused tests, related evidence/transport/runtime regressions and compileall; verify actual frontend requests against the configured Agent model.
- [x] Record results and remaining limitations without claiming untested behavior.

## Progress and limitations

The first browser reproduction invoked activity prediction for both the isolated
and mixed prior-value questions. An earlier revised real browser run reused the
actual CCO/CCN BuChE observations with no second scientific execution. The
following refinement hides model identifiers from the summary and keeps them in
the full source view. A separate 459-test reverse-target regression passed.

Independent review identified unsafe history claims, old-format raw-answer
leakage, long-summary truncation, and retry loss after a RAG conflict. Tests now
cover those examples; concept input has no model conversation history, output
is screened for historical claims, and validated values are rendered first.
Natural-language screening cannot prove that arbitrary model prose is correct;
the structured session record remains the sole authority for historical values.
The RAG toggle grants capability; it is not itself a retrieval obligation. A
pure concept-plus-prior-value request therefore follows the same zero-step
ordinary-chat plan with the toggle either on or off; explicit additional
retrieval clauses are not removed. If an unexpected tool observation reaches
that path, the history view is preserved and the concept portion is marked
incomplete. On model failure, the historical view is displayed while the
failure status remains intact; cancellation publishes no historical view.
The updated 11-file regression passed 896 tests with seven existing deprecation
warnings after the RAG, failure-path and Web chat guidance refinements. A real Web session first discussed
pIC50, then ran actual CCO/CCN BuChE prediction, then asked the mixed question.
The source-bound original values and inconsistency were displayed without a new
prediction, followed by a separate model concept explanation. A fresh session
also showed the missing-evidence notice and concept explanation. The test
instance used the frontend-configured `deepseek-v4-pro`; no molecular-generation
model settings were changed. A later real RAG-enabled session completed both
missing-history and accepted-history cases with no retrieval performed. One
model explanation included an unsupported generic potency threshold, so the
Web chat system guidance was strengthened; this is a probabilistic model
instruction, not proof of scientific correctness. The final real-model retest
did not repeat that potency threshold, but another reply made an unsupported
general claim about data distribution despite the guidance. This remains open.
An explicit additional literature-search request was safely rejected with a
request to split tasks; simultaneous mixed retrieval is not yet supported.
No code was staged or committed.
