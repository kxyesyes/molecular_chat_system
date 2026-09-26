# Preserve complete Agent coverage while partitioning CI capacity

## Authority and evidence

The user delegates recommended choices and subsequent gated merges through
package8. This independent infrastructure prerequisite does not authorize model
activation, deployment, business refactoring or scientific assertion changes.
Source baseline is mainfb62f5322e1e299e6e871970476e46379817fc23.

PR89 head3d6a872 CI36255095764 completed failure. Agent job108440253195 hit the
unchanged600s command deadline (exit124) around97%; six other partitions passed.
There were no reported FAILED assertion lines before termination, which does
not prove remaining assertions pass. New218 source-hook cases span6.05s. Previous
PR88 Agent passed575.08s. Current partial Web lifecycle span77.27s and ordinary
Web runtime/lifecycle spans70.20/33.13s identify a useful partition, not a unique
performance root cause. Do not retry until lucky or describe this as a code fix.

## Choice

Choose a deterministic two-way Agent partition. Keeping the entire expanding
suite in one600s job has little headroom. Increasing the single deadline changes
that guard. Instead preserve every per-command deadline and assertion, move
exactly three slow modules into a required sibling job, and prove no cases vanish.
Combined available Agent execution capacity explicitly grows600 to1200 seconds,
plus separately bounded collection verification. This is capacity redistribution,
not unchanged total budget or application performance improvement.

## Exact selection and files

Keep matrix name agent, target tests/agent with exact --ignore paths:

- tests/agent/test_ordinary_web_lifecycle.py
- tests/agent/test_ordinary_web_runtime.py
- tests/agent/test_web_decision_runtime_lifecycle.py

New agent-web-lifecycle runs exactly those three modules in the listed order.
Both keep -vv --durations=25 and command_timeout600. Existing sandbox180/600,
per-test60s flags, root/task partitions, root Bash exclusion array, Node setup,
dependencies, offline flags, job deadlines and fail-fast:false are unchanged.
offline-quality continues to require the whole matrix and static-quality.
There will be nine workflow checks, all required for our merge gate.

Allowlist: .github/workflows/quality.yml, tests/test_quality_workflow_contract.py,
tests/test_deployment_assets.py, and this design/implementation plan. The latter
test independently asserts six rows and the old exact Agent selection; update
those affected expectations openly, retaining all other deployment assertions.
No YAML formatting evasion, glob filters, skip/xfail, retry or continue-on-error.

## Same-head coverage proof

An Agent-only workflow step after dependencies and before execution reads the
actual checked-out quality.yml. Reject duplicate row names; require one agent
and one agent-web-lifecycle. Three fresh pytest collection processes run original
tests/agent and the two actual YAML selections/arguments. Use shlex and argv,
never shell/eval. A constant inline collection plugin captures complete node IDs.
Check child success, valid native capture, unique IDs before set conversion,
nonempty partitions, empty intersection, and exact union with original selection.
Print only counts and fixed success/failure messages, never child output or IDs.

Collection imports code. Follow approved offline-launcher isolation: sanitized
environment, temporary cwd/config/data locations, known repository paths only,
socket prohibition before site initialization (preserve socketpair support),
installed plugins retained, no host secrets/config/weights. Do not present
--collect-only or environment filtering as an OS sandbox. Keep fixed total
collection deadline180s and each child at most60s; terminate/reap on timeout and
fail the step, with no retry/fallback. Scientific execution deadlines stay600s.

Synthetic contracts execute the exact extracted inline program against temporary
repositories using actual matrix selections. Cover parametrization, current and
future/nested modules, similar filenames, nested same basename in valid packages,
non-Agent sentinels, overlap/omission/duplicate rows, failed/timed-out collection,
and suppression of child errors. Preserve existing root partition proof.

## Review and release

Russell independent source-only review approves the strategy and inline proof in
principle after parent accepted the third-file scope correction. This is not
implementation approval or a test pass. Written plan, actual RED before workflow
changes, isolated focused tests, fresh SOURCE/QUALITY, current-head real CI
collection comparison and all nine execution checks are mandatory. Do not run
local project collection outside the approved launcher. Respect the single local
scientific test process slot and poll actual handles to terminal.

Only after gated squash should PR89 align the actual landing and rerun its full
CI. Retain its original failure. No P7/P8 completion is inferred from CI work.
