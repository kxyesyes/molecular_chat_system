# Root CI workload partition — design and implementation plan

> **For agentic workers:** use TDD, then independent SPEC and QUALITY reviews. This is a separate infrastructure PR, not part of route extraction.

## Evidence and diagnosis

PR64 head6dd92db, run36048361209/root job107797417567 reached the unchanged600s command limit (exit124) near89% without a displayed failed assertion. Agent/sandbox/task/static jobs passed. Prior PR63 root job107782639409 passed3563 cases with81 skips in572.36s, already close to the limit. The new route boundary adds68 cases. The exact machine scheduling contribution is unknown; do not claim the route code caused a performance regression or that no later assertion could fail.

Read-only collection associates the long early progress intervals with activity family chain tests exercising synthetic CPU RGNN, ASGI, WebSocket and Node. A local isolated full-root duration run is in progress; append actual results. This is suite capacity, not grounds to remove these tests.

## Options and choice

Increasing the sole600s timeout would hide the workload concentration; rewriting/caching scientific integration tests risks reducing coverage. Chosen: two disjoint root workloads with the same dependency profile and600s per-job cap, keeping every test and assertion. Total available root compute budget therefore grows from600 to1200s across independent jobs; it is not claimed unchanged. No single-test timeout, scientific gate, warning or skip changes.

Keep existing root job name for compatibility and add root-activity. Activity target is the shell-expanded top-level pattern tests/test_activity*.py. Root retains tests with the existing three directory ignores and adds --ignore-glob=tests/test_activity*.py. This intentionally excludes only top-level activity files; new nested areas still belong to root. Both jobs install Node20 for existing chain tests. Matrix fail-fast staysfalse and offline-quality still requires every python-tests matrix result plus static-quality. Eight checks replace seven; the new check is mandatory through the aggregate, never optional.

## Write set

Only .github/workflows/quality.yml, tests/test_quality_workflow_contract.py, this document and a bounded handoff. No business code, dependencies, secret/config/asset reads, external model calls or deployment.

## TDD tasks

- [ ] Baseline existing quality workflow tests.
- [ ] Update the two old assertions for6 matrixentries and Node setup in both root jobs. Add YAML structural tests for exact targets, root exclusions,600s limits, untouched180s sandbox-api limit, two60s test-timeouts, fail-fastfalse and unchanged all-jobs aggregate.
- [ ] Add real pytest collection proof using a tiny temporary test tree. Exercise both patterns exactly (expand activity shell glob using pathlib for cross-platform subprocess), collect old root and each new shard, assert nonempty/disjoint/exhaustive node IDs. Include future activity filenames, unrelated root tests and nested activity filenames, plus the three excluded domains. No test execution or network.
- [ ] Verify RED before editing workflow.
- [ ] Add root-activity matrixentry with600s, root ignore-glob, Node conditional forboth. Preserve allother steps/policies.
- [ ] Verify GREEN, syntax/YAML, diffcheck.
- [ ] In isolated environment collect the actual repository node IDs using old root target and bothnewshards; compare sets, zero omissions/duplicates. Record counts without printing test/privatepayloads.
- [ ] Run bothactual groups (or full equivalent root run plus focused partition test) and record failures honestly. Capture representative durations; don't claim Windows timing equalsLinuxCI.
- [ ] Fresh SPEC then QUALITY; exact staging and draftPR. Required8checks allpass beforemerge. Then merge this infrastructure main ancestor intoPR64 and rerun itscompleteCI beforemerging64. No blind rerun of failed run; originalfailureevidence stays.

