# Agent CI timeout diagnostics for PR87

## Evidence and delegated choice

Current head9f44b78 run36249902754/attempt1 is terminal failure: Agent job
108425852094 reached76% then GNU timeout returned124 at600 seconds. Six other
jobs passed; aggregate offline-quality failed. Previous PR87 head d976422 run
36249184214 also timed out at600 seconds, around98%. Retain both. Prior green
PR84/86 runs near575/582 seconds do not identify this failure's cause.

Progress-line gaps are distributed (17–28%,40–41%,64%); the latest64% lines were
56.78 and61.58 seconds apart. This is not evidence of a single hanging test.
Quiet mode names no current test and produces no completed duration summary.
No production fix, runner defect or unique environment cause is established.

The user delegates recommended choices. Considered: repeat unchanged tests
(adds no attribution); split/increase budgets (premature and changes gate);
diagnostic verbosity while retaining all limits (selected). This is a bounded
test-observability follow-up to the existing PR, not a performance-fix claim.

## Exact design

Change only the existing Agent row's pytest_args in .github/workflows/quality.yml
from empty to '-vv --durations=25'. Keep existing targets, -q, options ordering,
all six partitions, test selection, 600/180s command deadlines, 60s sandbox test
deadlines, dependency profile and aggregate all-jobs gate unchanged. Pytest -vv
overrides the previous -q into named progress; GitHub timestamps associate output
with test boundaries. Completion yields the slowest25 durations. No additional
rerun, fail suppression, artifact capture, environment dump or application log
emission. Tests run with the existing synthetic/no-real-family CI configuration;
do not read or include real credentials. No host local assets or real providers.

Add a focused test in tests/test_quality_workflow_contract.py that parses the
actual YAML, requires only Agent's exact new options, verifies its same target
and600s deadline, and verifies all other rows' options unchanged. Existing
partition/complete collection/deadline/all-jobs assertions remain unchanged.

## Execution plan and gates

1. Independent source/design review before diagnostic code changes.
2. Add the YAML contract test; obtain behavioral RED under current empty args
   through the approved isolated launcher, after explicit test-slot handover.
3. Change only Agent pytest_args; run the complete workflow contract module,
   recording RED/GREEN and launcher hash. Do not run simultaneous local science.
4. Independent review, scoped diagnostic commit, push exact PR87 head and inspect
   new CI logs. Keep both old failures. No repeated same-head reruns by default.
5. Locate actual slow test(s) from new evidence before choosing any fix; no gate
   relaxation or completion claim. Fresh all-gate checks are mandatory for merge.

Independent Epicurus source/design review approves: -q plus -vv produces net
verbosity1 (individual test names), not verbosity2. Boundary timestamps localize
gaps but cannot precisely separate setup/call/teardown. A duration summary may
still be absent on external timeout. Parent added the focused YAML test only;
workflow remains unchanged until behavioral RED under the existing launcher.
P7/P8 remain incomplete; normal-Web worker proceeds in its disjoint tree.

Parent TDD after explicit Rawls terminal slot release:
`python -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_quality_workflow_contract.py::test_agent_timeout_diagnostics_only_add_named_progress_and_durations`
failed1/0.17s, exit1, terminal547193: actual empty pytest_args differed from the
expected '-vv --durations=25'. After that single YAML value edit, the complete
test_quality_workflow_contract.py module passed10/1.27s, exit0, terminal987f2a.
This proves configuration/partition contracts, not the cause of the600s failure.
Launcher SHA8C044090FB83203EC756C1C99A975BA9505E6E3B3422D930E9484A1EFA2C3703
is unchanged. Independent code review remains required before publication.

Epicurus SOURCE/code and fresh Ptolemy QUALITY approve the bounded diagnostic
diff with no actionable findings. Both reviews are source-only, not independent
test executions. Ptolemy independently verified the launcher hash. Parent's
actual RED/GREEN above remains the test evidence. Ready for scoped publication
and new exact-head CI, not merge or timeout-resolution claims.
