from __future__ import annotations

import hashlib
import json
import os
import threading
from pathlib import Path
from typing import Any

import httpx
import pytest

from src.agent.contracts import AgentErrorCode, ObservationStatus
from src.docking.sandbox_runner import SandboxDockingRunner


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
        "docking_inputs": {"receptor_provided": True, "ligand_provided": True},
    }
    assert sleeps == [0.5, 0.5]
    assert progress
    assert [request.method for request in requests] == ["POST", "GET", "GET", "GET", "GET"]
    if os.name == "posix":
        assert expected_pose.parent.stat().st_mode & 0o777 == 0o700


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

    monkeypatch.setattr(
        "src.docking.sandbox_runner.execute_tool_compat",
        lambda *_args, **_kwargs: pytest.fail("local fallback is forbidden"),
        raising=False,
    )
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
        raise AssertionError("invalid manifest must prevent artifact download")

    result = _runner(tmp_path, handler).execute(payload, job_id="task-7")

    assert result.success is False
    assert result.data is None
    assert str(tmp_path) not in result.message
    assert requests == ["/v1/docking/jobs", f"/v1/docking/jobs/{BROKER_JOB_ID}/manifest"]
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


def test_post_replace_failure_removes_only_the_uncommitted_pose(
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

    real_chmod = os.chmod

    def fail_final_chmod(path: os.PathLike[str] | str, mode: int) -> None:
        if Path(path).name == "result.pdbqt":
            raise OSError("post-replace failure")
        real_chmod(path, mode)

    monkeypatch.setattr("src.docking.sandbox_runner.os.chmod", fail_final_chmod)
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


def test_from_env_creates_a_fresh_uds_transport_for_each_execution(
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
    assert calls == ["task-1", "task-2"]


def test_cancel_uses_a_fresh_uds_transport_after_request_client_closes(
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

    assert result.status == ObservationStatus.CANCELLED
    assert len(transports) == 2
    assert paths == [
        "/v1/docking/jobs",
        f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel",
    ]
