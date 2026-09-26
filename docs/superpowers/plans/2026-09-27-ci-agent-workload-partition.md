# CI Agent capacity partition implementation

Implement only the companion approved-design scope. The original planning
checkpoint had no code changes; the implementation evidence below supersedes
that checkpoint without satisfying the outstanding release gates.

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

## Local implementation evidence — 2026-09-27

On docs checkpoint `a9d747d`, the parent added the contracts before changing
the workflow. Isolated run `37524/1e1a8a` failed as expected: **12 failed,
14 passed, 7 warnings in 10.25s**, exit1. Failures covered the missing seventh
matrix row, actual collection verifier and duplicate deployment contract.
No scientific assertion, skip or deadline was relaxed.

After implementing the approved three-file change, the same focused command
completed as `9122/09bf1d`: **26 passed, 7 warnings in 16.04s**, process and
runner exit0. This is synthetic selection/failure coverage, not actual full
repository collection or remote CI success. The launcher was copied from the
approved source-hook integration launcher with only the literal REPO changed.

Frozen SHA256 values:

- `.github/workflows/quality.yml`: `A15CB8A5B15713E1A0F8E616CD6B83B09D3ED378B01313B8F856AF6692CB3936`
- `tests/test_quality_workflow_contract.py`: `FF1506AF5CB706BFADAB259371045B031A76BE0458BC62C94A8AB855A4ACC27F`
- `tests/test_deployment_assets.py`: `5EC8050805D1384DBD15E247ACFB51DBF9FB63ADE00D6B0A101042EDAAA1D0D2`

Parent rechecked these hashes and `git diff --check` after resuming. Fresh
independent SOURCE/SPEC is requested; QUALITY and its independent repeat remain
pending. No implementation commit, PR or merge is implied. The original PR89
Agent timeout remains a failed run; the new capacity allocation is explicitly
600 seconds per Agent partition (1200 combined), not a performance repair.

### Independent SOURCE findings and correction

Gibbs rejected the first freeze: UDP/DNS bypassed the pre-site network guard,
and final parsing/cleanup could exceed the total deadline before a success print.
The reviewed bounded amendment is in the companion design. Actual parent RED
`70346/903517`: **8 failed, 26 passed, 7 warnings in 27.19s**, exit1. Fake native
callbacks ensured no UDP/DNS request occurred even on RED; final-cleanup clock
injection reproduced the incorrect success after expiry.

First corrected run `43762/42480a`: **6 failed, 28 passed, 2 skipped, 7 warnings
in 27.55s**, exit1. All six failures were Windows socketpair tests stacking the
new guard on the approved launcher's guard, not the actual standalone collector.
The test now executes the exact AST-extracted pre-site prologue in a fresh
`-I -S -B` child, preserving fake native callbacks and positive real socketpair.
No production guard was weakened for the fixture correction.

Final parent `54158/3d09b1`: **34 passed, 2 skipped, 7 warnings in 23.77s**,
process/runner exit0. Skips are Linux GNU watchdog and unavailable native sendmsg
on Windows; both remain mandatory real Linux CI cases, not local successes.

Corrected source SHA256:

- workflow: `34B34840E7753682C545AAA7BA3DF144C5BEA300E0606FE32F7C6B3DF9A80A36`
- workflow contracts: `291659AFDDA4EDB2EAE06F62534B448EA6C2D5097E541F2DD2E40A928CA0D049`
- deployment contracts unchanged: `5EC8050805D1384DBD15E247ACFB51DBF9FB63ADE00D6B0A101042EDAAA1D0D2`

Corrected SOURCE re-review and fresh QUALITY/repeat remain outstanding. No
actual full collection or remote gate pass is inferred from this local run.

Corrected Gibbs SOURCE approves the frozen three-file implementation. Fresh
Hilbert QUALITY also approves after independent exact focused run17448:
**34 passed, 2 skipped, 7 warnings in 22.63s**, runner/process exit0, all hashes
unchanged. Launcher SHA256 is
`6DE45F357FD2E57CDEF13403FC0A848EC559889987933F87134B9FBDFA960C99`,
identical to the approved source-hook integration copy except REPO. Warnings
are three SWIG type and four FastAPI lifespan deprecations. Test slot released.
Parent in-memory compilation of both test modules, diff check and filename-only
credential-pattern scan passed. Local review gates now satisfied; Linux-only
cases, actual collection equivalence and all nine current-head CI checks still
must pass before merge. No science/production performance claim is made.
