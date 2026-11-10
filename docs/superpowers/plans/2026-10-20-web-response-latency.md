# Web response latency implementation plan

> Use subagent-driven-development for implementation and independent specification/quality review.

**Goal:** Restore responsive pages and APIs while the application's model configuration monitor performs blocking filesystem/security work.

**Architecture:** Keep FastAPI, existing user configuration security validation and cross-process locking. Move synchronous configuration observation, reload and persistence off the event loop using the existing cancellation-draining helper; do not release configuration/model ownership before physical I/O completion. Keep full reverse-source validation and scientific evidence unchanged.

**Tech Stack:** Python 3.10, asyncio, FastAPI, pytest, Windows.

## Evidence

The running server's homepage returned in 5.23 s / 0.59 s / 5.76 s. Concurrent health, small CSS and page requests all stalled for approximately 7.2 s. A live stack sample found MainThread executing `_watch_llm_env_config -> _refresh_llm_config_from_env -> user_llm_signature -> Windows filesystem/ACL calls`; the reverse-source prewarm worker concurrently consumed a CPU core generating fingerprints. A separate idle-process signature call took only 0.094 s. This supports checking event-loop blocking under worker contention, not assuming an external CDN problem.

## Implementation

- [x] Add `tests/test_llm_config_responsiveness.py` with real application methods and event-controlled blocking filesystem seams. Before releasing the seam, a concurrent ASGI health request must complete. Cover initial signature, locked signature recheck, reload and save.
- [x] Run the tests and record RED. Include repeated cancellation retaining the configuration lock / exclusive model gate until the worker physically finishes, plus failed reads retaining the existing model.
- [x] Modify `src/web/app.py` with `await finish_on_cancel(asyncio.to_thread(...))` around synchronous config signature, reload and save operations. Keep model installation on the event-loop thread. Review save cancellation carefully: a committed save must not leave an incorrectly acknowledged in-memory state.
- [x] Run focused responsiveness, existing config, model-lifecycle and ordinary-web lifecycle regression suites, then `python -m compileall -q src scripts` and `git diff --check`.
- [x] Independent SPEC review, then QUALITY review; close valid findings before completion.

## Runtime validation and delivery

- [x] Confirm the local development service has no active work before restarting it. Apply only validated task changes while preserving all original user edits; do not merge unrelated branch state into the dirty checkout.
- [x] Measure the same endpoint requests after the change while source prewarm is active. If latency remains, profile again and revise the hypothesis rather than declaring success.
- [x] Record exact commands, timings, skipped checks and remaining limitations in a validation document and handoff. Delivery uses explicit task-file staging, the task branch and a draft PR to main; the resulting commit/PR records the final publication state. Do not merge without a new explicit authorization.

No new external model calls, scientific output, data changes, dependency changes or permission weakening are part of this fix. The temporary sampling profiler is installed outside the repository and project environment.

## Runtime follow-up: background CPU fairness

The first patch removed global event-loop stalls: five concurrent endpoint rounds returned homepage responses in 237.5–633.6 ms, with health in 90.8–183.8 ms. However, authenticated configuration GETs still took 6437.7 and 5640.2 ms. A new live stack confirmed the configuration checks now run in a thread while the prewarm worker continuously generates fingerprints holding the GIL. This residual delay also affects operations that must refresh configuration before dispatch.

- [x] Add deterministic RED coverage for foreground-I/O priority in the application-owned prewarm worker, including overlapping I/O owners, cancellation during a yield and full validation before readiness.
- [x] Add a reference-counted foreground-I/O context exposed through the owned reverse-target tool. Configuration filesystem operations enter this context inside their worker thread. Only while an I/O owner exists, existing prewarm cancellation checkpoints perform a bounded interruptible wait, then recheck cancellation. Preserve every row, file identity, hash, timeout and publication check; avoid global interpreter settings, extra workers or caching security decisions.
- [x] Run source-runtime/compact-source/owned-source regression and compile checks, then independent SPEC and QUALITY review of the extension.
- [x] Reapply only the validated extra hunk locally and compare configuration GET plus page latency while fingerprint computation is active. Record any remaining limitations.

An isolated temporary-configuration microbenchmark rejected unconditional scheduling: signature reads under a synthetic MACCS CPU worker took 10.02/10.15 s without yielding and 10.86/12.02 s with periodic `sleep(0)`. A positive 1 ms sleep reduced reads to 47/37 ms, but Windows/Python 3.10 rounded sleeps to approximately 15.6 ms. Therefore fixed sleeps on every short work slice would substantially slow full-corpus validation; the selected design limits yielding to actual foreground configuration I/O ownership. Temporary fixtures contain only dummy Ollama settings.
