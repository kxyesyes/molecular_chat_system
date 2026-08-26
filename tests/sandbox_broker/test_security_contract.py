"""Cross-boundary security and scientific failure contracts for OpenSandbox."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from scripts.run_temporal_docking_acceptance import summarize_runs
from src.agent.validators import AgentResultValidator
from src.docking.sandbox_runner import SandboxDockingRunner
from src.sandbox_broker.app import create_app
from src.sandbox_broker.artifacts import ArtifactRegistry
from src.sandbox_broker.models import BrokerErrorCode
from src.sandbox_broker.opensandbox_client import OpenSandboxClient, SandboxHandle
from src.sandbox_broker.service import SandboxBrokerService
from src.sandbox_broker.store import BrokerStore
from tests.sandbox_broker.test_api import _config, _service, _submit_api
from tests.sandbox_broker.test_opensandbox_client import FakeSandboxFactory
from tests.sandbox_broker.test_worker_runner import (
    ARTIFACT_ID,
    BROKER_JOB_ID,
    _inputs,
    _job,
    _manifest,
    _pose,
    _runner,
)


_DANGEROUS_POLICY_ATTEMPTS = [
    pytest.param({"image": "attacker/image:latest"}, id="image"),
    pytest.param({"command": "cat /etc/passwd"}, id="command"),
    pytest.param({"entrypoint": ["sh", "-c"]}, id="entrypoint"),
    pytest.param({"env": {"TOKEN": "attacker"}}, id="env"),
    pytest.param({"mounts": ["/:/host"]}, id="mounts"),
    pytest.param({"network_policy": "allow-all"}, id="network-policy"),
    pytest.param({"Image": "attacker/image:latest"}, id="case-image"),
    pytest.param({"COMMAND": "id"}, id="case-command"),
    pytest.param({"entryPoint": ["cmd.exe"]}, id="alias-entrypoint"),
    pytest.param({"environment": {"TOKEN": "attacker"}}, id="alias-env"),
    pytest.param({"volumes": ["/:/host"]}, id="alias-mounts"),
    pytest.param({"networkPolicy": "allow-all"}, id="alias-network-policy"),
    pytest.param(
        {
            "runtime": {
                "image": "attacker/image:latest",
                "command": "id",
                "entrypoint": ["sh"],
                "env": {"TOKEN": "attacker"},
                "mounts": ["/:/host"],
                "network_policy": "allow-all",
            }
        },
        id="nested-runtime",
    ),
]


def _assert_non_scientific(result: Any) -> None:
    payload = result.to_legacy_dict()
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True).lower()
    assert result.success is False
    for forbidden in (
        '"binding_energy"',
        '"pose_file"',
        "kcal/mol",
        "remark vina result",
        "model 1",
        "endmdl",
    ):
        assert forbidden not in encoded
    assert payload["data"] is None
    assert payload["artifacts"] == []
    assert payload["quality"].get("real_execution") is not True


@pytest.mark.parametrize("attempt", _DANGEROUS_POLICY_ATTEMPTS)
def test_caller_cannot_control_sandbox_policy_or_reach_sdk(
    tmp_path: Path,
    attempt: dict[str, Any],
) -> None:
    service, store, config, sandbox_client = _service(tmp_path)
    app = create_app(config, service=service)

    with TestClient(app) as client:
        response = _submit_api(
            client,
            key="adversarial-policy",
            parameters={
                "center": [1, 2, 3],
                "size": [20, 20, 20],
                **attempt,
            },
        )

    assert response.status_code == 422
    assert response.json() == {"error": {"code": "invalid_input"}}
    assert sandbox_client.create_count == 0
    assert sandbox_client.run_count == 0
    assert store.jobs_updated_before(time.time() + 1) == []


@pytest.mark.parametrize(
    "failure",
    [
        BrokerErrorCode.OPENSANDBOX_UNAVAILABLE,
        BrokerErrorCode.PROVISIONING_FAILED,
        BrokerErrorCode.UPLOAD_FAILED,
        BrokerErrorCode.EXECUTION_TIMEOUT,
        BrokerErrorCode.COMMAND_FAILED,
        BrokerErrorCode.SCIENTIFIC_OUTPUT_INVALID,
        BrokerErrorCode.ARTIFACT_FAILED,
        BrokerErrorCode.CLEANUP_FAILED,
    ],
)
def test_every_broker_failure_returns_no_scientific_claim(
    tmp_path: Path,
    failure: BrokerErrorCode,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    requests: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(
                202,
                json=_job(
                    "failed",
                    receptor,
                    ligand,
                    error_code=failure.value,
                    cleanup_status="failed" if failure is BrokerErrorCode.CLEANUP_FAILED else "succeeded",
                ),
            )
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError(request.url.path)

    result = _runner(tmp_path, handler).execute(
        payload,
        job_id=f"failure-{failure.value}",
    )

    _assert_non_scientific(result)
    assert requests[-1] == f"/v1/docking/jobs/{BROKER_JOB_ID}/cancel"


def _corrupt_manifest(
    manifest: dict[str, Any],
    corruption: str,
) -> dict[str, Any]:
    if corruption == "nan-energy":
        manifest["best_energy"] = math.nan
    elif corruption == "inf-energy":
        manifest["best_energy"] = math.inf
    elif corruption == "wrong-hash":
        manifest["artifacts"][0]["sha256"] = "f" * 64
    elif corruption == "wrong-path":
        manifest["artifacts"][0]["path"] = "/workspace/output/poses/result.pdbqt"
    elif corruption == "runc":
        manifest["provenance"]["secure_runtime"] = "runc"
    elif corruption == "demo":
        manifest["provenance"]["demo_mode"] = True
    elif corruption == "fallback":
        manifest["provenance"]["fallback_used"] = True
    elif corruption == "cleanup":
        manifest["provenance"]["cleanup_status"] = "failed"
    else:  # pragma: no cover - protects the parametrized test itself
        raise AssertionError(corruption)
    return manifest


@pytest.mark.parametrize(
    "corruption",
    [
        "nan-energy",
        "inf-energy",
        "wrong-hash",
        "wrong-path",
        "runc",
        "demo",
        "fallback",
        "cleanup",
    ],
)
def test_corrupted_success_manifest_cannot_reach_scientific_success(
    tmp_path: Path,
    corruption: str,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    pose = _pose(-7.2)
    downloads = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal downloads
        if request.url.path == "/v1/docking/jobs":
            return httpx.Response(202, json=_job("succeeded", receptor, ligand))
        if request.url.path.endswith("/manifest"):
            return httpx.Response(
                200,
                json=_corrupt_manifest(_manifest(receptor, ligand, pose), corruption),
            )
        if request.url.path.endswith(f"/artifacts/{ARTIFACT_ID}"):
            downloads += 1
            return httpx.Response(
                200,
                stream=httpx.ByteStream(pose),
                headers={
                    "Content-Length": str(len(pose)),
                    "Content-Type": "chemical/x-pdbqt",
                },
            )
        if request.url.path.endswith("/cancel"):
            return httpx.Response(202, json=_job("cancelled", receptor, ligand))
        raise AssertionError(request.url.path)

    result = _runner(tmp_path, handler).execute(
        payload,
        job_id=f"corrupt-{corruption}",
    )

    _assert_non_scientific(result)
    assert not (tmp_path / "output" / f"docking_corrupt-{corruption}" / "result.pdbqt").exists()
    if corruption != "wrong-hash":
        assert downloads == 0


def test_verified_worker_result_passes_domain_trust_gate(tmp_path: Path) -> None:
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
        raise AssertionError(request.url.path)

    worker_result = _runner(tmp_path, handler).execute(payload, job_id="trusted-worker")
    validated = AgentResultValidator().validate_tool_result(worker_result)

    assert worker_result.quality["cleanup_status"] == "succeeded"
    assert validated.success is True


@pytest.mark.parametrize("case", ["cancelled", "idempotency-conflict"])
def test_cancellation_and_idempotency_errors_remain_non_scientific(
    tmp_path: Path,
    case: str,
) -> None:
    payload, receptor, ligand = _inputs(tmp_path)
    cancelled = threading.Event()
    requests: list[str] = []
    if case == "cancelled":
        cancelled.set()

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request.url.path)
        assert case == "idempotency-conflict"
        return httpx.Response(409, json={"error": {"code": "idempotency_conflict"}})

    result = _runner(tmp_path, handler).execute(
        payload,
        job_id=f"security-{case}",
        cancel_event=cancelled if case == "cancelled" else None,
    )

    _assert_non_scientific(result)
    assert requests == ([] if case == "cancelled" else ["/v1/docking/jobs"])


def test_secret_and_host_details_are_redacted_from_sdk_to_report(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "sk-" + "fake-security-contract-value-123456789"
    endpoint = "127.0.0.1:8080"
    host_path = r"C:\Users\private\receptor.pdb"
    sandbox_id = "sandbox-internal-container-42"
    prompt = "PROMPT_DO_NOT_SERIALIZE"
    receptor_contents = "ATOM_RECEPTOR_CONTENTS_DO_NOT_SERIALIZE"
    ligand_contents = "LIGAND_CONTENTS_DO_NOT_SERIALIZE"
    raw_stderr = "RAW_STDERR_DO_NOT_SERIALIZE"
    injected = (
        f"api_key={secret} endpoint={endpoint} {host_path} {raw_stderr}"
    )

    config = _config(tmp_path)
    factory = FakeSandboxFactory()
    factory.raw.id = sandbox_id
    factory.execution = SimpleNamespace(
        exit_code=9,
        logs=SimpleNamespace(stdout=[], stderr=[]),
    )
    factory.stdout_chunks = [injected]
    factory.stderr_chunks = [injected]
    sdk_client = OpenSandboxClient(config, sandbox_factory=factory)

    sdk_result = asyncio.run(sdk_client.run(SandboxHandle(sandbox_id, factory.raw)))
    sdk_surface = json.dumps(
        {"stdout": sdk_result.stdout, "stderr": sdk_result.stderr},
        ensure_ascii=False,
    )

    store = BrokerStore(config.state_root / "broker.sqlite")
    service = SandboxBrokerService(
        config,
        store,
        sdk_client,
        ArtifactRegistry(config.state_root, store),
    )
    app = create_app(config, service=service)
    caplog.set_level(logging.DEBUG)
    with TestClient(app) as api:
        submitted = _submit_api(api, key="redaction-chain")
        assert submitted.status_code == 202
        job_id = submitted.json()["job_id"]
        deadline = time.monotonic() + 5.0
        while True:
            observed = api.get(f"/v1/docking/jobs/{job_id}")
            assert observed.status_code == 200
            if observed.json()["status"] == "failed":
                break
            assert time.monotonic() < deadline
            time.sleep(0.01)
        api_surface = observed.json()
        stored = store.get(job_id)
        assert stored is not None
        store_surface = {
            "status": stored.status.value,
            "error_code": stored.error_code,
            "warnings": list(stored.warnings),
            "cleanup_status": stored.cleanup_status,
        }

        uds_payloads: list[dict[str, Any]] = []

        def uds_handler(request: httpx.Request) -> httpx.Response:
            uds_payloads.append(api_surface)
            if request.url.path == "/v1/docking/jobs":
                return httpx.Response(202, json=api_surface)
            if request.url.path.endswith("/cancel"):
                cancelled = api.post(f"/v1/docking/jobs/{job_id}/cancel")
                return httpx.Response(cancelled.status_code, json=cancelled.json())
            raise AssertionError(request.url.path)

        payload, _, _ = _inputs(tmp_path)
        runner = SandboxDockingRunner(
            tmp_path / "broker.sock",
            tmp_path / "worker-output",
            transport=httpx.MockTransport(uds_handler),
        )
        tool_result = runner.execute(payload, job_id="redaction-chain")

    report = summarize_runs(
        [
            {
                "status": "failed",
                "backend": "temporal",
                "workflow_id": "redaction-chain",
                "latency_ms": 1,
                "api_key": secret,
                "opensandbox_endpoint": endpoint,
                "sandbox_id": sandbox_id,
                "host_path": host_path,
                "prompt": prompt,
                "receptor_contents": receptor_contents,
                "ligand_contents": ligand_contents,
                "raw_stderr": raw_stderr,
            }
        ]
    )
    surfaces = {
        "sdk": sdk_surface,
        "store": store_surface,
        "api": api_surface,
        "uds": uds_payloads,
        "worker": tool_result.to_legacy_dict(),
        "logs": caplog.text,
        "report": report,
    }
    encoded = json.dumps(surfaces, ensure_ascii=False, default=str)
    for forbidden in (
        secret,
        endpoint,
        host_path,
        sandbox_id,
    ):
        assert forbidden not in encoded
    report_encoded = json.dumps(report, ensure_ascii=False)
    for forbidden in (
        prompt,
        receptor_contents,
        ligand_contents,
        raw_stderr,
    ):
        assert forbidden not in report_encoded
    _assert_non_scientific(tool_result)
