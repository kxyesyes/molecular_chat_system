# Temporal Production Canary Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the existing Temporal docking canary into a single-host Linux production candidate with safe 0/5/10/25 rollout, evidence-gated promotion, Prometheus/Grafana observability, systemd worker isolation, and tested PostgreSQL backup/restore.

**Architecture:** Keep MedChat Web and the scientific docking worker host-native under systemd, while Temporal Server, its dedicated PostgreSQL, Temporal UI, Prometheus, and Grafana run in Docker Compose. Add a strict rollout domain and a read-only observation projector over the existing SQLite task/event/artifact records; promotion consumes only hashed, current, sanitized evidence, and rollback changes only new traffic.

**Tech Stack:** Python 3.10+, SQLite, Temporal Python SDK 1.30.0, prometheus-client 0.26.0, Docker Compose, Temporal Server 1.29.7, Temporal admin-tools 1.29.7, PostgreSQL 16.14, postgres-exporter 0.20.1, Prometheus 3.13.2, Grafana 12.4.8, systemd, pytest/pytest-asyncio, AutoDock Vina.

---

## File map

### Runtime and release logic

- Modify `requirements-agent-temporal.txt`: pin the official Python Prometheus client alongside the existing Temporal SDK.
- Modify `src/task_runtime/config.py`: validate production metrics and latency-baseline settings without exposing addresses or paths.
- Create `src/task_runtime/rollout.py`: own legal canary levels, transition validation, evidence schema, hash verification, and promotion decisions.
- Create `src/task_runtime/observation.py`: normalize real Temporal task history, enforce scientific/runtime gates, compute rates and p50/p95, and emit a redacted report.
- Modify `src/task_runtime/store.py`: provide a bounded, read-only Temporal observation query by terminal time; do not change task mutation semantics.
- Create `src/task_runtime/prometheus_metrics.py`: own fixed-name, fixed-label Prometheus metrics and the loopback exporter lifecycle.
- Modify `src/task_runtime/temporal/activities.py`: report process attempts and terminal outcomes to the optional metrics sink without changing docking results.
- Modify `src/task_runtime/temporal/worker.py`: report readiness, heartbeat, and clean shutdown to the metrics sink.
- Modify `scripts/run_temporal_docking_worker.py`: start the metrics endpoint, inject the metrics sink, and fail startup when production metrics cannot bind.

### Operator commands

- Create `scripts/observe_temporal_canary.py`: read real TaskStore history plus local Prometheus/backup state and write a sanitized observation report.
- Create `scripts/manage_temporal_canary.py`: show status, validate preflight/observation evidence, atomically promote one level, and roll back to zero.
- Create `scripts/run_temporal_production_preflight.py`: combine infrastructure health, contract repeat 3, real Vina repeat 3, alert state, and backup verification into one integrity-hashed local report.
- Create `scripts/backup_temporal_postgres.py`: produce an atomic logical dump plus SHA-256 manifest without exposing credentials.
- Create `scripts/restore_temporal_postgres.py`: verify and restore a dump only into an explicitly different verification database.
- Create `scripts/validate_temporal_deployment.py`: statically validate Compose, systemd, Prometheus, Grafana, environment examples, and secret/path policy on Windows or Linux.

### Deployment and operations

- Create `deployment/temporal/docker-compose.yml`: run PostgreSQL, its read-only metrics exporter, Temporal, Temporal UI, Prometheus, and Grafana on loopback with health checks and named volumes.
- Create `deployment/temporal/env.example`: document non-secret keys and force real secrets to be supplied at runtime.
- Create `deployment/temporal/postgres-init/010-exporter.sh`: create a `pg_monitor` exporter role from a mounted runtime secret without logging it.
- Create `deployment/temporal/prometheus/prometheus.yml`: scrape Temporal, worker, and Prometheus itself.
- Create `deployment/temporal/prometheus/rules/medchat-temporal.yml`: define local blocking alerts.
- Create `deployment/temporal/grafana/provisioning/datasources/prometheus.yml`: provision the Prometheus datasource.
- Create `deployment/temporal/grafana/provisioning/dashboards/dashboard.yml`: provision the dashboard directory.
- Create `deployment/temporal/grafana/dashboards/medchat-temporal-docking.json`: show rollout, health, latency, execution, terminal, artifact, and provenance gates without scientific payloads.
- Create `deployment/medchat-temporal-worker.service`: run exactly one host-native docking worker.
- Modify `deployment/medchat.service`: use the controlled `/etc/medchat/medchat.env` boundary while preserving the existing Web command.
- Create `docs/runbooks/temporal_docking_canary.md`: document installation, startup, preflight, promotion, observation, rollback, backup, recovery, and incident handling.
- Modify `docs/PROJECT_STANDARDS.md`: add Stage 3B deployment and release rules.
- Modify `docs/handoff/latest.md`: record implementation scope, commands, results, limitations, branch, and commits.

### Tests

- Modify `tests/task_runtime/test_temporal_config.py`: production config parsing and safe serialization.
- Create `tests/task_runtime/test_temporal_rollout.py`: rollout state machine, evidence validation, locking, and atomic update behavior.
- Create `tests/task_runtime/test_temporal_observation.py`: sample gates, percentiles, redaction, and report integrity.
- Create `tests/task_runtime/test_temporal_prometheus.py`: metric names, labels, worker/activity hooks, and bind failures.
- Create `tests/test_temporal_operator_scripts.py`: CLI behavior for observe, manage, preflight, backup, and restore.
- Create `tests/test_temporal_deployment_assets.py`: Compose/systemd/Prometheus/Grafana/static security contract.
- Modify `tests/task_runtime/test_temporal_acceptance.py`: production evidence projection and no-regression checks.

This plan does not modify molecular docking algorithms, `MolecularDockingService`, Vina parameters, Validator rules, non-docking Agent workflows, frontend behavior, or the MedChat TaskStore database engine.

## Task 1: Add strict production runtime settings

**Files:**
- Modify: `requirements-agent-temporal.txt`
- Modify: `src/task_runtime/config.py`
- Test: `tests/task_runtime/test_temporal_config.py`

- [ ] **Step 1: Write failing production-config tests**

Append tests that require loopback metrics binding, a bounded port, a positive latency baseline, and safe serialization:

```python
def test_production_metrics_defaults_are_loopback_and_safe(monkeypatch):
    for name in (
        "MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS",
        "MEDCHAT_TEMPORAL_WORKER_METRICS_PORT",
        "MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS",
        "MEDCHAT_TEMPORAL_BACKUP_STATE",
    ):
        monkeypatch.delenv(name, raising=False)
    config = TaskRuntimeConfig.from_env()
    assert config.worker_metrics_address == "127.0.0.1"
    assert config.worker_metrics_port == 9465
    assert config.baseline_p95_seconds == 40.0
    assert config.backup_state_path.name == "latest-verified.json"
    safe = config.to_safe_dict()
    assert "worker_metrics_address" not in safe
    assert safe["worker_metrics_address_configured"] is False
    assert safe["worker_metrics_port"] == 9465
    assert safe["baseline_p95_seconds"] == 40.0
    assert "backup_state_path" not in safe
    assert safe["backup_state_path_configured"] is False


@pytest.mark.parametrize(
    ("name", "value", "warning"),
    [
        ("MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS", "0.0.0.0", "invalid_worker_metrics_address"),
        ("MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS", "metrics.internal", "invalid_worker_metrics_address"),
        ("MEDCHAT_TEMPORAL_WORKER_METRICS_PORT", "0", "invalid_worker_metrics_port"),
        ("MEDCHAT_TEMPORAL_WORKER_METRICS_PORT", "65536", "invalid_worker_metrics_port"),
        ("MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS", "nan", "invalid_temporal_baseline_p95"),
        ("MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS", "0", "invalid_temporal_baseline_p95"),
        ("MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS", "61", "invalid_temporal_baseline_p95"),
    ],
)
def test_invalid_production_metrics_settings_fail_closed(monkeypatch, name, value, warning):
    monkeypatch.setenv("MEDCHAT_TASK_BACKEND", "temporal_canary")
    monkeypatch.setenv("MEDCHAT_TEMPORAL_CANARY_PERCENT", "5")
    monkeypatch.setenv(name, value)
    config = TaskRuntimeConfig.from_env()
    assert (config.backend, config.canary_percent) == ("local", 0)
    assert warning in config.warnings
```

- [ ] **Step 2: Run the tests and verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_config.py -q -p no:cacheprovider
```

Expected: the new tests fail because `TaskRuntimeConfig` has no metrics/baseline fields.

- [ ] **Step 3: Pin the metrics dependency and implement validation**

Set `requirements-agent-temporal.txt` exactly to:

```text
temporalio==1.30.0
prometheus-client==0.26.0
```

Extend `TaskRuntimeConfig` with these fields and safe flags:

```python
worker_metrics_address: str = field(repr=False)
worker_metrics_port: int
baseline_p95_seconds: float
backup_state_path: Path = field(repr=False)
_worker_metrics_address_configured: bool = field(default=False, repr=False, compare=False)
_backup_state_path_configured: bool = field(default=False, repr=False, compare=False)
```

Add parsing with the following exact rules:

```python
metrics_address_text, metrics_address_configured = _read_environment(
    "MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS", "127.0.0.1"
)
metrics_port_text = os.getenv("MEDCHAT_TEMPORAL_WORKER_METRICS_PORT", "9465")
baseline_text = os.getenv("MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS", "40")
backup_state_text, backup_state_configured = _read_environment(
    "MEDCHAT_TEMPORAL_BACKUP_STATE", "scratch/temporal_backups/latest-verified.json"
)

metrics_address_valid = metrics_address_text in {"127.0.0.1", "::1"}
metrics_port_valid = _CANONICAL_PORT.fullmatch(metrics_port_text) is not None and int(metrics_port_text) <= 65535
try:
    baseline_p95_seconds = float(baseline_text)
except (TypeError, ValueError, OverflowError):
    baseline_p95_seconds = 0.0
baseline_valid = math.isfinite(baseline_p95_seconds) and 0 < baseline_p95_seconds <= 60
```

Resolve the backup-state file from project root when relative. Accept only a regular file or a not-yet-created basename `latest-verified.json` whose nearest existing parent is a directory; reject dot components, symlink parents, filesystem root, project root, and files beneath the repository outside `scratch/temporal_backups`. Append stable warning codes in `_WARNING_ORDER`, fail closed through the existing warning path, validate direct construction in `__post_init__`, and expose only `worker_metrics_port`, `baseline_p95_seconds`, `worker_metrics_address_configured`, and `backup_state_path_configured` from `to_safe_dict()`.

- [ ] **Step 4: Verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pip install -r requirements-agent-temporal.txt
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pip check
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_config.py -q -p no:cacheprovider
```

Expected: `pip check` succeeds and all temporal-config tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- requirements-agent-temporal.txt src/task_runtime/config.py tests/task_runtime/test_temporal_config.py
git commit -m "feat(runtime): validate temporal production settings"
```

## Task 2: Define rollout levels and evidence contracts

**Files:**
- Create: `src/task_runtime/rollout.py`
- Test: `tests/task_runtime/test_temporal_rollout.py`

- [ ] **Step 1: Write failing state-machine and evidence tests**

```python
from datetime import datetime, timedelta, timezone
import hashlib
import json

import pytest

from src.task_runtime.rollout import (
    EvidenceError,
    RolloutEvidence,
    validate_transition,
)


def _evidence(current_level: int, stage: str = "observation") -> RolloutEvidence:
    generated = datetime(2026, 8, 21, 10, 0, tzinfo=timezone.utc)
    payload = {
        "schema_version": 1,
        "stage": stage,
        "status": "passed",
        "current_level": current_level,
        "generated_at": generated.isoformat(),
        "gates": {"all_required_gates": "passed"},
    }
    digest = hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    return RolloutEvidence.from_payload(payload, digest)


@pytest.mark.parametrize("current,target", [(0, 10), (0, 25), (5, 25), (10, 5), (25, 10)])
def test_rollout_rejects_skips_and_nonzero_downgrades(current, target):
    with pytest.raises(EvidenceError):
        validate_transition(current, target, _evidence(current))


@pytest.mark.parametrize("current", [5, 10, 25])
def test_any_nonzero_level_can_rollback_to_zero_without_evidence(current):
    decision = validate_transition(current, 0, None)
    assert (decision.allowed, decision.reason) == (True, "rollback_to_zero")


def test_zero_to_five_requires_current_preflight_evidence():
    now = datetime(2026, 8, 21, 10, 5, tzinfo=timezone.utc)
    decision = validate_transition(0, 5, _evidence(0, "preflight"), now=now)
    assert decision.allowed is True


def test_promotion_rejects_failed_stale_or_hash_mismatched_evidence():
    with pytest.raises(EvidenceError):
        RolloutEvidence.from_payload({"status": "failed"}, "0" * 64)
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_rollout.py -q -p no:cacheprovider
```

Expected: collection fails because `src.task_runtime.rollout` does not exist.

- [ ] **Step 3: Implement the rollout domain**

Create immutable public contracts:

```python
ALLOWED_LEVELS = (0, 5, 10, 25)
MAX_EVIDENCE_AGE = timedelta(hours=1)


class EvidenceError(ValueError):
    pass


@dataclass(frozen=True)
class RolloutEvidence:
    schema_version: int
    stage: str
    status: str
    current_level: int
    generated_at: datetime
    gates: dict[str, str]
    sha256: str

    @classmethod
    def from_payload(cls, payload: object, expected_sha256: str) -> "RolloutEvidence":
        if not isinstance(payload, dict):
            raise EvidenceError("invalid evidence payload")
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        actual = hashlib.sha256(canonical).hexdigest()
        if not hmac.compare_digest(actual, expected_sha256):
            raise EvidenceError("evidence hash mismatch")
        if payload.get("schema_version") != 1 or payload.get("status") != "passed":
            raise EvidenceError("evidence is not passed")
        stage = payload.get("stage")
        if stage not in {"preflight", "observation"}:
            raise EvidenceError("invalid evidence stage")
        level = payload.get("current_level")
        if type(level) is not int or level not in ALLOWED_LEVELS:
            raise EvidenceError("invalid evidence level")
        generated = datetime.fromisoformat(str(payload.get("generated_at")))
        if generated.tzinfo is None:
            raise EvidenceError("evidence timestamp must be timezone-aware")
        gates = payload.get("gates")
        if not isinstance(gates, dict) or gates.get("all_required_gates") != "passed":
            raise EvidenceError("required gates did not pass")
        return cls(1, stage, "passed", level, generated, dict(gates), actual)


@dataclass(frozen=True)
class RolloutDecision:
    allowed: bool
    reason: str
    current_level: int
    target_level: int
```

`validate_transition()` must reject booleans, levels outside `ALLOWED_LEVELS`, same-level writes, skips, stale/future evidence, wrong evidence level, `preflight` evidence outside 0→5, and `observation` evidence outside 5→10 or 10→25. Only nonzero→0 bypasses evidence.

- [ ] **Step 4: Verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_rollout.py -q -p no:cacheprovider
```

Expected: all rollout-domain tests pass.

- [ ] **Step 5: Commit**

```powershell
git add -- src/task_runtime/rollout.py tests/task_runtime/test_temporal_rollout.py
git commit -m "feat(runtime): enforce temporal rollout gates"
```

## Task 3: Build the real-history observation projector

**Files:**
- Create: `src/task_runtime/observation.py`
- Modify: `src/task_runtime/store.py`
- Create: `tests/task_runtime/test_temporal_observation.py`
- Modify: `tests/task_runtime/test_task_store.py`

- [ ] **Step 1: Write failing observation and query tests**

```python
from datetime import datetime, timedelta, timezone

from src.task_runtime.observation import ObservationPolicy, build_observation_report


def _sample(index: int, latency: float = 30.0) -> dict:
    return {
        "task_fingerprint": f"task-{index:02d}",
        "workflow_fingerprint": f"workflow-{index:02d}",
        "status": "succeeded",
        "attempt": 1,
        "terminal_event_count": 1,
        "pose_count": 10,
        "binding_energy_valid": True,
        "artifact_exists": True,
        "artifact_hash_matches": True,
        "provenance_complete": True,
        "demo_mode": False,
        "fallback_used": False,
        "latency_seconds": latency,
        "error_code": None,
    }


def test_twenty_real_tasks_pass_all_observation_gates():
    end = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    report = build_observation_report(
        [_sample(index) for index in range(20)],
        current_level=5,
        window_start=end - timedelta(hours=2),
        window_end=end,
        worker_health={"available": True, "age_seconds": 5.0},
        infrastructure={"temporal": True, "namespace": True, "queue": True},
        blocking_alerts=[],
        backup_verified=True,
        policy=ObservationPolicy(baseline_p95_seconds=40.0),
    )
    assert report["status"] == "passed"
    assert report["sample_count"] == 20
    assert report["latency_seconds"] == {"p50": 30.0, "p95": 30.0}
    assert report["gates"]["all_required_gates"] == "passed"


def test_three_tasks_need_a_complete_twenty_four_hour_window():
    end = datetime(2026, 8, 21, 12, 0, tzinfo=timezone.utc)
    partial = build_observation_report(
        [_sample(index) for index in range(3)],
        current_level=5,
        window_start=end - timedelta(hours=23, minutes=59),
        window_end=end,
        worker_health={"available": True, "age_seconds": 1.0},
        infrastructure={"temporal": True, "namespace": True, "queue": True},
        blocking_alerts=[],
        backup_verified=True,
        policy=ObservationPolicy(40.0),
    )
    assert partial["status"] == "partial"
    assert partial["gates"]["sample_window"] == "insufficient_evidence"


def test_scientific_or_runtime_violation_is_failed_and_redacted():
    samples = [_sample(index) for index in range(20)]
    samples[0].update({"attempt": 2, "terminal_event_count": 2, "artifact_hash_matches": False})
    samples[0]["prompt"] = "private payload"
    report = build_observation_report(
        samples,
        current_level=5,
        window_start=datetime(2026, 8, 21, 1, 0, tzinfo=timezone.utc),
        window_end=datetime(2026, 8, 21, 2, 0, tzinfo=timezone.utc),
        worker_health={"available": True, "age_seconds": 1.0},
        infrastructure={"temporal": True, "namespace": True, "queue": True},
        blocking_alerts=[],
        backup_verified=True,
        policy=ObservationPolicy(40.0),
    )
    assert report["status"] == "failed"
    serialized = json.dumps(report, ensure_ascii=False)
    assert "private payload" not in serialized
    assert report["gates"]["single_vina_attempt"] == "failed"
```

Add a store test creating local and Temporal terminal rows on both sides of a window, then assert `list_temporal_observation(start, end)` returns only Temporal docking rows whose `finished_at` is inside the closed-open interval `[start, end)` in ascending `finished_at, task_id` order.

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_observation.py tests\task_runtime\test_task_store.py -q -p no:cacheprovider
```

Expected: observation import and store method fail.

- [ ] **Step 3: Implement bounded store projection**

Add this method to `TaskStore`:

```python
def list_temporal_observation(
    self,
    window_start: datetime | str,
    window_end: datetime | str,
    *,
    limit: int = 10000,
) -> list[TaskRecord]:
    start = _as_timestamp(window_start)
    end = _as_timestamp(window_end)
    if start >= end:
        raise ValueError("invalid observation window")
    if type(limit) is not int or not 1 <= limit <= 10000:
        raise ValueError("invalid observation limit")
    with connection(self.db_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE backend = 'temporal' AND task_type = 'docking'
              AND finished_at >= ? AND finished_at < ?
            ORDER BY finished_at ASC, task_id ASC LIMIT ?
            """,
            (start, end, limit),
        ).fetchall()
    return [TaskRecord.from_row(row) for row in rows]
```

- [ ] **Step 4: Implement strict observation gates**

Create:

```python
@dataclass(frozen=True)
class ObservationPolicy:
    baseline_p95_seconds: float
    max_failure_rate: float = 0.05
    worker_stale_seconds: float = 60.0
    max_absolute_p95_seconds: float = 60.0
    min_task_count: int = 20
    min_window_task_count: int = 3
    min_window_hours: float = 24.0


def percentile(values: Sequence[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    return ordered[max(0, math.ceil(len(ordered) * fraction) - 1)]
```

`build_observation_report()` must copy only allowlisted fields, fingerprint identifiers with SHA-256, classify failed scientific/runtime gates before insufficient sample gates, require failure rate ≤ 0.05, require p95 ≤ both `1.5 * baseline` and 60 seconds, and set:

```python
overall = (
    "failed" if "failed" in gates.values()
    else "partial" if "insufficient_evidence" in gates.values()
    else "passed"
)
gates["all_required_gates"] = overall
```

Return `schema_version=1`, `stage="observation"`, timezone-aware window timestamps, counts/rates, p50/p95, stable error-code distribution, worker/infrastructure summaries, blocking alert names, backup status, redacted task rows, and a canonical SHA-256 computed without the `sha256` field.

- [ ] **Step 5: Verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_observation.py tests\task_runtime\test_task_store.py -q -p no:cacheprovider
```

Expected: all observation and store tests pass.

- [ ] **Step 6: Commit**

```powershell
git add -- src/task_runtime/observation.py src/task_runtime/store.py tests/task_runtime/test_temporal_observation.py tests/task_runtime/test_task_store.py
git commit -m "feat(runtime): project temporal canary evidence"
```

## Task 4: Add the safe canary manager and observer CLIs

**Files:**
- Create: `scripts/manage_temporal_canary.py`
- Create: `scripts/observe_temporal_canary.py`
- Create: `tests/test_temporal_operator_scripts.py`

- [ ] **Step 1: Write failing CLI and atomic-write tests**

```python
def test_promote_updates_only_percent_atomically(tmp_path):
    env_file = tmp_path / "medchat.env"
    env_file.write_text(
        "MEDCHAT_TASK_BACKEND=temporal_canary\n"
        "MEDCHAT_TEMPORAL_CANARY_PERCENT=5\n"
        "SAFE_UNRELATED=value\n",
        encoding="utf-8",
    )
    evidence = write_passed_observation(tmp_path, current_level=5)
    result = run_manage(
        "promote", "--env-file", str(env_file), "--to", "10",
        "--evidence", str(evidence), "--reason", "observed twenty tasks",
    )
    assert result.returncode == 0
    assert env_file.read_text(encoding="utf-8") == (
        "MEDCHAT_TASK_BACKEND=temporal_canary\n"
        "MEDCHAT_TEMPORAL_CANARY_PERCENT=10\n"
        "SAFE_UNRELATED=value\n"
    )
    assert "SAFE_UNRELATED" not in result.stdout


def test_manage_rejects_duplicate_key_symlink_and_force_flag(tmp_path):
    duplicate = tmp_path / "duplicate.env"
    duplicate.write_text(
        "MEDCHAT_TEMPORAL_CANARY_PERCENT=5\nMEDCHAT_TEMPORAL_CANARY_PERCENT=10\n",
        encoding="utf-8",
    )
    assert run_manage("status", "--env-file", str(duplicate)).returncode != 0
    linked = tmp_path / "linked.env"
    linked.symlink_to(duplicate)
    assert run_manage("status", "--env-file", str(linked)).returncode != 0
    assert run_manage("promote", "--force").returncode != 0


def test_rollback_to_zero_never_deletes_task_data(tmp_path):
    env_file = write_env(tmp_path, level=25)
    result = run_manage(
        "rollback", "--env-file", str(env_file), "--reason", "artifact alert"
    )
    assert result.returncode == 0
    assert "MEDCHAT_TEMPORAL_CANARY_PERCENT=0" in env_file.read_text(encoding="utf-8")
```

Add observer tests with a temporary TaskStore, mocked loopback Prometheus response, and verified-backup marker. Assert that unavailable Prometheus yields `partial`, firing blocking alerts yield `failed`, and JSON output never contains the database path, input SMILES, prompt, DSN, or environment values.

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_operator_scripts.py -q -p no:cacheprovider
```

Expected: both scripts are missing.

- [ ] **Step 3: Implement secure environment-file updates**

In `manage_temporal_canary.py`, expose:

```python
def read_rollout_config(path: Path) -> tuple[list[str], int]:
    file_stat = path.lstat()
    if stat.S_ISLNK(file_stat.st_mode) or not stat.S_ISREG(file_stat.st_mode):
        raise ValueError("rollout config must be a regular file")
    if os.name == "posix" and file_stat.st_mode & 0o022:
        raise ValueError("rollout config is group/world writable")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    matches = [line for line in lines if line.startswith("MEDCHAT_TEMPORAL_CANARY_PERCENT=")]
    if len(matches) != 1:
        raise ValueError("rollout percent must appear exactly once")
    raw = matches[0].split("=", 1)[1].strip()
    if raw not in {"0", "5", "10", "25"}:
        raise ValueError("unsupported rollout percent")
    return lines, int(raw)
```

Use an adjacent `.lock` file with `msvcrt.locking` on Windows and `fcntl.flock` on POSIX. Re-read after obtaining the lock. Write a unique adjacent temporary file using mode `0o600`, flush, `os.fsync`, preserve original mode/owner where supported, then `os.replace`. Remove the temporary file on every exception. Never print unrelated environment lines.

CLI subcommands are exactly:

```text
status --env-file PATH
promote --env-file PATH --to {5,10,25} --evidence REPORT --reason TEXT
rollback --env-file PATH --reason TEXT
```

`promote` loads the report's top-level `sha256`, removes that field before canonical hash verification, and calls `validate_transition()`. `rollback` always targets zero and never opens TaskStore or artifact paths.

- [ ] **Step 4: Implement read-only observation CLI**

The observer CLI accepts:

```text
--db-path PATH
--level {5,10,25}
--window-start ISO8601
--window-end ISO8601
--prometheus-url http://127.0.0.1:9090
--backup-state PATH
--baseline-p95-seconds FLOAT
--output PATH
```

Reject non-loopback Prometheus URLs. Query `/api/v1/alerts` and `/api/v1/query` with a five-second timeout; map only allowlisted alert names and booleans into `build_observation_report()`. Read `latest-verified.json`, require `status="passed"`, a SHA-256 dump hash, and `verified_at` no older than 24 hours. Write reports through a temporary file plus `os.replace`, and print only `status`, `sample_count`, `current_level`, and report hash.

- [ ] **Step 5: Verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_operator_scripts.py -q -p no:cacheprovider
```

Expected: all operator-script tests pass.

- [ ] **Step 6: Commit**

```powershell
git add -- scripts/manage_temporal_canary.py scripts/observe_temporal_canary.py tests/test_temporal_operator_scripts.py
git commit -m "feat(runtime): manage evidence-gated canary rollout"
```

## Task 5: Export fixed-cardinality worker metrics

**Files:**
- Create: `src/task_runtime/prometheus_metrics.py`
- Modify: `src/task_runtime/temporal/activities.py`
- Modify: `src/task_runtime/temporal/worker.py`
- Modify: `scripts/run_temporal_docking_worker.py`
- Create: `tests/task_runtime/test_temporal_prometheus.py`
- Modify: `tests/task_runtime/test_temporal_workflow.py`

- [ ] **Step 1: Write failing metrics and lifecycle tests**

```python
from prometheus_client import CollectorRegistry, generate_latest

from src.task_runtime.prometheus_metrics import TemporalWorkerMetrics


def test_metrics_have_fixed_names_and_no_private_labels():
    registry = CollectorRegistry()
    metrics = TemporalWorkerMetrics(registry)
    metrics.set_ready(True)
    metrics.configure_release(canary_percent=5, baseline_p95_seconds=40.0)
    metrics.record_backup_verification(1_787_300_000.0)
    metrics.record_worker_heartbeat()
    metrics.activity_started("docking")
    metrics.activity_finished("docking", 12.5)
    metrics.terminal_projected("docking", "succeeded")
    rendered = generate_latest(registry).decode("utf-8")
    assert "medchat_temporal_worker_ready 1.0" in rendered
    assert "medchat_temporal_canary_percent 5.0" in rendered
    assert "medchat_temporal_baseline_p95_seconds 40.0" in rendered
    assert 'task_type="docking"' in rendered
    for forbidden in ("task_id", "trace_id", "workflow_id", "smiles", "prompt", "path"):
        assert forbidden not in rendered.lower()


def test_metrics_reject_unregistered_dimensions():
    metrics = TemporalWorkerMetrics(CollectorRegistry())
    with pytest.raises(ValueError):
        metrics.activity_finished("private-task", 1.0)
    with pytest.raises(ValueError):
        metrics.terminal_projected("docking", "invented")


@pytest.mark.asyncio
async def test_worker_sets_ready_only_while_polling(fake_worker, store):
    metrics = RecordingMetrics()
    await exercise_worker_until_shutdown(fake_worker, store, metrics)
    assert metrics.ready_transitions == [True, False]


@pytest.mark.asyncio
async def test_activity_records_one_attempt_and_one_terminal(fake_dependencies):
    metrics = RecordingMetrics()
    activities = TemporalDockingActivities(*fake_dependencies, metrics=metrics)
    result = await activities._run_docking_activity(valid_payload(), attempt=1, **callbacks())
    await activities._project_terminal(terminal_payload(result))
    assert metrics.attempts == ["docking"]
    assert len(metrics.durations) == 1
    assert metrics.terminals == [("docking", "succeeded")]
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_prometheus.py tests\task_runtime\test_temporal_workflow.py -q -p no:cacheprovider
```

Expected: metrics module and injected constructor arguments are missing.

- [ ] **Step 3: Implement the metrics sink**

Create a `TemporalWorkerMetrics` registry with no dynamic labels beyond fixed enums:

```python
TASK_TYPES = frozenset({"docking"})
TERMINAL_STATUSES = frozenset({"succeeded", "failed", "canceled", "timed_out"})


class TemporalWorkerMetrics:
    def __init__(self, registry: CollectorRegistry | None = None) -> None:
        self.registry = registry or CollectorRegistry()
        self.ready = Gauge("medchat_temporal_worker_ready", "Worker polling readiness", registry=self.registry)
        self.last_heartbeat = Gauge(
            "medchat_temporal_worker_last_heartbeat_timestamp_seconds",
            "Last process heartbeat timestamp",
            registry=self.registry,
        )
        self.active = Gauge(
            "medchat_temporal_active_tasks", "Active docking tasks",
            labelnames=("task_type",), registry=self.registry,
        )
        self.attempts = Counter(
            "medchat_temporal_docking_process_attempts_total", "Docking process attempts",
            labelnames=("task_type",), registry=self.registry,
        )
        self.terminals = Counter(
            "medchat_temporal_task_terminal_total", "Terminal task projections",
            labelnames=("task_type", "status"), registry=self.registry,
        )
        self.duration = Histogram(
            "medchat_temporal_task_duration_seconds", "Temporal task duration",
            labelnames=("task_type",), buckets=(5, 10, 20, 30, 45, 60, 90, 120, 300, 600),
            registry=self.registry,
        )
        self.canary_percent = Gauge(
            "medchat_temporal_canary_percent", "Configured Temporal canary percentage",
            registry=self.registry,
        )
        self.baseline_p95 = Gauge(
            "medchat_temporal_baseline_p95_seconds", "Configured real-docking p95 baseline",
            registry=self.registry,
        )
        self.backup_verified = Gauge(
            "medchat_temporal_backup_verified_timestamp_seconds", "Latest verified backup timestamp",
            registry=self.registry,
        )
        self.duplicate_vina = Counter(
            "medchat_temporal_duplicate_vina_execution_total", "Attempts above one",
            registry=self.registry,
        )
        self.terminal_violations = Counter(
            "medchat_temporal_terminal_invariant_violation_total", "Terminal projection conflicts",
            registry=self.registry,
        )
        self.artifact_failures = Counter(
            "medchat_temporal_artifact_validation_failure_total", "Invalid docking artifacts",
            registry=self.registry,
        )
        self.provenance_failures = Counter(
            "medchat_temporal_provenance_validation_failure_total", "Invalid scientific provenance",
            registry=self.registry,
        )
```

Methods validate exact task/status enums, reject booleans/non-finite/negative durations, maintain active-task balance, and expose `start_loopback_server(address, port)` using `prometheus_client.start_http_server`. `configure_release()` accepts only 0/5/10/25 and a baseline in `(0, 60]`. `refresh_backup_verification(path)` parses only `status`, `verified_at`, and SHA-256 from the configured marker and sets the timestamp gauge to zero when the marker is missing or invalid.

- [ ] **Step 4: Inject metrics without changing scientific results**

Add `metrics: TemporalWorkerMetrics | None = None` to `TemporalDockingActivities.__init__` and `run_temporal_worker()`. Increment attempts immediately before creating the docking task; increment the duplicate counter when `attempt > 1`. Measure docking Activity duration with `time.monotonic()` and balance `active` in `try/finally`, independent of scientific success. Record terminal only after `project_temporal_terminal` succeeds; an idempotent duplicate projection must not increment a second time, while a conflicting terminal increments the terminal-invariant counter. Failed terminal payloads with `DOCKING_ARTIFACT_INVALID` increment artifact failures; succeeded payloads lacking strict non-demo/non-fallback provenance increment provenance failures before being rejected. Worker readiness changes to true only after `worker.run()` starts and false in `finally`; process heartbeat updates both SQLite and the process metric and refreshes the backup marker.

In `scripts/run_temporal_docking_worker.py`, construct one metrics object, bind it to `config.worker_metrics_address/config.worker_metrics_port` before connecting to Temporal, call `configure_release(config.canary_percent, config.baseline_p95_seconds)`, refresh `config.backup_state_path`, and pass the same object to activities and worker. A bind error exits nonzero before worker polling.

- [ ] **Step 5: Verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_prometheus.py tests\task_runtime\test_temporal_workflow.py -q -p no:cacheprovider
```

Expected: metric-contract and existing workflow tests pass.

- [ ] **Step 6: Commit**

```powershell
git add -- src/task_runtime/prometheus_metrics.py src/task_runtime/temporal/activities.py src/task_runtime/temporal/worker.py scripts/run_temporal_docking_worker.py tests/task_runtime/test_temporal_prometheus.py tests/task_runtime/test_temporal_workflow.py
git commit -m "feat(runtime): expose temporal worker metrics"
```

## Task 6: Add the single-host Temporal Compose stack

**Files:**
- Create: `deployment/temporal/docker-compose.yml`
- Create: `deployment/temporal/env.example`
- Create: `deployment/temporal/postgres-init/010-exporter.sh`
- Create: `deployment/temporal/scripts/temporal-schema-setup.sh`
- Create: `deployment/temporal/scripts/temporal-server-entrypoint.sh`
- Create: `deployment/temporal/scripts/temporal-namespace-setup.sh`
- Create: `deployment/temporal/scripts/rotate-exporter-password.sh`
- Create: `tests/test_temporal_deployment_assets.py`
- Create: `tests/test_temporal_deployment_integration.py`

- [ ] **Step 1: Write failing Compose security tests**

```python
def test_compose_uses_pinned_images_loopback_ports_and_healthchecks():
    compose = load_yaml("deployment/temporal/docker-compose.yml")
    services = compose["services"]
    assert services["postgres"]["image"] == "postgres:16.14-alpine3.23"
    assert services["postgres-exporter"]["image"] == "ghcr.io/prometheus-community/postgres-exporter:v0.20.1"
    assert services["temporal"]["image"] == "temporalio/server:1.29.7"
    assert services["temporal-schema"]["image"] == "temporalio/admin-tools:1.29.7"
    assert services["temporal-namespace"]["image"] == "temporalio/admin-tools:1.29.7"
    assert services["temporal-ui"]["image"] == "temporalio/ui:2.53.1"
    assert services["prometheus"]["image"] == "prom/prometheus:v3.13.2"
    assert services["grafana"]["image"] == "grafana/grafana:12.4.8-ubuntu"
    for name, service in services.items():
        assert not service["image"].endswith(":latest")
        if name in {"temporal-schema", "temporal-namespace", "temporal-exporter-role-sync"}:
            assert service.get("restart", "").startswith("on-failure:")
        else:
            assert "healthcheck" in service
            assert service.get("restart") == "unless-stopped"
        for mapping in service.get("ports", []):
            assert str(mapping).startswith("127.0.0.1:")


def test_compose_has_no_embedded_secret_or_repo_wide_mount():
    text = Path("deployment/temporal/docker-compose.yml").read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=${" not in text
    assert "POSTGRES_PWD=${" not in text
    assert "POSTGRES_PASSWORD_FILE=/run/secrets/temporal_postgres_password" in text
    assert "GF_SECURITY_ADMIN_PASSWORD__FILE=/run/secrets/grafana_admin_password" in text
    assert "/var/run/docker.sock" not in text
    assert "../..:/" not in text


def test_postgres_exporter_role_is_runtime_secret_and_read_only():
    script = Path("deployment/temporal/postgres-init/010-exporter.sh").read_text(encoding="utf-8")
    assert "set -eu" in script
    assert "set -x" not in script
    assert "/run/secrets/temporal_postgres_exporter_password" in script
    assert "DROP ROLE IF EXISTS temporal_exporter" in script
    assert "GRANT pg_monitor TO temporal_exporter" in script
    assert "NOBYPASSRLS" in script
    assert "REASSIGN OWNED" not in script
    assert "DROP OWNED" not in script
    assert "pg_auth_members" not in script
    assert "aclexplode" not in script
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_deployment_assets.py -q -p no:cacheprovider
```

Expected: the previous six-service stack fails the server/admin-tools job, file-secret,
network-segmentation, rotation, and bounded-logging contracts.

- [ ] **Step 3: Create the pinned Compose stack**

Define nine services: six long-running services named `postgres`,
`postgres-exporter`, `temporal`, `temporal-ui`, `prometheus`, and
`grafana`, plus one-shot jobs named `temporal-schema`,
`temporal-namespace`, and `temporal-exporter-role-sync`. Pin the Temporal
server to `temporalio/server:1.29.7` and both Temporal administrative jobs to
`temporalio/admin-tools:1.29.7`. Use named volumes
`temporal-postgres-data`, `prometheus-data`, and `grafana-data`. Bind host
ports as:

```yaml
ports:
  - "127.0.0.1:5433:5432"   # PostgreSQL
  - "127.0.0.1:7233:7233"   # Temporal
  - "127.0.0.1:8233:8080"   # Temporal UI
  - "127.0.0.1:9090:9090"   # Prometheus
  - "127.0.0.1:3000:3000"   # Grafana
```

The schema job depends on healthy PostgreSQL, creates missing databases
idempotently, validates database/user names as ASCII PostgreSQL identifiers
before invoking any client, and runs the matching main and visibility schema
migrations. The server starts only after that job completes successfully. The
namespace job depends on healthy Temporal, idempotently creates or confirms
`default`, then always runs `temporal operator namespace update --retention` so
configured retention drift converges. The role-sync job is an explicit one-shot
available on every deployment after PostgreSQL is healthy; it validates both
database names and the administrator role, then runs `DROP ROLE IF EXISTS
temporal_exporter` without `REASSIGN OWNED` or `DROP OWNED`. PostgreSQL's own
cluster-wide dependency check is the only fail-closed ownership/ACL gate: any
remaining object or privilege dependency aborts the job with a stable error,
while role memberships are removed by DROP ROLE. A successful reset creates the
fixed login role with `NOSUPERUSER NOCREATEDB NOCREATEROLE NOINHERIT
NOREPLICATION NOBYPASSRLS`, then grants only `pg_monitor` with membership options
`INHERIT TRUE`, `SET FALSE`, and `ADMIN FALSE`. The stack relies on PostgreSQL's
default PUBLIC database CONNECT, avoiding a self-created ACL dependency that
would block the next reset. `postgres-exporter` starts only
after role-sync succeeds and uses the dynamic `${TEMPORAL_POSTGRES_DB:-temporal}`
database. Its healthcheck fetches metrics with a finite timeout and requires
`pg_up 1`; an HTTP 200 alone is not healthy.

PostgreSQL, Grafana, server, schema, and role-sync credentials come only from
Compose secret files. PostgreSQL uses `POSTGRES_PASSWORD_FILE`; Grafana uses
`GF_SECURITY_ADMIN_PASSWORD__FILE`; wrappers read secrets inside the
container when an image lacks native file support. No password is present in a
Compose service environment value or psql argv. Compose project isolation owns
all network names: retain internal `database` and Temporal API `temporal`
networks, and use separate `temporal-metrics`, `postgres-metrics`, and
`prometheus-grafana` planes. Grafana reaches only Prometheus, and the UI does not
join a metrics network. Every service uses bounded `json-file` logging. Long-running
services use `unless-stopped` plus healthchecks; one-shot jobs use bounded
`on-failure:N` restart and `service_completed_successfully` dependencies.
Prometheus and Grafana retain only their exact read-only configuration mounts.
`env.example` declares empty `*_FILE` path keys and non-secret defaults, plus the
regular-file, no-symlink, no-trailing-newline, 1024-byte, and host-permission
contract. On POSIX the deployment owner controls a mode `0700` parent directory
whose single-value secret files are mode `0444`; the protected parent blocks
other host users from traversing to the files while non-root containers can read
their individual read-only mounts. Local Compose `file:` secrets use bind mounts,
so service-level `uid`, `gid`, and `mode` cannot repair host file permissions and
must not be treated as a substitute for this host contract. Docker Compose
`>= 2.17` is required for long-form dependency restart.
The only supported exporter password rotation path is
`deployment/temporal/scripts/rotate-exporter-password.sh`: after replacing the
protected file, it reruns role-sync to completion and force-recreates
`postgres-exporter` so the process rereads the secret. Before changing services,
the script verifies `docker compose up --help` exposes both `--wait` and
`--wait-timeout`; unsupported clients fail with a stable error. Recreate uses a
120-second bounded wait and reports success only after the exporter's `pg_up 1`
healthcheck passes. The complete operator runbook remains Task 11 scope.

- [ ] **Step 4: Verify GREEN and Compose rendering when Docker is available**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_deployment_assets.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_deployment_integration.py -q -p no:cacheprovider -rs
$env:TEMPORAL_POSTGRES_PASSWORD_FILE = "<temporary non-secret file outside the repository>"
$env:TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE = "<temporary non-secret file outside the repository>"
$env:GRAFANA_ADMIN_PASSWORD_FILE = "<temporary non-secret file outside the repository>"
docker compose --env-file deployment/temporal/env.example -f deployment/temporal/docker-compose.yml config --quiet
Remove-Item Env:TEMPORAL_POSTGRES_PASSWORD_FILE,Env:TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE,Env:GRAFANA_ADMIN_PASSWORD_FILE
```

Expected: static and shell-stub pytest passes. The integration test is skipped
unless `MEDCHAT_RUN_TEMPORAL_COMPOSE_INTEGRATION=1` and Docker Compose are both
available. Opt-in execution uses a unique test project, creates a deployment-owner
`0700` secret directory with `0444` files, and verifies PostgreSQL, schema,
Temporal Server, role-sync, exporter, and Grafana can consume only their assigned
secrets; long-running services must become healthy and one-shot jobs must complete.
The successful rotation assertion checks exporter health immediately after the
script returns, while a deterministic wrong-password wait timeout must return
nonzero without `OK`. A fixture teardown checks `down --volumes --remove-orphans`
and reports cleanup failure without replacing an earlier test assertion. Test-only
Grafana bind sources live under the temporary directory, so the integration test
does not create or populate Task 7 paths. Compose rendering uses temporary non-secret files
outside tracked paths and never starts or pulls a container. Record
`docker compose config` as skipped when Docker is unavailable; never infer
runtime readiness from static tests. Clear process environment and temporary
files after rendering.

- [ ] **Step 5: Commit**

```powershell
git add -- deployment/temporal/docker-compose.yml deployment/temporal/env.example deployment/temporal/postgres-init/010-exporter.sh deployment/temporal/scripts/temporal-schema-setup.sh deployment/temporal/scripts/temporal-server-entrypoint.sh deployment/temporal/scripts/temporal-namespace-setup.sh deployment/temporal/scripts/rotate-exporter-password.sh tests/test_temporal_deployment_assets.py tests/test_temporal_deployment_integration.py docs/superpowers/plans/2026-08-21-temporal-production-canary-readiness.md docs/superpowers/specs/2026-08-21-temporal-production-canary-readiness-design.md
git commit -m "fix(deploy): close temporal production safety gaps"
```

## Task 7: Provision Prometheus alerts and Grafana dashboard

**Files:**
- Create: `deployment/temporal/prometheus/prometheus.yml`
- Create: `deployment/temporal/prometheus/rules/medchat-temporal.yml`
- Create: `deployment/temporal/grafana/provisioning/datasources/prometheus.yml`
- Create: `deployment/temporal/grafana/provisioning/dashboards/dashboard.yml`
- Create: `deployment/temporal/grafana/dashboards/medchat-temporal-docking.json`
- Modify: `tests/test_temporal_deployment_assets.py`

- [ ] **Step 1: Write failing monitoring-asset tests**

```python
def test_prometheus_scrapes_worker_temporal_and_itself():
    config = load_yaml("deployment/temporal/prometheus/prometheus.yml")
    jobs = {job["job_name"]: job for job in config["scrape_configs"]}
    assert {"prometheus", "temporal-server", "temporal-postgres", "medchat-temporal-worker"} <= jobs.keys()
    assert jobs["medchat-temporal-worker"]["static_configs"][0]["targets"] == ["host.docker.internal:9465"]
    assert jobs["temporal-postgres"]["static_configs"][0]["targets"] == ["postgres-exporter:9187"]


def test_blocking_rules_cover_release_invariants():
    rules = load_yaml("deployment/temporal/prometheus/rules/medchat-temporal.yml")
    names = {rule["alert"] for group in rules["groups"] for rule in group["rules"]}
    assert names == {
        "TemporalWorkerHeartbeatStale",
        "TemporalQueueBacklogGrowing",
        "TemporalWorkflowStartFailures",
        "TemporalDuplicateVinaExecution",
        "TemporalMultipleTerminalEvents",
        "TemporalArtifactValidationFailure",
        "TemporalDockingP95TooHigh",
        "TemporalRuntimeFailureRateHigh",
        "TemporalPostgresUnavailable",
        "TemporalBackupVerificationStale",
    }


def test_dashboard_contains_required_panels_and_no_sensitive_queries():
    dashboard = json.loads(Path(DASHBOARD).read_text(encoding="utf-8"))
    titles = {panel["title"] for panel in dashboard["panels"]}
    assert {"Canary level", "Worker readiness", "Queue backlog", "p50 / p95 latency", "Scientific gates"} <= titles
    serialized = json.dumps(dashboard).lower()
    for forbidden in ("smiles", "prompt", "user_id", "task_id", "file_path"):
        assert forbidden not in serialized
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_deployment_assets.py -q -p no:cacheprovider
```

Expected: monitoring files are absent.

- [ ] **Step 3: Add scrape and alert contracts**

Use 15-second scrape/evaluation intervals. Configure `rule_files: ["/etc/prometheus/rules/*.yml"]`. Alert expressions must use only fixed metric names and include `for` durations so one missed scrape does not page: heartbeat 60 seconds, backlog 10 minutes, start failures 5 minutes, p95 10 minutes, failure rate 10 minutes, and backup stale 15 minutes. Duplicate Vina, multi-terminal, and artifact invalid alerts have no grace period because they are hard scientific invariants.

Map the hard alerts to the counters from Task 5 using `increase(...[15m]) > 0`. Compute runtime failure rate from `medchat_temporal_task_terminal_total{status!="succeeded"}` divided by all terminal statuses, and use `time() - medchat_temporal_backup_verified_timestamp_seconds > 86400` for stale backup detection. Queue backlog and workflow-start failures use Temporal Server's official Prometheus metrics; tests assert the configured expressions reference metrics exposed by the pinned Temporal image. Add `extra_hosts: ["host.docker.internal:host-gateway"]` to the Prometheus service so the host-native worker is reachable on Linux.

The latency alert uses both conditions:

```promql
histogram_quantile(0.95, sum by (le) (rate(medchat_temporal_task_duration_seconds_bucket[15m])))
  > medchat_temporal_baseline_p95_seconds * 1.5
or
histogram_quantile(0.95, sum by (le) (rate(medchat_temporal_task_duration_seconds_bucket[15m]))) > 60
```

- [ ] **Step 4: Add provisioned Grafana assets**

Provision a default Prometheus datasource at `http://prometheus:9090`. Dashboard JSON uses a stable UID `medchat-temporal-docking`, a 15-second refresh, and panels for canary level, worker readiness/heartbeat, queue backlog, Temporal accepted count, terminal distribution, p50/p95, Vina attempts, duplicate prevention, terminal violations, artifact validation, provenance gate, and firing release blockers.

- [ ] **Step 5: Validate assets**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_deployment_assets.py -q -p no:cacheprovider
promtool check config deployment/temporal/prometheus/prometheus.yml
promtool check rules deployment/temporal/prometheus/rules/medchat-temporal.yml
```

Expected: pytest passes. `promtool` results are reported separately; absence of `promtool` is a truthful skip and blocks production-readiness status.

- [ ] **Step 6: Commit**

```powershell
git add -- deployment/temporal/prometheus deployment/temporal/grafana tests/test_temporal_deployment_assets.py
git commit -m "feat(deploy): monitor temporal docking canary"
```

## Task 8: Add hardened systemd service boundaries

**Files:**
- Create: `deployment/medchat-temporal-worker.service`
- Modify: `deployment/medchat.service`
- Modify: `tests/test_temporal_deployment_assets.py`

- [ ] **Step 1: Write failing systemd tests**

```python
def test_worker_service_is_single_process_host_native_and_restart_safe():
    unit = parse_systemd("deployment/medchat-temporal-worker.service")
    service = unit["Service"]
    assert service["User"] == "medchat"
    assert service["EnvironmentFile"] == "/etc/medchat/medchat.env"
    assert service["ExecStart"] == "/opt/conda/envs/medchat/bin/python scripts/run_temporal_docking_worker.py"
    assert service["Restart"] == "on-failure"
    assert service["TimeoutStopSec"] == "45"
    assert service["KillMode"] == "control-group"
    assert "MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY=1" in service["Environment"]


def test_web_and_worker_are_independent_units():
    web = parse_systemd("deployment/medchat.service")
    worker = parse_systemd("deployment/medchat-temporal-worker.service")
    assert web["Service"]["ExecStart"] != worker["Service"]["ExecStart"]
    assert "medchat-temporal-worker.service" not in web.get("Unit", {}).get("Requires", "")
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_deployment_assets.py -q -p no:cacheprovider
```

Expected: worker unit is absent and Web still uses the repository `.env`.

- [ ] **Step 3: Create worker unit and narrow Web environment loading**

Use this service contract:

```ini
[Unit]
Description=MedChat Temporal Docking Worker
After=network-online.target docker.service
Wants=network-online.target

[Service]
Type=simple
User=medchat
Group=medchat
WorkingDirectory=/opt/medchat/molecular_chat_system
EnvironmentFile=/etc/medchat/medchat.env
Environment=MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY=1
ExecStartPre=/opt/conda/envs/medchat/bin/python -c "from src.task_runtime.config import TaskRuntimeConfig; c=TaskRuntimeConfig.from_env(); assert not c.warnings and c.docking_concurrency == 1"
ExecStart=/opt/conda/envs/medchat/bin/python scripts/run_temporal_docking_worker.py
Restart=on-failure
RestartSec=5
TimeoutStartSec=60
TimeoutStopSec=45
KillMode=control-group
PrivateTmp=true
NoNewPrivileges=true
ProtectSystem=full
ReadWritePaths=/opt/medchat/molecular_chat_system/scratch /opt/medchat/molecular_chat_system/temp_docking

[Install]
WantedBy=multi-user.target
```

Change only `EnvironmentFile` in `deployment/medchat.service` to `/etc/medchat/medchat.env`; preserve its `ExecStart`, working directory, user, restart, and timeout behavior.

- [ ] **Step 4: Validate units**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_deployment_assets.py -q -p no:cacheprovider
systemd-analyze verify deployment/medchat.service deployment/medchat-temporal-worker.service
```

Expected: pytest passes. `systemd-analyze` is run on Linux; on Windows its absence is recorded as a truthful skip and remains a production-host validation requirement.

- [ ] **Step 5: Commit**

```powershell
git add -- deployment/medchat.service deployment/medchat-temporal-worker.service tests/test_temporal_deployment_assets.py
git commit -m "feat(deploy): isolate temporal docking worker"
```

## Task 9: Add atomic PostgreSQL backup and isolated restore verification

**Files:**
- Create: `scripts/backup_temporal_postgres.py`
- Create: `scripts/restore_temporal_postgres.py`
- Modify: `tests/test_temporal_operator_scripts.py`

- [ ] **Step 1: Write failing backup/restore command tests**

```python
def test_backup_uses_stdin_environment_and_atomic_manifest(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    calls = RecordingRunner()
    manifest = create_backup(
        output_dir=tmp_path,
        database="temporal",
        host="127.0.0.1",
        port=5433,
        user="temporal",
        runner=calls,
        now=aware_now(),
    )
    assert "runtime-secret" not in " ".join(calls.argv)
    assert calls.environment["PGPASSWORD"] == "runtime-secret"
    dump_path = tmp_path / manifest["dump_file"]
    assert dump_path.is_file()
    assert manifest["sha256"] == sha256_file(dump_path)
    assert not list(tmp_path.glob("*.partial"))


def test_restore_refuses_source_database_and_bad_hash(tmp_path):
    dump, manifest = write_backup_fixture(tmp_path)
    with pytest.raises(ValueError, match="verification database must differ"):
        verify_restore(dump, manifest, source_database="temporal", target_database="temporal")
    manifest["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="hash mismatch"):
        verify_restore(dump, manifest, source_database="temporal", target_database="temporal_verify")


def test_reports_never_include_postgres_password(tmp_path, monkeypatch):
    monkeypatch.setenv("TEMPORAL_POSTGRES_PASSWORD", "runtime-secret")
    result = run_backup_cli(tmp_path, dry_run=True)
    assert "runtime-secret" not in result.stdout + result.stderr
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_operator_scripts.py -q -p no:cacheprovider
```

Expected: backup and restore scripts are missing.

- [ ] **Step 3: Implement atomic backup**

The backup CLI accepts `--host`, `--port`, `--database`, `--user`, `--output-dir`, `--manifest-output`, and `--dry-run`. It reads only `TEMPORAL_POSTGRES_PASSWORD` from the environment, invokes:

```text
pg_dump --format=custom --no-owner --no-acl --host HOST --port PORT --username USER --file PARTIAL_PATH DATABASE
```

Pass the password through the subprocess environment, never argv. On success fsync and atomically rename the dump, calculate SHA-256, and write a manifest containing schema version, logical database name, PostgreSQL major version, generated timestamp, dump basename, hash, size, and `status="passed"`. Refuse symlink output directories, an existing destination basename, and unbounded database/user values.

- [ ] **Step 4: Implement isolated restore verification**

The restore CLI accepts the backup manifest, `--source-database temporal`, `--target-database temporal_verify`, connection fields, and `--drop-verification-database`. It rejects identical source/target names, production-like target names other than the explicit verification name, hash mismatch, PostgreSQL major mismatch, and a target database that already exists unless `--drop-verification-database` is present. It creates the verification database, runs `pg_restore --exit-on-error --single-transaction`, checks required Temporal schema tables through `psql`, writes `latest-verified.json` atomically, then drops only the verification database when requested.

- [ ] **Step 5: Verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_operator_scripts.py -q -p no:cacheprovider
```

Expected: all backup/restore contract tests pass without a real database.

- [ ] **Step 6: Commit**

```powershell
git add -- scripts/backup_temporal_postgres.py scripts/restore_temporal_postgres.py tests/test_temporal_operator_scripts.py
git commit -m "feat(deploy): verify temporal postgres backups"
```

## Task 10: Add deployment validation and production preflight

**Files:**
- Create: `scripts/validate_temporal_deployment.py`
- Create: `scripts/run_temporal_production_preflight.py`
- Modify: `scripts/run_temporal_docking_acceptance.py`
- Modify: `tests/test_temporal_operator_scripts.py`
- Modify: `tests/task_runtime/test_temporal_acceptance.py`

- [ ] **Step 1: Write failing validator and preflight tests**

```python
def test_deployment_validator_has_explicit_pass_fail_skip_results(tmp_path):
    report = validate_deployment(repo_root=PROJECT_ROOT, command_runner=MissingToolsRunner())
    assert report["status"] == "partial"
    assert report["checks"]["python_static"]["status"] == "passed"
    assert report["checks"]["docker_compose"]["status"] == "skipped"
    assert report["checks"]["promtool"]["status"] == "skipped"
    assert report["checks"]["systemd_analyze"]["status"] == "skipped"


def test_preflight_requires_repeat_three_real_science_and_backup():
    report = build_preflight_report(
        deployment={"status": "passed"},
        contract={"status": "passed", "run_count": 3},
        real={"status": "passed", "run_count": 3, "scientific_execution": True},
        infrastructure={"temporal": True, "namespace": True, "queue": True, "worker": True},
        blocking_alerts=[],
        backup={"status": "passed"},
        current_level=0,
        now=aware_now(),
    )
    assert report["status"] == "passed"
    assert report["stage"] == "preflight"
    assert report["current_level"] == 0


@pytest.mark.parametrize("field", ["contract", "real", "infrastructure", "backup"])
def test_preflight_fails_or_partials_when_required_evidence_is_missing(field):
    inputs = valid_preflight_inputs()
    inputs[field] = {"status": "skipped"}
    report = build_preflight_report(**inputs)
    assert report["status"] != "passed"
```

- [ ] **Step 2: Verify RED**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_operator_scripts.py tests\task_runtime\test_temporal_acceptance.py -q -p no:cacheprovider
```

Expected: validator/preflight APIs are missing.

- [ ] **Step 3: Implement static deployment validation**

`validate_temporal_deployment.py` performs the same Python-level assertions as `tests/test_temporal_deployment_assets.py`, then conditionally runs exact commands:

```text
docker compose --env-file deployment/temporal/env.example -f deployment/temporal/docker-compose.yml config --quiet
promtool check config deployment/temporal/prometheus/prometheus.yml
promtool check rules deployment/temporal/prometheus/rules/medchat-temporal.yml
systemd-analyze verify deployment/medchat.service deployment/medchat-temporal-worker.service
```

For Compose rendering the validator creates a temporary non-secret exporter-password file, passes validation-only password values only in the child-process environment, and deletes the file after the command. Each check returns `passed`, `failed`, or `skipped` with a stable code, never raw command output. Any failed check yields overall failed; any skipped required host check yields partial; only all passed yields passed.

- [ ] **Step 4: Implement production preflight aggregation**

The preflight script runs deployment validation, `run_temporal_docking_acceptance.py --mode contract --repeat 3`, and `--mode real --repeat 3` into temporary reports. The real subprocess receives an isolated environment override with `MEDCHAT_TASK_BACKEND=temporal_canary` and `MEDCHAT_TEMPORAL_CANARY_PERCENT=100`; this does not edit the production environment file or production selector. It reads local infrastructure/alerts and verified-backup state using the same safe projectors as the observer. It emits schema version 1, stage `preflight`, current level 0, generated time, individual check states, `all_required_gates`, and canonical SHA-256. A skipped Docker/promtool/systemd check makes the report partial, so Windows can validate code but cannot generate production promotion evidence.

Extend the existing acceptance report only to include stable `failure_type_distribution` and a top-level `provenance_gate`; do not add task payloads, absolute paths, or secret state.

- [ ] **Step 5: Verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_temporal_operator_scripts.py tests\task_runtime\test_temporal_acceptance.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\validate_temporal_deployment.py --output scratch\temporal-deployment-validation.json
```

Expected: tests pass. Local validator status is passed only when Docker, promtool, and systemd-analyze were truly executed; otherwise it is partial with exact skipped codes.

- [ ] **Step 6: Commit**

```powershell
git add -- scripts/validate_temporal_deployment.py scripts/run_temporal_production_preflight.py scripts/run_temporal_docking_acceptance.py tests/test_temporal_operator_scripts.py tests/task_runtime/test_temporal_acceptance.py
git commit -m "feat(runtime): gate temporal production preflight"
```

## Task 11: Document operations and run the complete release gate

**Files:**
- Create: `docs/runbooks/temporal_docking_canary.md`
- Modify: `docs/PROJECT_STANDARDS.md`
- Modify: `docs/handoff/latest.md`

- [ ] **Step 1: Write the runbook with exact operator commands**

Document these ordered procedures:

```bash
sudo install -d -m 0750 -o root -g medchat /etc/medchat
sudo install -m 0640 -o root -g medchat deployment/temporal/env.example /etc/medchat/temporal.env
sudo install -m 0640 -o root -g medchat deployment/temporal/env.example /etc/medchat/medchat.env.example
docker compose --env-file /etc/medchat/temporal.env -f deployment/temporal/docker-compose.yml up -d
sudo systemctl daemon-reload
sudo systemctl enable --now medchat.service medchat-temporal-worker.service
```

The runbook must require the operator to set real runtime values without echoing them, set `BACKUP_RETENTION_DAYS` to an organization-approved positive integer before production, execute backup plus isolated restore verification, run preflight, promote 0→5, observe 20 tasks or 24 hours with at least 3 tasks, promote 5→10, repeat, promote 10→25, and never skip a level.

Include incident commands that first rollback new traffic:

```bash
/opt/conda/envs/medchat/bin/python scripts/manage_temporal_canary.py rollback \
  --env-file /etc/medchat/medchat.env --reason "release blocker firing"
sudo systemctl restart medchat.service
/opt/conda/envs/medchat/bin/python -m src.task_runtime.temporal.reconcile
```

State explicitly that rollback does not delete or locally replay accepted Temporal tasks.

- [ ] **Step 2: Update standards and handoff**

Add concise rules to `docs/PROJECT_STANDARDS.md`: Linux+systemd production target, Compose infrastructure boundary, worker concurrency 1, loopback monitoring, runtime-only secrets, legal rollout levels, evidence gates, rollback semantics, and truthful skip handling.

Replace `docs/handoff/latest.md` with this stage's objective, exact changed files, commits, commands/results, real Vina values, skipped host-only checks, open production-host actions, and confirmation that the original dirty workspace was untouched.

- [ ] **Step 3: Run focused unit and static tests**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime\test_temporal_config.py tests\task_runtime\test_temporal_rollout.py tests\task_runtime\test_temporal_observation.py tests\task_runtime\test_temporal_prometheus.py tests\test_temporal_operator_scripts.py tests\test_temporal_deployment_assets.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
```

Expected: all focused tests pass and compileall exits zero.

- [ ] **Step 4: Run Stage 3A and Agent regressions**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\task_runtime tests\test_temporal_docking_routes.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\agent -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest tests\test_agent_anti_hallucination_fallbacks.py tests\test_agent_platform_health_check.py -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_agent_acceptance.py --mode contract
```

Expected: all existing tests remain green; any pre-existing environment skip is reported separately.

- [ ] **Step 5: Run local Temporal contract repeat 3**

Start the dev server and worker in separate terminals, using runtime-only environment variables and canary 100 only for the isolated acceptance process:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_temporal_dev_server.py
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_temporal_docking_worker.py
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_temporal_docking_acceptance.py --mode contract --repeat 3 --output scratch\temporal-contract-repeat3.json
```

Expected: status passed, run_count 3, completion reuse true, cancellation confirmed, startup-only fallback true, and no sensitive output.

- [ ] **Step 6: Run real MAGL/Vina repeat 3**

```powershell
$env:MEDCHAT_TASK_BACKEND = "temporal_canary"
$env:MEDCHAT_TEMPORAL_CANARY_PERCENT = "100"
$env:MEDCHAT_TEMPORAL_ADDRESS = "127.0.0.1:7233"
$env:MEDCHAT_TEMPORAL_DOCKING_QUEUE = "medchat-docking"
$env:MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY = "1"
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\run_temporal_docking_acceptance.py --mode real --repeat 3 --timeout-seconds 2400 --output scratch\temporal-real-repeat3.json
```

Expected for passed status: every run uses backend Temporal, attempt 1, exactly one terminal event, pose_count > 0, finite binding energy parsed from Vina, existing pose artifact, matching SHA-256, and provenance with `demo_mode=false` and `fallback_used=false`. Missing scientific dependencies produce failed/partial, never a fabricated energy.

- [ ] **Step 7: Validate deployment tools truthfully**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\validate_temporal_deployment.py --output scratch\temporal-deployment-validation.json
$env:TEMPORAL_POSTGRES_PASSWORD = "compose-validation-only"
$env:GRAFANA_ADMIN_PASSWORD = "compose-validation-only"
$env:TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE = "deployment/temporal/env.example"
docker compose --env-file deployment/temporal/env.example -f deployment/temporal/docker-compose.yml config --quiet
Remove-Item Env:TEMPORAL_POSTGRES_PASSWORD,Env:GRAFANA_ADMIN_PASSWORD,Env:TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE
promtool check config deployment/temporal/prometheus/prometheus.yml
promtool check rules deployment/temporal/prometheus/rules/medchat-temporal.yml
systemd-analyze verify deployment/medchat.service deployment/medchat-temporal-worker.service
```

Expected: each available tool passes. Windows-unavailable commands remain explicit skipped production-host checks; they are not converted to success.

- [ ] **Step 8: Verify real PostgreSQL backup and isolated restore when Compose is available**

Run only against the local Compose PostgreSQL instance, using the runtime password already loaded by the operator:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\backup_temporal_postgres.py --host 127.0.0.1 --port 5433 --database temporal --user temporal --output-dir scratch\temporal-backups --manifest-output scratch\temporal-backups\latest.json
C:\Users\xkx52\.conda\envs\MedChat\python.exe scripts\restore_temporal_postgres.py --manifest scratch\temporal-backups\latest.json --source-database temporal --target-database temporal_verify --host 127.0.0.1 --port 5433 --user temporal --drop-verification-database
```

Expected: backup manifest and dump hashes match, restore verification reports passed, required Temporal schema tables are readable, and the production `temporal` database is never dropped or overwritten. If PostgreSQL client tools or Compose are unavailable, record this as skipped and keep production readiness partial.

- [ ] **Step 9: Run security scans and inspect the final diff**

```powershell
git diff --check
rg -n "sk-[A-Za-z0-9]|OPENAI_COMPATIBLE_API_KEY\s*=|POSTGRES_PASSWORD\s*=\S+|GRAFANA_ADMIN_PASSWORD\s*=\S+|BEGIN (RSA|OPENSSH) PRIVATE KEY" deployment docs scripts src tests
git status --short
git diff --stat
```

Expected: no trailing whitespace, no committed secret value, and only this stage's files are changed.

- [ ] **Step 10: Commit documentation and handoff**

```powershell
git add -- docs/runbooks/temporal_docking_canary.md docs/PROJECT_STANDARDS.md docs/handoff/latest.md
git commit -m "docs(runtime): hand off temporal production canary"
```

## Final acceptance checklist

- [ ] Current branch is `codex/temporal-production-canary-readiness`, never `main`.
- [ ] The original mixed workspace `D:\MedChat\molecular_chat_system` was not modified.
- [ ] Default runtime remains local/canary 0.
- [ ] Only docking tasks can enter Temporal canary.
- [ ] Promotion supports only 0→5→10→25 and never accepts a force bypass.
- [ ] Any nonzero level can roll back new traffic directly to zero.
- [ ] Already accepted Temporal work never replays through local backend.
- [ ] Observation requires 20 real terminal tasks or a full 24 hours with at least 3 real terminal tasks.
- [ ] Failure rate, p95, heartbeat, queue, alert, backup, Vina-attempt, terminal, artifact, hash, and provenance gates all pass before promotion.
- [ ] Metrics labels contain no task/user/prompt/SMILES/path identifiers.
- [ ] Compose ports bind only to loopback and images use pinned tags.
- [ ] PostgreSQL backup password is runtime-only and restore verification targets an isolated database.
- [ ] Real Vina repeat 3 either passes with verified artifacts or fails honestly.
- [ ] Docker/promtool/systemd checks are never claimed passed when unavailable.
- [ ] No secret, user data, absolute machine path, runtime database, artifact, or report is committed.
