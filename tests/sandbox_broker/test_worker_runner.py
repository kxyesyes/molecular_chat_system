from __future__ import annotations

import hashlib
import json
import os
import socket
import stat
import threading
import time
from pathlib import Path
from typing import Any

import httpx
import httpcore
import pytest

from src.agent.contracts import AgentErrorCode, ObservationStatus
import src.docking.sandbox_runner as runner_module
from src.docking.sandbox_runner import SandboxDockingRunner
from src.sandbox_broker.models import BrokerErrorCode


BROKER_JOB_ID = "1" * 32
ARTIFACT_ID = "2" * 32
IMAGE_DIGEST = "a" * 64


def _pose(*energies: float) -> bytes:
    return "".join(
        f"MODEL {index}\n"
        f"REMARK VINA RESULT: {energy:.3f} 0.000 0.000\n"
        "ROOT\n"
        "ATOM      1  C   LIG A   1       0.000   0.000   0.000  0.00  0.00    +0.000 C\n"
        "ENDROOT\nTORSDOF 0\nENDMDL\n"
        for index, energy in enumerate(energies, start=1)
    ).encode("ascii")


def _provenance(receptor: bytes, ligand: bytes, **updates: Any) -> dict[str, Any]:
    value = {
        "sandbox_id": "sandbox-public-id",
        "image_uri": "medchat-docking",
        "image_digest": IMAGE_DIGEST,
        "secure_runtime": "gvisor",
        "vina_version": "1.2.5",
        "meeko_version": "0.6.1",
        "receptor_sha256": hashlib.sha256(receptor).hexdigest(),
        "ligand_sha256": hashlib.sha256(ligand).hexdigest(),
        "cleanup_status": "succeeded",
        "demo_mode": False,
        "fallback_used": False,
    }
    value.update(updates)
    return value


def _job(
    status: str,
    receptor: bytes,
    ligand: bytes,
    **updates: Any,
) -> dict[str, Any]:
    value = {
        "job_id": BROKER_JOB_ID,
        "trace_id": "trace-public",
        "status": status,
        "phase": status,
        "error_code": None,
        "warnings": ["validated_in_sandbox"] if status == "succeeded" else [],
        "provenance": (
            _provenance(receptor, ligand) if status == "succeeded" else None
        ),
        "cancel_requested": False,
        "cleanup_status": "succeeded" if status == "succeeded" else "not_started",
        "created_at": 1.0,
        "updated_at": 2.0,
    }
    value.update(updates)
    return value


def _manifest(
    receptor: bytes,
    ligand: bytes,
    pose: bytes,
    **updates: Any,
) -> dict[str, Any]:
    value = {
        "schema_version": 1,
        "job_id": BROKER_JOB_ID,
        "trace_id": "trace-public",
        "pose_count": 1,
        "best_energy": -7.2,
        "artifacts": [
            {
                "artifact_id": ARTIFACT_ID,
                "media_type": "chemical/x-pdbqt",
                "sha256": hashlib.sha256(pose).hexdigest(),
                "size_bytes": len(pose),
            }
        ],
        "warnings": ["validated_in_sandbox"],
        "provenance": _provenance(receptor, ligand),
    }
    value.update(updates)
    return value


def _inputs(tmp_path: Path) -> tuple[dict[str, Any], bytes, bytes]:
    receptor = b"ATOM\n"
    ligand = b"$$$$\n"
    receptor_path = tmp_path / "receptor.pdb"
    ligand_path = tmp_path / "ligand.sdf"
    receptor_path.write_bytes(receptor)
    ligand_path.write_bytes(ligand)
    return (
        {
            "receptor_path": str(receptor_path),
            "ligand_path": str(ligand_path),
            "center": [1, 2, 3],
            "size": [20, 20, 20],
            "exhaustiveness": 8,
            "num_modes": 10,
            "energy_range": 3.0,
        },
        receptor,
        ligand,
    )


def _runner(
    tmp_path: Path,
    handler,
) -> SandboxDockingRunner:
    return SandboxDockingRunner(
        tmp_path / "broker.sock",
        tmp_path / "output",
        transport=httpx.MockTransport(handler),
    )


@pytest.mark.parametrize(
    ("role", "suffix"),
    [
        ("receptor", ".pdbqt"),
        ("ligand", ".pdb"),
        ("ligand", ".pdbqt"),
    ],
)
def test_remote_suffix_contract_rejects_before_network(
    tmp_path: Path, role: str, suffix: str
) -> None:
    payload, _, _ = _inputs(tmp_path)
    replacement = tmp_path / f"{role}{suffix}"
    replacement.write_bytes(b"input\n")
    payload[f"{role}_path"] = str(replacement)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        raise AssertionError("unsupported input must not reach the broker")

    result = _runner(tmp_path, handler).execute(payload, job_id=f"bad-{role}-suffix")

    assert result.success is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.INVALID_INPUT
    assert result.error.details == {"reason": "input_invalid"}
    assert calls == 0


@pytest.mark.parametrize(
    ("role", "suffix"),
    [("receptor", ".pdb"), ("ligand", ".sdf"), ("ligand", ".mol")],
)
def test_remote_suffix_contract_allows_exact_matrix_to_reach_network(
    tmp_path: Path, role: str, suffix: str
) -> None:
    payload, _, _ = _inputs(tmp_path)
    replacement = tmp_path / f"{role}{suffix}"
    replacement.write_bytes(b"input\n")
    payload[f"{role}_path"] = str(replacement)
    calls = 0

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(422)

    result = _runner(tmp_path, handler).execute(payload, job_id=f"ok-{role}-suffix")

    assert result.success is False
    assert calls == 1


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class _FailureElapsedClock:
    def __init__(self, elapsed_seconds: float = 0.125) -> None:
        self._started = 10.0
        self._finished = self._started + elapsed_seconds
        self._calls = 0

    def monotonic(self) -> float:
        self._calls += 1
        return self._started if self._calls == 1 else self._finished


@pytest.mark.parametrize(
    "clock_outcome",
    [
        pytest.param(OSError("secret clock failure"), id="clock-error"),
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-inf"),
        pytest.param(float("-inf"), id="negative-inf"),
        pytest.param(True, id="bool"),
    ],
)
def test_initial_clock_failure_returns_local_error_without_network_or_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    clock_outcome: object,
) -> None:
    from src.task_runtime.backends.local import _AGENT_ERROR_MAP
    from src.task_runtime.docking_execution import _tool_failure_reason
    from src.task_runtime.errors import TaskErrorCode

    payload, receptor, ligand = _inputs(tmp_path)
    private_root = tmp_path / "secret-payload"
    private_root.mkdir()
    receptor_path = private_root / "private-receptor.pdb"
    ligand_path = private_root / "private-ligand.sdf"
    receptor_path.write_bytes(receptor)
    ligand_path.write_bytes(ligand)
    payload["receptor_path"] = str(receptor_path)
    payload["ligand_path"] = str(ligand_path)
    requests = 0
    clock_calls = 0

    def monotonic() -> object:
        nonlocal clock_calls
        clock_calls += 1
        if isinstance(clock_outcome, BaseException):
            raise clock_outcome
        return clock_outcome

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        raise AssertionError("invalid initial clock must not reach the broker")

    monkeypatch.setattr(runner_module.time, "monotonic", monotonic)

    result = _runner(tmp_path, handler).execute(
        payload,
        job_id="secret-clock-job",
    )

    encoded = json.dumps(result.to_legacy_dict(), default=str)
    assert clock_calls == 1
    assert requests == 0
    assert result.success is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.INTERNAL_ERROR
    assert result.error.details == {"reason": "process_failed"}
    assert _tool_failure_reason(result.error.code, result.error.details) == "process_failed"
    task_error_code = _AGENT_ERROR_MAP[result.error.code.value]
    assert task_error_code is TaskErrorCode.DOCKING_PROCESS_FAILED
    assert task_error_code is not TaskErrorCode.DOCKING_ARTIFACT_INVALID
    assert result.elapsed_ms == 0
    assert "secret" not in encoded
    assert str(tmp_path) not in encoded


@pytest.mark.parametrize(
    ("failure", "expected_reason"),
    [
        ("smiles", "smiles_not_supported_by_opensandbox"),
        ("invalid", "input_invalid"),
    ],
)
@pytest.mark.parametrize(
    "clock_outcome",
    [
        pytest.param(float("nan"), id="nan"),
        pytest.param(float("inf"), id="positive-inf"),
        pytest.param(float("-inf"), id="negative-inf"),
        pytest.param(OSError("secret clock failure"), id="clock-error"),
    ],
)
def test_early_failure_invalid_elapsed_clock_falls_back_to_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_reason: str,
    clock_outcome: object,
) -> None:
    payload, _, _ = _inputs(tmp_path)
    if failure == "smiles":
        payload.pop("ligand_path")
        payload["smiles"] = "CCO"
    else:
        payload["unexpected"] = True
    calls = 0

    def monotonic() -> float:
        nonlocal calls
        calls += 1
        if calls == 1:
            return 10.0
        if isinstance(clock_outcome, BaseException):
            raise clock_outcome
        assert type(clock_outcome) is float
        return clock_outcome

    monkeypatch.setattr(runner_module.time, "monotonic", monotonic)

    result = _runner(
        tmp_path,
        lambda _request: pytest.fail("early failure must not reach the broker"),
    ).execute(payload, job_id=f"clock-{failure}")

    assert calls == 2
    assert result.success is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.INVALID_INPUT
    assert result.error.details == {"reason": expected_reason}
    assert result.elapsed_ms == 0
    assert "secret clock failure" not in json.dumps(
        result.to_legacy_dict(), default=str
    )


def test_early_failure_negative_elapsed_delta_is_clamped_to_zero(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, _, _ = _inputs(tmp_path)
    payload["unexpected"] = True
    clock = _FailureElapsedClock(elapsed_seconds=-0.125)
    monkeypatch.setattr(runner_module.time, "monotonic", clock.monotonic)

    result = _runner(
        tmp_path,
        lambda _request: pytest.fail("invalid input must not reach the broker"),
    ).execute(payload, job_id="negative-elapsed")

    assert result.success is False
    assert result.elapsed_ms == 0


def test_failure_elapsed_does_not_swallow_result_logic_errors() -> None:
    class BrokenResult:
        @property
        def success(self) -> bool:
            raise RuntimeError("result logic failed")

    with pytest.raises(RuntimeError, match="result logic failed"):
        runner_module._with_failure_elapsed(BrokenResult(), 10.0)


class _TrackingStream(httpx.SyncByteStream):
    def __init__(
        self,
        chunks: list[bytes],
        *,
        after_eof=None,
    ) -> None:
        self.chunks = chunks
        self.after_eof = after_eof
        self.read_count = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.read_count += 1
            yield chunk
        if self.after_eof is not None:
            self.after_eof()

    def close(self) -> None:
        self.closed = True


class _ReadTimeoutStream(httpx.SyncByteStream):
    def __iter__(self):
        raise httpx.ReadTimeout("deadline")
        yield b""  # pragma: no cover


def test_stream_timeout_keeps_type_and_triggers_bounded_broker_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("queued", receptor, ligand))
        if request.url.path == f"/v1/docking/jobs/{BROKER_JOB_ID}":
            return httpx.Response(
                200,
                stream=_ReadTimeoutStream(),
                headers={"Content-Type": "application/json"},
            )
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError

    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    result = _runner(tmp_path, handler).execute(payload, job_id="stream-timeout")

    assert result.success is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.TOOL_TIMEOUT
    assert result.error.details == {"reason": "sandbox_timeout"}
    assert requests == [
        "/v1/docking/jobs",
        f"/v1/docking/jobs/{BROKER_JOB_ID}",
        f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel",
    ]


def test_post_deadline_cancel_has_independent_qemu_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _, receptor, ligand = _inputs(tmp_path)
    deadlines: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel"
        return httpx.Response(
            202,
            json=_job(
                "cancelled",
                receptor,
                ligand,
                error_code="cancelled",
                cancel_requested=True,
            ),
        )

    runner = _runner(tmp_path, handler)

    def transport(deadline: float) -> httpx.BaseTransport:
        deadlines.append(deadline - time.monotonic())
        return httpx.MockTransport(handler)

    monkeypatch.setattr(runner, "_new_transport", transport)
    runner._cancel_best_effort(BROKER_JOB_ID)

    assert len(deadlines) == 1
    assert 1.5 <= deadlines[0] <= 2.0
    assert runner_module._TOTAL_DEADLINE_SECONDS == 420.0


def test_connect_failure_has_stable_unavailable_mapping_without_raw_error(
    tmp_path: Path,
) -> None:
    payload, _, _ = _inputs(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(
            r"secret C:\private\broker.sock token=do-not-leak",
            request=request,
        )

    result = _runner(tmp_path, handler).execute(payload, job_id="connect-failure")

    assert result.success is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE
    assert result.error.details == {"reason": "opensandbox_unavailable"}
    assert "secret" not in result.message.lower()
    assert "private" not in result.message.lower()


def test_connect_timeout_maps_to_unavailable_instead_of_execution_timeout(
    tmp_path: Path,
) -> None:
    payload, _, _ = _inputs(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout(
            "secret broker socket timeout",
            request=request,
        )

    result = _runner(tmp_path, handler).execute(payload, job_id="connect-timeout")

    assert result.success is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE
    assert result.error.details == {"reason": "opensandbox_unavailable"}
    assert "secret" not in result.message.lower()


@pytest.mark.parametrize(
    ("broker_code", "status", "agent_code", "reason"),
    [
        (
            BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
            "failed",
            AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
            "opensandbox_unavailable",
        ),
        (
            BrokerErrorCode.PROVISIONING_FAILED,
            "failed",
            AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
            "opensandbox_unavailable",
        ),
        (
            BrokerErrorCode.UPLOAD_FAILED,
            "failed",
            AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
            "opensandbox_unavailable",
        ),
        (
            BrokerErrorCode.CLEANUP_FAILED,
            "failed",
            AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
            "opensandbox_unavailable",
        ),
        (
            BrokerErrorCode.EXECUTION_TIMEOUT,
            "failed",
            AgentErrorCode.TOOL_TIMEOUT,
            "sandbox_timeout",
        ),
        (
            BrokerErrorCode.EXPIRED,
            "expired",
            AgentErrorCode.TOOL_TIMEOUT,
            "sandbox_timeout",
        ),
        (
            BrokerErrorCode.SCIENTIFIC_OUTPUT_INVALID,
            "failed",
            AgentErrorCode.INVALID_OUTPUT,
            "artifact_invalid",
        ),
        (
            BrokerErrorCode.ARTIFACT_FAILED,
            "failed",
            AgentErrorCode.INVALID_OUTPUT,
            "artifact_invalid",
        ),
        (
            BrokerErrorCode.COMMAND_FAILED,
            "failed",
            AgentErrorCode.INVALID_OUTPUT,
            "artifact_invalid",
        ),
        (
            BrokerErrorCode.INVALID_INPUT,
            "failed",
            AgentErrorCode.INVALID_INPUT,
            "input_invalid",
        ),
        (
            BrokerErrorCode.IDEMPOTENCY_CONFLICT,
            "failed",
            AgentErrorCode.INVALID_INPUT,
            "idempotency_conflict",
        ),
        (
            BrokerErrorCode.UNAUTHORIZED,
            "failed",
            AgentErrorCode.UNAUTHORIZED_TOOL,
            "opensandbox_unauthorized",
        ),
        (
            BrokerErrorCode.QUEUE_SATURATED,
            "failed",
            AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE,
            "sandbox_queue_saturated",
        ),
    ],
)
def test_terminal_broker_error_code_has_stable_agent_mapping(
    tmp_path: Path,
    broker_code: BrokerErrorCode,
    status: str,
    agent_code: AgentErrorCode,
    reason: str,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(
                202,
                json=_job(
                    status,
                    receptor,
                    ligand,
                    error_code=broker_code.value,
                    cleanup_status="succeeded",
                ),
            )
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError(request.url.path)

    result = _runner(tmp_path, handler).execute(
        payload,
        job_id=f"terminal-{broker_code.value}",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code is agent_code
    assert result.error.details == {"reason": reason}
    assert requests[-1] == f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel"


@pytest.mark.parametrize(
    ("broker_code", "status"),
    [
        (BrokerErrorCode.EXECUTION_TIMEOUT, "failed"),
        (BrokerErrorCode.EXPIRED, "expired"),
    ],
)
def test_terminal_broker_timeout_preserves_runner_elapsed_in_projection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    broker_code: BrokerErrorCode,
    status: str,
) -> None:
    import scripts.run_opensandbox_docking_acceptance as acceptance

    payload, receptor, ligand = _inputs(tmp_path)
    clock = _Clock()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            clock.advance(300.125)
            return httpx.Response(
                202,
                json=_job(
                    status,
                    receptor,
                    ligand,
                    error_code=broker_code.value,
                    cleanup_status="succeeded",
                ),
            )
        if request.url.path.endswith("/cancel"):
            clock.advance(0.125)
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError(request.url.path)

    monkeypatch.setattr(runner_module.time, "monotonic", clock.monotonic)

    result = _runner(tmp_path, handler).execute(
        payload,
        job_id=f"secret-{broker_code.value}",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.TOOL_TIMEOUT
    assert type(result.elapsed_ms) is int
    assert result.elapsed_ms == 300250

    projected = acceptance._project_result(
        result,
        trace_id=f"projected-{broker_code.value}",
        project_root=tmp_path,
        observer=object(),
    )
    encoded = json.dumps(projected)
    assert projected["latency_ms"] == 300250
    assert projected["failure_codes"] == ["tool_timeout"]
    assert "secret" not in encoded
    assert str(tmp_path) not in encoded


@pytest.mark.parametrize(
    ("failure", "expected_code"),
    [
        ("cancel", AgentErrorCode.CANCELLED),
        ("http", AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE),
        ("invalid-output", AgentErrorCode.INVALID_OUTPUT),
        ("exception", AgentErrorCode.INVALID_OUTPUT),
    ],
)
def test_representative_runner_failure_exits_record_elapsed_without_leaks(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
    expected_code: AgentErrorCode,
) -> None:
    payload, _, _ = _inputs(tmp_path)
    clock = _FailureElapsedClock()
    cancel = threading.Event()
    if failure == "cancel":
        cancel.set()

    def handler(request: httpx.Request) -> httpx.Response:
        if failure == "http":
            return httpx.Response(
                500,
                text=f"secret traceback at {tmp_path / 'private' / 'broker.py'}",
            )
        if failure == "invalid-output":
            return httpx.Response(
                202,
                json={"unexpected": f"secret {tmp_path / 'private' / 'job.json'}"},
            )
        if failure == "exception":
            raise RuntimeError(
                f"secret transport failure at {tmp_path / 'private' / 'broker.sock'}"
            )
        raise AssertionError("cancelled input must not reach the broker")

    monkeypatch.setattr(runner_module.time, "monotonic", clock.monotonic)

    result = _runner(tmp_path, handler).execute(
        payload,
        job_id=f"failure-{failure}",
        cancel_event=cancel,
    )

    encoded = json.dumps(result.to_legacy_dict(), default=str)
    assert result.success is False
    assert result.error is not None
    assert result.error.code is expected_code
    assert type(result.elapsed_ms) is int
    assert result.elapsed_ms == 125
    assert "secret" not in encoded
    assert str(tmp_path) not in encoded


def test_verified_tool_failure_warning_has_stable_agent_mapping(
    tmp_path: Path,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(
                202,
                json=_job(
                    "failed",
                    receptor,
                    ligand,
                    error_code=BrokerErrorCode.COMMAND_FAILED.value,
                    warnings=["sandbox_tool_failed"],
                    cleanup_status="succeeded",
                ),
            )
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError(request.url.path)

    result = _runner(tmp_path, handler).execute(
        payload,
        job_id="verified-tool-failure",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.PROVIDER_ERROR
    assert result.error.details == {"reason": "sandbox_tool_failed"}


@pytest.mark.parametrize("status_code", [404, 405])
def test_submit_missing_broker_api_maps_to_unavailable(
    tmp_path: Path,
    status_code: int,
) -> None:
    payload, _, _ = _inputs(tmp_path)

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, text="secret raw broker response")

    result = _runner(tmp_path, handler).execute(
        payload,
        job_id=f"submit-{status_code}",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE
    assert result.error.details == {"reason": "opensandbox_unavailable"}
    assert "secret" not in result.message.lower()


def test_submit_unprocessable_input_remains_invalid_input(tmp_path: Path) -> None:
    payload, _, _ = _inputs(tmp_path)

    result = _runner(
        tmp_path,
        lambda _request: httpx.Response(422, text="secret raw broker response"),
    ).execute(payload, job_id="submit-422")

    assert result.success is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.INVALID_INPUT
    assert result.error.details == {"reason": "input_invalid"}
    assert "secret" not in result.message.lower()


@pytest.mark.parametrize("stage", ["poll", "manifest", "artifact"])
@pytest.mark.parametrize("status_code", [404, 405, 422])
def test_post_submit_http_contract_failure_is_not_user_input_and_cancels(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    stage: str,
    status_code: int,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/docking/jobs":
            status = "queued" if stage == "poll" else "succeeded"
            return httpx.Response(202, json=_job(status, receptor, ligand))
        if request.url.path == f"/v1/docking/jobs/{BROKER_JOB_ID}":
            assert stage == "poll"
            return httpx.Response(status_code, text="secret raw poll response")
        if request.url.path.endswith("/manifest"):
            if stage == "manifest":
                return httpx.Response(
                    status_code,
                    text="secret raw manifest response",
                )
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            assert stage == "artifact"
            return httpx.Response(status_code, text="secret raw artifact response")
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError(request.url.path)

    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    result = _runner(tmp_path, handler).execute(
        payload,
        job_id=f"{stage}-{status_code}",
    )

    assert result.success is False
    assert result.error is not None
    assert result.error.code is AgentErrorCode.EXTERNAL_TOOL_UNAVAILABLE
    assert result.error.details == {"reason": "opensandbox_unavailable"}
    assert requests[-1] == f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel"
    assert "secret" not in result.message.lower()


class _DripStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes], clock: _Clock) -> None:
        self.chunks = chunks
        self.clock = clock
        self.read_count = 0
        self.closed = False

    def __iter__(self):
        for chunk in self.chunks:
            self.clock.advance(100.0)
            self.read_count += 1
            yield chunk

    def close(self) -> None:
        self.closed = True


class _BlockingNetworkStream:
    def __init__(self) -> None:
        self.timeouts: list[float | None] = []
        self.closed = False

    def read(self, _max_bytes: int, timeout: float | None = None) -> bytes:
        self.timeouts.append(timeout)
        assert timeout is not None
        time.sleep(timeout)
        raise httpcore.ReadTimeout

    def write(self, _buffer: bytes, timeout: float | None = None) -> None:
        self.timeouts.append(timeout)

    def close(self) -> None:
        self.closed = True

    def start_tls(self, *args, **kwargs):
        raise AssertionError("TLS is forbidden for the UDS broker")

    def get_extra_info(self, _info: str):
        return None


class _BlockingDripNetworkStream(_BlockingNetworkStream):
    def __init__(self) -> None:
        super().__init__()
        self.read_count = 0

    def read(self, _max_bytes: int, timeout: float | None = None) -> bytes:
        self.timeouts.append(timeout)
        assert timeout is not None
        self.read_count += 1
        delay = 0.04
        time.sleep(min(delay, timeout))
        if timeout < delay:
            raise httpcore.ReadTimeout
        return b"x"


def test_absolute_deadline_interrupts_blocking_stream_without_thread_leak() -> None:
    raw = _BlockingNetworkStream()
    before = {thread.ident for thread in threading.enumerate()}
    started = time.monotonic()
    stream = runner_module._AbsoluteDeadlineNetworkStream(
        raw,
        deadline=started + 0.08,
    )

    with pytest.raises(httpcore.ReadTimeout):
        stream.read(1024, timeout=5.0)

    elapsed = time.monotonic() - started
    assert elapsed <= 0.18
    assert raw.timeouts and 0 < raw.timeouts[0] <= 0.081
    assert {thread.ident for thread in threading.enumerate()} == before


def test_absolute_deadline_reclips_every_blocking_drip_read() -> None:
    raw = _BlockingDripNetworkStream()
    started = time.monotonic()
    stream = runner_module._AbsoluteDeadlineNetworkStream(
        raw,
        deadline=started + 0.12,
    )

    with pytest.raises(httpcore.ReadTimeout):
        while True:
            stream.read(1024, timeout=5.0)

    elapsed = time.monotonic() - started
    assert elapsed <= 0.22
    assert raw.read_count <= 4
    assert raw.timeouts == sorted(raw.timeouts, reverse=True)


def _http_response(
    content: bytes,
    *,
    content_type: str = "application/json",
) -> bytes:
    return (
        b"HTTP/1.1 200 OK\r\n"
        + f"Content-Length: {len(content)}\r\n".encode("ascii")
        + f"Content-Type: {content_type}\r\n".encode("ascii")
        + b"Connection: close\r\n\r\n"
        + content
    )


def _http_json_response(status: int, payload: dict[str, Any]) -> bytes:
    content = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode(
        "ascii"
    )
    reason = "Accepted" if status == 202 else "OK"
    return (
        f"HTTP/1.1 {status} {reason}\r\n".encode("ascii")
        + f"Content-Length: {len(content)}\r\n".encode("ascii")
        + b"Content-Type: application/json\r\n"
        + b"Connection: close\r\n\r\n"
        + content
    )


class _ScriptedUnixServer:
    def __init__(self, socket_path: Path, scripts: list[Any]) -> None:
        self.socket_path = socket_path
        self.scripts = scripts
        self.ready = threading.Event()
        self.peer_closed = threading.Event()
        self.requests: list[bytes] = []
        self.error: BaseException | None = None
        self.thread = threading.Thread(target=self._serve, name="test-uds-server")

    def start(self) -> None:
        self.thread.start()
        assert self.ready.wait(timeout=2.0)

    def join(self) -> None:
        self.thread.join(timeout=2.0)
        assert not self.thread.is_alive()
        if self.error is not None:
            raise self.error

    @staticmethod
    def _read_request(connection: socket.socket) -> bytes:
        connection.settimeout(1.0)
        content = bytearray()
        while b"\r\n\r\n" not in content:
            chunk = connection.recv(64 * 1024)
            if not chunk:
                return bytes(content)
            content.extend(chunk)
        head, body = bytes(content).split(b"\r\n\r\n", 1)
        length = 0
        for line in head.split(b"\r\n")[1:]:
            if line.lower().startswith(b"content-length:"):
                length = int(line.split(b":", 1)[1].strip())
        while len(body) < length:
            chunk = connection.recv(min(64 * 1024, length - len(body)))
            if not chunk:
                break
            body += chunk
        return head + b"\r\n\r\n" + body

    def _serve(self) -> None:
        listener: socket.socket | None = None
        try:
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(self.socket_path))
            listener.listen(4)
            listener.settimeout(1.5)
            self.ready.set()
            for script in self.scripts:
                connection, _ = listener.accept()
                with connection:
                    request = self._read_request(connection)
                    self.requests.append(request.split(b"\r\n", 1)[0])
                    try:
                        script(connection)
                    except (BrokenPipeError, ConnectionResetError, socket.timeout):
                        self.peer_closed.set()
                    connection.settimeout(0.5)
                    try:
                        if connection.recv(1) == b"":
                            self.peer_closed.set()
                    except (BrokenPipeError, ConnectionResetError, socket.timeout, OSError):
                        self.peer_closed.set()
        except BaseException as exc:
            self.error = exc
            self.ready.set()
        finally:
            if listener is not None:
                listener.close()


def _send_slow_header(connection: socket.socket) -> None:
    time.sleep(0.45)
    connection.sendall(b"HTTP/1.1 202 Accepted\r\n")


def _send_drip(connection: socket.socket, response: bytes) -> None:
    head, body = response.split(b"\r\n\r\n", 1)
    connection.sendall(head + b"\r\n\r\n")
    width = max(1, len(body) // 12)
    for index in range(0, len(body), width):
        connection.sendall(body[index : index + width])
        time.sleep(0.04)


def _fd_count() -> int | None:
    root = Path("/proc/self/fd")
    return len(list(root.iterdir())) if root.is_dir() else None


@pytest.mark.skipif(os.name != "posix", reason="real Unix socket required")
def test_real_uds_slow_header_is_cut_off_at_absolute_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, _, _ = _inputs(tmp_path)
    socket_path = tmp_path / "broker.sock"
    before_threads = {thread.ident for thread in threading.enumerate()}
    before_fds = _fd_count()
    server = _ScriptedUnixServer(socket_path, [_send_slow_header])
    server.start()
    monkeypatch.setattr(runner_module, "_TOTAL_DEADLINE_SECONDS", 0.2)
    runner = SandboxDockingRunner(socket_path, tmp_path / "output")

    started = time.monotonic()
    result = runner.execute(payload, job_id="slow-header")
    elapsed = time.monotonic() - started
    server.join()

    assert result.success is False
    assert elapsed <= 0.3
    assert server.peer_closed.is_set()
    assert {thread.ident for thread in threading.enumerate()} == before_threads
    if before_fds is not None:
        assert _fd_count() == before_fds
    assert not (tmp_path / "output" / "docking_slow-header").exists()


@pytest.mark.skipif(os.name != "posix", reason="real Unix socket required")
@pytest.mark.parametrize("phase", ["poll", "manifest", "artifact"])
def test_real_uds_drip_is_cut_off_per_socket_read_and_cancel_is_bounded(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    socket_path = tmp_path / "broker.sock"
    succeeded = _http_json_response(202, _job("succeeded", receptor, ligand))
    scripts: list[Any]
    if phase == "poll":
        scripts = [
            lambda connection: connection.sendall(
                _http_json_response(202, _job("queued", receptor, ligand))
            ),
            lambda connection: _send_drip(
                connection,
                _http_json_response(200, _job("succeeded", receptor, ligand)),
            ),
        ]
    elif phase == "manifest":
        scripts = [
            lambda connection: connection.sendall(succeeded),
            lambda connection: _send_drip(
                connection,
                _http_json_response(200, _manifest(receptor, ligand, pose)),
            ),
        ]
    else:
        scripts = [
            lambda connection: connection.sendall(succeeded),
            lambda connection: connection.sendall(
                _http_json_response(200, _manifest(receptor, ligand, pose))
            ),
            lambda connection: _send_drip(
                connection,
                _http_response(pose, content_type="chemical/x-pdbqt"),
            ),
        ]
    scripts.append(_send_slow_header)
    before_threads = {thread.ident for thread in threading.enumerate()}
    before_fds = _fd_count()
    server = _ScriptedUnixServer(socket_path, scripts)
    server.start()
    monkeypatch.setattr(runner_module, "_TOTAL_DEADLINE_SECONDS", 0.2)
    monkeypatch.setattr(runner_module, "_CANCEL_DEADLINE_SECONDS", 0.03)
    runner = SandboxDockingRunner(socket_path, tmp_path / "output")

    started = time.monotonic()
    result = runner.execute(payload, job_id=f"drip-{phase}")
    elapsed = time.monotonic() - started
    server.join()

    assert result.success is False
    assert elapsed <= 0.3
    assert server.requests[-1].endswith(
        f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel HTTP/1.1".encode("ascii")
    )
    assert server.peer_closed.is_set()
    assert {thread.ident for thread in threading.enumerate()} == before_threads
    if before_fds is not None:
        assert _fd_count() == before_fds
    assert not (
        tmp_path / "output" / f"docking_drip-{phase}" / "result.pdbqt"
    ).exists()


def test_runner_uses_broker_manifest_and_publishes_verified_pose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    requests: list[httpx.Request] = []
    polls = iter(["running", "succeeded"])

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        if request.method == "POST" and request.url.path == "/v1/docking/jobs":
            assert request.headers["Idempotency-Key"] == "task-7"
            body = request.read()
            assert b"request_json" in body and b"receptor" in body and b"ligand" in body
            assert str(tmp_path).encode() not in body
            return httpx.Response(202, json=_job("queued", receptor, ligand))
        if request.method == "GET" and request.url.path == f"/v1/docking/jobs/{BROKER_JOB_ID}":
            return httpx.Response(200, json=_job(next(polls), receptor, ligand))
        if request.method == "GET" and request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.method == "GET" and request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            return httpx.Response(
                200,
                stream=httpx.ByteStream(pose),
                headers={
                    "Content-Length": str(len(pose)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        raise AssertionError(f"unexpected request: {request.method} {request.url.path}")

    sleeps: list[float] = []
    monkeypatch.setattr("src.docking.sandbox_runner.time.sleep", sleeps.append)
    progress: list[tuple[str, float]] = []
    result = _runner(tmp_path, handler).execute(
        payload,
        job_id="task-7",
        progress_callback=lambda phase, percent: progress.append((phase, percent)),
    )

    expected_pose = tmp_path / "output" / "docking_task-7" / "result.pdbqt"
    assert result.success is True
    assert result.message == "Real AutoDock Vina docking completed in OpenSandbox."
    assert result.data == {
        "job_id": "task-7",
        "total_poses": 1,
        "best_pose": {"binding_energy": -7.2, "pose_file": str(expected_pose)},
        "pose_file": str(expected_pose),
        "warnings": ["validated_in_sandbox"],
    }
    assert expected_pose.read_bytes() == pose
    assert result.artifacts[0].label == "Docking pose"
    assert result.artifacts[0].path == str(expected_pose)
    assert result.provenance is not None
    assert result.provenance.tool_name == "molecular_docking"
    assert result.provenance.model_name == "AutoDock Vina"
    assert result.provenance.model_version == "1.2.5"
    assert result.provenance.demo_mode is False
    assert result.provenance.fallback_used is False
    assert result.quality == {
        "real_execution": True,
        "execution_backend": "opensandbox",
        "secure_runtime": "gvisor",
        "sandbox_image_digest": IMAGE_DIGEST,
        "cleanup_status": "succeeded",
        "docking_inputs": {"receptor_provided": True, "ligand_provided": True},
    }
    assert sleeps == [0.5, 0.5]
    assert progress
    assert [request.method for request in requests] == ["POST", "GET", "GET", "GET", "GET"]
    if os.name == "posix":
        assert expected_pose.parent.stat().st_mode & 0o777 == 0o700


def test_slow_poll_response_hits_absolute_deadline_before_manifest(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    clock = _Clock()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("queued", receptor, ligand))
        if request.url.path == f"/v1/docking/jobs/{BROKER_JOB_ID}":
            clock.advance(runner_module._TOTAL_DEADLINE_SECONDS + 1.0)
            return httpx.Response(200, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError("deadline expiry must prevent manifest access")

    monkeypatch.setattr(runner_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(runner_module.time, "sleep", lambda seconds: None)

    result = _runner(tmp_path, handler).execute(payload, job_id="deadline-poll")

    assert result.success is False
    assert requests == [
        "/v1/docking/jobs",
        f"/v1/docking/jobs/{BROKER_JOB_ID}",
        f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel",
    ]


def test_slow_manifest_response_hits_absolute_deadline_before_download(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    clock = _Clock()
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            clock.advance(runner_module._TOTAL_DEADLINE_SECONDS + 1.0)
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError("deadline expiry must prevent artifact access")

    monkeypatch.setattr(runner_module.time, "monotonic", clock.monotonic)

    result = _runner(tmp_path, handler).execute(payload, job_id="deadline-manifest")

    assert result.success is False
    assert requests == [
        "/v1/docking/jobs",
        f"/v1/docking/jobs/{BROKER_JOB_ID}/manifest",
        f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel",
    ]


def test_artifact_eof_cannot_cross_absolute_deadline_and_publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    clock = _Clock()
    stream = _TrackingStream(
        [pose],
        after_eof=lambda: clock.advance(
            runner_module._TOTAL_DEADLINE_SECONDS + 1.0
        ),
    )

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            return httpx.Response(
                200,
                stream=stream,
                headers={
                    "Content-Length": str(len(pose)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        raise AssertionError

    monkeypatch.setattr(runner_module.time, "monotonic", clock.monotonic)

    result = _runner(tmp_path, handler).execute(payload, job_id="deadline-eof")

    assert result.success is False
    assert stream.closed is True
    assert not (tmp_path / "output" / "docking_deadline-eof" / "result.pdbqt").exists()


def test_drip_artifact_checks_deadline_for_each_transport_chunk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    clock = _Clock()
    width = max(1, len(pose) // 10)
    chunks = [pose[index : index + width] for index in range(0, len(pose), width)]
    stream = _DripStream(chunks, clock)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            return httpx.Response(
                200,
                stream=stream,
                headers={
                    "Content-Length": str(len(pose)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        raise AssertionError

    monkeypatch.setattr(runner_module.time, "monotonic", clock.monotonic)

    result = _runner(tmp_path, handler).execute(payload, job_id="deadline-drip")

    assert result.success is False
    assert stream.read_count <= int(
        runner_module._TOTAL_DEADLINE_SECONDS // 100.0
    ) + 1
    assert stream.closed is True
    assert not (tmp_path / "output" / "docking_deadline-drip" / "result.pdbqt").exists()


def test_remaining_deadline_clips_manifest_and_download_timeouts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    clock = _Clock()
    observed: dict[str, dict[str, float]] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            clock.advance(runner_module._TOTAL_DEADLINE_SECONDS - 1.0)
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            observed["manifest"] = request.extensions["timeout"]
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            observed["artifact"] = request.extensions["timeout"]
            return httpx.Response(
                200,
                stream=httpx.ByteStream(pose),
                headers={
                    "Content-Length": str(len(pose)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        raise AssertionError

    monkeypatch.setattr(runner_module.time, "monotonic", clock.monotonic)

    result = _runner(tmp_path, handler).execute(payload, job_id="deadline-timeout")

    assert result.success is True
    assert all(value <= 1.0 for timeout in observed.values() for value in timeout.values())


def test_poll_sleep_is_clipped_to_remaining_absolute_deadline(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    clock = _Clock()
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/docking/jobs"
        clock.advance(runner_module._TOTAL_DEADLINE_SECONDS - 0.2)
        return httpx.Response(202, json=_job("queued", receptor, ligand))

    def sleep(seconds: float) -> None:
        sleeps.append(seconds)
        clock.advance(seconds)

    monkeypatch.setattr(runner_module.time, "monotonic", clock.monotonic)
    monkeypatch.setattr(runner_module.time, "sleep", sleep)

    result = _runner(tmp_path, handler).execute(payload, job_id="deadline-sleep")

    assert result.success is False
    assert len(sleeps) == 1
    assert 0 < sleeps[0] <= 0.2


def test_json_response_is_streamed_and_closed_at_one_mib_cap(tmp_path: Path) -> None:
    payload, _, _ = _inputs(tmp_path)
    stream = _TrackingStream([b"x" * (64 * 1024)] * 40)

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/docking/jobs"
        return httpx.Response(
            202,
            stream=stream,
            headers={"Content-Type": "application/json"},
        )

    result = _runner(tmp_path, handler).execute(payload, job_id="oversize-json")

    assert result.success is False
    assert stream.read_count == 17
    assert stream.closed is True


@pytest.mark.parametrize(
    "failure",
    ["poll-extra", "poll-500", "manifest-invalid", "artifact-invalid"],
)
def test_submitted_job_failure_always_ends_with_bounded_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/docking/jobs":
            status = "queued" if failure.startswith("poll-") else "succeeded"
            return httpx.Response(202, json=_job(status, receptor, ligand))
        if request.url.path == f"/v1/docking/jobs/{BROKER_JOB_ID}":
            if failure == "poll-extra":
                return httpx.Response(
                    200,
                    json={**_job("queued", receptor, ligand), "unexpected": True},
                )
            if failure == "poll-500":
                return httpx.Response(500, text="secret broker traceback")
        if request.url.path.endswith("/manifest"):
            manifest = _manifest(receptor, ligand, pose)
            if failure == "manifest-invalid":
                manifest["unexpected"] = True
            return httpx.Response(200, json=manifest)
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            assert failure == "artifact-invalid"
            corrupted = pose[:-1] + b"X"
            return httpx.Response(
                200,
                stream=httpx.ByteStream(corrupted),
                headers={
                    "Content-Length": str(len(corrupted)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError(request.url.path)

    monkeypatch.setattr(runner_module.time, "sleep", lambda _seconds: None)

    result = _runner(tmp_path, handler).execute(payload, job_id=f"cancel-{failure}")

    assert result.success is False
    assert requests[-1] == f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel"
    assert "secret" not in result.message.lower()


def test_pre_submit_input_failure_does_not_attempt_broker_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, _, _ = _inputs(tmp_path)
    payload["unexpected"] = f"secret {tmp_path / 'private' / 'input.json'}"
    requests: list[str] = []
    clock = _FailureElapsedClock()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        raise AssertionError("input rejection must happen before submit")

    monkeypatch.setattr(runner_module.time, "monotonic", clock.monotonic)

    result = _runner(tmp_path, handler).execute(payload, job_id="invalid-input")

    assert result.success is False
    assert result.elapsed_ms == 125
    assert requests == []
    encoded = json.dumps(result.to_legacy_dict(), default=str)
    assert "secret" not in encoded
    assert str(tmp_path) not in encoded


def test_runner_cancels_remotely_without_local_fallback(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    cancel = threading.Event()
    requests: list[tuple[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append((request.method, request.url.path))
        if request.url.path == "/v1/docking/jobs":
            cancel.set()
            return httpx.Response(202, json=_job("queued", receptor, ligand))
        if request.url.path.endswith("/cancel"):
            return httpx.Response(
                202,
                json=_job(
                    "cancelled",
                    receptor,
                    ligand,
                    cancel_requested=True,
                    cleanup_status="succeeded",
                ),
            )
        raise AssertionError("cancelled execution must not poll, download, or fall back")

    result = _runner(tmp_path, handler).execute(payload, job_id="task-7", cancel_event=cancel)

    assert result.success is False
    assert result.status == ObservationStatus.CANCELLED
    assert result.error is not None and result.error.code == AgentErrorCode.CANCELLED
    assert requests == [
        ("POST", "/v1/docking/jobs"),
        ("POST", f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel"),
    ]
    assert not (tmp_path / "output" / "docking_task-7" / "result.pdbqt").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda manifest: {**manifest, "unexpected": True},
        lambda manifest: {**manifest, "best_energy": float("nan")},
        lambda manifest: {
            **manifest,
            "provenance": {**manifest["provenance"], "secure_runtime": "runc"},
        },
        lambda manifest: {
            **manifest,
            "provenance": {**manifest["provenance"], "demo_mode": True},
        },
        lambda manifest: {**manifest, "job_id": "3" * 32},
        lambda manifest: {
            **manifest,
            "artifacts": [{**manifest["artifacts"][0], "media_type": "text/plain"}],
        },
    ],
    ids=["extra", "nan", "runtime", "demo", "identity", "media-type"],
)
def test_runner_rejects_untrusted_manifest_without_download_or_path_leak(
    tmp_path: Path,
    mutation,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=mutation(_manifest(receptor, ligand, pose)))
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError("invalid manifest must prevent artifact download")

    result = _runner(tmp_path, handler).execute(payload, job_id="task-7")

    assert result.success is False
    assert result.data is None
    assert str(tmp_path) not in result.message
    assert requests == [
        "/v1/docking/jobs",
        f"/v1/docking/jobs/{BROKER_JOB_ID}/manifest",
        f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel",
    ]
    assert not (tmp_path / "output" / "docking_task-7" / "result.pdbqt").exists()


@pytest.mark.parametrize("failure", ["length", "digest", "pose-count", "redirect"])
def test_runner_rejects_untrusted_artifact_before_publish(
    tmp_path: Path,
    failure: str,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    manifest = _manifest(receptor, ligand, pose)
    if failure == "digest":
        manifest["artifacts"][0]["sha256"] = "f" * 64
    if failure == "pose-count":
        manifest["pose_count"] = 2

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=manifest)
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            if failure == "redirect":
                return httpx.Response(307, headers={"Location": "http://attacker.invalid/pose"})
            return httpx.Response(
                200,
                stream=httpx.ByteStream(pose),
                headers={
                    "Content-Length": str(len(pose) + (1 if failure == "length" else 0)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        raise AssertionError

    result = _runner(tmp_path, handler).execute(payload, job_id="task-7")

    assert result.success is False
    assert not (tmp_path / "output" / "docking_task-7" / "result.pdbqt").exists()


def test_existing_result_collision_fails_closed_without_overwrite(tmp_path: Path) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    final = tmp_path / "output" / "docking_collision" / "result.pdbqt"
    final.parent.mkdir(parents=True)
    final.write_bytes(b"untrusted-existing-result\n")

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            return httpx.Response(
                200,
                stream=httpx.ByteStream(pose),
                headers={
                    "Content-Length": str(len(pose)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        raise AssertionError

    result = _runner(tmp_path, handler).execute(payload, job_id="collision")

    assert result.success is False
    assert final.read_bytes() == b"untrusted-existing-result\n"


def test_existing_pose_matching_current_manifest_is_reused_without_overwrite(
    tmp_path: Path,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2, -6.8)
    final = tmp_path / "output" / "docking_retry" / "result.pdbqt"
    final.parent.mkdir(parents=True)
    final.write_bytes(pose)
    final.chmod(0o600)
    before = final.stat()
    requests: list[str] = []
    manifest = _manifest(
        receptor,
        ligand,
        pose,
        pose_count=2,
        best_energy=-7.2,
    )

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=manifest)
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError("matching retries must not redownload or replace the pose")

    result = _runner(tmp_path, handler).execute(payload, job_id="retry")

    after = final.stat()
    assert result.success is True
    assert result.data["total_poses"] == 2
    assert final.read_bytes() == pose
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    assert requests == [
        "/v1/docking/jobs",
        f"/v1/docking/jobs/{BROKER_JOB_ID}/manifest",
    ]


@pytest.mark.parametrize(
    "failure",
    ["hash", "energy", "mode", "symlink", "hardlink"],
)
def test_existing_pose_must_strictly_match_manifest_before_reuse(
    tmp_path: Path,
    failure: str,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    existing = pose
    manifest = _manifest(receptor, ligand, pose)
    if failure == "hash":
        existing = _pose(-6.2)
    elif failure == "energy":
        manifest["best_energy"] = -6.2
    elif failure == "mode":
        existing = _pose(-7.2, -6.2)
        manifest = _manifest(
            receptor,
            ligand,
            existing,
            pose_count=1,
            best_energy=-7.2,
        )

    final = tmp_path / "output" / f"docking_reject-{failure}" / "result.pdbqt"
    final.parent.mkdir(parents=True)
    linked: Path | None = None
    if failure == "symlink":
        target = tmp_path / "existing-target.pdbqt"
        target.write_bytes(existing)
        target.chmod(0o600)
        try:
            final.symlink_to(target)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks unavailable: {exc}")
    else:
        final.write_bytes(existing)
        final.chmod(0o600)
        if failure == "hardlink":
            linked = tmp_path / "existing-hardlink.pdbqt"
            try:
                os.link(final, linked)
            except OSError as exc:
                pytest.skip(f"hardlinks unavailable: {exc}")

    before = final.lstat()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=manifest)
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError("an existing pose is never downloaded over or replaced")

    result = _runner(tmp_path, handler).execute(
        payload,
        job_id=f"reject-{failure}",
    )

    after = final.lstat()
    assert result.success is False
    assert (after.st_dev, after.st_ino) == (before.st_dev, before.st_ino)
    if failure != "symlink":
        assert final.read_bytes() == existing
    if linked is not None:
        assert linked.read_bytes() == existing


@pytest.mark.skipif(os.name != "posix", reason="POSIX owner/mode semantics required")
def test_existing_pose_with_non_private_permissions_is_not_reused(tmp_path: Path) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    final = tmp_path / "output" / "docking_bad-mode" / "result.pdbqt"
    final.parent.mkdir(parents=True)
    final.write_bytes(pose)
    final.chmod(0o640)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError

    result = _runner(tmp_path, handler).execute(payload, job_id="bad-mode")

    assert result.success is False
    assert stat.S_IMODE(final.stat().st_mode) == 0o640


def test_posix_publish_uses_descriptor_relative_rename_noreplace() -> None:
    source = Path(runner_module.__file__).read_text(encoding="utf-8")

    assert "renameat2" in source
    assert "RENAME_NOREPLACE" in source


@pytest.mark.skipif(os.name != "posix", reason="POSIX symlink mode semantics required")
def test_output_symlink_is_rejected_before_chmod_side_effect(tmp_path: Path) -> None:
    payload, _, _ = _inputs(tmp_path)
    output = tmp_path / "output"
    output.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o755)
    (output / "docking_output-link").symlink_to(outside, target_is_directory=True)

    result = _runner(
        tmp_path,
        lambda _request: pytest.fail("output rejection must precede network access"),
    ).execute(payload, job_id="output-link")

    assert result.success is False
    assert stat.S_IMODE(outside.stat().st_mode) == 0o755


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory-fd swap test")
def test_parent_swap_during_descriptor_relative_publish_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    job_root = tmp_path / "output" / "docking_parent-swap"
    moved = tmp_path / "output" / "moved-parent-swap"
    real_rename = runner_module._rename_noreplace
    swapped = False

    def swap_then_rename(src, dst, parent_descriptor):
        nonlocal swapped
        if dst == "result.pdbqt" and not swapped:
            os.rename(job_root, moved)
            job_root.mkdir(mode=0o700)
            swapped = True
        return real_rename(src, dst, parent_descriptor)

    monkeypatch.setattr(runner_module, "_rename_noreplace", swap_then_rename)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            return httpx.Response(
                200,
                stream=httpx.ByteStream(pose),
                headers={
                    "Content-Length": str(len(pose)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        raise AssertionError

    result = _runner(tmp_path, handler).execute(payload, job_id="parent-swap")

    assert swapped is True
    assert result.success is False
    assert not (job_root / "result.pdbqt").exists()
    assert not (moved / "result.pdbqt").exists()


@pytest.mark.parametrize("window", ["eof", "validator", "success-return"])
def test_cancellation_windows_remove_uncommitted_pose_and_cancel_broker(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    window: str,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    cancel = threading.Event()
    cancellations: list[str] = []
    stream: httpx.SyncByteStream = httpx.ByteStream(pose)
    if window == "eof":
        stream = _TrackingStream([pose], after_eof=cancel.set)
    if window == "validator":
        from src.task_runtime.docking_execution import _VinaPoseStreamValidator

        real_finish = _VinaPoseStreamValidator.finish

        def finish_then_cancel(self) -> None:
            real_finish(self)
            cancel.set()

        monkeypatch.setattr(_VinaPoseStreamValidator, "finish", finish_then_cancel)

    runner = _runner(tmp_path, lambda _request: pytest.fail("handler not installed"))
    if window == "success-return":
        real_download = runner._download_and_publish

        def download_then_cancel(*args, **kwargs):
            path = real_download(*args, **kwargs)
            cancel.set()
            return path

        monkeypatch.setattr(runner, "_download_and_publish", download_then_cancel)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            return httpx.Response(
                200,
                stream=stream,
                headers={
                    "Content-Length": str(len(pose)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        if request.url.path.endswith("/cancel"):
            cancellations.append(request.url.path)
            return httpx.Response(
                202,
                json=_job(
                    "cancelled",
                    receptor,
                    ligand,
                    cancel_requested=True,
                    cleanup_status="succeeded",
                ),
            )
        raise AssertionError

    runner._transport = httpx.MockTransport(handler)
    result = runner.execute(payload, job_id=f"cancel-{window}", cancel_event=cancel)

    assert result.status == ObservationStatus.CANCELLED
    assert cancellations == [f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel"]
    assert not (
        tmp_path / "output" / f"docking_cancel-{window}" / "result.pdbqt"
    ).exists()


@pytest.mark.skipif(os.name != "posix", reason="POSIX immutable rename publication test")
def test_cancellation_during_publish_removes_linked_pose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    cancel = threading.Event()
    cancellations: list[str] = []
    real_rename = runner_module._rename_noreplace

    def rename_then_cancel(*args, **kwargs):
        result = real_rename(*args, **kwargs)
        cancel.set()
        return result

    monkeypatch.setattr(runner_module, "_rename_noreplace", rename_then_cancel)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            return httpx.Response(
                200,
                stream=httpx.ByteStream(pose),
                headers={
                    "Content-Length": str(len(pose)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        if request.url.path.endswith("/cancel"):
            cancellations.append(request.url.path)
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError

    result = _runner(tmp_path, handler).execute(
        payload, job_id="cancel-publish", cancel_event=cancel
    )

    assert result.status == ObservationStatus.CANCELLED
    assert cancellations == [f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel"]
    assert not (
        tmp_path / "output" / "docking_cancel-publish" / "result.pdbqt"
    ).exists()


def test_smiles_is_stable_invalid_input_without_network_access(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, _, _ = _inputs(tmp_path)
    payload.pop("ligand_path")
    payload["smiles"] = f"secret {tmp_path / 'private' / 'molecule.smi'}"
    clock = _FailureElapsedClock()

    monkeypatch.setattr(runner_module.time, "monotonic", clock.monotonic)

    result = _runner(
        tmp_path,
        lambda _request: pytest.fail("SMILES must be rejected before broker access"),
    ).execute(payload, job_id="smiles")

    assert result.success is False
    assert result.error is not None
    assert result.error.code == AgentErrorCode.INVALID_INPUT
    assert result.error.details == {"reason": "smiles_not_supported_by_opensandbox"}
    assert result.elapsed_ms == 125
    encoded = json.dumps(result.to_legacy_dict(), default=str)
    assert "secret" not in encoded
    assert str(tmp_path) not in encoded


def test_runner_rejects_linked_input_before_network_access(tmp_path: Path) -> None:
    payload, _, _ = _inputs(tmp_path)
    external = tmp_path / "external.pdb"
    external.write_bytes(b"PRIVATE\n")
    receptor = Path(payload["receptor_path"])
    receptor.unlink()
    try:
        receptor.symlink_to(external)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")

    def handler(_request: httpx.Request) -> httpx.Response:
        raise AssertionError("unsafe input must be rejected before network access")

    result = _runner(tmp_path, handler).execute(payload, job_id="task-7")

    assert result.success is False
    assert "PRIVATE" not in result.message
    assert str(external) not in result.message


def test_pre_publish_fchmod_failure_removes_only_the_uncommitted_pose(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(200, json=_manifest(receptor, ligand, pose))
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            return httpx.Response(
                200,
                stream=httpx.ByteStream(pose),
                headers={
                    "Content-Length": str(len(pose)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        raise AssertionError

    if not hasattr(os, "fchmod"):
        pytest.skip("descriptor mode control is unavailable")
    real_fchmod = os.fchmod

    def fail_pose_fchmod(descriptor: int, mode: int) -> None:
        if stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise OSError("pre-publish mode failure")
        real_fchmod(descriptor, mode)

    monkeypatch.setattr("src.docking.sandbox_runner.os.fchmod", fail_pose_fchmod)
    result = _runner(tmp_path, handler).execute(payload, job_id="task-7")
    job_root = tmp_path / "output" / "docking_task-7"

    assert result.success is False
    assert not (job_root / "result.pdbqt").exists()
    assert not list(job_root.glob(".result.*.part"))


def test_from_env_builds_only_an_httpx_uds_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    socket_path = tmp_path / "broker.sock"
    captured: dict[str, Any] = {}
    payload, _, _ = _inputs(tmp_path)
    sentinel = httpx.MockTransport(lambda _request: httpx.Response(500))

    def transport(**kwargs: Any):
        captured.update(kwargs)
        return sentinel

    monkeypatch.setenv("MEDCHAT_SANDBOX_BROKER_SOCKET", str(socket_path))
    monkeypatch.setattr(httpx, "HTTPTransport", transport)

    runner = SandboxDockingRunner.from_env(tmp_path / "output")
    runner.execute(payload, job_id="task-7")

    assert captured == {"uds": str(socket_path)}
    assert runner.socket_path == socket_path


def test_unexpected_http_transport_type_is_closed_and_never_used(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, _, _ = _inputs(tmp_path)
    calls = 0

    class UnexpectedTransport(httpx.BaseTransport):
        def __init__(self) -> None:
            self.closed = False

        def handle_request(self, _request: httpx.Request) -> httpx.Response:
            nonlocal calls
            calls += 1
            return httpx.Response(500)

        def close(self) -> None:
            self.closed = True

    sentinel = UnexpectedTransport()
    monkeypatch.setattr(httpx, "HTTPTransport", lambda **_kwargs: sentinel)
    runner = SandboxDockingRunner(tmp_path / "broker.sock", tmp_path / "output")

    result = runner.execute(payload, job_id="unexpected-transport")

    assert result.success is False
    assert sentinel.closed is True
    assert calls == 0


def test_from_env_closes_each_unexpected_transport_for_each_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    transports: list[object] = []
    calls: list[str] = []

    class OneUseTransport(httpx.BaseTransport):
        def __init__(self) -> None:
            self.closed = False

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            if self.closed:
                raise RuntimeError("transport already closed")
            calls.append(request.headers["Idempotency-Key"])
            return httpx.Response(
                202,
                json=_job(
                    "failed",
                    receptor,
                    ligand,
                    error_code="command_failed",
                    cleanup_status="succeeded",
                ),
            )

        def close(self) -> None:
            self.closed = True

    def transport(**kwargs: Any) -> OneUseTransport:
        assert kwargs == {"uds": str(tmp_path / "broker.sock")}
        created = OneUseTransport()
        transports.append(created)
        return created

    monkeypatch.setenv("MEDCHAT_SANDBOX_BROKER_SOCKET", str(tmp_path / "broker.sock"))
    monkeypatch.setattr(httpx, "HTTPTransport", transport)
    runner = SandboxDockingRunner.from_env(tmp_path / "output")

    first = runner.execute(payload, job_id="task-1")
    second = runner.execute(payload, job_id="task-2")

    assert first.success is False and second.success is False
    assert len(transports) == 2
    assert all(transport.closed for transport in transports)
    assert calls == []


def test_unexpected_transport_cannot_be_used_for_submit_or_cancel(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    cancel = threading.Event()
    transports: list[object] = []
    paths: list[str] = []

    class CloseAwareTransport(httpx.BaseTransport):
        def __init__(self, sequence: int) -> None:
            self.sequence = sequence
            self.closed = False

        def handle_request(self, request: httpx.Request) -> httpx.Response:
            if self.closed:
                raise RuntimeError("transport already closed")
            paths.append(request.url.path)
            if self.sequence == 0:
                cancel.set()
                return httpx.Response(202, json=_job("queued", receptor, ligand))
            return httpx.Response(
                202,
                json=_job(
                    "cancelled",
                    receptor,
                    ligand,
                    cancel_requested=True,
                    cleanup_status="succeeded",
                ),
            )

        def close(self) -> None:
            self.closed = True

    def transport(**kwargs: Any) -> CloseAwareTransport:
        assert kwargs == {"uds": str(tmp_path / "broker.sock")}
        created = CloseAwareTransport(len(transports))
        transports.append(created)
        return created

    monkeypatch.setenv("MEDCHAT_SANDBOX_BROKER_SOCKET", str(tmp_path / "broker.sock"))
    monkeypatch.setattr(httpx, "HTTPTransport", transport)
    runner = SandboxDockingRunner.from_env(tmp_path / "output")

    result = runner.execute(payload, job_id="task-7", cancel_event=cancel)

    assert result.status == ObservationStatus.FAILED
    assert len(transports) == 1
    assert transports[0].closed is True
    assert paths == []
