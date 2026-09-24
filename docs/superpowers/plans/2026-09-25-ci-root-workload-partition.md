# Root CI workload partition — design and implementation plan

> **For agentic workers:** use TDD, then independent SPEC and QUALITY reviews. This is a separate infrastructure PR, not part of route extraction.

## Evidence and diagnosis

PR64 head6dd92db, run36048361209/root job107797417567 reached the unchanged600s command limit (exit124) near89% without a displayed failed assertion. Agent/sandbox/task/static jobs passed. Prior PR63 root job107782639409 passed3563 cases with81 skips in572.36s, already close to the limit. The new route boundary adds68 cases. The exact machine scheduling contribution is unknown; do not claim the route code caused a performance regression or that no later assertion could fail.

Read-only collection associates the long early progress intervals with activity family chain tests exercising synthetic CPU RGNN, ASGI, WebSocket and Node. A local isolated full-root duration run is in progress; append actual results. This is suite capacity, not grounds to remove these tests.

## Options and choice

Increasing the sole600s timeout would hide the workload concentration; rewriting/caching scientific integration tests risks reducing coverage. Chosen: two disjoint root workloads with the same dependency profile and600s per-job cap, keeping every test and assertion. Total available root compute budget therefore grows from600 to1200s across independent jobs; it is not claimed unchanged. No single-test timeout, scientific gate, warning or skip changes.

Keep existing root job name for compatibility and add root-activity. Activity target is the shell-expanded top-level pattern tests/test_activity*.py. Root retains tests with the existing three directory ignores and uses exact --ignore arguments for the same top-level paths, expanded by Bash into an argument array. No ignore-glob: pytest fnmatch can cross path separators. New nested areas, including activity-prefixed directories, still belong to root. Both jobs install Node20 for existing chain tests. Matrix fail-fast staysfalse and offline-quality still requires every python-tests matrix result plus static-quality. Eight checks replace seven; the new check is mandatory through the aggregate, never optional.

## Write set

Only .github/workflows/quality.yml, tests/test_quality_workflow_contract.py, the existing duplicate workflow assertion in tests/test_deployment_assets.py, this document and a bounded handoff. No business code, dependencies, secret/config/asset reads, external model calls or deployment.

## TDD tasks

- [ ] Baseline existing quality workflow tests.
- [ ] Update the two old assertions for6 matrixentries and Node setup in both root jobs. Add YAML structural tests for exact targets, root exclusions,600s limits, untouched180s sandbox-api limit, two60s test-timeouts, fail-fastfalse and unchanged all-jobs aggregate.
- [ ] Add real pytest collection proof using a tiny temporary test tree. Exercise both patterns exactly (expand activity shell glob using pathlib for cross-platform subprocess), collect old root and each new shard, assert nonempty/disjoint/exhaustive node IDs. Include future activity filenames, unrelated root tests and nested activity filenames, plus the three excluded domains. No test execution or network.
- [ ] Verify RED before editing workflow.
- [ ] Add root-activity matrixentry with600s, exact root exclusion array, Node conditional forboth. Preserve allother steps/policies.
- [ ] Verify GREEN, syntax/YAML, diffcheck.
- [ ] In isolated environment collect the actual repository node IDs using old root target and bothnewshards; compare sets, zero omissions/duplicates. Record counts without printing test/privatepayloads.
- [ ] Run bothactual groups (or full equivalent root run plus focused partition test) and record failures honestly. Capture representative durations; don't claim Windows timing equalsLinuxCI.
- [ ] Fresh SPEC then QUALITY; exact staging and draftPR. Required8checks allpass beforemerge. Then merge this infrastructure main ancestor intoPR64 and rerun itscompleteCI beforemerging64. No blind rerun of failed run; originalfailureevidence stays.

## Execution evidence

- Separate branch starts at main aa86377. Only workflow and its contract tests changed; no dependency/scientific code edits.
- Existing quality test baseline:7 passed in0.15s. RED:4 failed/5 passed in1.22s (missing new shard/Node condition and old matrixcount). GREEN:9 passed in1.49s. Normal isolated MedChat Python runner, no caches/real switches.
- Tiny real pytest subprocess collection tests prove future root/nested activity filenames remain covered; excluded Agent/sandbox/task directories stay excluded.
- Actual repository collection ran three separate isolated pytest processes on this branch, using the real conftest and old/new arguments. Old root3646 nodeIDs, root1785, root-activity1861. Both nonempty, duplicateIDs0, intersection0, union equals old root. Sorted old collection SHA256:0fe265050c18ea498f0373285bbb86fc25a9b750464b6415b7526548053d66d3. Counts include the2 new partition tests relative to main; this is collection evidence, not execution success.
- Collection wrapper reuses the RAG isolation plan's whitelist/tempcwd/environment. A Capture plugin prints nodeIDs to captured memory only; parent compares sets. Subprocess timeout180s bounds diagnosis, not a test performance assertion.
- git diff --check passed. Full root profiling in the separate PR64 branch completed:3559 passed,153 skipped,6 warnings,173 subtests passed in701.12s. This used Windows with concurrent bounded probes, not the Linux CI timing environment. Slowest cases included operator-script subprocess13.20s, family worker9.85s/9.49s, family preparation CLI9.74s and training interruption subprocesses6.53–6.90s. Skips include POSIX/Linux-only checks and explicitly disabled trained-model acceptance; this is offline coverage, not real-model acceptance.
- SPEC review found a future-coverage defect in the first ignore-glob approach: tests/test_activity_extra/test_nested.py would be dropped by pytest fnmatch although shell selection never included it. Added actual collection regression:1 failed/8 passed in1.40s, showing both omitted parametrized IDs. Replaced broad exclusion with exact Bash-array paths;9 passed in1.44s. Original current-tree collection was valid but insufficient to prove future coverage; rerun exact-argument collection before release. No production files changed.
- The final exact-ignore implementation was recollected in three isolated subprocesses:3646=1785+1861, intersection0, duplicateIDs0, exact union and unchanged sorted old-ID SHA256 above. This confirms current-tree coverage after the review fix, in addition to the synthetic future-directory regression.
- Independent SPEC approved after the exclusion fix (9 passed1.38s). Independent QUALITY approved (9 passed1.26s), additionally checking real Git Bash syntax and actual argv for all six shards, with30 top-level activity files. Parent final focused rerun9 passed1.35s. YAML, in-memory Python compilation and diff checks passed. Linux CI remains a required release gate, not yet claimed passed.
- First PR65 CI run36051787565 failed: root job107808884358 finished in158.25s with1 failed/1709 passed/75 skipped. tests/test_deployment_assets.py retained a duplicate five-shard assertion, missed by focused review; local reproduction1 failed/16 passed6.72s. Corrected that exact count to6 and additionally required the new activity target, retaining both60s per-test timeout assertions and all credential/static gates. This is a test expectation fix for the approved matrix, not a relaxed scientific assertion. The other Python shards and static-quality passed; offline-quality correctly failed because root failed. The original failure is retained. The earlier full root run was on PR64's old workflow and could not validate this changed workflow assertion.
- Local combined deployment-assets and quality-contract GREEN:17 passed7 warnings4.22s. Repo-wide search confirms both workflow matrix-count assertions now expect6. Incremental SPEC review requested the aggregate-failure clarification above; no code issue was found.
- Incremental independent SPEC and QUALITY approved the two-line deployment assertion correction and corrected failure wording. First-run root-activity independently completed1855 passed6 skipped5568 warnings214.24s. Rerun all CI checks on the corrected head before release.
