# Activity concept routing

## Evidence and decision

Real frontend on the running local instance reproduced two errors in a fresh chat:
`请解释分类与回归不一致意味着什么。不要重新计算。` demanded prior activity evidence;
`什么是 pIC50？` attempted activity prediction and then demanded SMILES.
The former is intercepted by the read-only projection; the latter is absent from
the complete-concept grammar in Web requirements.

Use a shared, complete-request concept recognizer. Preserve the existing concept
vocabulary and add activity endpoint/classification/regression concepts. The
recognizer accepts only complete explanation clauses and explicit read-only
qualifiers; candidates, prior-result references, unknown suffixes, and appended
execution requests are not concept-only. It grants no scientific obligations.
Ordinary answers still come from the frontend-configured Agent model, not fixed
scientific prose. The generation model and model settings do not change.

A subsequent real-browser user follow-up exposed the same boundary for threshold
applicability: `你刚才说 pIC50 ≥ 6 值得关注，这是否是所有靶点通用的有效性标准？请只解释判断依据，不要预测任何分子。`
Include user-supplied numeric pIC50 threshold applicability questions in both
languages. A premise plus anaphoric question must be paired; a bare premise,
candidate-bound value, or unrelated anaphor is not a complete concept question.
No threshold is hardcoded into the answer; the configured model must explain
that potency criteria depend on target/assay/project context, not a universal rule.

For existing valid same-session evidence, preserve the current contextual
inspection behavior. If there is no usable source, a pure concept question may
fall through to ordinary chat; a numerical/selected/prior-result inspection must
still fail closed. A malformed same-session source remains an explicit integrity
failure when the request invokes contextual activity inspection. An unrelated
standalone concept question does not inspect or import that source and remains
eligible for ordinary chat, even when the prior source is unusable.
Concept-only chat does not manufacture or retain a scientific evidence snapshot.

Alternatives rejected: hardcoded educational answers duplicate model capability;
removing the inspection guard would let missing-result questions trigger science.

## Acceptance

- Fresh Chinese/English concept questions use the configured model with zero
  scientific tool calls and no request for SMILES.
- Complete concept recognition is shared by projection and Web admission.
- Existing result inspection, source integrity, session isolation and missing
  numerical evidence refusal continue to pass.
- Mixed concept plus execution is never silently accepted as ordinary chat.
- Verify with RED-first tests, real browser, relevant regression and compileall.
- No API/schema, scientific model, data, dependencies or settings changes.

User experience: explain terminology without requiring a calculation. Developer
experience: one shared intent boundary. Agent experience: correct empty-science
requirements permit normal explanation without scientific authority.
