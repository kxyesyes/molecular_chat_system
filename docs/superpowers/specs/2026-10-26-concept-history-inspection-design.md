# Concept questions with prior activity evidence

## Problem and scope

Real Web requests `What was my previous pIC50?` and `What is pIC50? What was my previous pIC50?` incorrectly enter activity prediction and request a structure. A prior-value request is a read-only inspection, not permission to predict. A mixed question must also retain the concept question.

## Boundaries

Recognize complete, explicit prior-activity-value clauses in Chinese and English. Keep the existing closed-world parser: an unrecognized clause, a new candidate, or a request to calculate must not be stripped or converted into ordinary concept chat. Only the immediately preceding, same-session accepted and freshly validated activity evidence can supply values; corrupted, foreign, missing, ambiguous, or intervening records must never be replaced with model prose or guessed values. Preserve all candidates where the user has not selected one.

For a prior-value-only request, reuse the existing deterministic inspection path and its truthful missing/integrity responses. For a complete mixed concept plus prior-value request, use the configured Agent model for the concept portion with no scientific execution, then combine that model output with a separately rendered, validated read-only evidence view. Do not supply fabricated values or treat model text as evidence. Preserve the original user utterance in conversation history and retain eligible source evidence only via the existing session-local mechanism. Model failure must not look like a complete answer; cancellation, missing details, and streaming must preserve existing lifecycle semantics.

## Verification

Add failing tests first for both real observed requests and Chinese equivalents, no evidence, valid same-session rows, corrupt and foreign source, extra compute/unknown clauses, and unchanged history. Cover both direct routing and full Web execution: no scientific calls for pure read-only requests; mixed concept invokes the configured model, while numeric values come only from the accepted source. Run relevant regressions and real UI tests with the configured model and actual project, including failure and multiple-row ambiguity.
