# Analytical item versus target identity boundary

## Goal and authority

The active user objective delegates recommended bounded choices through package8.
This isolated correction fixes a reproduced P7 positive case without rewriting
that case, weakening scientific checks or activating live services. Main baseline
is PR92 squash82322c3. No original mixed checkout files are changed.

## Evidence and root cause

Normal-Web Task6 on `dynamic-bindings-b1` sends the unchanged request:

```text
评估分子量和类药性和 ADMET 和 PDE5A 活性；SMILES: CCO; CCN
```

Admission recognizes four obligations for the complete two-molecule batch.
Actual isolated Web runs30266/924114 and54273/35c5a3 fail before model or tool
execution in `B1BindingResolver._journal_inputs`. Its target projection calls
`analyze_target_request`: uppercase ADMET passes `_identifier_like`, the following
PDE5A supplies a known-target anchor, and the inferred list becomes a known PDE5A
plus unknown ADMET. The journal correctly rejects the apparent conflict; the
upstream positional inference is wrong. No dependency failure explains this case.

The existing Web analysis grammar already separates analytical items from target
activity items. Importing Web code into the shared target contract would invert
dependencies. Replacing the shared parser with activity-family resolution would
also lose distinct PDE subtype identities. Neither is acceptable.

## Selected design

Add a small positional recognizer to `src/agent/contracts/target_request.py`.
It identifies wholly consumed assessment-clause intervals with analytical roles.
It does not alter original query bytes, target mentions, explicit-label scans,
qualification scans, molecular parsing, obligations, journal or prefix seals.

Recognized clauses must:

1. Start at the query or a semicolon/newline boundary, allow whitespace and one
   optional `请` or `please`, and require an assessment action: 计算/预测/评估/分析
   or calculate/compute/predict/assess/evaluate/analyse/analyze.
2. Contain a sequence of complete analytical items joined by 和/及/与/并/、/comma
   or English `and`; whitespace alone does not join items. Items are ADME/ADMET,
   ordinary property names (分子量, 分子性质, 分子属性, 理化性质, 性质, 属性,
   LogP, TPSA, HBD, HBA, QED, molecular weight, property/properties), drug-likeness
   (类药性, 成药性, Lipinski, drug-likeness/drug likeness), or a complete known
   target identifier followed by optional 的 and 活性/activity/potency/pIC50/IC50.
3. Contain at least one ADME/ADMET item AND at least one complete target-activity
   item. Case-insensitive English spelling and horizontal spaces are accepted;
   identifiers retain the existing exact target vocabulary and boundaries.
4. End at the same clause boundary or query end, with optional one final
   sentence punctuation mark and whitespace. Unknown tails, partial words,
   empty items, repeated/trailing joiners, target labels and SMILES fields make
   the recognizer decline the entire clause. It never searches forward past
   unknown text to manufacture a recognized clause.

Use a deterministic forward scanner: action, item, join, item, end. Publish a
clause interval only after full success. Exclude only inferred unlabelled-list
starts (`list_matches`) inside that interval, never the combined `starts` list.
This preserves analytical QED/LOGP items before or after ADMET as well as ADMET
itself; excluding ADMET starts alone leaves false unknowns from sibling items.
Never remove a word globally or filter `unknown` by value. Explicit target/for/
against labels and their coordinated values keep precedence. `_LABEL`, `_FOR`,
`TARGET_PATTERN` and qualification detection traverse the original text unchanged.
Compute intervals once and consume them with an ordered cursor; do not check
every token against every interval. An actual label alone may still supply the
`explicit` flag; suppressing this redundant heuristic need not manufacture it.

Negation/switching/selectivity remain visible and existing entry points reject
them. Multiple complete activity items must retain all target identities, so
PDE5A plus PDE4A is never collapsed to PDE family identity. Unknown target-like
tokens, including ADMET2 and UNKNOWN42, never receive this exemption.

The recognizer is linear in query length with bounded per-token alternatives;
do not introduce nested unbounded regex repetition or rescanning every suffix.
Keep the existing candidate-selection grammar unchanged.

## Alternatives not selected

- Global ADMET action-word whitelist is smaller but hides explicit unknown
  target requests and changes unrelated contexts.
- B-only rewritten query or filtered unknown list would mask the source defect,
  duplicate role handling and risk hiding another explicit ADMET occurrence.
- Broad language/parser replacement is unnecessary and would expand this task.

## Scope and honest boundaries

Production write scope is only `target_request.py`; new focused tests belong in
`tests/agent/test_target_analysis_phrase.py`. Existing target identity, selection,
routing and Planner suites remain unchanged and must pass. This design and its
implementation plan are the only additional tracked files.

The current journal does not itself reject every unknown-only or qualified
request. This correction does not claim to fix that separate asymmetry. Tests
must distinguish parser preservation, actual Web admission rejection and journal
rejection. Do not use `needs_clarification` wholesale for targetless property work.

After exact donor admission integration, a separate integration gate must carry
the unchanged positive request through empty initial journal closure, all four
obligations and actual Task6 Web dataflow. Parser unit passes alone do not close
Task6. Its two native-wire receive timeouts remain independently unresolved.

## Validation requirements

- TDD: require observed RED for the exact reproducer and demonstrated failing
  variants; retain already-green spelling/order variants as compatibility
  controls. Assert original query unchanged, PDE5A retained and no
  false analytical unknown. Include QED before/after ADMET, LOGP/LogP case variants,
  longer analytical lists, and both ordering directions for analytical items.
- Explicit target:ADMET/against ADMET/for ADMET and PDE5A remain unknown, including
  a second explicit occurrence beside a valid analytical clause.
- Preserve unknown identifiers before/after a target, ADMET2, two PDE subtypes or
  mixed families, negation/switch/selectivity, missing activity noun, incomplete
  clauses and unknown suffixes. Verify the existing Router/Planner rejects the
  target ambiguity cases rather than merely checking parser output.
- Keep molecular fields entirely outside the exemption; existing invalid whole
  SMILES and journal tamper/conflicting-clarification regressions remain required
  at the later integration gate, not falsely covered by parser-only tests.
- Test deterministic scanning operation growth and all existing bounded
  selection tests. No performance threshold or timeout changes.
- Approved isolated launcher only, REPO-only substitution. One local scientific
  test process, no live providers/weights/config discovery. Independent SOURCE
  then fresh QUALITY, exact-head CI and inspected integration are required.

This design is subject to independent source review before TDD release. Package7
and package8 stay incomplete; deployment is excluded.

## Source review correction

Banach source review confirmed that exempting only ADMET starts is insufficient:
QED can itself start a list containing ADMET and PDE5A. The selected whole-clause
`list_matches` exclusion above corrects the design, while explicit/known-target
traversal remains unchanged. This is source reasoning, not runtime evidence.
Final SOURCE review approves d8e3eea with the validation wording correction now
applied above: already-correct variants remain GREEN controls, not required REDs.
