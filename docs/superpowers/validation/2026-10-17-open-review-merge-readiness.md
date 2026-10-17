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

Independent review and exact-head CI outcomes are recorded in the current PR
status sections before merging. This saved document is not proof of a future
merge or future CI outcome. No auto-merge, admin bypass or human approval is
implied by agent review. After PR 209 merges, PR 210 must retain its documentation
scope and preserve the new prewarm / supported multi-worker guidance.
