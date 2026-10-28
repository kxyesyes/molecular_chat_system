# Comparison display clauses

## Evidence

Real frontend rejected `比较 CCO 和 CCN 的性质、ADMET 和 BuChE 活性，保留原始预测结果与一致性检查。` before scientific execution. All requested endpoints exist; the shared comparison admission view only recognizes a few literal presentation sentences.

## Decision

User authorizes autonomous solutions/default approval during ongoing frontend testing. Keep this bounded to comparison admission. Three options considered: another one-off sentence, broad removal of presentation words, or a closed full-clause display grammar. Use the third: it covers common combinations without silently discarding unknown scientific obligations.

Recognize Chinese display verbs (保留、展示、显示、列出、提供), optional 请, and an explicit list of existing report objects: original results/predictions/data, consistency checks/results, exclusion reasons, prediction disagreements. Lists may join with 与、和、及、以及、顿号. English equivalents use preserve/keep/show/display/include and raw/original results/predictions/data, consistency checks/results, exclusion reasons, prediction disagreements joined by and. Recognize only complete punctuation-delimited clauses; never remove matching substrings inside unknown requests. Existing complete comparison/ranking/disagreement clauses stay valid.

These clauses request existing presentation material, not new calculations, validation methods, filtering or ranking. Do not admit unknown metrics embedded in their object lists or surrounding words. Do not repair SMILES. Preserve original query and tool inputs, requested endpoints/exclusions, immutable observations/provenance, consistency exclusions and follow-up reuse. No model, API, schema or scientific algorithm changes.

Strip accepted display clauses before parsing molecular operand sentences. Preserve their preceding separator and discard their own terminator so an English operand ending in one period does not acquire a second period. ASCII periods delimit prose only before whitespace or end-of-input; internal disconnected-SMILES dots and ambiguous unspaced text remain intact. Invalid explicit fields still reject; no display phrase may repair an invalid structure.

## Acceptance

- Exact real prompt and common display-list variants compile to precisely properties, ADMET and activity, with original query unchanged.
- Explicit endpoint exclusion and unrequested ranking remain respected.
- Unknown metrics embedded before/inside/after display clauses reject the whole request with zero tool execution.
- Full offline Web path retains raw predictions, inconsistency flags, exclusion reasons and failed observations without mutation.
- Restart only the owned test server after all requests end; real browser exact prompt executes, details retain actual outcomes, and an unsupported-metric variation rejects.

Unrecognized prose still fails closed; this patch is not a general natural-language capability parser. No claim that presentation checks experimentally validate predictions.

## Real-browser binding follow-up

English browser acceptance exposed a second boundary: admission compiled the correct two molecules, but activity parsing only recognized a molecule's sentence period at the end of the entire input. A following display sentence left `CCN.` in the target/molecule context; the existing binding journal correctly stopped execution (`invalid_dynamic_binding`, zero tool attempts).

Share the exact closed presentation grammar in a dependency-free `src/agent/contracts/comparison_presentation.py`. Comparison admission and comparison-only activity context parsing use that same local text view. Preserve the original returned query and all structured molecule fields. Keep the existing period validator and binding journal unchanged; do not bypass validation or generally trim periods. Cover the actual complete Web execution path as well as direct string/structured activity parsing, evidence binding and invalid-field negatives.
