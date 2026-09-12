# Latest handoff

## 历史代码集成持续任务（2026-09-12）

PR #14、#16、#17、#18、#19、#21、#22 已获具体授权并合并，main 为 `57c677e`；历史功能集成尚未全部完成。
总体计划见 [历史集成计划](../superpowers/plans/2026-09-12-historical-integration-completion.md)，
当前证据/续接存储子批次见 [agent-evidence-continuation-store.md](agent-evidence-continuation-store.md)，
完整残差见 [historical-integration-status.md](historical-integration-status.md)。
PR #18 修复 Linux run-id 问题后 CI 7/7 通过并合并；详情见
[family-training-run-integration.md](family-training-run-integration.md)。前端 PR #15 最新 CI 有关闭测试失败，独立排查中，暂停合并。
决策协议 PR #19 最新 head 双审、本地回归及 CI 7/7 通过，已获具体授权合并。
PR #20 独立修复关闭测试，双审及 CI 7/7 通过、等待授权；PR #21 经双审和 CI 7/7 通过后获授权合并。
证据隔离/续接存储 PR #22 在长文本矩阵完整分组后通过双审、联合2686 passed/1 skipped和最新CI7/7，已授权合并；首次CI失败保留。
动态会话已通过独立双审，修复矛盾完成态与错误引用隔离两项问题，联合2744 passed/1 skipped，待独立PR/CI，见 [动态会话记录](agent-dynamic-session-integration.md)。
PR #23 独立修复沙盒late-create测试同步，双审及532 passed/2 skipped，CI进行中；不声称修复旧manifest偶发问题。
动态执行、调用方续接校验与隔离入口仍未全部集成；下一批必须处理旧loop修正输入后的PARTIAL兼容与终结兜底。
用户优先代码集成；不启动生产模型或部署服务。以下旧交接仅作历史依据。

## 家族双模型隔离推理（2026-09-10）

PR #12 已合并为 main `132a600`。本批记录见
[activity-family-predictor-integration.md](activity-family-predictor-integration.md)，
仅集成 Python 预测器，不接线线上入口、不启用生产模型。以下旧交接保留。

## 家族双模型成组管理（2026-09-09）

PR #11 已合并为 main `c85775a`。下一批独立集成见
[activity-family-bundle-integration.md](activity-family-bundle-integration.md)，
仅含成组注册与选择，不训练或切换线上模型。以下旧交接保留。

## 家族数据层分批集成（2026-09-09）

最新任务见 [activity-family-data-integration.md](activity-family-data-integration.md)。
PR #1 已作为旧版文档关闭；本批从已合并 PR #10 的 main 拆出家族数据层，
不启用模型，不改变线上入口。下方既有 OpenSandbox 记录保留供追溯。

# OpenSandbox docking broker handoff

## OpenSandbox runtime remediation plan finalized (2026-08-31)

Branch: `codex/opensandbox-stability-hardening`

Final plan commit: `2e0b9d5bac0a8d62e6e59f73ff6606dfff05f1a3`

The separate runtime-remediation implementation plan is now complete at
`docs/superpowers/plans/2026-08-30-opensandbox-real-stability-remediation.md`.
Independent specification and implementation-quality reviewers both returned
`APPROVED` with no remaining Critical or Important finding.

The plan closes the formal-gate contracts for root-only SQLite authority,
immutable claims and sealed evidence, actor orphan recovery, OpenSandbox/Docker
resource observation, an authority volume that survives QEMU OS-disk rewind,
static fail-closed systemd interlocks, phase-isolated output mounts, and
transactional service-start capabilities. The final start boundary uses a
root-owned guard wrapper that revalidates journal and capability state under the
lifecycle lock and directly executes a fixed, hash-attested command after
dropping to the configured service identity. The first-mutation lock remains
exclusive from capability revocation through staging, `STAGED`, `ACTIVATING`,
and durable publication of the current authorization.

This planning stage did not implement those future runtime changes and did not
run QEMU, Docker, OpenSandbox, Vina, acceptance, or a new 30-job soak. The
preserved `79860ec` QEMU report and its failures remain unchanged. The next stage
is to execute the reviewed remediation plan test-first; it must not be reported
as already implemented or promoted.

## OpenSandbox soak evaluator correction (2026-08-30)

Branch: `codex/opensandbox-stability-hardening`

Tested commit: `2d4797ec54288b5b5e28a191621f7e2892a37010`

This sub-project corrected the stability-soak evaluator without changing the
meaning of real runtime failures. A measured submission is now correlated to
the single Broker lifecycle found inside its diagnostics window by
`(trace_id, job_id)`; the caller idempotency key remains an independent worker
request key. Event order, attempt numbering, cleanup state, and terminal state
are validated strictly. A valid recovered provisioning or cleanup retry keeps
`events_complete=true`, but adds `broker_retry_observed`, so the stability gate
still fails instead of silently treating recovery as a clean run.

Validated tool provenance comes from the Broker `validation_completed` event.
Vina must agree with `ToolResult` provenance and Meeko is mandatory. Invalid or
missing clock samples fail closed; an initial clock failure is classified as
`process_failed`. Failed-run latency is bounded before report aggregation. The
report writer remains sanitized, atomic, and non-clobbering.

### Corrected offline evidence

The preserved-shape offline regression contains 30 runs:

```text
18 success
5 provider_error / server_500
5 tool_timeout / command_timeout
2 invalid_output / none
pass_rate=0.6
event_completeness_rate=1.0
overall_status=failed
```

This is an offline evaluator result, not a new real soak. It proves that the
evaluator no longer turns complete Broker lifecycles into false negatives while
retaining all 12 real failure outcomes.

The prior real QEMU evidence remains immutable:

```text
runtime_commit=79860ec175f0d3235dfd0597f248d7d52788fc76
report=/opt/medchat/molecular_chat_system/outputs/agent_evaluation/opensandbox_stability_soak-79860ec.json
sha256=c66e07c3e9ad4669a8ab703fa57e887d16b8d71b938ba8a0755725692ae3c67f
```

This sub-project did not rerun QEMU, Docker, OpenSandbox, or Vina and did not
overwrite that report.

### Verification and review

```text
focused sandbox-broker tests: 688 passed, 14 skipped
full tests/sandbox_broker: 2008 passed, 75 skipped
compileall: passed
static OpenSandbox deployment validation: passed
independent review: APPROVED
secret scan: clean
placeholder scan: clean
git diff --check: clean
git status --short: clean
```

Before the final green verification, one focused run reported `686 passed,
14 skipped, 2 failed` in a capacity-timing case and a worker-cancellation case.
The two isolated tests then passed 5/5 and 10/10, and the complete focused suite
passed on rerun. Earlier, the full service test file also reported `169 passed,
1 failed`; that timing-sensitive case passed in isolation. These are
non-stably-reproduced test-timing signals, not evidence that a deterministic
runtime defect was fixed or that runtime stability is proven.

The `OpenSandbox soak evaluator correction` section added in this sub-project
contains no API key, credential, raw environment value, user-specific absolute
path, or other sensitive runtime value. Older historical handoff sections are
outside this statement's scope.

Date: 2026-08-27

Branch: `codex/opensandbox-docking-broker`

Workspace: isolated task worktree

## Objective and current state

This stage added a docking-only OpenSandbox Broker between the Temporal worker
and AutoDock Vina. The Broker is reachable only through a protected Unix domain
socket, runs one fixed digest-pinned docking image under gVisor, denies outbound
network access, enforces CPU/memory/PID and timeout limits, validates all input,
scientific output, artifacts, hashes and provenance, and always performs bounded
sandbox cleanup. The Web process receives no OpenSandbox credential.

The implementation and the Ubuntu 24.04 QEMU pre-production deployment have a
verified functional and security baseline. This is not a production promotion:
later smoke runs exposed intermittent OpenSandbox execution latency/failures,
so rollout remains gated by both that stability issue and the existing Temporal
canary, production-host observation and backup gates documented below.

## Final stability fixes

- Acceptance now uses the production default Vina `exhaustiveness=8`. The
  previous value 32 was a stress workload that intermittently exceeded the
  fixed 267-second tool deadline on a 2-CPU gVisor sandbox and produced only
  1/3 successful measured runs. Stress testing must be a separate explicit
  profile, not the baseline availability gate.
- A non-zero sandbox command no longer automatically loses the wrapper's
  structured failure. The Broker reads only the fixed `result.json` path and
  accepts only an exact allowlisted schema plus valid phase/error-code pairs.
  Verified `tool_timeout` becomes `execution_timeout`; missing, malformed or
  impossible output remains fail-closed as `command_failed`. A verified Vina or
  Meeko failure retains the existing `command_failed` persistence/API value plus
  a safe `sandbox_tool_failed` warning marker; the new worker maps that marker to
  a provider/tool failure while an older worker remains fail-closed and can still
  read the record.
- OpenSandbox creation readiness failures are reconciled by metadata and retried
  once only after cleanup is confirmed. Acceptance observers synchronize with
  sandbox readiness, and cancellation waits long enough for bounded cleanup.
- Both failure-result reads and successful output download/validation now have
  hard deadlines and cooperative cancellation. A cancellation-resistant SDK
  coroutine is tracked after a short grace period so external sandbox cleanup
  can proceed without leaving the Broker job indefinitely non-terminal. Remote
  download is side-effect-free; local validation and artifact publication occur
  only after the bounded download completes, preventing writes after terminal.
- The destructive PID-exhaustion probe now runs after the setsid and tool-version
  probes. This prevents a deliberately exhausted sandbox from destabilizing
  subsequent security observations in the same acceptance run.

## Real QEMU evidence

The final command ran through the worker-side UDS client, Broker, OpenSandbox,
gVisor and real Vina; it did not call Vina directly and did not use demo or
fallback results.

```text
status=passed
repeat=3
pass_rate=1.0
p50_latency_ms=24412
p95_latency_ms=26142
security_failures=0
Vina=1.2.5
Meeko=0.7.1
secure_runtime=gvisor
pose_count=7,9,9
best_energy=-3.348,-3.360,-3.313
cleanup_status=succeeded,succeeded,succeeded
```

Each run produced a verified repository-relative pose artifact and SHA-256.
Warnings accurately record receptor normalization, duplicate-atom conformer
selection, and ligand hydrogen/3D preparation. The report is a local ignored
artifact at `outputs/agent_evaluation/opensandbox_docking_acceptance.json` and
is not committed.

## Post-deployment bounded smoke

After deploying the final timeout/cancellation and failure-semantics changes, a
single non-retried `repeat=1` smoke was run. Its measured scientific run passed
through gVisor and real Vina with 9 poses, best energy `-3.388`, verified
artifact/provenance, 90.924-second latency and successful cleanup. The later
idempotency probe exceeded the fixed 267-second execution deadline, was recorded
as `execution_timeout`, and also cleaned up successfully. The overall smoke is
therefore honestly `failed` with `idempotency_execution_failed`, despite a
scientific run pass rate of 1.0. No retry was used to turn this result green.

This is consistent with other late pre-production observations of intermittent
OpenSandbox command/server-proxy 500/502 responses and variable execution time.
The Broker now terminates these paths with bounded, typed failures and cleanup,
but the OpenSandbox daemon/proxy stability issue remains an explicit production
blocker rather than an application-level success.

After the final review fixes were deployed, one focused real Vina task (without
re-running the full destructive/idempotency probe suite) succeeded with 10
poses, best energy `-3.391`, a present pose artifact and successful cleanup.
There were no running `opensandbox.io/id` containers afterward. This confirms
the refactored success publication path; it does not erase the failed bounded
smoke or the remaining infrastructure stability blocker above.

## Verification

```powershell
$PY -m pytest tests\sandbox_broker -q -p no:cacheprovider
# 1247 passed, 73 skipped

$PY -m compileall -q src scripts deployment\opensandbox\run_docking.py
# passed
```

The deployment validator passed both static and runtime checks after the final
source synchronization and service restart. The formal repeat-3 acceptance
exercised same-key reuse, cooperative cancellation, worker timeout cleanup,
read-only filesystem checks, PID limits, host-data isolation, fixed tool
versions, digest pinning and denied outbound networking with no security
failure. The later bounded smoke result and its idempotency timeout are recorded
separately above and must not be conflated with that earlier passing evidence.

## Operational state and boundaries

- QEMU pre-production runtime: `/opt/medchat/molecular_chat_system`.
- Release source: `/usr/local/src/medchat-release`.
- Active services: OpenSandbox, Sandbox Broker, Temporal worker and firewall.
- Active image digest:
  `sha256:5370d55a6e667bb78c8bf5d24c3c8d797879416032c5f56b8855133e5a820509`.
- OpenSandbox and Broker secrets remain only in root-managed runtime environment
  files; no key, token or raw environment value is present in Git or reports.
- The original mixed workspace was not modified. All edits and commands used
  this isolated worktree; no reset, clean or broad staging command was used.

## Previous handoff: Temporal production canary readiness

Date: 2026-08-25

Branch: `codex/temporal-production-canary-readiness`

Task 11 starting HEAD: `73b17026609df6e14705df728791ba0a93baca6f`

Workspace: isolated task worktree

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
2. Run the isolated live-install runner in its private mount namespace and stage
   the immutable worker generation without activating it. If a flat predecessor
   exists, exercise the allowlisted migration only during the later activation
   window and confirm legacy tmpfiles remains quarantined.
3. Run `systemd-analyze verify` for the Web and worker units, real activation/
   rollback/migration signal tests, mixed lifecycle tests, and the runtime-mask +
   held old `cgroup.kill` descriptor integration test on cgroup v2.
4. Provision runtime-only Compose secrets and both environment files through the
   trusted host process; set an approved positive backup retention policy.
5. As root with an empty inherited environment, render a metrics-relay
   generation from the root-only env files. With its generation override,
   stop/force-recreate only the Prometheus container without starting it so
   Compose creates the dedicated labeled bridge. Confirm Prometheus is not
   running, then activate the relay and require its pinned nginx config
   test/reload to pass. With the base Compose file and published live override,
   start/wait for healthy Temporal and its PostgreSQL/schema dependencies, then
   synchronously run `temporal-namespace` with `--rm --no-deps --no-TTY` and
   require exit code 0. Only then run
   `up -d --wait --wait-timeout 180 prometheus`; its
   remaining dependency closure supplies role-sync and PostgreSQL exporter. Do
   not use `sudo -E`, `--preserve-env`, or loosen env/secret modes.
6. Activate the staged worker generation, then require the Prometheus query
   `up{job="medchat-temporal-worker"}` to return exactly one target with value
   `1`. Only after that gate, start `temporal-ui` and `grafana` with the same base
   and live override. Run promtool config/rules/tests, dashboard, health, queue,
   and alert checks.
7. Use a trusted version-specific PostgreSQL bin directory to create a real
   atomic backup and verify restore into `temporal_verify`; confirm the passed
   `latest-verified.json` marker.
8. At canary 0, run production preflight. It must include deployment, contract
   repeat 3, real Temporal/Vina repeat 3 with verified artifacts/provenance,
   infrastructure, release-blocker alerts, and backup/restore evidence.
9. Promote only `0 -> 5 -> 10 -> 25`. At each nonzero level collect 20 real
   terminal tasks, or observe 24 hours with at least 3, before the next level.

Any missing host action keeps readiness `partial`. Rollback first routes only new
traffic to 0; it does not delete or locally replay Temporal-accepted work.

## Workspace integrity

All commands and edits in this task used the isolated task worktree. No command
was run against, and no file was changed in, the original mixed workspace.
Runtime reports remain ignored and outside the documentation commit.

## OpenSandbox stability hardening pre-soak snapshot (2026-08-30)

- Branch: `codex/opensandbox-stability-hardening`.
- Locally tested source commit: `b4c912d8d5c0562fbbaaa6e47e0e58f95f297ab6`.
- QEMU soak at this checkpoint: `not run`. The Windows host exposes a stopped WSL2 Ubuntu
  distribution, but no QEMU CLI or prepared Ubuntu 24.04 QEMU acceptance host.
  WSL2 is not an allowed substitute for this production gate.
- Runtime report at this checkpoint: not created. No failed report was replaced and no passing
  runtime status is inferred from unit tests.

### Local verification

All commands used `C:\Users\xkx52\.conda\envs\MedChat\python.exe` with the
pytest cache disabled:

```powershell
python -m pytest tests/sandbox_broker/test_opensandbox_client.py tests/sandbox_broker/test_service.py tests/sandbox_broker/test_stability_soak.py -q -p no:cacheprovider
# 436 passed, 2 skipped

python -m pytest tests/sandbox_broker -q -p no:cacheprovider
# 1878 passed, 75 skipped

python -m pytest tests -q -p no:cacheprovider
# 4873 passed, 230 skipped, 9 subtests passed, 6 dependency/deprecation warnings

python -m compileall -q src scripts
# passed with no output

python scripts/validate_opensandbox_deployment.py --static
# opensandbox_deployment_validation=passed
```

The first full-repository run encountered one transient Temporal test-server
download failure and exposed a real stale Temporal worker-unit validator
contract. The download succeeded on focused retry; the validator was aligned
with the hardened Broker dependency and UDS access, after which the complete
repository passed.

Independent review of `c9eed10..0f68b3c` found no Critical issue and three
Important issues: unknown remote sandbox identity could lose cleanup
uncertainty, one raised measured run could prevent later submissions, and a
later run could replace an existing failed report. Commit `58a0cb4` added
regressions and closed all three. Reviewer re-verification found no remaining
Critical or Important issue.

### Historical pre-soak QEMU evidence state

Because the real 30-job suite had not yet run at this checkpoint, the following values were unavailable
and must remain `N/A`: pass rate, cleanup rate, event completeness, p50/p95
latency, phase p95 latency, failure-class distribution, pose-count range,
binding-energy range, Vina/Meeko runtime versions, image digest, and
artifact/hash observations. No docking energy or pose count is reported from
contract tests.

Likewise, real runtime evidence is unavailable for the idempotency sandbox
count, queue saturation count, cancellation/timeout/security probes, and final
running labelled container count. Local tests verify the contracts and failure
preservation only; they do not satisfy the production gate.

### Pre-soak production gate

On the reviewed Ubuntu 24.04 QEMU generation, operators must verify source
identity and service state, then run exactly one gated invocation of
`scripts/run_opensandbox_stability_soak.py --repeat 30`. Promotion remains
blocked until its report is `passed`, all gates are true, and an independent
Docker label query reports zero running labelled containers. A `partial`,
`failed`, `skipped`, Windows, or WSL result is non-promotable. Operators must
preserve the first failed report and must not replace it with a passing rerun.

## OpenSandbox real 30-job QEMU soak (2026-08-30)

- Branch: `codex/opensandbox-stability-hardening`.
- Active QEMU release commit: `79860ec175f0d3235dfd0597f248d7d52788fc76`.
- Ubuntu 24.04, 4 vCPU, approximately 12 GiB RAM, Docker 29.1.3 and gVisor
  `runsc release-20260817.0` were observed. The Broker, OpenSandbox, Temporal
  worker and firewall services remained active after the run.
- Linux deployment exposed two acceptance blockers before the real suite:
  five reviewed assets had Windows-CRLF hashes even though Linux Git checkouts
  use LF, and the documented `medchat-temporal` soak user did not exist and
  could not perform host-firewall validation. Commits `e3f4bde` and `79860ec`
  fix the line-ending contract and document the operator/root execution model.
- Post-run independent review found that an already-existing Windows checkout
  would not automatically rewrite otherwise unchanged assets after the new
  `.gitattributes` rules. Commit `f39bb7d` changes each of the five reviewed
  blobs with a common LF identity marker and updates their pins, forcing safe
  rematerialization on upgrade. This post-run commit passed local static/tests
  but was not deployed to, and does not change the identity of, the completed
  `79860ec` QEMU evidence.
- The first `e3f4bde` invocation failed before submitting a job with
  `deployment_validation_failed`. Its 0600 report remains preserved in the
  prior runtime generation; it was not overwritten or reclassified.
- The reviewed `79860ec` generation passed static/runtime deployment validation
  and executed one fixed 30-job suite. The root-owned 0600 runtime report is
  `outputs/agent_evaluation/opensandbox_stability_soak-79860ec.json` with
  SHA-256 `c66e07c3e9ad4669a8ab703fa57e887d16b8d71b938ba8a0755725692ae3c67f`.

The formal report is honestly `failed`:

```text
repeat=30
report_pass_rate=0.0
scientific_pose_artifacts=18/30
cleanup_rate=0.6
event_completeness_rate=0.0
p50_latency_ms=22781
p95_latency_ms=86924.55
queue_accepted=7
queue_saturated=1
running_labelled_containers=0
security_probe=true
cancellation_probe=false
timeout_probe=false
```

Eighteen measured submissions produced real gVisor/Vina artifacts with valid
SHA-256 values, 8-9 poses and best energies between `-3.286` and `-3.452`.
Twelve submissions did not produce accepted scientific output: five reported
`provider_error`, five `tool_timeout`, and two `invalid_output`. These are real
stability failures and block promotion. The queue gate also failed because only
seven requests succeeded instead of nine, and the cancellation/timeout probes
did not satisfy their contracts. The independent post-run Docker label query
reported zero running OpenSandbox containers.

The report runner also has two confirmed false-negative defects that do not
erase the twelve real failures: it correlates Broker telemetry with the caller
idempotency key instead of the Broker-generated trace ID, so all lifecycle
sequences become `events_incomplete`; and measured observers disable intrusive
version probes without sourcing the Meeko version from the Broker validation
event, so valid pose runs receive `tool_version_invalid` and
`tool_version_mismatch`. Fix these contracts with recorded-report unit tests
before any new reviewed QEMU generation. Do not rerun the `79860ec` report.

Local verification after the deployment fixes:

```powershell
python -m pytest tests/sandbox_broker/test_deployment_assets.py -q -p no:cacheprovider
# 157 passed, 40 skipped

python -m pytest tests/sandbox_broker -q -p no:cacheprovider
# 1883 passed, 75 skipped

python -m compileall -q src scripts
# passed
```

No API key state or value is required for this stage, and no credential was
recorded in source, tests, logs, or this handoff.
