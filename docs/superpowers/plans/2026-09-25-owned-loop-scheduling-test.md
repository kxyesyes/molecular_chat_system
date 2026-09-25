# Owned-loop scheduling test isolation

## Scope and evidence

Independent test-only branch from landed A2 `ed284b7`. This is a bounded verification repair needed by remaining package 7, not a production ownership or model change. The user delegates recommended choices and gated merges through package 8; no deployment.

During ordinary-chat policy review the unchanged existing owned-loop test failed its first five-second tool-entry barrier twice (502 passed/1 failed; later 587 passed/1 failed). A corrected temporary instrumented run passed once. Historical failure causes remain UNKNOWN. Source inspection proves a permitted schedule: the inner adapter Future can still be pending when its real 0.03-second wait expires; successful cancellation then prevents tool entry. The original test assumes entry before timeout without enforcing that setup. Its outer workflow uses the remaining loop budget, not that same 0.03 seconds.

An earlier diagnostic hid `worker_owner` from signature introspection and must not be used as production failure evidence. Correcting it preserved the signature and physical drain/join order. Neither diagnostic supplies unique attribution of the historical failures.

## Requirements

1. No production changes. Limit edits to `tests/agent/test_decision_loop.py`, an optional small helper in `tests/agent/test_worker_ownership.py`, and this evidence document.
2. Reproduce the original test's unsupported scheduling assumption before repairing it, using a bounded real-thread schedule. No fabricated Future, timeout, scientific result or success.
3. For the running-worker lifetime branch, control inner submit-to-entry ordering before returning the original Future to the adapter. Preserve the actual 0.03-second `Future.result` timeout, existing repeated cancellation, actual worker-exit and join barriers, one model/tool invocation, failed outcome, `TOOL_TIMEOUT`, retained ownership and final zero roots.
4. Add a separate pending-before-start branch using a real inner executor initializer barrier. Assert actual timeout and Future cancellation, zero tool calls, no terminal while the real thread/join is held, then release and physically join; outcome remains failed `TOOL_TIMEOUT` with settled owner and zero roots. Cover cancellation consistently when applicable.
5. Every path must release test barriers and physically join real threads in `finally`, including failed assertions. Use bounded waits as deadlock guards, not production deadline changes. Do not fake wall-clock performance guarantees or remove existing scientific/error/lifecycle assertions.
6. Avoid broad helper changes affecting unrelated tests. Do not modify global time functions or shared production adapter instances.
7. Preserve the original failures and distinguish controlled reproduction from unique historical attribution. Do not use repeated passing runs to erase failures.

## Execution and gates

- Read local AGENTS and relevant test/runtime paths. Establish RED from a controlled schedule, implement minimal test-only split, and record exact commands/results.
- Use only the ignored `scratch/owned_loop_offline_runner.py`, copied from the already-reviewed ordinary offline runner with only the repo constant changed. Run `C:/Users/xkx52/.conda/envs/MedChat/python.exe -I -S -B scratch/owned_loop_offline_runner.py` with explicit test paths or node IDs. It clears inherited configuration, redirects stores/config to a temporary directory, blocks networking and cleans up. No credentials, real models or production assets.
- Focused scope: the changed loop test nodes, the complete `test_decision_loop.py`, `test_worker_ownership.py`, and `test_decision_protocol_recovery.py` modules.
- Serialize heavy tests. Independent SPEC review then QUALITY review; all findings must close. Explicit staging and a local commit only after parent review. Publication/CI8/merge handled separately by parent; not implementation authority for the worker.
- The ordinary policy remains separately frozen/unpublished. This branch does not complete ordinary output gating, continuation budgets, Web integration, B/C or final real acceptance.

## Results

Pending implementation. No RED/GREEN or runtime repair claim yet.
