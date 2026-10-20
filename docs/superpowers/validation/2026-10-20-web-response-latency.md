# Web response latency validation — 2026-10-20

## Scope and diagnosis

Base: `616d8e9e`; branch: `codex/fix-web-response-latency`.
The reported issue affected the homepage, feature pages and function responses.

The running Windows/Python 3.10 development server exhibited simultaneous stalls
across `/health`, a small CSS asset, `/molecular-docking` and `/`. An initial batch
returned all four in approximately 7.2 seconds. A live sampling stack placed the
main event-loop thread inside the configuration watcher's synchronous trusted
filesystem/Windows ACL checks while `reverse-source-prewarm` generated molecular
fingerprints holding the GIL. A separate idle signature read took about 94 ms.

Five additional baseline rounds (four concurrent GETs per round; milliseconds):

| Route | Round 1 | Round 2 | Round 3 | Round 4 | Round 5 |
|---|---:|---:|---:|---:|---:|
| `/health` | 2736.2 | 4877.8 | 231.1 | 3813.3 | 170.0 |
| CSS | 2738.8 | 4881.7 | 403.1 | 3814.7 | 401.9 |
| `/molecular-docking` | 2734.8 | 4877.1 | 252.4 | 3812.5 | 179.0 |
| `/` | 2914.7 | 4900.0 | 514.3 | 3850.2 | 642.4 |

## Implementation and intermediate measurement

Configuration signature, reload, save and publication-time persistent-state
validation run in threads. Model construction/publication remain on the event
loop. Cancellation drains physical I/O before releasing configuration/model
ownership. A started save drains publication and retirement before acknowledging
cancellation, because cancellation cannot undo a committed filesystem write.

This first patch removed the global event-loop stall. Five initial post-restart
rounds returned the homepage in 237.5–633.6 ms, health in 90.8–183.8 ms and docking
in 103.1–202.9 ms. However, authenticated configuration GETs still took 6437.7 and
5640.2 ms. A live stack confirmed the configuration checks had moved to a worker
thread, with the prewarm worker still continuously competing for the GIL.

An isolated temporary-configuration probe using a synthetic MACCS CPU worker
measured signature reads at 10.02/10.15 s without yielding and 10.86/12.02 s with
periodic `sleep(0)`. Positive sleeps improved I/O, but Windows/Python 3.10 rounded
requested 1 ms sleeps to approximately 15.6 ms. Unconditional sleeps were rejected
to avoid imposing that delay throughout full-corpus validation.

The follow-up design gives configuration I/O a reference-counted foreground
priority context. Prewarm performs bounded interruptible waits at existing
cancellation checkpoints only while that I/O is physically active. All rows,
hashes, file identities, readiness checks and the acquisition deadline remain
required.

## Final runtime measurement

After the final restart, a live stack confirmed `reverse-source-prewarm` was
executing `compute_query_fingerprints`/MACCS and the main thread was back in the
asyncio event poll. The same four-route, five-round concurrent probe returned
HTTP 200 for every request. Five authenticated configuration GETs also returned
200. This is a small local sample, not a production percentile benchmark.

| Route | Before median (ms) | Final median (ms) | Final range (ms) |
|---|---:|---:|---:|
| `/health` | 2736.2 | 758.0 | 58.4–871.2 |
| CSS | 2738.8 | 760.0 | 62.8–971.4 |
| `/molecular-docking` | 2734.8 | 756.9 | 57.7–966.1 |
| `/` | 2914.7 | 1089.9 | 69.2–1263.9 |
| `/api/llm/config` | 6039.0 (two intermediate samples) | 960.5 | 671.2–1252.8 |

The original 5–7 second shared event-loop pauses did not recur in this batch.
Heavy prewarm still causes timing variation; these measurements do not establish
zero CPU contention or a browser-rendering SLA. The configured acquisition timeout
is unchanged and full-corpus readiness is not claimed.

## Tests and review

Commands use the existing Python 3.10 scientific environment, with
`PYTHONDONTWRITEBYTECODE=1`.

- Initial RED: `python -m pytest tests/test_llm_config_responsiveness.py -q --tb=short`
  — 9 failed, 4 passed; failures demonstrate that the old implementation blocks
  concurrent ASGI health requests.
- Initial corrected and extended responsiveness suite: 19 passed.
- `python -m pytest tests/test_llm_config_responsiveness.py tests/test_llm_runtime_config.py tests/test_user_llm_config.py tests/test_user_llm_routes.py tests/test_llm_security_policy.py tests/test_model_request_lifecycle.py tests/test_design_model_switch.py tests/test_agent_llm_wiring.py tests/agent/test_ordinary_web_lifecycle.py -q --tb=short`
  — 195 passed, 3 skipped, before the last four publication-guard cases were
  added. Skips are existing Windows symlink/POSIX permission limitations.
- `python -m pytest tests/agent/test_web_decision_runtime_lifecycle.py tests/agent/test_allocation_handoff.py -q --tb=short -p no:cacheprovider`
  — 133 passed.
- Initial I/O patch independent SPEC and QUALITY reviews: PASS.
- Foreground-priority extension RED: 14 failures demonstrated the missing context
  and ownership behavior in the earlier implementation.
- Final combined command: the nine-file command above plus
  `tests/test_reverse_target_source_runtime.py tests/test_reverse_target_compact_source.py tests/test_reverse_target_invocation_receipts.py`,
  with `-q --tb=short -rs` — 587 passed, 3 skipped. The three skips are two Windows
  symlink-permission cases and one POSIX-mode case.
- `python -m pytest tests/test_reverse_target_source_runtime.py::test_foreground_wait_still_expires_original_acquisition_deadline tests/test_rdkit_logging.py -q --tb=short -rs`
  — 27 passed. The deadline case advances the controlled acquisition clock across
  the original deadline during a foreground wait and verifies timeout, zero
  verified rows and no ready publication.
- Applied-local-checkout verification:
  `python -m pytest tests/test_llm_config_responsiveness.py tests/test_reverse_target_source_runtime.py -q --tb=short -p no:cacheprovider`
  — 58 passed. This additionally checks the narrowly applied patch alongside the
  user's existing local branch and edits.
- Final extension independent SPEC and QUALITY reviews: PASS.
- `python -m compileall -q src scripts` and `git diff --check`: passed.

These batches overlap and are not a unique total. Existing SWIG and FastAPI
deprecation warnings are not hidden. The final commit's CI status is independent
of these local results.

## Runtime and compatibility boundaries

The three source files and two regression test files were applied to the user's
local development checkout after backing up changed existing files. Hashes confirmed all nine existing
modified UI/test files were unchanged. No queued/running task, Agent run, tool
execution or child scientific process existed before the local server restart.
Only the development server process was restarted; no running scientific task
was interrupted.

HTTP probes record route, status and elapsed time only. Browser cookies and
configuration bodies are not printed or saved. Profiling is temporary and is not
added as a project dependency. Browser automation was unavailable under the saved
permission setting, so these measurements establish HTTP responsiveness, not
full browser rendering or a live model's generation speed.

No scientific algorithm, source asset, API response schema, credential validation
or model-provider configuration is changed. Full-corpus prewarm completion and
external model/Vina acceptance are not claimed by these measurements. No deployment
or data files changed, so deployment strict health checks were not run. Local
full-suite and Node tests were not rerun for this Python-only change; the relevant
Python regressions and remote quality workflow cover the delivery separately.

Rollback is to revert this task's source changes and restart the development
server, preserving unrelated local edits. No database migration is required.
