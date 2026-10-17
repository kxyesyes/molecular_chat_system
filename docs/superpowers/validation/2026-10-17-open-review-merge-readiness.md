# Open-review remediation and merge readiness

## Scope and authority

Starting PR 209 head: `1c567b309b7b883bb1f0440c457130c96cd467d8`.
Maintainer accepted the ordered repair/review/CI process, then explicitly selected
**Squash merge** for PR 209 followed by PR 210 only once gates pass. Earlier
"do not merge" checkpoints remain historical; this later authority does not
permit bypassing failures, protections, approval boundaries or rewriting history.

## Verified GitHub feedback

The four automated threads were read against actual code, not presumed correct.
The descriptor grammar/checker mismatch was already repaired in `1c567b30`.
The other three gaps remained: plain bounded generation omitted independent
property checks/reporting, unrelated docking negation blocked exact PDB lookup,
and generic Chinese possessive property questions selected molecular assessment.
Additional local routing probes showed descriptor thresholds could outrank plain
generation intent; that necessary consumer path is included in this repair.

## Tests first and minimal implementation

- Ordinary design: 32 failing tests / 20 controls, then one additional failing
  catalog multi-step test; final focused 53 passed. A fixture error-code type was
  corrected during test setup, not worked around in production.
- Router: 8 failed / 7 controls before production edits; 359 affected tests passed
  after repair. Descriptor-bound generation retains its workflow, while bare
  possessive wording alone does not create molecular input authority.
- Exact PDB: 16 failed / 26 controls, then original-syntax protection 2 failed /
  54 controls; focused/adjacent batch 217 passed, final affected batch 691 passed,
  1 skipped / 1 deselected. Original positive label grammar is preserved; compact
  qualified PDB text is rejected rather than made into a new positive syntax.

Bounded plain generation adds only `property_calculator`, bound to generated
`$.outputs.result` and candidate identity. Unconstrained single-step plans remain
unchanged. Missing/failed property observations render constraints as unverified,
not guessed values or silence. Plain-design policy still excludes ADMET,
activity and docking; enabling descriptor support does not authorize any of them.
The exact lookup exemptions cover bounded complete docking prohibitions only;
unknown qualification remains conservative. Original query text is retained.

## Local verification

Independent SPEC review of ordinary design additionally found that a numeric
bound must not override an explicit prohibition on property calculation. Eight
new prohibition cases failed before the planner guard. Two further direct
Chinese prohibitions failed before extending the guard. Three structure/property
preservation controls then failed because `without property changes` was being
treated as a calculation prohibition; the guard now requires calculation intent
or a complete descriptor-prohibition clause. These are approval-scope fixes,
not permission to run other scientific tools. The final parent regression below
covers the updated implementation; earlier counts remain historical batches.

All commands use the existing MedChat Python 3.10 environment, pytest
`-q -p no:cacheprovider --tb=short` and isolated temporary roots. App construction
tests require a root outside a Git checkout; that is a credential boundary, not
a reason to weaken `_safe_path`.

```powershell
python -m pytest tests/agent/test_plain_design_constraints.py tests/agent/test_open_review_routing.py tests/agent/test_browser_property_constraints.py tests/agent/test_target_role_property_constraints.py
# Parent: 299 passed (2.49s)
python -m pytest tests/test_exact_pdb_negation_scope.py tests/agent/test_exact_pdb_safe_lookup.py tests/agent/test_target_database_identity.py tests/agent/test_plain_design_constraints.py tests/agent/test_open_review_routing.py
# Parent cross-slice integration: 169 passed (2.23s)
python -m pytest tests/agent/test_allocation_deadline.py tests/agent/test_allocation_handoff.py
# Parent: 43 passed / 7 deprecation warnings (20.38s), external isolated basetemp
python -m pytest tests/agent/test_plain_design_constraints.py tests/agent/test_open_review_routing.py tests/agent/test_task_planner.py tests/agent/test_browser_s02_context.py tests/agent/test_negation_token_dots.py tests/agent/test_target_role_property_constraints.py
# Final parent design/approval regression: 578 passed (9.46s)
python -m pytest tests/test_exact_pdb_negation_scope.py tests/agent/test_exact_pdb_safe_lookup.py tests/agent/test_target_database_identity.py tests/agent/test_plain_design_constraints.py tests/agent/test_open_review_routing.py tests/agent/test_browser_property_constraints.py tests/agent/test_target_role_property_constraints.py
# Final parent cross-scope integration: 420 passed (3.51s)
python -m pytest tests/test_exact_pdb_negation_scope.py tests/agent/test_exact_pdb_safe_lookup.py tests/agent/test_target_database_identity.py tests/agent/test_plain_design_constraints.py tests/agent/test_open_review_routing.py tests/agent/test_browser_property_constraints.py tests/agent/test_target_role_property_constraints.py tests/agent/test_task_planner.py tests/agent/test_browser_s02_context.py tests/agent/test_negation_token_dots.py
# Final post-QUALITY-fix combined regression: 739 passed (10.37s)
python -m compileall -q src scripts
git diff --check
```

Final-source `compileall` and `git diff --check` were rerun successfully after
the preservation fix. A tracked non-Markdown credential-pattern filename scan
returned no matches (grep exit 1); this limited scan is not a comprehensive
secret-history audit. All task files still require explicit staging review.

Implementation regression batches: plain design/templates/compiler/bindings/
catalog/alignment/property reports 651 passed; exact-PDB parser/wrapper/service/
resolver consumers 691 passed, 1 skipped / 1 deselected. Batches overlap and must
not be added into a unique total. Fixtures use a synthetic generator/service;
descriptor integration uses real local RDKit, never an invented scientific value.
No new browser, model, Vina or full local-suite acceptance is claimed.

## PR 210 prior failure diagnosis

The existing head `bd643246` changes six documents only. Its historical CI run
38864284064 failed the Agent allocation slow-planning test (13296 passed,
2 skipped / 1 failed); aggregate failed as a consequence. The exact failed test
passed locally on that unchanged head (1 passed / 28 deselected), which alone
does not establish a flaky root cause or close the remote failure. PR 209 contains
the separately tested expired-allocation-consumer cleanup repair `2c3a509b` and
43 related tests passed locally. No timeout/assertion was loosened. PR 210 must
receive new main normally after PR 209's squash merge and pass its own new-head CI.

An initial allocation regression batch used a basetemp below the Git worktree:
32 passed / 11 setup errors because user-configuration paths under a checkout are
rejected. Retrying with an external isolated temporary root passed all 43; no
credential file was read or safety check changed, and the failed batch is not a pass.

## Publication checkpoint

Final independent SPEC: all three tasks PASS. The original reviewer closed the
last preservation/prohibition finding after 399 tests and 9 additional real
orchestrator/fixture controls; no edits, network or scientific-service execution.
QUALITY review and exact-new-head CI remain separate gates, not inferred passes.

The first QUALITY pass found three further P2 gaps through independent runtime
probes: descriptor prohibitions with `the`, Chinese physicochemical/possessive
subjects; a standard PDB label mixed with an additional compact alternative;
and Chinese `化合物` omitted from explicit molecular subjects. Parent tests first
reproduced 13 failed / 144 controls, then minimal subject/qualifier and ambiguity
guards passed all 157. Preservation and double-negative controls remain in the
same batch. The reviewer ran 144 / 767 / 469 overlapping tests before these
findings; those passes did not close the counterexamples. Follow-up SPEC and
original QUALITY closure remain required after the repair.

Follow-up independent SPEC PASS: 58 focused tests plus 28 additional offline
controls, covering all three QUALITY counterexamples and preserved positive/
negative boundaries. The original QUALITY reviewer is checking closure against
this final code, not against the earlier passing batches.

Final original-reviewer QUALITY PASS: all three P2 findings closed, no remaining
finding. Independent final run: four focused files 157 passed (2.49s), plus 31
closed-loop offline probes, diff check passed. No repeated broad review batches.
This completes both independent review gates for the frozen source/test changes;
publication and exact-new-head CI are still pending at document commit time.

## First publication CI and compatibility follow-up

Published repair `49b4fec6a3221adf0beb66f8d9d7765cf59effb8` passed nine
checks, but [quality39023955092](https://github.com/kxyesyes/molecular_chat_system/actions/runs/39023955092)
failed Agent and consequently the aggregate: 13683 passed / 2 skipped / 1 failed
in 1062.15s. The sole failure was existing
`test_prompt_acceptance.py::test_hf004_generation_overreach_is_bounded_to_generator`:
catalog descriptor capability was being returned in routing permissions even
for an unbounded request. This CI is a failure, not a waived gate or flaky rerun.

Parent reproduced the original assertion and added request-scope/availability
controls: RED 4 failed / 19 passed. Minimal router change filters descriptor
permission to bounded, non-prohibited ordinary-design requests for both final
permissions and tool-availability scoring. Catalog execution capability, planner,
other workflows and the existing CI assertion remain unchanged. Full prompt
acceptance plus affected design/planner tests: 328 passed (2.83s); compileall and
diff passed. An initial command referenced nonexistent `test_router.py` and ran
no tests; it is not counted as verification. Independent review and a new
exact-head CI are required before merging.

Affected existing router/browser/routing-matrix/react-routing plus new controls:
207 passed (2.55s). Independent follow-up SPEC PASS: 172 focused tests and 114
offline controls, including baseline comparison of other workflows. No important
finding; original CI assertion, catalog and planner unchanged. QUALITY pending.

Final follow-up QUALITY PASS: 170 focused tests plus original HF004/HF005
2 passed, and 84 independent permission/availability/planner/simulated-LLM
controls. No important finding, no source edits by reviewers. Original overreach
assertion passes unchanged. New exact-head CI remains required at commit time.

## Green CI followed by new independent GitHub findings

`7db27b2e38256f8a0bc5332ba96758bb307fe04d` independently passed
[quality39025652897](https://github.com/kxyesyes/molecular_chat_system/actions/runs/39025652897),
all 11 jobs success, no workflow reruns. Final gate audit found four additional
automated threads posted at 10:21 UTC (1 P1 / 3 P2): English bound conjunction,
absolute-only optimization goals, unrelated negation of display counts, fresh
molecule-bound top-one reverse searches. CI green did not waive review findings;
the PR remained unmerged and each feedback item was checked against real code.

Numerical tests first: 21 failed / 10 controls; then the two smallest consumer
changes passed 348 affected tests (2.88s). SPEC then found that an unsupported
second condition (`logP = 3`, `logP ~ 3`, approximate QED) could be dropped;
three added tests failed, and the English conjunction boundary now requires a
recognized metric followed by a supported comparison operator. It does not add
equality/approximation syntax. Final affected batch: 351 passed (2.81s).

Absolute goals use only matched candidate constraint checks; no relative
comparisons are fabricated. Missing/failed/foreign evidence and unknown
structural constraints remain unverified. The original no-goal warning remains
for genuinely absent goals. No model or experimental validation is claimed.

Initial numerical SPEC closed its conjunction finding (88 tests /31 offline
controls), but QUALITY found an important shared-parser gap: punctuation-separated
unsupported conditions were still silently skipped, enabling false absolute-goal
support. Parent reproduced 30 failed /3 relative-goal controls across six clause
separators and five unsupported numeric forms. Instead of more conjunction-only
exceptions, the parser now records explicit unsupported numeric intent as unknown
when no supported operator matches; it does not evaluate equality/approximation.
Seven affected files passed 621 tests (3.79s). Follow-up SPEC found a relative
wording regression (`logP is lower`, `QED is higher`); three new controls failed
before requiring textual numeric introducers to be followed by numeric intent.
That seven-file batch passed 624 tests (3.79s), compileall/diff passed. SPEC then
found prefixed comparisons (`logP is < 3`, `approximately <= 3`) still omitted;
18 added cases failed /30 controls passed. Numeric-intent detection now reuses
the existing complete comparison-operator grammar, without evaluating unsupported
prefix combinations. Final parent seven-file batch: 642 passed (3.88s).
Original SPEC PASS: 142 offline tests /1 deselected, 252 prefixed-operator controls
and 12 relative/legal-bound controls. No remaining SPEC finding; original QUALITY
closure is still required. QUALITY then found combined prefixes (`is approximately
3`, `is about 3`) still dropped numeric intent: 18 new cases failed /56 controls
passed. Prefixes are now consumed iteratively before the numeric-intent check,
instead of enumerating one-level combinations in a regex. Numeric comparisons
and units remain unchanged; no numeric intent means normal relative behavior.
Final parent seven-file batch: 662 passed (3.89s). Original numerical SPEC final
PASS: 162 offline tests /1 deselected, 324 combined-prefix controls and 11
relative/legal-bound controls; scanning strictly advances without combination
backtracking. Intermediate passes are not
substituted for independent closure or exact-new-head CI.

Web worker first reproduced 14 failed /27 controls; further count-negation
controls 3 failed /50 passed and old top-one-reference controls 2 failed /8
passed. Minimal web fixes passed 57 focused tests, 165 ranking tests, and 742
affected tests /4 existing FastAPI deprecation warnings (overlapping batches).
Only the count phrase's governing negation vetoes cap5. Explicit molecule-bound
fresh reverse requests bypass stale rank-reference interpretation, with a routing
view that preserves validation/permissions and sends the original query to
execution/history. The full ten-row observation/checkpoint/provenance is retained.
Scope limitation: top-one text is now admitted as fresh dispatch, not implemented
as cap1; display still supports 5/10 and tests explicitly preserve that contract.
English `show top 1` already dispatched fresh at baseline; the reproduced blockers
were Chinese ranked-first and English `show rank 1`. No real network/science or
production database was used. Web SPEC independently ran 57/165 tests and found
two count edge cases (postfix modal negation and Chinese adversative conjunction);
the worker is adding RED controls and repairing them before re-review. Fresh
dispatch scope passed SPEC. Worker reproduced the two count findings with
6 failed /8 controls, then added postfix modal negation and Chinese adversative
clause boundaries: 14 targeted passed, 179 ranking regression passed; affected
regression 742 passed /4 existing warnings. Independent final web SPEC/QUALITY
remain pending. The cap1 limitation is unchanged and explicitly tested.
Original web SPEC subsequently PASS: independent memory RED 6 failed /8 controls,
then 179 current tests passed; no remaining important finding. Parent combined
web/numeric/old prompt acceptance seven-file regression: 523 passed (17.01s),
compileall/diff passed. Final numerical original-reviewer QUALITY PASS: 108 tests
(1.58s) and 74 independent controls. All previous numeric P1/P2 findings closed,
no important finding; long 2048-prefix scan completes normally. Only web final
QUALITY and exact-new-head CI remained outstanding at that checkpoint.
Final web QUALITY PASS: 179 related tests (35.54s), 22 independent assertions,
memory compilation/diff passed, no important permission/provenance/regression or
performance finding. Service network attempts: 0. All SQLite test files were
isolated outside Git. No cap1 acceptance or actual scientific run is claimed.

Final parent commands (same existing Python, cache disabled, Git-external
isolated basetemp, no real service):

```powershell
python -m pytest tests/agent/test_review_absolute_bounds.py tests/agent/test_lead_optimization_verifier.py tests/agent/test_browser_property_constraints.py tests/agent/test_target_role_property_constraints.py tests/agent/test_plain_design_constraints.py tests/agent/test_open_review_routing.py tests/agent/test_task_planner.py -q -p no:cacheprovider --tb=short
# 662 passed (3.89s)
python -m pytest tests/agent/test_review_absolute_bounds.py tests/agent/test_web_reverse_rank_review.py tests/agent/test_browser_property_constraints.py tests/agent/test_target_role_property_constraints.py tests/agent/test_open_review_routing.py tests/agent/test_prompt_acceptance.py tests/agent/test_plain_design_constraints.py -q -p no:cacheprovider --tb=short
# 523 passed (17.01s), overlaps the prior batch
python -m compileall -q src scripts
git diff --check
```

All four new GitHub findings now have SPEC then QUALITY closure. Publication
requires a new commit, evidence replies/thread resolution and its own full CI;
the previous 7db27b2e green result does not cover this subsequent code.

Independent review and exact-head CI outcomes are recorded in the current PR
status sections before merging. This saved document is not proof of a future
merge or future CI outcome. No auto-merge, admin bypass or human approval is
implied by agent review. After PR 209 merges, PR 210 must retain its documentation
scope and preserve the new prewarm / supported multi-worker guidance.
