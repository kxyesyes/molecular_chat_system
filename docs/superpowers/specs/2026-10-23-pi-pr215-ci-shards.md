# PR #215 bounded CI partition design

The maintainer approved bounded Agent CI sharding while retaining all tests, coverage verification and existing per-job time limits. No production runtime or model configuration changes are part of this design.

## Evidence and choice

Run 40049269829's Agent job collected 15215 tests and continued making progress until its 1200-second deadline at 24%. Timestamped completed-test groups account for about 410 seconds in binding continuation, 156 in binding loop and hundreds more in browser/chat suites. This is accumulated real Pi execution, not evidence that the final interrupted test hangs. Six observed browser failures separately reproduce as stale success-status expectations.

Keep the 30-minute job deadline and existing command budgets. Add five named 1200-second groups: browser/chat, decision bindings, remaining decision tests, Pi runtime tests and remaining Web tests. Keep general core, Web lifecycle, docking and docking publication groups. This increases the Python matrix from nine to fourteen jobs. It avoids both a longer monolithic timeout and a change to production process/session isolation.

## Coverage and failure behavior

Selectors apply only to top-level file families. Future nested files remain in general core. Every new group excludes top-level docking files; decision excludes its binding subgroup and Web excludes the existing lifecycle files. Core excludes all newly assigned top-level families. Explicit glob-expanded file arguments retain both ignore and deselect handling.

The actual isolated pytest collection verifier enumerates every named Agent partition and compares node-ID sets against the original entire Agent suite. Duplicate matrix names, unknown/missing Agent groups, empty selections, duplicate IDs, overlap or omissions fail closed. Keep its 180-second total deadline, 60-second child bound, network guard and cleanup checks. If real CI cannot meet this collection bound, investigate measured collection cost rather than silently lengthening it.

Synthetic collector regressions exercise the exact workflow program and Bash selection across all partitions, including future/nested files, docking collisions, omission/overlap, duplicate IDs and deadline faults. Final acceptance requires new-head remote CI; offline tests alone do not prove Linux execution.

Cloud DNS rejection, local-model protocol failures and incomplete deployment health stay explicitly unverified. The PR remains draft and is not merged.
