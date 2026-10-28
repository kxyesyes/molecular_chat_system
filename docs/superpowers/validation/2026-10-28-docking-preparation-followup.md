# Docking preparation and request responsiveness

## Change and compatibility

Single-record SDF/MOL inputs passed to Meeko are checked for missing explicit
hydrogens before launching its CLI. Such inputs use the existing RDKit
hydrogen-addition and geometry-convergence path, avoiding a known failed first
launch. Other formats, multi-record files, legacy preparers and explicit-H inputs
keep their existing path. Provenance records `rdkit_explicit_h_then_meeko` for
the new ordering. No Vina search settings or geometry acceptance policy changed.

During reverse-target source prewarming, the CPU-bound Python worker competes
with foreground filesystem, ACL and database operations. Docking and task HTTP
requests now hold the existing foreground-I/O scheduling hint through the full
ASGI response. The prewarm worker briefly yields at its existing checkpoints;
all source validation, deadlines, cancellation and ownership checks remain.
The middleware wraps session resolution without bypassing authentication.
There is no public response, database or frontend contract change.

## Measurements

Local repository sample: `data/samples/MAGL_5zun.pdb` and `data/samples/5.sdf`;
center `(5.99, 3.01, 17.345)`, box `(20, 20, 20)`, exhaustiveness 8,
num_modes 10 and energy_range 3. Real RDKit, Meeko and Vina were used.

- Isolated ligand preparation before: 3.082, 2.854, 2.724 seconds; after:
  1.913, 1.370, 1.358 seconds. Median reduction approximately 52%.
- All six prepared ligand outputs were byte-identical, SHA-256
  `798f74691a0fdcececfb372d8c3088ac95d31568d375dff338d89e46d45e0e33`.
- With prewarm active before request prioritization, three task submissions
  took 11.849, 3.300 and 2.504 seconds; summaries took 3.862, 7.946 and
  5.442 seconds. Task completion was observed at 31.289, 23.314 and 20.567 seconds.
- One full run after request prioritization: submission 0.549 seconds,
  observed successful task completion 15.362 seconds, summary 0.147 seconds,
  PDBQT 0.063 seconds and pose SDF 0.071 seconds. A regression suite was running
  concurrently during this final measurement. Additional repetitions were
  stopped at the maintainer's request; these samples are not a performance SLA.

The benefit to users is shorter submission/result waits. Developers reuse one
existing scheduling mechanism with focused regressions. Agent-facing task
requests retain the same response and ownership contracts while avoiding the
same prewarm contention.

## Verification already completed

Commands use Python from the project's MedChat environment:

```text
python -m pytest tests/test_docking_request_priority.py tests/test_llm_config_responsiveness.py tests/test_reverse_target_source_runtime.py -q --tb=short
```

68 passed. Tests cover complete responses, excluded paths, overlapping requests,
failure/cancellation cleanup, existing configuration I/O and prewarm lifecycle.

```text
python -m pytest tests/test_agent_session.py tests/test_agent_session_entrypoints.py tests/test_durable_docking_artifact_routes.py tests/test_docking_consent_legacy_routes.py tests/test_temporal_docking_routes.py tests/test_security_headers.py -q --tb=short
```

414 passed, 3 skipped. This invocation had already completed when the maintainer
requested that testing stop; no replacement run was started.

Earlier preparation-focused and related scientific/execution regressions:
360 passed, 10 skipped, recorded during implementation. `python -m compileall
-q src scripts` and `git diff --check` also passed before the stop request.
Read-only independent reviews found no blocking issue in either optimization.

No additional local tests were run for publication. Full repository acceptance,
Linux/server benchmarks, strict deployment checks, long-duration prewarm
completion under sustained request traffic and full real/all acceptance were
not performed for this follow-up. No JS or deployment assets changed.

## Scope and rollback

Runtime data, logs, temporary profiling scripts and local configuration are not
included. Server Node/Pi installation and the separately reported model-test
ClientDisconnect were diagnosed only; this change does not claim to fix them.
Revert the follow-up commit to restore the previous preparation order and remove
request scheduling hints; no data migration is required.
