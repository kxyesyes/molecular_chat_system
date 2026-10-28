# Read-only activity evidence inspection

## Observed problem

On the real frontend, immediately after comparing CCO and CCN properties and BuChE activity, the request `为什么上一轮比较中的 CCN 被排除？请列出它的原始活性概率和 pIC50，并解释分类与回归不一致意味着什么。不要重新计算，也不要把预测当作实验结论。` attempted activity prediction and then asked for a target. The preceding validated comparison already contained both original values and the consistency flag. A separate independent model connection probe succeeded; this is not model connectivity failure.

## Decision and boundaries

The user authorizes autonomous implementation/default approval. Use a deterministic read-only evidence view. Alternatives were sending assistant prose back to the model (not evidence) or allowing another scientific call (contradicts no-recalculation intent). Neither addresses the observed request correctly.

Admit Chinese and English inspection clauses for existing activity values, exclusions and classification/regression disagreement. Parse complete clauses, with an explicit optional SMILES selector, pronoun/all-candidate references, and optional no-recalculation/non-experimental disclaimers. Do not silently drop unknown requested metrics or reinterpret a new target as an existing target. Recognized inspection intent with an explicit no-recalculation instruction but unsupported clauses returns an explanatory not-calculated response and never reaches science execution. This is not a general natural-language resolver or a global ban on other requests.

Revalidate only the immediately preceding same-socket accepted structured activity snapshot using the existing evidence acceptance routine. Exact/canonical SMILES selection must match that snapshot; missing structures, missing/invalid observations, cross-session data, assistant prose or intervening unrelated turns cannot supply evidence. Missing evidence is reported explicitly, without default targets or inferred values. No database search or arbitrary historical retrieval is added.

Render original activity probability, pIC50, activity class, consistency flag and exclusion reason, with source trace/evidence identity. Include rejected-from-comparison rows; exclusions do not erase their raw predictions. Explain that independently predicted classification and regression can disagree at the existing model-contract thresholds; this is a review flag, not evidence of experimental activity or inactivity. Do not change thresholds, values, source observations or rankings, and do not rank unless the existing ranking operation was requested.

Successful read-only follow-ups carry a detached, revalidated copy of the same activity snapshot into their socket-local entry, permitting another immediate inspection without minting evidence or rerunning tools. Other requests retain the existing invalidation behavior. Unsupported/missing-evidence views do not preserve stale authority. Existing ranking follow-ups keep their output/acceptance semantics and may carry the same validated snapshot forward.

Inspection admission and execution restriction are separate: original clauses must fully match before a view is admitted. For the restrictive no-recalculation guard only, the affirmative prefix before a prohibition determines the domain; activity mentioned only in a negative qualifier cannot capture a property or generic-results request. Unsupported attached phrasing remains explicitly not-calculated rather than falling back to science. Explicit all-candidate requests preserve that scope without discarding any explicit selector that still needs validation.

Before rendering or carrying a snapshot, recheck row/stage/model integrity through the existing `ActivityRawOutput` domain schema. Validate its row-bearing fields without coercing/rewriting observations; the reduced enclosing provenance retains the existing accepted-evidence binding checks. Failed rows may have no model provenance, but supplied malformed provenance/model containers reject the view. Concise mode omits raw trace/evidence footer IDs while the legacy full report retains them and the detached state preserves source identity.

## Acceptance

- Exact real request and Chinese/English inspection variants return original CCN values and exclusion explanation with zero additional scientific calls.
- No-recalculation requests with unsupported metrics/targets do not trigger execution or claim those obligations were satisfied.
- Missing, cross-session, intervening, malformed or prose-only evidence is rejected without invented science.
- Unknown candidate selectors cannot fall back to another candidate or all candidates.
- Successive read-only questions retain source identity and immutable observations; a later ordinary/new-science request does not implicitly retain inspection authority.
- Full Web regression covers actual runtime frames and summary delivery; source fixtures remain explicitly synthetic. Final acceptance is performed through a fresh real frontend after restarting only the idle owned test server.
- Frontend-configured Agent and dedicated generator models, APIs, scientific algorithms and data are unchanged. No staging/commit/PR/merge in this bounded continuation.
