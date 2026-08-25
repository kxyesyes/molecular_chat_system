# Temporal production canary readiness handoff

Date: 2026-08-25

Branch: `codex/temporal-production-canary-readiness`

Task 11 starting HEAD: `73b17026609df6e14705df728791ba0a93baca6f`

Isolated worktree: `D:\MedChat\molecular_chat_system_worktrees\industrial-agent-temporal-docking`

## Objective and current state

This stage hardened the docking-only Temporal canary for a native Linux +
systemd production candidate. It added evidence-gated rollout, real observation
projections, loopback monitoring, an independent single-concurrency worker,
immutable generation staging/activation, descriptor-confined PostgreSQL backup
and isolated restore verification, and a canonical production preflight.

The implementation is complete for local contract/static verification, but it is
not yet production-ready. This Windows session did not run native Linux/systemd,
Docker Compose, promtool, Temporal, PostgreSQL, or real Vina evidence. Deployment
validation therefore correctly reported `partial`; no skipped check is reported
as passed.

The repository defaults remain `MEDCHAT_TASK_BACKEND=local` and
`MEDCHAT_TEMPORAL_CANARY_PERCENT=0`. Production supports only
`0 -> 5 -> 10 -> 25`, without a force or level-skipping path.

## Changed categories and key paths

- Runtime configuration, rollout, observation, persistence, and metrics:
  `src/task_runtime/config.py`, `rollout.py`, `observation.py`,
  `prometheus_metrics.py`, `deployment_contract.py`, `secure_io.py`,
  `trusted_files.py`, and `trusted_process.py`.
- Temporal docking execution and worker safety:
  `src/task_runtime/temporal/`, `production_worker.py`,
  `scripts/run_temporal_docking_worker.py`, and
  `scripts/validate_temporal_worker_production.py`.
- Operator rollout and evidence commands:
  `scripts/manage_temporal_canary.py`, `observe_temporal_canary.py`,
  `run_temporal_docking_acceptance.py`,
  `run_temporal_production_preflight.py`, and
  `validate_temporal_deployment.py`.
- Compose, metrics, alerts, and dashboards:
  `deployment/temporal/docker-compose.yml`, its mounted bootstrap/role scripts,
  Prometheus configuration/rules/tests, Grafana provisioning/dashboard, and
  `scripts/configure_temporal_metrics_relay.py`.
- Immutable worker deployment:
  `deployment/install-temporal-worker.sh`,
  `activate-temporal-worker-generation.sh`,
  `libexec/install-temporal-worker-bundle.py`, both systemd units, trusted
  environment/directory helpers, and `temporal-worker.env.example`.
- PostgreSQL safety:
  `scripts/backup_temporal_postgres.py` and
  `scripts/restore_temporal_postgres.py` use held descriptors, atomic reports,
  trusted version-specific tools, server-major checks, and fixed verification
  database `temporal_verify`.
- Verification:
  focused tests under `tests/task_runtime/`, Temporal operator/deployment/relay
  tests, stdlib Linux trust tests, opt-in systemd/cgroup tests, and the isolated
  live-install runner source.
- Operational documentation:
  `docs/runbooks/temporal_docking_canary.md`, this handoff, deployment guidance,
  and the production-readiness specifications/plans.

The Task 11 documentation commit changes only:

- `docs/runbooks/temporal_docking_canary.md`
- `docs/PROJECT_STANDARDS.md`
- `docs/handoff/latest.md`

## Key commits

- `b56eb66` production Temporal configuration validation.
- `5d0d3f6`, `73f2c18`, `91e201b` rollout gates, observation projection, and
  atomic canary management.
- `60c3bbf`, `cb618c6`, `bbec8d6` worker metrics, release-blocker monitoring,
  and the loopback/private metrics relay.
- `b82f396` pinned Temporal/PostgreSQL/Prometheus/Grafana Compose stack.
- `82a8fc3`, `4749530`, `26ee610` immutable generation staging,
  transactional activation, and verified rollback.
- `3ecd59b`, `ad3f950`, `8ea28cb`, `a05571f` atomic backup/restore and subsequent
  descriptor/tool trust hardening.
- `39ef218`, `097f1d6`, `56ede66` production preflight and full deployment asset
  validation.
- `544890a`, `bcfbb97`, `976eb64`, `73b1702` final alert, real-evidence,
  artifact, timeout, and cancellation hardening.

## Verification run in this session

`PY` below is the requested MedChat Conda Python interpreter. Pytest cache was
disabled in every test command.

```powershell
$PY -m pytest tests\task_runtime\test_temporal_config.py tests\task_runtime\test_temporal_rollout.py tests\task_runtime\test_temporal_observation.py tests\task_runtime\test_temporal_prometheus.py tests\test_temporal_operator_scripts.py tests\test_temporal_deployment_assets.py -q -p no:cacheprovider
# 921 passed, 92 skipped in 39.27s

$PY -m pytest tests\task_runtime tests\test_temporal_docking_routes.py -q -p no:cacheprovider
# 1515 passed, 21 skipped in 86.11s

$PY -m pytest tests\agent -q -p no:cacheprovider
# 647 passed, 1 skipped, 5 existing deprecation warnings in 12.23s

$PY -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py -q -p no:cacheprovider
# 17 passed, 5 existing deprecation warnings in 11.40s

$PY scripts\run_agent_acceptance.py --mode contract --output scratch\task11-agent-contract.json
# passed, 34/34 contract cases

$PY scripts\run_temporal_docking_acceptance.py --mode contract --repeat 3 --output scratch\task11-trusted-reports\temporal-contract-repeat3.json
# passed, run_count=3, scientific_execution=false
# sha256=34c96e1e4850e7c507b9257bf8e7ec9d529f4f097d33c6408b35731e5b2a27f6

$PY scripts\validate_temporal_deployment.py --output scratch\task11-trusted-reports\temporal-deployment-validation.json
# partial (expected nonzero partial exit)
# python_static=passed
# docker_compose=skipped code=docker_unavailable
# promtool=skipped code=promtool_unavailable
# systemd_analyze=skipped code=linux_systemd_required
# sha256=c511237adf67e6bf697140b4e02bf8e684f6d4a7b50d8933cd839da9d9f876ae

$PY -m compileall -q src scripts deployment\libexec
# passed
```

The Temporal contract and deployment report canonical SHA-256 values were both
recomputed from their JSON payloads and matched the recorded values.

The first Temporal contract attempt targeted the inherited `scratch` directory
and failed closed with `report_write_failed`. Inspection showed that its Windows
DACL did not satisfy the hardened writer's trusted-parent contract. A dedicated
ignored report directory with inheritance removed and write access restricted to
the current user passed `capture_trusted_path_boundary`; the exact contract then
passed without a code change.

Agent/contract reports are local ignored artifacts and are not committed:

- `scratch/task11-agent-contract.json`
- `scratch/task11-trusted-reports/temporal-contract-repeat3.json`
- `scratch/task11-trusted-reports/temporal-deployment-validation.json`

## Evidence not run

- No real Vina acceptance was run, so this handoff records no binding energy,
  pose count, latency, or other real-science value. Nothing was fabricated from
  contract output.
- No Temporal server or production worker was started. Contract repeat 3 used
  controlled lifecycle evidence only.
- PostgreSQL backup and isolated restore were not run against a real server.
- Docker Compose config/health and promtool were unavailable.
- Native Linux/WSL was not invoked. `systemd-analyze`, systemd activation and
  migration, cgroup mixed-kill/runtime-mask checks, stdlib root trust tests, and
  the isolated live-root runner were not run.
- Production preflight was not run because real Temporal, Vina, Prometheus,
  alerts, and verified PostgreSQL restore evidence were unavailable. Running it
  here could only produce non-promotable partial/failed evidence.

## Required production-host actions

1. On a disposable native Linux production candidate, verify root-owned `go-w`
   release/Conda/deployment sources and run the trust-boundary stdlib suites.
2. Run the isolated live-install runner in its private mount namespace, then
   stage and activate the immutable generation. If a flat predecessor exists,
   exercise the allowlisted migration and confirm legacy tmpfiles remains
   quarantined.
3. Run `systemd-analyze verify` for the Web and worker units, real activation/
   rollback/migration signal tests, mixed lifecycle tests, and the runtime-mask +
   held old `cgroup.kill` descriptor integration test on cgroup v2.
4. Provision runtime-only Compose secrets and both environment files through the
   trusted host process; set an approved positive backup retention policy.
5. Start the pinned Compose stack, configure the loopback metrics relay, and run
   Docker Compose config, promtool config/rules/tests, dashboard, health, queue,
   and alert checks.
6. Use a trusted version-specific PostgreSQL bin directory to create a real
   atomic backup and verify restore into `temporal_verify`; confirm the passed
   `latest-verified.json` marker.
7. At canary 0, run production preflight. It must include deployment, contract
   repeat 3, real Temporal/Vina repeat 3 with verified artifacts/provenance,
   infrastructure, release-blocker alerts, and backup/restore evidence.
8. Promote only `0 -> 5 -> 10 -> 25`. At each nonzero level collect 20 real
   terminal tasks, or observe 24 hours with at least 3, before the next level.

Any missing host action keeps readiness `partial`. Rollback first routes only new
traffic to 0; it does not delete or locally replay Temporal-accepted work.

## Workspace integrity

All commands and edits in this task used the isolated worktree named above. No
command was run against, and no file was changed in, the original
`D:\MedChat\molecular_chat_system` workspace. Runtime reports remain ignored and
outside the documentation commit.
