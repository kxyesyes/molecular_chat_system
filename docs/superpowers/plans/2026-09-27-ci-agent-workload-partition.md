# CI Agent capacity partition implementation

Implement only the companion approved-design scope. No code has changed yet.

1. Verify branch/base/AGENTS, current workflow and both contract modules. Prepare
   approved local isolated launcher by exact copy with REPO literal substitution;
   no test starts without sole-slot transfer. Record initial hashes.
2. Add failing contracts for exact seven matrix rows/two Agent selections, their
   original600s/diagnostic args and unchanged other rows. Add actual synthetic
   pytest collection coverage tests derived from YAML, including future/nested
   names and overlap/omission/duplicate/error/timeout/output-suppression negatives.
   Check same-head inline verifier ordering,180s total/60s child budgets and
   sanitized socket-before-site subprocess boundary. Run focused modules, record
   actual RED cause, distinguish setup errors from behavior failures.
3. Implement matrix selection and bounded Agent-only inline collection verifier.
   Keep exact existing Run Python tests command, root extra_args and final gate.
   Adjust only the duplicate deployment contract's affected selection/count.
   No standalone framework or business code. Run focused GREEN and regressions.
4. Fresh SOURCE/SPEC then QUALITY with exact source freeze, independently repeat
   isolated focused modules. Compile/check YAML, diff, scan tracked changes by
   filenames for credential patterns. Explicit stage/commit only allowlist.
5. Draft PR, attach, current-head CI. Actual original Agent collection must equal
   disjoint union of the two actual partitions on that same head; both execution
   jobs and all other jobs must complete successfully, nine checks total.
   Inspect all remote review pages/head/base before SHA-guarded squash; fetch and
   compare reviewed/landed trees. Preserve prior failures and reports.
6. Align PR89 and other affected integration branches to the verified landing,
   inspect upstream-only changes and reverify impacted contracts/CI. Do not
   silently count old eight-check runs as the new nine-check gate.

Focused command (after launcher and slot verification):

```text
python -I -S -B scratch/ordinary_chat_offline_runner.py tests/test_quality_workflow_contract.py tests/test_deployment_assets.py
```

Quality/deployment contracts may launch synthetic subprocesses; these must retain
their own sanitized environment and bounded lifetimes. No real provider calls.
