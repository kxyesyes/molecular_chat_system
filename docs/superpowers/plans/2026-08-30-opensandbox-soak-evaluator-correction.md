# OpenSandbox Soak Evaluator Correction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Correct the two proven false-negative paths in the OpenSandbox stability evaluator so that each measured submission is correlated to its Broker lifecycle and receives Vina/Meeko provenance from validated Broker telemetry, while preserving every real scientific or control-plane failure.

**Architecture:** Treat the diagnostics snapshot delta around one measured submission as the sole correlation window, reduce that window to one `(trace_id, job_id)` lifecycle, and validate successful and failed lifecycles with separate fail-closed rules. Extend the existing result projector with an explicit validated-version input so the stability runner can use non-intrusive Docker observation for isolation and Broker telemetry for tool versions without weakening the standalone real-acceptance path.

**Tech Stack:** Python 3.10+, dataclasses, FastAPI Broker telemetry contracts, Pydantic 2 result contracts, pytest, Docker/gVisor observation, AutoDock Vina, Meeko.

---

## Scope boundary and follow-up

This is the first executable sub-project of the approved design in
`docs/superpowers/specs/2026-08-30-opensandbox-soak-correctness-and-stability-design.md`.
It deliberately fixes evaluator correctness before changing runtime behavior.

The real `provider_error`, `tool_timeout`, `invalid_output`, queue, cancellation,
and timeout-probe failures remain failures after this plan. A second implementation
plan will be written from the corrected failure evidence before any new formal
30-job QEMU generation is created. This separation prevents an unproven runtime
change from being bundled with the evaluator correction.

The preserved `79860ec` report is not copied into Git, rewritten, or reclassified
as passed. No task in this plan runs a formal QEMU soak.

## File map and compatibility boundaries

| Path | Responsibility in this plan |
|---|---|
| `scripts/run_opensandbox_stability_soak.py` | Add lifecycle correlation/evaluation and use Broker lifecycle identity and validated versions for measured runs. |
| `scripts/run_opensandbox_docking_acceptance.py` | Add an explicit optional validated tool-version source to `_project_result`; standalone acceptance keeps using the Docker observer by default. |
| `tests/sandbox_broker/test_stability_soak.py` | Add sanitized report-shaped lifecycle fixtures and mutation tests for correlation, ordering, cleanup, terminal state, and failure preservation. |
| `tests/sandbox_broker/test_real_opensandbox_acceptance.py` | Prove explicit Broker versions are accepted, mismatches fail closed, and the legacy observer path remains unchanged. |
| `docs/handoff/latest.md` | Record the corrected offline evidence and the unresolved real-runtime failure classes after all tests pass. |

Compatibility rules for every task:

- Keep `REPORT_FIELDS`, `RUN_FIELDS`, report schema version `1`, and all public CLI arguments unchanged.
- Keep Broker-generated `trace_id` and `job_id` unchanged; do not force either to equal the worker idempotency key.
- Keep the Docker observer authoritative for gVisor, image digest, resource limits, network isolation, and cleanup observation.
- Do not re-enable intrusive version probes in measured runs.
- Do not retry Vina, Meeko, a failed scientific command, or the complete soak suite.
- Do not convert `provider_error`, `tool_timeout`, `invalid_output`, cancellation, cleanup failure, or control-plane failure into success.
- Do not add raw exception text, response bodies, sandbox IDs, absolute host paths, environment values, or credentials to reports.
- Stage only the files named in each task; never use `git add -A`.

### Task 1: Add sanitized lifecycle fixtures that reproduce the false negatives

**Files:**
- Modify: `tests/sandbox_broker/test_stability_soak.py:14-99`
- Modify: `tests/sandbox_broker/test_stability_soak.py:222-299`

- [ ] **Step 1: Add one strict Broker-event fixture helper**

Add the following helper below `diagnostics()` so tests can use a worker key that
differs from Broker identity without copying the runtime report:

```python
def lifecycle_event(
    trace_id: str,
    job_id: str,
    phase: str,
    *,
    outcome: str | None = None,
    failure_class: str | None = None,
    cleanup_status: str | None = None,
    vina_version: str | None = None,
    meeko_version: str | None = None,
) -> dict[str, object]:
    event = soak.synthetic_event(job_id, phase)
    event["trace_id"] = trace_id
    event["job_id"] = job_id
    if outcome is not None:
        event["outcome"] = outcome
    if failure_class is not None:
        event["failure_class"] = failure_class
    if cleanup_status is not None:
        event["cleanup_status"] = cleanup_status
    if phase == "validation_completed":
        event["vina_version"] = vina_version or "1.2.5"
        event["meeko_version"] = meeko_version or "0.7.1"
    return event


def successful_lifecycle(
    trace_id: str = "broker-trace-1",
    job_id: str = "a" * 32,
) -> list[dict[str, object]]:
    events = [lifecycle_event(trace_id, job_id, phase) for phase in PHASES]
    events[-3]["cleanup_status"] = "in_progress"
    events[-2]["outcome"] = "passed"
    events[-2]["cleanup_status"] = "succeeded"
    events[-1]["outcome"] = "passed"
    events[-1]["cleanup_status"] = "succeeded"
    return events
```

- [ ] **Step 2: Write the failing identity/provenance regression**

Replace the old assumption in the executor test with a diagnostics client whose
Broker identity is deliberately unrelated to the worker idempotency key:

```python
def test_measured_run_uses_single_broker_identity_and_validation_versions(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    broker_trace = "broker-trace-real"
    broker_job = "b" * 32
    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    after = diagnostics(1)
    after["telemetry"]["recent_events"] = successful_lifecycle(
        broker_trace,
        broker_job,
    )

    class DiagnosticsClient:
        def __init__(self) -> None:
            self.values = iter([before, after])

        def snapshot(self) -> dict[str, object]:
            return next(self.values)

    class Runner:
        def execute(self, payload: object, *, job_id: str) -> object:
            del payload
            assert job_id.startswith("soak-")
            assert job_id != broker_trace
            return object()

    class Observer:
        failure_codes: list[str] = []
        tool_versions: dict[str, str] = {}

        def disable_intrusive_probes(self) -> None: pass
        def start(self) -> None: pass
        def stop(self) -> None: pass

    captured: dict[str, object] = {}

    def project(*args: object, **kwargs: object) -> dict[str, object]:
        del args
        captured.update(kwargs)
        return {
            "status": "passed",
            "latency_ms": 100,
            "pose_count": 9,
            "best_energy": -7.0,
            "artifact_path": "outputs/opensandbox_stability/run/result.pdbqt",
            "artifact_sha256": HEX,
            "cleanup_status": "succeeded",
            "image_digest": HEX,
            "secure_runtime": "gvisor",
            "tool_versions": kwargs["validated_tool_versions"],
            "warning_codes": [],
            "failure_codes": [],
        }

    monkeypatch.setattr(soak, "_project_result", project)
    executor = soak.StabilityExecutor(
        runner=Runner(),
        payload={},
        observer_factory=Observer,
        diagnostics_client=DiagnosticsClient(),
        project_root=tmp_path,
    )

    run = executor.measured_run(1)

    assert run["trace_id"] == broker_trace
    assert run["events_complete"] is True
    assert run["vina_version"] == "1.2.5"
    assert run["meeko_version"] == "0.7.1"
    assert captured["validated_tool_versions"] == {
        "vina": "1.2.5",
        "meeko": "0.7.1",
    }
```

- [ ] **Step 3: Add fail-closed mutation cases**

Add a parametrized test that calls the new `soak.evaluate_lifecycle()` helper
planned in Task 2:

```python
@pytest.mark.parametrize(
    "mutate",
    [
        lambda events: events[:-1],
        lambda events: events[:4] + [events[3]] + events[4:],
        lambda events: events[:5] + [events[6], events[5]] + events[7:],
        lambda events: events + successful_lifecycle("other-trace", "c" * 32),
        lambda events: [
            {**event, "meeko_version": None}
            if event["phase"] == "validation_completed"
            else event
            for event in events
        ],
    ],
)
def test_success_lifecycle_mutations_fail_closed(mutate: object) -> None:
    evidence = soak.evaluate_lifecycle(
        mutate(successful_lifecycle()),  # type: ignore[operator]
        result_succeeded=True,
    )
    assert evidence.events_complete is False
    assert "events_incomplete" in evidence.failure_codes
```

Add the explicit field-mutation regression:

```python
@pytest.mark.parametrize(
    ("phase", "field", "value"),
    [
        ("job_terminal", "outcome", "failed"),
        ("job_terminal", "cleanup_status", "failed"),
        ("validation_completed", "vina_version", None),
    ],
)
def test_success_lifecycle_rejects_terminal_cleanup_or_version_mismatch(
    phase: str,
    field: str,
    value: object,
) -> None:
    events = successful_lifecycle()
    next(event for event in events if event["phase"] == phase)[field] = value
    evidence = soak.evaluate_lifecycle(events, result_succeeded=True)
    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)


def test_success_lifecycle_rejects_two_validation_events() -> None:
    events = successful_lifecycle()
    validation = next(
        event for event in events if event["phase"] == "validation_completed"
    )
    events.insert(events.index(validation) + 1, dict(validation))
    assert soak.evaluate_lifecycle(
        events,
        result_succeeded=True,
    ).events_complete is False
```

- [ ] **Step 4: Add a truthful failed-lifecycle fixture**

```python
def test_failed_lifecycle_remains_failed_without_requiring_success_phases() -> None:
    events = successful_lifecycle("broker-failed", "d" * 32)
    del events[8:10]
    events[7]["outcome"] = "failed"
    events[-1]["outcome"] = "failed"
    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)

    assert evidence.trace_id == "broker-failed"
    assert evidence.events_complete is True
    assert evidence.tool_versions == {}
    assert evidence.terminal_outcome == "failed"
```

Add the failed-lifecycle mutation test:

```python
@pytest.mark.parametrize("mutation", ["passed", "missing", "early", "second_identity"])
def test_failed_lifecycle_rejects_untruthful_terminal(mutation: str) -> None:
    events = successful_lifecycle("broker-failed", "d" * 32)
    del events[8:10]
    events[7]["outcome"] = "failed"
    events[-1]["outcome"] = "failed"
    if mutation == "passed":
        events[-1]["outcome"] = "passed"
    elif mutation == "missing":
        events.pop()
    elif mutation == "early":
        terminal = events.pop()
        events.insert(-2, terminal)
    else:
        events.extend(successful_lifecycle("other-trace", "e" * 32))

    evidence = soak.evaluate_lifecycle(events, result_succeeded=False)
    assert evidence.events_complete is False
    assert "events_incomplete" in evidence.failure_codes
```

- [ ] **Step 5: Run the new tests and verify RED**

Run:

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest \
  tests/sandbox_broker/test_stability_soak.py \
  -q -p no:cacheprovider
```

Expected: failures show that `evaluate_lifecycle` does not exist, measured runs
still correlate on the caller key, and Meeko is not sourced from telemetry.

- [ ] **Step 6: Commit only the failing tests**

```powershell
git add tests/sandbox_broker/test_stability_soak.py
git commit -m "test: reproduce opensandbox soak false negatives"
```

### Task 2: Implement one-identity lifecycle evaluation

**Files:**
- Modify: `scripts/run_opensandbox_stability_soak.py:14-30`
- Modify: `scripts/run_opensandbox_stability_soak.py:1031-1138`
- Test: `tests/sandbox_broker/test_stability_soak.py`

- [ ] **Step 1: Add the immutable lifecycle evidence contract**

Import `dataclass` and add this private contract above `StabilityExecutor`:

```python
@dataclass(frozen=True)
class LifecycleEvidence:
    trace_id: str
    job_id: str
    events_complete: bool
    terminal_outcome: str | None
    cleanup_status: str | None
    failure_class: str
    tool_versions: dict[str, str]
    failure_codes: tuple[str, ...]
```

- [ ] **Step 2: Implement diagnostics-window correlation**

Replace `_event_failure_class()` and `_events_complete()` with one evaluator.
The complete implementation must use only the supplied delta events:

```python
def evaluate_lifecycle(
    events: Sequence[Mapping[str, object]],
    *,
    result_succeeded: bool,
) -> LifecycleEvidence:
    groups: dict[tuple[str, str], list[Mapping[str, object]]] = {}
    for event in events:
        trace_id = event.get("trace_id")
        job_id = event.get("job_id")
        if type(trace_id) is not str or type(job_id) is not str:
            continue
        groups.setdefault((trace_id, job_id), []).append(event)
    if len(groups) != 1:
        return LifecycleEvidence(
            trace_id="trace-unavailable",
            job_id="job-unavailable",
            events_complete=False,
            terminal_outcome=None,
            cleanup_status=None,
            failure_class="unknown_control_plane_failure",
            tool_versions={},
            failure_codes=("events_incomplete",),
        )

    (trace_id, job_id), correlated = next(iter(groups.items()))
    phases = [event.get("phase") for event in correlated]
    terminals = [event for event in correlated if event.get("phase") == "job_terminal"]
    failure_classes = {
        event.get("failure_class")
        for event in correlated
        if event.get("failure_class") not in (None, "none")
    }
    failure_class = (
        next(iter(failure_classes))
        if len(failure_classes) == 1
        else "none" if not failure_classes else "unknown_control_plane_failure"
    )
    terminal = terminals[0] if len(terminals) == 1 else {}
    terminal_outcome = terminal.get("outcome")
    cleanup_status = terminal.get("cleanup_status")
    versions: dict[str, str] = {}
    validations = [
        event for event in correlated
        if event.get("phase") == "validation_completed"
    ]
    cleanup_started_events = [
        event for event in correlated
        if event.get("phase") == "cleanup_started"
    ]
    cleanup_completed_events = [
        event for event in correlated
        if event.get("phase") == "cleanup_completed"
    ]
    if len(validations) == 1:
        validation = validations[0]
        vina = validation.get("vina_version")
        meeko = validation.get("meeko_version")
        if (
            type(vina) is str
            and _SAFE_VERSION.fullmatch(vina)
            and type(meeko) is str
            and _SAFE_VERSION.fullmatch(meeko)
        ):
            versions = {"vina": vina, "meeko": meeko}

    cleanup_consistent = (
        len(cleanup_started_events) == 1
        and cleanup_started_events[0].get("cleanup_status") == "in_progress"
        and len(cleanup_completed_events) == 1
        and cleanup_completed_events[0].get("outcome") == "passed"
        and cleanup_completed_events[0].get("cleanup_status") == cleanup_status
    )

    if result_succeeded:
        complete = (
            phases == list(EXPECTED_PHASES)
            and len(terminals) == 1
            and terminal_outcome == "passed"
            and cleanup_status == "succeeded"
            and cleanup_consistent
            and set(versions) == {"vina", "meeko"}
            and failure_class == "none"
            and all(
                event.get("outcome") == "passed"
                for event in correlated
                if str(event.get("phase", "")).endswith("_completed")
            )
        )
    else:
        terminal_index = phases.index("job_terminal") if len(terminals) == 1 else -1
        cleanup_started = phases.index("cleanup_started") if "cleanup_started" in phases else -1
        cleanup_completed = phases.index("cleanup_completed") if "cleanup_completed" in phases else -1
        phase_positions = [EXPECTED_PHASES.index(str(phase)) for phase in phases]
        ordered_lifecycle = all(
            left < right
            for left, right in zip(phase_positions, phase_positions[1:])
        )
        complete = (
            bool(phases)
            and phases[0] == "job_received"
            and len(terminals) == 1
            and terminal_index == len(phases) - 1
            and terminal_outcome in {"failed", "cancelled"}
            and cleanup_status in {"succeeded", "failed"}
            and cleanup_consistent
            and cleanup_started >= 0
            and cleanup_started < cleanup_completed < terminal_index
            and ordered_lifecycle
        )

    return LifecycleEvidence(
        trace_id=trace_id,
        job_id=job_id,
        events_complete=complete,
        terminal_outcome=terminal_outcome if type(terminal_outcome) is str else None,
        cleanup_status=cleanup_status if type(cleanup_status) is str else None,
        failure_class=failure_class,
        tool_versions=versions,
        failure_codes=() if complete else ("events_incomplete",),
    )
```

This strictly increasing phase-index rule accepts a failed lifecycle such as
`command_completed -> cleanup_started -> cleanup_completed -> job_terminal`
while rejecting duplicate, reversed, or post-terminal events. Do not loosen the
exact successful sequence.

- [ ] **Step 3: Run the direct lifecycle tests and verify GREEN**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest \
  tests/sandbox_broker/test_stability_soak.py \
  -q -p no:cacheprovider -k "lifecycle and not measured_run"
```

Expected: all selected lifecycle-evaluator tests pass without contacting QEMU,
Docker, OpenSandbox, or Vina. The measured-run identity test remains red until
Task 3 extends the shared result projector and wires the evaluator into the
executor.

- [ ] **Step 4: Commit the lifecycle evaluator**

```powershell
git add scripts/run_opensandbox_stability_soak.py
git commit -m "fix: correlate soak runs with broker lifecycle"
```

### Task 3: Source measured tool versions from validated Broker telemetry

**Files:**
- Modify: `scripts/run_opensandbox_docking_acceptance.py:202-373`
- Modify: `tests/sandbox_broker/test_real_opensandbox_acceptance.py:199-339`
- Modify: `tests/sandbox_broker/test_stability_soak.py`

- [ ] **Step 1: Write explicit-source and mismatch tests**

Add tests beside the existing `_project_result` version tests:

```python
def test_project_result_accepts_validated_broker_versions_when_observer_is_non_intrusive(
    tmp_path: Path,
) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    result = _success_result(pose, digest)
    result.provenance.tool_version = "1.2.5"
    observer = _FakeObserver()
    observer.tool_versions = {}

    projected = acceptance._project_result(
        result,
        trace_id="broker-version-source",
        project_root=tmp_path,
        observer=observer,
        validated_tool_versions={"vina": "1.2.5", "meeko": "0.7.1"},
    )

    assert projected["status"] == "passed"
    assert projected["tool_versions"] == {
        "vina": "1.2.5",
        "meeko": "0.7.1",
    }
    assert "tool_version_invalid" not in projected["failure_codes"]
    assert "tool_version_mismatch" not in projected["failure_codes"]


@pytest.mark.parametrize(
    "versions",
    [
        {},
        {"vina": "1.2.5"},
        {"vina": "1.2.4", "meeko": "0.7.1"},
        {"vina": "development", "meeko": "0.7.1"},
    ],
)
def test_project_result_fails_closed_on_bad_validated_broker_versions(
    tmp_path: Path,
    versions: dict[str, str],
) -> None:
    acceptance = _module()
    pose, digest = _pose(tmp_path)
    result = _success_result(pose, digest)
    result.provenance.tool_version = "1.2.5"
    projected = acceptance._project_result(
        result,
        trace_id="broker-version-invalid",
        project_root=tmp_path,
        observer=_FakeObserver(),
        validated_tool_versions=versions,
    )
    assert projected["status"] == "failed"
    assert set(projected["failure_codes"]) & {
        "tool_version_invalid",
        "tool_version_mismatch",
    }
```

The tests intentionally reuse `_pose`, `_success_result`, `_FakeObserver`, and
`_module`, which already exist in this test module.

- [ ] **Step 2: Verify the tests fail against the current projector**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest \
  tests/sandbox_broker/test_real_opensandbox_acceptance.py \
  -q -p no:cacheprovider
```

Expected: `_project_result()` rejects the unknown
`validated_tool_versions` keyword.

- [ ] **Step 3: Extend `_project_result()` without changing its default path**

Change the signature to:

```python
def _project_result(
    result: object,
    *,
    trace_id: str,
    project_root: Path,
    observer: object,
    validated_tool_versions: Mapping[str, object] | None = None,
) -> dict[str, Any]:
```

Replace only the observed-version source:

```python
observed_versions: object = (
    validated_tool_versions
    if validated_tool_versions is not None
    else getattr(observer, "tool_versions", {})
)
if isinstance(observed_versions, Mapping):
    observed_vina = _safe_version(observed_versions.get("vina"))
    canonical_observed_vina = _canonical_vina_version(observed_vina)
    meeko_version = _safe_version(observed_versions.get("meeko"))
    if (
        observed_vina is None
        or canonical_observed_vina is None
        or canonical_observed_vina != canonical_vina_version
    ):
        failures.append("tool_version_mismatch")
    if meeko_version is not None:
        versions["meeko"] = meeko_version
if set(versions) != {"vina", "meeko"}:
    failures.append("tool_version_invalid")
```

The result provenance remains the independent Vina source and must match the
Broker validation event. Meeko remains mandatory. The existing standalone
acceptance caller omits the new argument and therefore continues to use its
sacrificial observer version probes.

- [ ] **Step 4: Preserve bounded elapsed time for failed ToolResults**

In the early failed-result branch, preserve only the safe structured latency:

```python
if not bool(getattr(result, "success", False)):
    failed = _empty_run(trace_id, [_error_code(result)])
    failed["warning_codes"] = _safe_warning_codes(getattr(result, "warnings", []))
    elapsed = getattr(result, "elapsed_ms", None)
    if type(elapsed) is int and 0 <= elapsed <= 420_000:
        failed["latency_ms"] = elapsed
    return failed
```

Add this regression asserting a failed ToolResult with `elapsed_ms=271000`
retains that value while exception text and absolute paths remain absent:

```python
def test_failed_projection_preserves_safe_latency_without_error_text(tmp_path: Path) -> None:
    acceptance = _module()
    result = SimpleNamespace(
        success=False,
        elapsed_ms=271_000,
        warnings=[],
        error=SimpleNamespace(
            code=SimpleNamespace(value="tool_timeout"),
            message="Authorization: secret at /host/private/path",
        ),
    )
    projected = acceptance._project_result(
        result,
        trace_id="failed-latency",
        project_root=tmp_path,
        observer=_FakeObserver(),
    )
    encoded = json.dumps(projected)
    assert projected["status"] == "failed"
    assert projected["latency_ms"] == 271_000
    assert projected["failure_codes"] == ["tool_timeout"]
    assert "Authorization" not in encoded
    assert "/host/private" not in encoded
```

- [ ] **Step 5: Wire lifecycle identity and versions into `measured_run()`**

After `diagnostics_delta(before, after)`, evaluate the lifecycle before projecting
the result:

```python
result_succeeded = getattr(result, "success", False) is True
lifecycle = evaluate_lifecycle(
    delta["events"],
    result_succeeded=result_succeeded,
)
projected = _project_result(
    result,
    trace_id=lifecycle.trace_id,
    project_root=self.project_root,
    observer=observer,
    validated_tool_versions=lifecycle.tool_versions,
)
failures = sorted(
    set(projected.get("failure_codes", ()))
    | set(lifecycle.failure_codes)
)
status = (
    "passed"
    if projected.get("status") == "passed" and lifecycle.events_complete
    else "failed"
)
```

Build the run with the Broker `trace_id`, lifecycle cleanup status, lifecycle
failure class, and lifecycle completeness. Use `projected["cleanup_status"]`
only for successful results and require it to equal the lifecycle cleanup
status. Preserve every projected scientific failure code; do not replace it
with `unknown_control_plane_failure` when telemetry truthfully reports no
control-plane class.

- [ ] **Step 6: Run both projector and stability suites**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest \
  tests/sandbox_broker/test_real_opensandbox_acceptance.py \
  tests/sandbox_broker/test_stability_soak.py \
  -q -p no:cacheprovider
```

Expected: all tests pass; explicit Broker versions work only when complete,
safe, and Vina-consistent.

- [ ] **Step 7: Commit the provenance correction**

```powershell
git add scripts/run_opensandbox_docking_acceptance.py \
  scripts/run_opensandbox_stability_soak.py \
  tests/sandbox_broker/test_real_opensandbox_acceptance.py \
  tests/sandbox_broker/test_stability_soak.py
git commit -m "fix: use broker validation provenance in soak"
```

### Task 4: Prove failure truthfulness and report safety offline

**Files:**
- Modify: `tests/sandbox_broker/test_stability_soak.py:301-519`
- Modify only if a test exposes a defect: `scripts/run_opensandbox_stability_soak.py`

- [ ] **Step 1: Add the report-shaped success/failure replay test**

Construct thirty sanitized measured windows: eighteen complete successful
lifecycles and twelve complete failed lifecycles whose ToolResults use the real
failure distribution (`provider_error=5`, `tool_timeout=5`,
`invalid_output=2`):

```python
def test_corrected_report_shape_keeps_eighteen_successes_and_twelve_failures() -> None:
    failure_sequence = [
        *(["provider_error"] * 5),
        *(["tool_timeout"] * 5),
        *(["invalid_output"] * 2),
    ]
    runs: list[dict[str, object]] = []
    for index in range(1, 31):
        trace_id = f"broker-replay-{index:02d}"
        job_id = f"{index:032x}"
        if index <= 18:
            evidence = soak.evaluate_lifecycle(
                successful_lifecycle(trace_id, job_id),
                result_succeeded=True,
            )
            runs.append(valid_run(index, trace_id=evidence.trace_id))
            continue
        events = successful_lifecycle(trace_id, job_id)
        del events[8:10]
        events[7]["outcome"] = "failed"
        events[-1]["outcome"] = "failed"
        evidence = soak.evaluate_lifecycle(events, result_succeeded=False)
        runs.append(
            valid_run(
                index,
                trace_id=evidence.trace_id,
                status="failed",
                pose_count=0,
                best_energy=None,
                artifact_present=False,
                artifact_path=None,
                artifact_sha256=None,
                artifact_sha256_valid=False,
                vina_version=None,
                meeko_version=None,
                failure_codes=[failure_sequence[index - 19]],
                events_complete=evidence.events_complete,
            )
        )

    before = diagnostics(0)
    before["telemetry"]["phase_latency_ms"] = {}
    report = soak.build_report(
        runs=runs,
        diagnostics=soak.diagnostics_delta(before, diagnostics(1)),
        idempotency={"passed": True, "sandbox_count": 1},
        queue={"passed": True, "accepted": 9, "saturated": 1},
        probes={"cancellation": True, "timeout": True, "security": True},
        running_labelled_containers=0,
    )

    assert report["status"] == "failed"
    assert report["pass_rate"] == 0.6
    assert report["event_completeness_rate"] == 1.0
assert len(runs) == 30
assert sum(run["status"] == "passed" for run in runs) == 18
assert sum(run["events_complete"] for run in runs) == 30
assert Counter(
    code
    for run in runs
    for code in run["failure_codes"]
    if code in {"provider_error", "tool_timeout", "invalid_output"}
) == {
    "provider_error": 5,
    "tool_timeout": 5,
    "invalid_output": 2,
}
assert all(
    "tool_version_invalid" not in run["failure_codes"]
    and "tool_version_mismatch" not in run["failure_codes"]
    for run in runs
    if run["status"] == "passed"
)
```

The report must remain `failed` with pass rate `0.6`; correcting evaluator
false negatives must not erase the twelve real failures.

- [ ] **Step 2: Add redaction and non-clobber assertions to the new path**

Use this credential and non-clobber regression:

```python
def test_corrected_replay_remains_redacted_and_non_clobbering(tmp_path: Path) -> None:
    secret = "sk-" + "x" * 24
    with pytest.raises(soak.SafeReportError):
        soak.validate_run(valid_run(1, warning_codes=[secret]))

    destination = tmp_path / "preserved-failed.json"
    failed = soak.gate_report("failed", "runtime_failed")
    soak.atomic_write_report(destination, failed)
    original = destination.read_bytes()
    with pytest.raises(soak.SafeReportError):
        soak.atomic_write_report(
            destination,
            soak.gate_report("passed", "runtime_failed"),
        )
    assert destination.read_bytes() == original
    assert secret.encode() not in original
```

- [ ] **Step 3: Add cleanup/terminal disagreement tests**

For successful and failed lifecycles, add this cleanup disagreement test:

```python
@pytest.mark.parametrize("result_succeeded", [True, False])
def test_lifecycle_rejects_cleanup_terminal_disagreement(
    result_succeeded: bool,
) -> None:
    events = successful_lifecycle()
    if not result_succeeded:
        del events[8:10]
        events[7]["outcome"] = "failed"
        events[-1]["outcome"] = "failed"
    terminal = next(event for event in events if event["phase"] == "job_terminal")
    cleanup = next(
        event for event in events if event["phase"] == "cleanup_completed"
    )
    terminal["cleanup_status"] = "failed"
    cleanup["cleanup_status"] = "succeeded"

    evidence = soak.evaluate_lifecycle(
        events,
        result_succeeded=result_succeeded,
    )
    assert evidence.events_complete is False
    assert evidence.failure_codes == ("events_incomplete",)
```

Repeat with the inverse statuses. Both must set `events_complete=False`; a
failed ToolResult may remain failed but cannot claim complete lifecycle evidence.

- [ ] **Step 4: Run the offline correction suite**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest \
  tests/sandbox_broker/test_stability_soak.py \
  tests/sandbox_broker/test_real_opensandbox_acceptance.py \
  -q -p no:cacheprovider
```

Expected: all tests pass entirely offline and the corrected report-shaped
fixture reports exactly 18 scientific successes and 12 retained failures.

- [ ] **Step 5: Commit any test-driven correction**

If Step 4 required a production correction, stage only the test and the exact
script it exercises:

```powershell
git add tests/sandbox_broker/test_stability_soak.py scripts/run_opensandbox_stability_soak.py
git commit -m "test: lock soak failure truthfulness"
```

If no production correction was required, commit only the tests:

```powershell
git add tests/sandbox_broker/test_stability_soak.py
git commit -m "test: lock soak failure truthfulness"
```

### Task 5: Full local verification and independent review

**Files:**
- Modify only for verified review findings: files already listed in Tasks 1-4

- [ ] **Step 1: Run focused Broker suites**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest \
  tests/sandbox_broker/test_stability_soak.py \
  tests/sandbox_broker/test_real_opensandbox_acceptance.py \
  tests/sandbox_broker/test_telemetry.py \
  tests/sandbox_broker/test_service.py \
  -q -p no:cacheprovider
```

Expected: all tests pass; no QEMU-only test is silently counted as real.

- [ ] **Step 2: Run the complete Broker regression and compilation**

```powershell
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m pytest \
  tests/sandbox_broker -q -p no:cacheprovider
C:\Users\xkx52\.conda\envs\MedChat\python.exe -m compileall -q src scripts
C:\Users\xkx52\.conda\envs\MedChat\python.exe \
  scripts/validate_opensandbox_deployment.py --static
```

Expected baseline: the Broker suite passes with only explicit platform/opt-in
skips; `compileall` exits `0`; static deployment validation passes.

- [ ] **Step 3: Run placeholder, secret, and scope scans**

```powershell
rg -n "TBD|TODO|implement later|fill in details" \
  scripts/run_opensandbox_stability_soak.py \
  scripts/run_opensandbox_docking_acceptance.py \
  tests/sandbox_broker/test_stability_soak.py \
  tests/sandbox_broker/test_real_opensandbox_acceptance.py
git diff --check
git status --short
```

Expected: no newly introduced placeholder; `git diff --check` is clean; no
runtime report, key, VM asset, or unrelated file is staged.

- [ ] **Step 4: Perform independent code review**

Invoke the `requesting-code-review` skill against the branch diff from
`3defbbb`. Require review of these exact properties:

- diagnostics-window identity is independent of the caller idempotency key;
- multiple identities and ambiguous validation events fail closed;
- failed lifecycles never acquire synthetic success phases;
- Broker Vina matches ToolResult Vina and Meeko is mandatory;
- standalone real acceptance still uses observer versions by default;
- scientific failures and cleanup failures remain failures;
- report schema, redaction, atomic non-clobber, and CLI stay compatible.

Expected: no unresolved Critical or Important finding. For each verified
finding, first add a failing regression, then apply the smallest fix, rerun the
focused tests, and commit as:

```powershell
git add scripts/run_opensandbox_stability_soak.py \
  scripts/run_opensandbox_docking_acceptance.py \
  tests/sandbox_broker/test_stability_soak.py \
  tests/sandbox_broker/test_real_opensandbox_acceptance.py
git commit -m "fix: address soak evaluator review"
```

### Task 6: Record the corrected evidence boundary and prepare runtime planning

**Files:**
- Modify: `docs/handoff/latest.md`

- [ ] **Step 1: Update the handoff with only verified local results**

Append a section containing the following fixed facts plus the literal reviewed
commit returned by `git rev-parse HEAD` and the literal pass/skip counts printed
by the verification commands. Do not write angle-bracket placeholders or infer
counts that were not printed:

```markdown
## OpenSandbox soak evaluator correction

- Lifecycle correlation: Broker `(trace_id, job_id)` from each diagnostics
  window; worker idempotency keys are no longer treated as Broker trace IDs.
- Measured provenance: Vina/Meeko from `validation_completed`; Vina also agrees
  with ToolResult provenance. Intrusive measured probes remain disabled.
- Offline preserved-shape result: 18 successful scientific runs remain
  successful at evaluator level; 5 provider errors, 5 tool timeouts, and 2
  invalid outputs remain failures. This is not a new real soak result.
- Formal QEMU soak: not run in this sub-project.
- Next planning inputs: corrected failure evidence for provider, worker timeout,
  invalid output, queue, cancellation, and timeout-probe boundaries.
```

Prepend `Tested commit:` using the exact `git rev-parse HEAD` output. Append a
`Verification:` bullet that copies the exact pytest pass/skip counts and records
only `passed` or the stable failure code for compileall, static validation, and
independent review.

- [ ] **Step 2: Commit the handoff and verify a clean worktree**

```powershell
git add docs/handoff/latest.md
git commit -m "docs: record soak evaluator correction"
git status --short
```

Expected: the worktree is clean. Runtime reports under `outputs/`, QEMU images,
SSH keys, environment files, and credentials are neither staged nor committed.

- [ ] **Step 3: Write the second implementation plan from corrected evidence**

Invoke `writing-plans` again for a separate
`opensandbox-real-stability-remediation` plan. Its input must be the corrected
failure boundary from this sub-project plus sanitized Broker counters/events.
That plan must define the exact project-controlled runtime fixes, cancellation
and timeout probe corrections, queue verification, new reviewed release
generation, and the one permitted formal 30-job QEMU run. Do not start that run
until its local tests, failure injection, static/runtime validation, and
independent review pass.

## Completion gate

This plan is complete only when Tasks 1-6 pass, the worktree is clean, and the
handoff explicitly says that no new formal QEMU soak was run. Evaluator
correction alone does not authorize promotion and does not imply that the real
OpenSandbox runtime is stable.
