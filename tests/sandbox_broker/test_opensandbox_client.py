from __future__ import annotations

import asyncio
import ast
import inspect
import json
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from packaging.requirements import Requirement
from packaging.specifiers import SpecifierSet
from packaging.version import Version

import src.sandbox_broker.opensandbox_client as opensandbox_client_module
from src.sandbox_broker.config import BrokerConfig
from src.sandbox_broker.opensandbox_client import (
    OpenSandboxClient,
    SandboxClientError,
    SandboxCommandResult,
    SandboxCreateError,
    SandboxDependencyUnavailableError,
    SandboxDestroyError,
    SandboxHandle,
    SandboxInputError,
    SandboxListError,
    SandboxProtocolError,
    SandboxReadError,
    SandboxRunError,
    SandboxUploadError,
    classify_control_plane_failure,
)
from src.sandbox_broker.telemetry import FailureClass

try:
    import tomllib
except ModuleNotFoundError:  # Python 3.10 deployment baseline
    import tomli as tomllib


SHA256 = "a" * 64
FIXED_COMMAND = (
    "/opt/conda/bin/python /opt/medchat/run_docking.py "
    "--request /workspace/input/request.json --output /workspace/output"
)


@dataclass
class FakeRawSandbox:
    id: str = "sandbox-1"


class FakeSandboxFactory:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.commands: list[str] = []
        self.uploads: list[tuple[str, str | bytes]] = []
        self.reads: list[tuple[str, int]] = []
        self.destroyed: list[str] = []
        self.destroyed_by_id: list[str] = []
        self.raw = FakeRawSandbox()
        self.read_chunks: list[object] = []
        self.read_stream_closes = 0
        self.execution: object = SimpleNamespace(
            exit_code=0,
            logs=SimpleNamespace(stdout=[], stderr=[]),
        )
        self.list_execution: object = SimpleNamespace(exit_code=0)
        self.stdout_chunks: list[object] = []
        self.stderr_chunks: list[object] = []
        self.list_stdout_chunks: list[object] = []
        self.list_stderr_chunks: list[object] = []
        self.run_calls: list[dict[str, object]] = []
        self.failures: dict[str, BaseException] = {}

    def _fail(self, operation: str) -> None:
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    async def create(self, **policy: Any) -> FakeRawSandbox:
        self._fail("create")
        self.create_calls.append(policy)
        return self.raw

    async def upload_text(
        self, raw: FakeRawSandbox, path: str, data: str | bytes
    ) -> None:
        self._fail("upload")
        del raw
        self.uploads.append((path, data))

    async def read_bytes_stream(
        self,
        raw: FakeRawSandbox,
        path: str,
        max_bytes: int,
    ) -> object:
        self._fail("read")
        del raw
        self.reads.append((path, max_bytes))

        async def chunks() -> Any:
            try:
                for chunk in self.read_chunks:
                    yield chunk
            finally:
                self.read_stream_closes += 1

        return chunks()

    async def run(
        self,
        raw: FakeRawSandbox,
        command: str,
        *,
        on_stdout: Any,
        on_stderr: Any,
        skip_accumulation: bool,
    ) -> object:
        is_list_helper = command != FIXED_COMMAND
        self._fail("list" if is_list_helper else "run")
        del raw
        self.commands.append(command)
        self.run_calls.append(
            {
                "command": command,
                "on_stdout": on_stdout,
                "on_stderr": on_stderr,
                "skip_accumulation": skip_accumulation,
            }
        )
        stdout_chunks = self.list_stdout_chunks if is_list_helper else self.stdout_chunks
        stderr_chunks = self.list_stderr_chunks if is_list_helper else self.stderr_chunks
        for chunk in stdout_chunks:
            message = chunk if not isinstance(chunk, str) else SimpleNamespace(text=chunk)
            await on_stdout(message)
        for chunk in stderr_chunks:
            message = chunk if not isinstance(chunk, str) else SimpleNamespace(text=chunk)
            await on_stderr(message)
        return self.list_execution if is_list_helper else self.execution

    async def destroy(self, raw: FakeRawSandbox) -> None:
        self.destroyed.append(raw.id)
        self._fail("destroy")

    async def destroy_by_id(self, sandbox_id: str) -> None:
        self.destroyed_by_id.append(sandbox_id)
        self._fail("destroy_by_id")


class FalseySandboxFactory(FakeSandboxFactory):
    def __bool__(self) -> bool:
        return False


def config(tmp_path: Path) -> BrokerConfig:
    state_root = tmp_path / "state"
    state_root.mkdir()
    socket_root = tmp_path / "run"
    socket_root.mkdir()
    return BrokerConfig(
        state_root=state_root,
        socket_path=socket_root / "broker.sock",
        image_uri="medchat-docking",
        image_digest=SHA256,
        opensandbox_domain="127.0.0.1:8080",
        opensandbox_api_key="test-only-credential",
    )


def _reconciliation_sdk(manager: object) -> object:
    class ConnectionConfig:
        def __init__(self, **_values: object) -> None:
            pass

    class SandboxFilter:
        def __init__(self, **_values: object) -> None:
            pass

    return opensandbox_client_module._OfficialSDK(
        ConnectionConfig=ConnectionConfig,
        Sandbox=object,
        SandboxManager=manager,
        WriteEntry=object,
        SearchEntry=object,
        ExecutionHandlers=object,
        SandboxFilter=SandboxFilter,
    )


def test_adapter_uses_only_pinned_policy_and_fixed_command(tmp_path: Path) -> None:
    async def scenario() -> tuple[FakeSandboxFactory, object, object]:
        fake = FakeSandboxFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

        handle = await client.create("job-1")
        await client.upload_text(handle, "/workspace/input/receptor.pdb", "ATOM\n")
        result = await client.run(handle)
        await client.destroy(handle)
        return fake, handle, result

    fake, handle, result = asyncio.run(scenario())

    assert fake.create_calls == [
        {
            "image": "medchat-docking@sha256:" + "a" * 64,
            "timeout_seconds": 300,
            "resource": {"cpu": "2", "memory": "4Gi"},
            "entrypoint": ["sleep", "infinity"],
            "metadata": {
                "medchat.operation": "molecular_docking",
                "medchat.job_id": "job-1",
            },
        }
    ]
    assert fake.commands == [FIXED_COMMAND]
    assert result.exit_code == 0
    assert result.stdout == ""
    assert result.stderr == ""
    assert fake.destroyed == [handle.sandbox_id]


def test_production_request_uses_gvisor_compatible_internal_network_contract(
    tmp_path: Path,
) -> None:
    root = Path(__file__).parents[2]
    server_config = tomllib.loads(
        (root / "deployment/opensandbox/sandbox.toml").read_text(encoding="utf-8")
    )
    fake = FakeSandboxFactory()

    asyncio.run(OpenSandboxClient(config(tmp_path), sandbox_factory=fake).create("job-1"))

    assert server_config["docker"]["network_mode"] == "medchat-opensandbox"
    assert "egress" not in server_config
    assert "network_default" not in fake.create_calls[0]
    assert "network_policy" not in fake.create_calls[0]


def test_public_api_has_no_dynamic_sandbox_policy_parameters() -> None:
    forbidden = {
        "mount",
        "mounts",
        "image",
        "command",
        "environment",
        "env",
        "network",
        "network_policy",
        "entrypoint",
        "resource",
        "resources",
    }

    for method_name, method in inspect.getmembers(
        OpenSandboxClient,
        predicate=inspect.isfunction,
    ):
        if method_name.startswith("_"):
            continue
        parameters = set(inspect.signature(method).parameters)
        assert parameters.isdisjoint(forbidden), method_name

    constructor_parameters = set(inspect.signature(OpenSandboxClient).parameters)
    assert constructor_parameters.isdisjoint(forbidden)


def test_explicit_falsey_factory_is_not_replaced_by_production_factory(
    tmp_path: Path,
) -> None:
    fake = FalseySandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    handle = asyncio.run(client.create("job-1"))

    assert handle.raw is fake.raw
    assert len(fake.create_calls) == 1


def _requirement_pins(path: Path) -> dict[str, Requirement]:
    requirements: dict[str, Requirement] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        requirement = Requirement(stripped)
        assert requirement.name not in requirements
        assert len(list(requirement.specifier)) == 1
        specifier = next(iter(requirement.specifier))
        assert specifier.operator == "=="
        requirements[requirement.name] = requirement
    return requirements


def test_dependency_profiles_are_exact_and_sdk_compatible() -> None:
    root = Path(__file__).parents[2]
    broker = _requirement_pins(root / "requirements-opensandbox-broker.txt")
    server = _requirement_pins(root / "requirements-opensandbox-server.txt")
    assert set(broker) == {
        "attrs",
        "fastapi",
        "httpx",
        "opensandbox",
        "pydantic",
        "python-dateutil",
        "python-multipart",
        "starlette",
        "uvicorn",
    }
    expected_versions = {
        # OpenSandbox 0.1.15's generated API clients use
        # ``attrs.field(alias=...)`` even though its wheel metadata only
        # declares attrs>=21.3.0.  attrs 21.3.0 therefore resolves cleanly but
        # crashes while importing the SDK.  Keep the runtime-verified pin.
        "attrs": "26.1.0",
        "fastapi": "0.104.1",
        "httpx": "0.27.0",
        "opensandbox": "0.1.15",
        "pydantic": "2.5.0",
        "python-dateutil": "2.8.2",
        "python-multipart": "0.0.6",
        "starlette": "0.27.0",
        "uvicorn": "0.24.0",
    }
    for name, version in expected_versions.items():
        assert str(broker[name].specifier) == f"=={version}"
    assert broker["uvicorn"].extras == {"standard"}

    # All non-extra Requires-Dist entries from the official 0.1.15 wheel.
    sdk_ranges = {
        "attrs": ">=21.3.0",
        "httpx": ">=0.27.0,<1.0",
        "pydantic": ">=2.4.2,<3.0",
        "python-dateutil": ">=2.8.2,<3.0",
    }
    for name, supported_range in sdk_ranges.items():
        assert Version(expected_versions[name]) in SpecifierSet(supported_range)

    assert set(server) == {"opensandbox-server"}
    assert str(server["opensandbox-server"].specifier) == "==0.2.2"
    assert not set(broker).intersection(server)


def test_contract_dataclasses_are_frozen_and_hide_raw() -> None:
    raw = object()
    handle = SandboxHandle("sandbox-1", raw)
    result = SandboxCommandResult(0, "out", "err")

    assert handle.raw is raw
    assert "raw" not in repr(handle)
    assert result == SandboxCommandResult(exit_code=0, stdout="out", stderr="err")
    with pytest.raises(Exception):
        handle.sandbox_id = "changed"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("sandbox_id", "raw"),
    [
        (True, object()),
        ("", object()),
        ("sandbox/1", object()),
        ("sandbox-1", None),
    ],
)
def test_handle_constructor_rejects_invalid_identity_values(
    sandbox_id: object,
    raw: object,
) -> None:
    with pytest.raises(SandboxInputError):
        SandboxHandle(sandbox_id, raw)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("exit_code", "stdout", "stderr"),
    [
        (True, "", ""),
        (0.0, "", ""),
        (0, b"bytes", ""),
        (0, "", None),
        (2**31, "", ""),
    ],
)
def test_command_result_constructor_rejects_type_confusion(
    exit_code: object,
    stdout: object,
    stderr: object,
) -> None:
    with pytest.raises(SandboxProtocolError):
        SandboxCommandResult(exit_code, stdout, stderr)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "job_id",
    [True, 1, "", " job-1", "job/1", "job\\1", "x" * 129],
)
def test_create_rejects_invalid_job_ids_without_calling_factory(
    tmp_path: Path,
    job_id: object,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    with pytest.raises(SandboxInputError):
        asyncio.run(client.create(job_id))  # type: ignore[arg-type]

    assert fake.create_calls == []


@pytest.mark.parametrize(
    "sandbox_id",
    [True, 1, "", " sandbox-1", "sandbox/1", "sandbox\\1", "x" * 129],
)
def test_create_rejects_invalid_server_ids_and_destroys_returned_raw_once(
    tmp_path: Path,
    sandbox_id: object,
) -> None:
    fake = FakeSandboxFactory()
    fake.raw.id = sandbox_id  # type: ignore[assignment]
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    with pytest.raises(SandboxProtocolError):
        asyncio.run(client.create("job-1"))

    assert len(fake.destroyed) == 1


def test_invalid_server_id_preserves_failed_cleanup_certainty(
    tmp_path: Path,
) -> None:
    fake = FakeSandboxFactory()
    fake.failures["destroy"] = ConnectionError("private destroy failure")
    fake.raw.id = "invalid/identifier"
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    with pytest.raises(SandboxProtocolError) as raised:
        asyncio.run(client.create("job-1"))

    assert raised.value.cleanup_confirmed is False
    assert len(fake.destroyed) == 1


@pytest.mark.parametrize(
    ("path", "data"),
    [
        ("/workspace/input/receptor.pdb", 1),
        (1, "text"),
        ("/workspace/input/unknown.txt", "text"),
        ("/workspace/output/result.json", "text"),
        ("/workspace/input/../output/result.json", "text"),
        ("/workspace/input//receptor.pdb", "text"),
        ("/workspace/input/./receptor.pdb", "text"),
        ("/workspace/input/receptor.pdb/", "text"),
        ("/workspace/input\\receptor.pdb", "text"),
        ("/workspace/input/receptor.pdb\x00", "text"),
    ],
)
def test_upload_rejects_type_confusion_aliases_and_unapproved_names(
    tmp_path: Path,
    path: object,
    data: object,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)

    with pytest.raises(SandboxInputError):
        asyncio.run(client.upload_text(handle, path, data))  # type: ignore[arg-type]

    assert fake.uploads == []


@pytest.mark.parametrize(
    "name",
    [
        "receptor.pdb",
        "receptor.pdbqt",
        "ligand.sdf",
        "ligand.mol",
        "ligand.pdb",
        "ligand.pdbqt",
        "request.json",
    ],
)
def test_upload_accepts_every_fixed_server_input_name(
    tmp_path: Path,
    name: str,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)

    asyncio.run(
        client.upload_text(handle, f"/workspace/input/{name}", "fixed input\n")
    )

    assert fake.uploads == [
        (f"/workspace/input/{name}", "fixed input\n")
    ]


def test_upload_preserves_non_utf8_scientific_input_bytes(tmp_path: Path) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)
    legacy_encoded_pdb = (
        b"REMARK legacy path: "
        + bytes([0xC9, 0xA3, 0xC0, 0xCF])
        + b"\nATOM\n"
    )

    asyncio.run(
        client.upload_text(
            handle,
            "/workspace/input/receptor.pdb",
            legacy_encoded_pdb,
        )
    )

    assert fake.uploads == [
        ("/workspace/input/receptor.pdb", legacy_encoded_pdb)
    ]


def test_upload_uses_utf8_byte_limits_for_each_fixed_input(tmp_path: Path) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)

    asyncio.run(
        client.upload_text(handle, "/workspace/input/request.json", "é" * 32_768)
    )
    with pytest.raises(SandboxInputError):
        asyncio.run(
            client.upload_text(
                handle,
                "/workspace/input/request.json",
                "é" * 32_768 + "x",
            )
        )

    assert len(fake.uploads) == 1


@pytest.mark.parametrize(
    "path",
    [
        True,
        "",
        "/workspace/input/request.json",
        "/workspace/output/../input/request.json",
        "/workspace/output//result.json",
        "/workspace/output/./result.json",
        "/workspace/output/result.json/",
        "/workspace/output\\result.json",
        "/workspace/output/result.json\x00",
        "/workspace/outputish/result.json",
    ],
)
def test_read_rejects_noncanonical_or_out_of_root_paths(
    tmp_path: Path,
    path: object,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)

    with pytest.raises(SandboxInputError):
        asyncio.run(client.read_text(handle, path))  # type: ignore[arg-type]

    assert fake.reads == []


def test_read_stream_is_transport_bounded_and_rejects_invalid_or_oversized_bytes(
    tmp_path: Path,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)

    fake.read_chunks = ["not bytes"]
    with pytest.raises(SandboxProtocolError):
        asyncio.run(client.read_text(handle, "/workspace/output/result.json"))

    fake.read_chunks = [b"x" * (64 * 1024)] * 257
    with pytest.raises(SandboxReadError):
        asyncio.run(client.read_text(handle, "/workspace/output/result.json"))

    assert all(limit == 16 * 1024 * 1024 for _, limit in fake.reads)
    assert fake.read_stream_closes == 2


def test_read_stream_returns_strict_utf8_without_using_unbounded_read_api(
    tmp_path: Path,
) -> None:
    fake = FakeSandboxFactory()
    encoded = "配体".encode("utf-8")
    fake.read_chunks = [encoded[:4], encoded[4:]]
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    result = asyncio.run(
        client.read_text(
            SandboxHandle("sandbox-1", fake.raw),
            "/workspace/output/result.json",
        )
    )

    assert result == "配体"
    assert fake.reads == [("/workspace/output/result.json", 16 * 1024 * 1024)]


@pytest.mark.parametrize(
    ("path", "pattern"),
    [
        (True, "*"),
        ("/workspace/output", True),
        ("/workspace/input", "*"),
        ("/workspace/output/..", "*"),
        ("/workspace/output\\poses", "*"),
        ("/workspace/output", "../*"),
        ("/workspace/output", "**/*"),
        ("/workspace/output", "*.exe"),
        ("/workspace/output", "*\x00"),
    ],
)
def test_list_rejects_unsafe_paths_and_patterns(
    tmp_path: Path,
    path: object,
    pattern: object,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)

    with pytest.raises(SandboxInputError):
        asyncio.run(client.list_files(handle, path, pattern))  # type: ignore[arg-type]

    assert fake.commands == []


def test_list_revalidates_deduplicates_and_bounds_server_paths(tmp_path: Path) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)
    valid = "/workspace/output/poses/result.pdbqt"
    fake.list_stdout_chunks = [
        json.dumps(
            [valid, valid, "/workspace/output/result.json"],
            separators=(",", ":"),
        )
    ]

    assert asyncio.run(client.list_files(handle, "/workspace/output", "*")) == [
        valid,
        "/workspace/output/result.json",
    ]

    for unsafe in (
        ["/workspace/output/../input/request.json"],
        ["/workspace/output\\result.json"],
        ["/workspace/outputish/result.json"],
        [1],
        "not-json\n",
        [f"/workspace/output/file-{index}.json" for index in range(257)],
    ):
        if type(unsafe) is str:
            fake.list_stdout_chunks = [unsafe]
        else:
            fake.list_stdout_chunks = [json.dumps(unsafe, separators=(",", ":"))]
        with pytest.raises((SandboxProtocolError, SandboxListError)):
            asyncio.run(client.list_files(handle, "/workspace/output", "*"))


def test_list_accepts_bounded_json_array_when_execd_strips_trailing_newline(
    tmp_path: Path,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)
    valid = "/workspace/output/poses/result.pdbqt"
    fake.list_stdout_chunks = [json.dumps([valid], separators=(",", ":"))]

    assert asyncio.run(
        client.list_files(handle, "/workspace/output/poses", "*.pdbqt")
    ) == [valid]


def test_list_helper_flushes_one_newline_terminated_array_frame() -> None:
    tree = ast.parse(opensandbox_client_module._LIST_HELPER_SOURCE)
    writes = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "write"
        and isinstance(node.func.value, ast.Attribute)
        and node.func.value.attr == "stdout"
        and isinstance(node.func.value.value, ast.Name)
        and node.func.value.value.id == "sys"
    ]

    assert len(writes) == 1
    frame = writes[0].args[0]
    assert isinstance(frame, ast.BinOp)
    assert isinstance(frame.op, ast.Add)
    assert isinstance(frame.right, ast.Constant)
    assert frame.right.value == "\n"


def test_list_257th_file_and_transport_overflow_fail_without_returning_prefix(
    tmp_path: Path,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)
    fake.list_stdout_chunks = ["x" * (1024 * 1024)] * 16

    with pytest.raises(SandboxListError):
        asyncio.run(client.list_files(handle, "/workspace/output", "*.json"))

    fake.list_stdout_chunks = [
        json.dumps(
            [f"/workspace/output/file-{index}.json" for index in range(256)],
            separators=(",", ":"),
        )
    ]
    fake.list_execution = SimpleNamespace(exit_code=75)
    with pytest.raises(SandboxListError):
        asyncio.run(client.list_files(handle, "/workspace/output", "*.json"))

    assert all(call["skip_accumulation"] is True for call in fake.run_calls)
    assert all(call["command"] != FIXED_COMMAND for call in fake.run_calls)


@pytest.mark.parametrize("operation", ["create", "upload", "read", "list", "run"])
def test_operation_errors_are_narrow_and_do_not_disclose_secrets_or_paths(
    tmp_path: Path,
    operation: str,
) -> None:
    fake = FakeSandboxFactory()
    secret = "test-only-credential"
    fake.failures[operation] = RuntimeError(
        f"endpoint=127.0.0.1:8080 key={secret} C:\\Users\\private\\file"
    )
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)
    calls = {
        "create": client.create("job-1"),
        "upload": client.upload_text(
            handle, "/workspace/input/receptor.pdb", "ATOM\n"
        ),
        "read": client.read_text(handle, "/workspace/output/result.json"),
        "list": client.list_files(handle, "/workspace/output", "*"),
        "run": client.run(handle),
    }
    expected = {
        "create": SandboxCreateError,
        "upload": SandboxUploadError,
        "read": SandboxReadError,
        "list": SandboxListError,
        "run": SandboxRunError,
    }

    with pytest.raises(expected[operation]) as raised:
        asyncio.run(calls[operation])

    message = str(raised.value)
    assert secret not in message
    assert "127.0.0.1" not in message
    assert "Users" not in message
    for name, coroutine in calls.items():
        if name != operation:
            coroutine.close()


class ExplosiveFailure(Exception):
    def __str__(self) -> str:
        raise AssertionError("failure text must not be inspected")

    @property
    def body(self) -> object:
        raise AssertionError("failure body must not be inspected")

    @property
    def headers(self) -> object:
        raise AssertionError("failure headers must not be inspected")


@pytest.mark.parametrize(
    ("operation", "failure", "expected"),
    [
        ("create", SimpleNamespace(status_code=500), FailureClass.SERVER_500),
        (
            "metadata",
            SimpleNamespace(response=SimpleNamespace(status_code=502)),
            FailureClass.PROXY_502,
        ),
        ("upload", SimpleNamespace(status_code=413), FailureClass.RESOURCE_LIMIT),
        (
            "command",
            SimpleNamespace(response=SimpleNamespace(status_code=429)),
            FailureClass.RESOURCE_LIMIT,
        ),
        ("create", ConnectionError("private endpoint"), FailureClass.CONNECTION_FAILED),
        (
            "readiness",
            ConnectionRefusedError("private endpoint"),
            FailureClass.CONNECTION_FAILED,
        ),
        (
            "destroy",
            ConnectionResetError("private endpoint"),
            FailureClass.CONNECTION_FAILED,
        ),
        ("create", asyncio.TimeoutError(), FailureClass.CREATE_TIMEOUT),
        ("readiness", TimeoutError(), FailureClass.READINESS_TIMEOUT),
        ("command", asyncio.TimeoutError(), FailureClass.COMMAND_TIMEOUT),
        (
            "metadata",
            TimeoutError(),
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        (
            "upload",
            TimeoutError(),
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        (
            "destroy",
            TimeoutError(),
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        (
            "command",
            RuntimeError("secret body"),
            FailureClass.COMMAND_TRANSPORT_FAILED,
        ),
        (
            "destroy",
            RuntimeError("secret body"),
            FailureClass.DESTROY_FAILED,
        ),
        (
            "metadata",
            RuntimeError("Authorization: bearer private"),
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        (
            "upload",
            RuntimeError("secret body"),
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        (
            "invalid",
            SimpleNamespace(status_code=500),
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        (
            "create",
            SimpleNamespace(status_code=True),
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        (
            "create",
            SimpleNamespace(
                status_code="500",
                response=SimpleNamespace(status_code=502),
            ),
            FailureClass.PROXY_502,
        ),
    ],
)
def test_classify_control_plane_failure_is_bounded_and_sanitized(
    operation: str,
    failure: object,
    expected: FailureClass,
) -> None:
    assert classify_control_plane_failure(operation, failure) is expected


def test_classifier_never_inspects_failure_text_body_or_headers() -> None:
    assert (
        classify_control_plane_failure("metadata", ExplosiveFailure())
        is FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE
    )


def test_sandbox_client_error_one_argument_compatibility_and_stable_attributes() -> None:
    failure = SandboxCreateError("sandbox provisioning failed")

    assert str(failure) == "sandbox provisioning failed"
    assert failure.operation == "metadata"
    assert failure.failure_class is FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE


def test_sandbox_client_error_accepts_strict_classification_attributes() -> None:
    failure = SandboxCreateError(
        "sandbox provisioning failed",
        operation="create",
        failure_class=FailureClass.PROXY_502,
    )

    assert str(failure) == "sandbox provisioning failed"
    assert failure.operation == "create"
    assert failure.failure_class is FailureClass.PROXY_502


@pytest.mark.parametrize(
    ("operation", "failure_class"),
    [
        ("invalid", FailureClass.SERVER_500),
        (1, FailureClass.SERVER_500),
        ("create", "server_500"),
        ("create", None),
    ],
)
def test_sandbox_client_error_rejects_invalid_classification_attributes(
    operation: object,
    failure_class: object,
) -> None:
    with pytest.raises(ValueError):
        SandboxCreateError(
            "sandbox provisioning failed",
            operation=operation,  # type: ignore[arg-type]
            failure_class=failure_class,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize(
    ("factory_operation", "client_operation", "status", "error_type", "message", "operation", "failure_class"),
    [
        (
            "create",
            "create",
            502,
            SandboxCreateError,
            "sandbox provisioning failed",
            "create",
            FailureClass.PROXY_502,
        ),
        (
            "upload",
            "upload",
            None,
            SandboxUploadError,
            "sandbox input upload failed",
            "upload",
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        (
            "read",
            "read",
            None,
            SandboxReadError,
            "sandbox output read failed",
            "metadata",
            FailureClass.CONNECTION_FAILED,
        ),
        (
            "list",
            "list",
            None,
            SandboxListError,
            "sandbox output listing failed",
            "metadata",
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        (
            "run",
            "run",
            None,
            SandboxRunError,
            "sandbox command execution failed",
            "command",
            FailureClass.COMMAND_TRANSPORT_FAILED,
        ),
        (
            "destroy",
            "destroy",
            None,
            SandboxDestroyError,
            "sandbox cleanup failed",
            "destroy",
            FailureClass.DESTROY_FAILED,
        ),
    ],
)
def test_generic_adapter_failures_carry_sanitized_classification(
    tmp_path: Path,
    factory_operation: str,
    client_operation: str,
    status: int | None,
    error_type: type[SandboxClientError],
    message: str,
    operation: str,
    failure_class: FailureClass,
) -> None:
    class AdapterFailure(ConnectionError if client_operation == "read" else RuntimeError):
        status_code = status

        def __str__(self) -> str:
            return "Authorization: bearer private C:\\Users\\private"

    fake = FakeSandboxFactory()
    fake.failures[factory_operation] = AdapterFailure()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)
    calls = {
        "create": lambda: client.create("job-1"),
        "upload": lambda: client.upload_text(
            handle, "/workspace/input/receptor.pdb", "ATOM\n"
        ),
        "read": lambda: client.read_text(handle, "/workspace/output/result.json"),
        "list": lambda: client.list_files(handle, "/workspace/output", "*"),
        "run": lambda: client.run(handle),
        "destroy": lambda: client.destroy(handle),
    }

    with pytest.raises(error_type) as raised:
        asyncio.run(calls[client_operation]())

    assert str(raised.value) == message
    assert raised.value.operation == operation
    assert raised.value.failure_class is failure_class
    assert raised.value.__cause__ is None
    assert raised.value.__suppress_context__ is True


@pytest.mark.parametrize(
    ("factory_operation", "client_operation", "expected_operation"),
    [
        ("create", "create", "create"),
        ("upload", "upload", "upload"),
        ("read", "read", "metadata"),
        ("list", "list", "metadata"),
        ("run", "run", "command"),
        ("destroy", "destroy", "destroy"),
    ],
)
def test_dependency_failures_carry_truthful_public_operation(
    tmp_path: Path,
    factory_operation: str,
    client_operation: str,
    expected_operation: str,
) -> None:
    fake = FakeSandboxFactory()
    fake.failures[factory_operation] = SandboxDependencyUnavailableError(
        "private dependency detail"
    )
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)

    async def invoke() -> None:
        if client_operation == "create":
            await client.create("job-1")
        elif client_operation == "upload":
            await client.upload_text(
                handle, "/workspace/input/receptor.pdb", "ATOM\n"
            )
        elif client_operation == "read":
            await client.read_text(handle, "/workspace/output/result.json")
        elif client_operation == "list":
            await client.list_files(handle, "/workspace/output", "*")
        elif client_operation == "run":
            await client.run(handle)
        else:
            await client.destroy(handle)

    with pytest.raises(SandboxDependencyUnavailableError) as raised:
        asyncio.run(invoke())

    assert str(raised.value) == "the pinned OpenSandbox SDK is unavailable"
    assert raised.value.operation == expected_operation
    assert (
        raised.value.failure_class
        is FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE
    )
    assert "private dependency detail" not in str(raised.value)


@pytest.mark.parametrize(
    ("factory_operation", "client_operation", "error_type", "expected_operation", "failure_class"),
    [
        ("create", "create", SandboxCreateError, "create", FailureClass.CREATE_TIMEOUT),
        (
            "upload",
            "upload",
            SandboxUploadError,
            "upload",
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        (
            "read",
            "read",
            SandboxReadError,
            "metadata",
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        (
            "list",
            "list",
            SandboxListError,
            "metadata",
            FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE,
        ),
        ("run", "run", SandboxRunError, "command", FailureClass.COMMAND_TIMEOUT),
    ],
)
def test_adapter_timeouts_carry_truthful_operation_and_class(
    tmp_path: Path,
    factory_operation: str,
    client_operation: str,
    error_type: type[SandboxClientError],
    expected_operation: str,
    failure_class: FailureClass,
) -> None:
    fake = FakeSandboxFactory()
    fake.failures[factory_operation] = asyncio.TimeoutError()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)

    async def invoke() -> None:
        if client_operation == "create":
            await client.create("job-1")
        elif client_operation == "upload":
            await client.upload_text(
                handle, "/workspace/input/receptor.pdb", "ATOM\n"
            )
        elif client_operation == "read":
            await client.read_text(handle, "/workspace/output/result.json")
        elif client_operation == "list":
            await client.list_files(handle, "/workspace/output", "*")
        else:
            await client.run(handle)

    with pytest.raises(error_type) as raised:
        asyncio.run(invoke())

    assert raised.value.operation == expected_operation
    assert raised.value.failure_class is failure_class


def test_internal_cleanup_timeout_carries_destroy_classification(tmp_path: Path) -> None:
    class BlockingCleanupFactory(FakeSandboxFactory):
        async def destroy(self, raw: FakeRawSandbox) -> None:
            del raw
            await asyncio.Event().wait()

    async def scenario() -> SandboxDestroyError:
        fake = BlockingCleanupFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        client._cleanup_hard_timeout_seconds = 0.01
        try:
            await client.destroy(SandboxHandle("sandbox-timeout", fake.raw))
        except SandboxDestroyError as raised:
            return raised
        raise AssertionError("cleanup timeout was not surfaced")

    raised = asyncio.run(scenario())
    assert str(raised) == "sandbox cleanup failed"
    assert raised.operation == "destroy"
    assert raised.failure_class is FailureClass.DESTROY_FAILED


def test_internal_cleanup_breaker_failure_carries_destroy_classification(
    tmp_path: Path,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    client._cleanup_breaker_generation = 1

    with pytest.raises(SandboxDestroyError) as raised:
        asyncio.run(client.destroy(SandboxHandle("sandbox-1", fake.raw)))

    assert str(raised.value) == "sandbox cleanup failed"
    assert raised.value.operation == "destroy"
    assert raised.value.failure_class is FailureClass.DESTROY_FAILED


@pytest.mark.parametrize(
    ("operation", "expected_operation"),
    [
        ("create", "create"),
        ("read", "metadata"),
        ("list", "metadata"),
        ("run", "command"),
    ],
)
def test_internal_protocol_errors_carry_truthful_operation(
    tmp_path: Path,
    operation: str,
    expected_operation: str,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)
    if operation == "create":
        fake.raw.id = "invalid identifier"
        call = client.create("job-1")
    elif operation == "read":
        fake.read_chunks = [object()]
        call = client.read_text(handle, "/workspace/output/result.json")
    elif operation == "list":
        fake.list_execution = SimpleNamespace(exit_code="invalid")
        call = client.list_files(handle, "/workspace/output", "*")
    else:
        fake.execution = SimpleNamespace(exit_code="invalid")
        call = client.run(handle)

    with pytest.raises(SandboxProtocolError) as raised:
        asyncio.run(call)

    assert raised.value.operation == expected_operation
    assert (
        raised.value.failure_class
        is FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE
    )


@pytest.mark.parametrize(
    ("operation", "expected_operation"),
    [
        ("create", "create"),
        ("upload", "upload"),
        ("read", "metadata"),
        ("list", "metadata"),
        ("run", "command"),
        ("destroy", "destroy"),
    ],
)
def test_public_input_errors_carry_truthful_operation(
    tmp_path: Path,
    operation: str,
    expected_operation: str,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)
    if operation == "create":
        call = client.create("invalid identifier")
    elif operation == "upload":
        call = client.upload_text(handle, "/outside/input.pdb", "ATOM\n")
    elif operation == "read":
        call = client.read_text(handle, "/outside/result.json")
    elif operation == "list":
        call = client.list_files(handle, "/workspace/output", "**")
    elif operation == "run":
        call = client.run(None)  # type: ignore[arg-type]
    else:
        call = client.destroy(None)  # type: ignore[arg-type]

    with pytest.raises(SandboxInputError) as raised:
        asyncio.run(call)

    assert raised.value.operation == expected_operation
    assert (
        raised.value.failure_class
        is FailureClass.UNKNOWN_CONTROL_PLANE_FAILURE
    )


def test_factory_domain_error_messages_are_also_remapped_and_sanitized(
    tmp_path: Path,
) -> None:
    fake = FakeSandboxFactory()
    fake.failures["run"] = SandboxRunError(
        "api_key=test-only-credential C:\\Users\\private\\file"
    )
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    with pytest.raises(SandboxRunError) as raised:
        asyncio.run(client.run(SandboxHandle("sandbox-1", fake.raw)))

    assert "test-only-credential" not in str(raised.value)
    assert "Users" not in str(raised.value)


class FatalSDKSignal(BaseException):
    pass


@pytest.mark.parametrize("failure", [asyncio.CancelledError(), FatalSDKSignal()])
def test_cancellation_and_base_exceptions_propagate_unchanged(
    tmp_path: Path,
    failure: BaseException,
) -> None:
    fake = FakeSandboxFactory()
    fake.failures["run"] = failure
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)

    with pytest.raises(type(failure)) as raised:
        asyncio.run(client.run(handle))

    if not isinstance(failure, asyncio.CancelledError):
        assert raised.value is failure


@pytest.mark.parametrize(
    "failure",
    [
        RuntimeError("callback test-only-credential C:\\Users\\private"),
        asyncio.CancelledError(),
        FatalSDKSignal(),
    ],
)
def test_stream_callback_failures_are_sanitized_or_propagated_unchanged(
    tmp_path: Path,
    failure: BaseException,
) -> None:
    class FailingMessage:
        @property
        def text(self) -> str:
            raise failure

    fake = FakeSandboxFactory()
    fake.stdout_chunks = [FailingMessage()]
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    if isinstance(failure, Exception):
        with pytest.raises(SandboxRunError) as raised:
            asyncio.run(client.run(SandboxHandle("sandbox-1", fake.raw)))
        assert "test-only-credential" not in str(raised.value)
        assert "Users" not in str(raised.value)
    else:
        with pytest.raises(type(failure)) as raised:
            asyncio.run(client.run(SandboxHandle("sandbox-1", fake.raw)))
        if not isinstance(failure, asyncio.CancelledError):
            assert raised.value is failure


def test_run_rejects_type_confusion_and_sanitizes_bounded_utf8_output(
    tmp_path: Path,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)

    fake.execution = SimpleNamespace(
        exit_code=True,
        logs=SimpleNamespace(stdout=[], stderr=[]),
    )
    with pytest.raises(SandboxProtocolError):
        asyncio.run(client.run(handle))

    dangerous = (
        "\x1b[31mRED\x1b[0m\x00\x07 "
        "Authorization: Bearer bearer-secret-value "
        "api_key=test-only-credential sk-supersecret123 "
        "C:\\Users\\private\\file /home/private/file "
        + "é" * 9_000
    )
    fake.execution = SimpleNamespace(
        exit_code=7,
        logs=SimpleNamespace(stdout=[], stderr=[]),
    )
    fake.stdout_chunks = [dangerous]
    fake.stderr_chunks = [dangerous]
    result = asyncio.run(client.run(handle))

    assert result.exit_code == 7
    assert len(result.stdout.encode("utf-8")) <= 16 * 1024
    assert len(result.stderr.encode("utf-8")) <= 16 * 1024
    for output in (result.stdout, result.stderr):
        assert "\x1b" not in output
        assert "\x00" not in output
        assert "bearer-secret-value" not in output
        assert "test-only-credential" not in output
        assert "sk-supersecret123" not in output
        assert "Users" not in output
        assert "/home/private" not in output
        output.encode("utf-8", errors="strict")


def test_redaction_normalizes_before_removing_complete_authorization_values(
    tmp_path: Path,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)
    fake.execution = SimpleNamespace(
        exit_code=0,
        logs=SimpleNamespace(stdout=[], stderr=[]),
    )
    fake.stdout_chunks = [
        "known=test\x00-only-",
        (
            "credential\nAuthorization: Basic dXNlcjpwYXNz\n"
            "Authorization:\x1b[31m Custom scheme-secret-value\x1b[0m\n"
            "X-API-\x1b[32mKey: split-ansi-secret\x1b[0m\n"
            "C:\\Users\\private\\host-secret /tmp/private/host-secret "
            "/etc/medchat/host-secret"
        ),
    ]

    result = asyncio.run(client.run(handle))

    assert "test-only-credential" not in result.stdout
    assert "dXNlcjpwYXNz" not in result.stdout
    assert "scheme-secret-value" not in result.stdout
    assert "split-ansi-secret" not in result.stdout
    assert "host-secret" not in result.stdout
    assert "/etc/medchat" not in result.stdout
    assert "\x00" not in result.stdout
    assert "\x1b" not in result.stdout


def test_redaction_stays_safe_at_multibyte_truncation_boundary(tmp_path: Path) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)
    payload = (
        "茅" * 8_180
        + " Authorization: Basic boundary-secret-value"
        + " api_token=tail-secret-value"
    )
    fake.execution = SimpleNamespace(
        exit_code=0,
        logs=SimpleNamespace(stdout=[], stderr=[]),
    )
    fake.stdout_chunks = [payload]

    result = asyncio.run(client.run(handle))

    assert len(result.stdout.encode("utf-8")) <= 16 * 1024
    assert "boundary-secret-value" not in result.stdout
    assert "tail-secret-value" not in result.stdout
    assert "锟斤拷" not in result.stdout
    result.stdout.encode("utf-8", errors="strict")


def test_run_uses_bounded_stream_handlers_without_execution_accumulation(
    tmp_path: Path,
) -> None:
    class ExecutionWithoutReadableLogs:
        exit_code = 0

        @property
        def logs(self) -> object:
            raise AssertionError("Execution.logs must not be read")

    fake = FakeSandboxFactory()
    fake.execution = ExecutionWithoutReadableLogs()
    huge_chunk = "x" * (1024 * 1024)
    fake.stdout_chunks = [huge_chunk] * 32
    fake.stderr_chunks = [huge_chunk] * 32
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    result = asyncio.run(client.run(SandboxHandle("sandbox-1", fake.raw)))

    assert fake.run_calls[0]["command"] == FIXED_COMMAND
    assert fake.run_calls[0]["skip_accumulation"] is True
    assert callable(fake.run_calls[0]["on_stdout"])
    assert callable(fake.run_calls[0]["on_stderr"])
    assert len(result.stdout.encode("utf-8")) <= 16 * 1024
    assert len(result.stderr.encode("utf-8")) <= 16 * 1024
    assert result.stdout.endswith("[TRUNCATED]")
    assert result.stderr.endswith("[TRUNCATED]")


def test_official_factory_builds_async_nonaccumulating_execution_handlers(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    handler_calls: list[dict[str, object]] = []
    option_calls: list[dict[str, object]] = []

    class FakeExecutionHandlers:
        def __init__(self, **values: object) -> None:
            handler_calls.append(values)
            self.on_stdout = values["on_stdout"]
            self.on_stderr = values["on_stderr"]

    class FakeCommands:
        async def run(self, command: str, *, opts: object, handlers: object) -> object:
            assert command == FIXED_COMMAND
            assert isinstance(handlers, FakeExecutionHandlers)
            assert opts is not None
            await handlers.on_stdout(SimpleNamespace(text="streamed"))
            return SimpleNamespace(exit_code=0)

    class FakeRunCommandOpts:
        def __init__(self, **values: object) -> None:
            option_calls.append(values)

    sdk = opensandbox_client_module._OfficialSDK(
        ConnectionConfig=object,
        Sandbox=object,
        SandboxManager=object,
        WriteEntry=object,
        SearchEntry=object,
        ExecutionHandlers=FakeExecutionHandlers,
        RunCommandOpts=FakeRunCommandOpts,
    )
    monkeypatch.setattr(opensandbox_client_module, "_load_official_sdk", lambda: sdk)
    client = OpenSandboxClient(config(tmp_path))
    raw = SimpleNamespace(commands=FakeCommands())

    result = asyncio.run(client.run(SandboxHandle("sandbox-1", raw)))

    assert result.stdout == "streamed"
    assert handler_calls[0]["skip_accumulation"] is True
    assert callable(handler_calls[0]["on_stdout"])
    assert callable(handler_calls[0]["on_stderr"])
    assert option_calls[0]["timeout"].total_seconds() == 270  # type: ignore[union-attr]


def test_official_factory_uses_streaming_read_and_fixed_bounded_list_helper(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reads: list[dict[str, object]] = []
    commands: list[tuple[str, object]] = []

    class ForbiddenSearchEntry:
        def __init__(self, **values: object) -> None:
            raise AssertionError(f"unbounded SearchEntry used: {values}")

    class FakeExecutionHandlers:
        def __init__(self, **values: object) -> None:
            self.on_stdout = values["on_stdout"]
            self.on_stderr = values["on_stderr"]
            self.skip_accumulation = values["skip_accumulation"]

    class FakeFiles:
        async def read_file(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("unbounded read_file used")

        async def search(self, *_args: object, **_kwargs: object) -> object:
            raise AssertionError("unbounded search used")

        async def read_bytes_stream(
            self,
            path: str,
            *,
            chunk_size: int,
            range_header: str,
        ) -> object:
            reads.append(
                {
                    "path": path,
                    "chunk_size": chunk_size,
                    "range_header": range_header,
                }
            )

            async def chunks() -> Any:
                yield "结果".encode("utf-8")

            return chunks()

    class FakeCommands:
        async def run(self, command: str, *, opts: object, handlers: object) -> object:
                commands.append((command, handlers))
                assert opts is not None
                assert handlers.skip_accumulation is True
                await handlers.on_stdout(
                    SimpleNamespace(
                        text=json.dumps(["/workspace/output/result.json"])
                    )
                )
                return SimpleNamespace(exit_code=0)

    sdk = opensandbox_client_module._OfficialSDK(
        ConnectionConfig=object,
        Sandbox=object,
        SandboxManager=object,
        WriteEntry=object,
        SearchEntry=ForbiddenSearchEntry,
        ExecutionHandlers=FakeExecutionHandlers,
        RunCommandOpts=lambda **values: SimpleNamespace(**values),
    )
    monkeypatch.setattr(opensandbox_client_module, "_load_official_sdk", lambda: sdk)
    client = OpenSandboxClient(config(tmp_path))
    raw = SimpleNamespace(files=FakeFiles(), commands=FakeCommands())
    handle = SandboxHandle("sandbox-1", raw)

    assert asyncio.run(client.read_text(handle, "/workspace/output/result.json")) == "结果"
    assert asyncio.run(client.list_files(handle, "/workspace/output", "*.json")) == [
        "/workspace/output/result.json"
    ]

    assert reads == [
        {
            "path": "/workspace/output/result.json",
            "chunk_size": 64 * 1024,
            "range_header": f"bytes=0-{16 * 1024 * 1024}",
        }
    ]
    assert len(commands) == 1
    assert "run_docking.py" not in commands[0][0]
    assert "base64.b64decode" in commands[0][0]
    assert commands[0][0].endswith("'/workspace/output' '*.json'")


def test_destroy_and_destroy_by_id_are_best_possible_exactly_once(tmp_path: Path) -> None:
    async def scenario() -> tuple[FakeSandboxFactory, FakeSandboxFactory]:
        first = FakeSandboxFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=first)
        handle = SandboxHandle("sandbox-1", first.raw)
        await asyncio.gather(client.destroy(handle), client.destroy(handle))
        await client.destroy_by_id("sandbox-1")

        second = FakeSandboxFactory()
        recovery = OpenSandboxClient(config(tmp_path / "second"), sandbox_factory=second)
        await asyncio.gather(
            recovery.destroy_by_id("sandbox-orphan"),
            recovery.destroy_by_id("sandbox-orphan"),
        )
        return first, second

    (tmp_path / "second").mkdir()
    first, second = asyncio.run(scenario())

    assert first.destroyed == ["sandbox-1"]
    assert first.destroyed_by_id == []
    assert second.destroyed_by_id == ["sandbox-orphan"]


def test_caller_cancellation_does_not_cancel_shared_cleanup(tmp_path: Path) -> None:
    class BlockingCleanupFactory(FakeSandboxFactory):
        def __init__(self) -> None:
            super().__init__()
            self.cleanup_started = asyncio.Event()
            self.release_cleanup = asyncio.Event()

        async def destroy(self, raw: FakeRawSandbox) -> None:
            self.destroyed.append(raw.id)
            self.cleanup_started.set()
            await self.release_cleanup.wait()

    async def scenario() -> BlockingCleanupFactory:
        fake = BlockingCleanupFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        handle = SandboxHandle("sandbox-1", fake.raw)
        caller = asyncio.create_task(client.destroy(handle))
        await fake.cleanup_started.wait()

        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller

        follower = asyncio.create_task(client.destroy_by_id("sandbox-1"))
        await asyncio.sleep(0)
        assert not follower.done()
        fake.release_cleanup.set()
        await follower
        await client.destroy(handle)
        return fake

    fake = asyncio.run(scenario())

    assert fake.destroyed == ["sandbox-1"]
    assert fake.destroyed_by_id == []


def test_concurrent_destroy_apis_share_one_in_flight_task(tmp_path: Path) -> None:
    class BlockingCleanupFactory(FakeSandboxFactory):
        def __init__(self) -> None:
            super().__init__()
            self.cleanup_started = asyncio.Event()
            self.release_cleanup = asyncio.Event()

        async def destroy(self, raw: FakeRawSandbox) -> None:
            self.destroyed.append(raw.id)
            self.cleanup_started.set()
            await self.release_cleanup.wait()

    async def scenario() -> BlockingCleanupFactory:
        fake = BlockingCleanupFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        handle = SandboxHandle("sandbox-1", fake.raw)
        by_handle = asyncio.create_task(client.destroy(handle))
        await fake.cleanup_started.wait()
        by_id = asyncio.create_task(client.destroy_by_id("sandbox-1"))
        await asyncio.sleep(0)
        assert not by_id.done()
        fake.release_cleanup.set()
        await asyncio.gather(by_handle, by_id)
        return fake

    fake = asyncio.run(scenario())

    assert fake.destroyed == ["sandbox-1"]
    assert fake.destroyed_by_id == []


@pytest.mark.parametrize("operation", ["destroy", "destroy_by_id"])
@pytest.mark.parametrize("failure", [asyncio.CancelledError(), FatalSDKSignal()])
def test_destroy_invocation_is_not_skipped_when_base_exception_propagates(
    tmp_path: Path,
    operation: str,
    failure: BaseException,
) -> None:
    fake = FakeSandboxFactory()
    fake.failures[operation] = failure
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    async def scenario() -> BaseException:
        if operation == "destroy":
            call = client.destroy(SandboxHandle("sandbox-1", fake.raw))
        else:
            call = client.destroy_by_id("sandbox-1")
        try:
            await call
        except BaseException as raised:
            propagated = raised
        else:
            raise AssertionError("cleanup BaseException was swallowed")

        del fake.failures[operation]
        if operation == "destroy":
            await client.destroy(SandboxHandle("sandbox-1", fake.raw))
        else:
            await client.destroy_by_id("sandbox-1")
        return propagated

    raised = asyncio.run(scenario())

    if not isinstance(failure, asyncio.CancelledError):
        assert raised is failure
    assert type(raised) is type(failure)
    assert len(fake.destroyed) + len(fake.destroyed_by_id) == 2


@pytest.mark.parametrize("operation", ["destroy", "destroy_by_id"])
def test_destroy_maps_ordinary_errors_and_allows_retry(
    tmp_path: Path,
    operation: str,
) -> None:
    fake = FakeSandboxFactory()
    fake.failures[operation] = RuntimeError(
        "key=test-only-credential C:\\Users\\private\\file"
    )
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    async def scenario() -> SandboxDestroyError:
        if operation == "destroy":
            first = client.destroy(SandboxHandle("sandbox-1", fake.raw))
        else:
            first = client.destroy_by_id("sandbox-1")
        try:
            await first
        except SandboxDestroyError as raised:
            failure = raised
        else:
            raise AssertionError("cleanup failure was swallowed")

        del fake.failures[operation]
        if operation == "destroy":
            await client.destroy(SandboxHandle("sandbox-1", fake.raw))
        else:
            await client.destroy_by_id("sandbox-1")
        return failure

    raised = asyncio.run(scenario())

    assert "test-only-credential" not in str(raised)
    assert "Users" not in str(raised)
    assert len(fake.destroyed) + len(fake.destroyed_by_id) == 2


@pytest.mark.parametrize(
    "bad_handle",
    [None, True, object(), SimpleNamespace(sandbox_id="sandbox-1", raw=object())],
)
def test_handle_type_confusion_is_rejected_before_sdk_calls(
    tmp_path: Path,
    bad_handle: object,
) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    with pytest.raises(SandboxInputError):
        asyncio.run(client.run(bad_handle))  # type: ignore[arg-type]

    assert fake.commands == []


def test_module_has_no_eager_opensandbox_import_and_missing_sdk_fails_closed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source_path = Path(opensandbox_client_module.__file__)
    tree = ast.parse(source_path.read_text(encoding="utf-8"), feature_version=(3, 10))
    eager_imports = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            eager_imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            eager_imports.append(node.module)
    assert not any(name == "opensandbox" or name.startswith("opensandbox.") for name in eager_imports)

    def missing_sdk() -> object:
        raise ModuleNotFoundError(
            "No module named opensandbox at C:\\Users\\private with test-only-credential"
        )

    monkeypatch.setattr(opensandbox_client_module, "_load_official_sdk", missing_sdk)
    client = OpenSandboxClient(config(tmp_path))

    with pytest.raises(SandboxDependencyUnavailableError) as raised:
        asyncio.run(client.create("job-1"))

    message = str(raised.value)
    assert "test-only-credential" not in message
    assert "Users" not in message
    assert "127.0.0.1" not in message


def test_production_destroy_does_not_skip_raw_cleanup_if_sdk_reload_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class RawWithDestroy:
        def __init__(self) -> None:
            self.calls = 0

        async def destroy(self) -> None:
            self.calls += 1

    raw = RawWithDestroy()

    def missing_sdk() -> object:
        raise ModuleNotFoundError("opensandbox disappeared")

    monkeypatch.setattr(opensandbox_client_module, "_load_official_sdk", missing_sdk)
    client = OpenSandboxClient(config(tmp_path))

    asyncio.run(client.destroy(SandboxHandle("sandbox-1", raw)))

    assert raw.calls == 1


def test_source_parses_as_python_310() -> None:
    source_path = Path(opensandbox_client_module.__file__)

    ast.parse(source_path.read_text(encoding="utf-8"), feature_version=(3, 10))


def test_official_create_sets_explicit_bounded_transport_and_ready_timeouts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    connections: list[dict[str, object]] = []
    creates: list[dict[str, object]] = []

    class ConnectionConfig:
        def __init__(self, **values: object) -> None:
            connections.append(values)

    class Sandbox:
        @classmethod
        async def create(cls, image: str, **values: object) -> object:
            creates.append({"image": image, **values})
            return SimpleNamespace(id="sandbox-1")

    sdk = opensandbox_client_module._OfficialSDK(
        ConnectionConfig=ConnectionConfig,
        Sandbox=Sandbox,
        SandboxManager=object,
        WriteEntry=object,
        SearchEntry=object,
        ExecutionHandlers=object,
    )
    monkeypatch.setattr(opensandbox_client_module, "_load_official_sdk", lambda: sdk)

    handle = asyncio.run(OpenSandboxClient(config(tmp_path)).create("job-1"))

    assert handle.sandbox_id == "sandbox-1"
    request_timeout = connections[0]["request_timeout"]
    ready_timeout = creates[0]["ready_timeout"]
    remote_lifetime = creates[0]["timeout"]
    assert request_timeout.total_seconds() == 20  # type: ignore[union-attr]
    # The Broker is intentionally outside the isolated Docker network.  All
    # execd/files traffic must therefore traverse the loopback OpenSandbox
    # server instead of trying to dial published sandbox ports directly.
    assert connections[0]["use_server_proxy"] is True
    assert ready_timeout.total_seconds() == 60  # type: ignore[union-attr]
    assert remote_lifetime.total_seconds() == 300  # type: ignore[union-attr]
    assert "network_policy" not in creates[0]


def test_cleanup_completion_memory_is_bounded_and_evicted_ids_retry(
    tmp_path: Path,
) -> None:
    async def scenario() -> tuple[OpenSandboxClient, FakeSandboxFactory]:
        fake = FakeSandboxFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        client._cleanup_completed_limit = 3
        for index in range(5):
            await client.destroy_by_id(f"sandbox-{index}")
        await client.destroy_by_id("sandbox-0")
        return client, fake

    client, fake = asyncio.run(scenario())

    assert len(client._cleanup_completed) == 3
    assert fake.destroyed_by_id.count("sandbox-0") == 2


def test_cleanup_completion_cache_refreshes_recency_on_hit(tmp_path: Path) -> None:
    async def scenario() -> tuple[OpenSandboxClient, FakeSandboxFactory]:
        fake = FakeSandboxFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        client._cleanup_completed_limit = 2
        await client.destroy_by_id("sandbox-a")
        await client.destroy_by_id("sandbox-b")
        await client.destroy_by_id("sandbox-a")
        await client.destroy_by_id("sandbox-c")
        await client.destroy_by_id("sandbox-a")
        await client.destroy_by_id("sandbox-b")
        return client, fake

    client, fake = asyncio.run(scenario())

    assert list(client._cleanup_completed) == ["sandbox-a", "sandbox-b"]
    assert fake.destroyed_by_id.count("sandbox-a") == 1
    assert fake.destroyed_by_id.count("sandbox-b") == 2
    assert fake.destroyed_by_id.count("sandbox-c") == 1


@pytest.mark.parametrize("cleanup_count", [3, 6])
def test_slow_cancellable_cleanups_timeout_drain_and_leave_no_inflight(
    tmp_path: Path,
    cleanup_count: int,
) -> None:
    class SlowCleanupFactory(FakeSandboxFactory):
        def __init__(self) -> None:
            super().__init__()
            self.all_started = asyncio.Event()
            self.started: list[str] = []
            self.cancelled: list[str] = []
            self.block = True

        async def destroy_by_id(self, sandbox_id: str) -> None:
            self.started.append(sandbox_id)
            if len(self.started) == cleanup_count:
                self.all_started.set()
            if not self.block:
                return
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled.append(sandbox_id)
                raise

    async def scenario() -> tuple[OpenSandboxClient, SlowCleanupFactory, list[object]]:
        fake = SlowCleanupFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        client._cleanup_hard_timeout_seconds = 0.02
        client._cleanup_drain_margin_seconds = 0.05
        calls = [
            asyncio.create_task(client.destroy_by_id(f"sandbox-slow-{index}"))
            for index in range(cleanup_count)
        ]
        await fake.all_started.wait()
        results = await asyncio.gather(*calls, return_exceptions=True)
        with pytest.raises(SandboxDestroyError, match="cleanup failed"):
            await client.drain_cleanup()
        await client.drain_cleanup()
        fake.block = False
        await client.destroy_by_id("sandbox-slow-0")
        await asyncio.sleep(0)
        return client, fake, results

    client, fake, results = asyncio.run(scenario())

    assert all(isinstance(result, SandboxDestroyError) for result in results)
    assert len(fake.cancelled) == cleanup_count
    assert fake.started.count("sandbox-slow-0") == 2
    assert client._cleanup_in_flight == {}
    assert client._cleanup_quarantine == set()


def test_cancelled_cleanup_caller_still_reaches_adapter_hard_timeout(
    tmp_path: Path,
) -> None:
    class SlowCleanupFactory(FakeSandboxFactory):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = False

        async def destroy(self, raw: FakeRawSandbox) -> None:
            del raw
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def scenario() -> tuple[OpenSandboxClient, SlowCleanupFactory]:
        fake = SlowCleanupFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        client._cleanup_hard_timeout_seconds = 0.02
        client._cleanup_drain_margin_seconds = 0.05
        caller = asyncio.create_task(
            client.destroy(SandboxHandle("sandbox-caller", fake.raw))
        )
        await fake.started.wait()
        caller.cancel()
        with pytest.raises(asyncio.CancelledError):
            await caller
        with pytest.raises(SandboxDestroyError):
            await client.drain_cleanup()
        await client.drain_cleanup()
        return client, fake

    client, fake = asyncio.run(scenario())
    assert fake.cancelled is True
    assert client._cleanup_in_flight == {}


def test_cancellation_resistant_cleanup_is_quarantined_until_completion(
    tmp_path: Path,
) -> None:
    class ResistantCleanupFactory(FakeSandboxFactory):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.cancel_seen = asyncio.Event()
            self.release = asyncio.Event()
            self.destroy_count = 0
            self.active_destroy = 0
            self.max_active_destroy = 0

        async def destroy_by_id(self, sandbox_id: str) -> None:
            del sandbox_id
            self.destroy_count += 1
            self.active_destroy += 1
            self.max_active_destroy = max(
                self.max_active_destroy, self.active_destroy
            )
            self.started.set()
            try:
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancel_seen.set()
                    await self.release.wait()
                raise RuntimeError("private-marker /private/cleanup")
            finally:
                self.active_destroy -= 1

    async def scenario() -> tuple[OpenSandboxClient, ResistantCleanupFactory, list[dict[str, object]]]:
        fake = ResistantCleanupFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        client._cleanup_hard_timeout_seconds = 0.02
        client._cleanup_drain_margin_seconds = 0.05
        loop_errors: list[dict[str, object]] = []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: loop_errors.append(context))
        try:
            cleanup = asyncio.create_task(
                client.destroy_by_id("sandbox-resistant")
            )
            await fake.started.wait()
            with pytest.raises(SandboxDestroyError, match="cleanup failed") as first:
                await cleanup
            with pytest.raises(SandboxDestroyError, match="cleanup failed") as second:
                await client.destroy_by_id("sandbox-resistant")
            assert first.value.completion_known is False
            assert second.value.completion_known is False
            assert second.value.__cause__ is None
            assert "private-marker" not in str(second.value)
            assert "private-marker" not in repr(second.value)
            assert fake.destroy_count == 1
            assert fake.max_active_destroy == 1
            with pytest.raises(SandboxDestroyError, match="cleanup failed"):
                await client.drain_cleanup()
            with pytest.raises(SandboxDestroyError, match="cleanup failed"):
                await client.drain_cleanup()
            assert len(client._cleanup_in_flight) == 1
            assert len(client._cleanup_quarantine) == 1
            assert fake.cancel_seen.is_set()
        finally:
            fake.release.set()
            await asyncio.sleep(0)
            await asyncio.sleep(0)
            loop.set_exception_handler(previous_handler)
        await client.drain_cleanup()
        return client, fake, loop_errors

    client, fake, loop_errors = asyncio.run(scenario())
    assert fake.cancel_seen.is_set()
    assert fake.destroy_count == 1
    assert fake.max_active_destroy == 1
    assert client._cleanup_in_flight == {}
    assert client._cleanup_quarantine == set()
    assert loop_errors == []


def test_cleanup_quarantine_caps_thirty_six_resistant_raw_id_and_metadata_calls(
    tmp_path: Path,
) -> None:
    class ResistantCleanupFactory(FakeSandboxFactory):
        def __init__(self) -> None:
            super().__init__()
            self.release = asyncio.Event()
            self.started: list[tuple[str, str]] = []
            self.cancelled: list[tuple[str, str]] = []

        async def _resist(self, kind: str, identity: str) -> None:
            operation = (kind, identity)
            self.started.append(operation)
            if self.release.is_set():
                return
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.append(operation)
                await self.release.wait()
            raise RuntimeError("private-marker /private/zombie")

        async def destroy(self, raw: FakeRawSandbox) -> None:
            await self._resist("raw", raw.id)

        async def destroy_by_id(self, sandbox_id: str) -> None:
            await self._resist("id", sandbox_id)

        async def destroy_by_job_id(self, job_id: str) -> int:
            await self._resist("metadata", job_id)
            raise AssertionError("unreachable")

    async def scenario() -> tuple[
        OpenSandboxClient,
        ResistantCleanupFactory,
        list[object],
        tuple[bool, bool],
        int,
        set[str],
    ]:
        fake = ResistantCleanupFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        client._cleanup_hard_timeout_seconds = 0.02
        client._cleanup_drain_margin_seconds = 0.02
        calls: list[asyncio.Task[object]] = []
        for index in range(12):
            calls.append(
                asyncio.create_task(
                    client.destroy(
                        SandboxHandle(
                            f"sandbox-raw-{index}",
                            FakeRawSandbox(f"sandbox-raw-{index}"),
                        )
                    )
                )
            )
            calls.append(
                asyncio.create_task(
                    client.destroy_by_id(f"sandbox-id-{index}")
                )
            )
            calls.append(
                asyncio.create_task(client.destroy_by_job_id(f"job-{index}"))
            )

        try:
            results = await asyncio.gather(*calls, return_exceptions=True)
            spawned_before_breaker_probe = len(fake.started)
            try:
                await client.destroy_by_id("sandbox-breaker-probe")
            except SandboxDestroyError:
                pass
            else:
                raise AssertionError("cleanup circuit breaker did not fail closed")
            spawned_after_breaker_probe = len(fake.started)
            drain_failures: list[bool] = []
            for _ in range(2):
                try:
                    await client.drain_cleanup()
                except SandboxDestroyError:
                    drain_failures.append(True)
                else:
                    drain_failures.append(False)
            quarantine_count = len(
                getattr(client, "_cleanup_quarantine", set())
            )
            assert all(
                isinstance(task, asyncio.Task)
                for task in client._cleanup_quarantine
            )
            started_kinds = {kind for kind, _identity in fake.started}
        finally:
            fake.release.set()
            for _ in range(10):
                await asyncio.sleep(0)

        await client.drain_cleanup()
        await client.destroy_by_id("sandbox-recovery-probe")
        await client.drain_cleanup()
        assert spawned_before_breaker_probe == spawned_after_breaker_probe
        return (
            client,
            fake,
            results,
            (drain_failures[0], drain_failures[1]),
            quarantine_count,
            started_kinds,
        )

    client, fake, results, drain_failures, quarantine_count, kinds = asyncio.run(
        scenario()
    )

    assert all(isinstance(result, SandboxDestroyError) for result in results)
    assert client._cleanup_quarantine_limit == 16
    assert len(fake.started) == 17
    assert fake.started[-1] == ("id", "sandbox-recovery-probe")
    assert len(fake.cancelled) == 16
    assert quarantine_count == 16
    assert kinds == {"raw", "id", "metadata"}
    assert drain_failures == (True, True)
    assert client._cleanup_in_flight == {}
    assert client._cleanup_quarantine == set()
    assert client._cleanup_active_operations == set()


def test_destroy_by_job_id_is_a_fixed_metadata_filtered_compensation(
    tmp_path: Path,
) -> None:
    class CompensationFactory(FakeSandboxFactory):
        def __init__(self) -> None:
            super().__init__()
            self.job_ids: list[str] = []

        async def destroy_by_job_id(self, job_id: str) -> int:
            self.job_ids.append(job_id)
            return 1

    async def scenario() -> tuple[int, CompensationFactory]:
        fake = CompensationFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        return await client.destroy_by_job_id("job-1"), fake

    destroyed, fake = asyncio.run(scenario())
    assert destroyed == 1
    assert fake.job_ids == ["job-1"]


def test_cancelled_metadata_waiter_does_not_cancel_shared_reconciliation(
    tmp_path: Path,
) -> None:
    class SharedCompensationFactory(FakeSandboxFactory):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.count = 0

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            self.count += 1
            self.started.set()
            await self.release.wait()
            return 0

    async def scenario() -> tuple[OpenSandboxClient, SharedCompensationFactory, object]:
        fake = SharedCompensationFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        cancelled = asyncio.create_task(client.destroy_by_job_id("job-shared"))
        survivor = asyncio.create_task(client.destroy_by_job_id("job-shared"))
        await fake.started.wait()
        cancelled.cancel()
        try:
            with pytest.raises(asyncio.CancelledError):
                await cancelled
            await asyncio.sleep(0)
            survivor_was_pending = not survivor.done()
        finally:
            fake.release.set()
        result = await asyncio.gather(survivor, return_exceptions=True)
        try:
            await client.drain_cleanup()
        except SandboxDestroyError:
            pass
        return client, fake, (survivor_was_pending, result[0])

    client, fake, outcome = asyncio.run(scenario())
    assert outcome == (True, 0)
    assert fake.count == 1
    assert client._cleanup_in_flight == {}
    assert client._cleanup_waiters == {}


def test_cancelled_official_waiter_retains_job_key_until_lifecycle_terminates(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> tuple[OpenSandboxClient, int, int, int]:
        list_started = asyncio.Event()
        list_cancelled = asyncio.Event()
        release = asyncio.Event()

        class Manager:
            create_calls = 0
            list_calls = 0
            close_calls = 0

            @classmethod
            async def create(cls, **_values: object) -> "Manager":
                cls.create_calls += 1
                return cls()

            async def list_sandbox_infos(self, _filter: object) -> object:
                type(self).list_calls += 1
                list_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    list_cancelled.set()
                    await release.wait()
                return SimpleNamespace(sandbox_infos=[])

            async def close(self) -> None:
                type(self).close_calls += 1

        monkeypatch.setattr(
            opensandbox_client_module,
            "_load_official_sdk",
            lambda: _reconciliation_sdk(Manager),
        )
        client = OpenSandboxClient(config(tmp_path))
        first = asyncio.create_task(client.destroy_by_job_id("job-official-owner"))
        await list_started.wait()
        first.cancel()
        with pytest.raises(asyncio.CancelledError):
            await first
        await list_cancelled.wait()
        retained_before_second = len(client._cleanup_in_flight)
        try:
            with pytest.raises(asyncio.CancelledError):
                await client.destroy_by_job_id("job-official-owner")
            retained_after_second = len(client._cleanup_in_flight)
        finally:
            release.set()
            for _ in range(20):
                if not client._factory_metadata_lifecycle_tasks():
                    break
                await asyncio.sleep(0)
        try:
            await client.drain_cleanup()
        except SandboxDestroyError:
            await client.drain_cleanup()
        assert client._cleanup_waiters == {}
        return client, retained_before_second, retained_after_second, Manager.list_calls

    client, retained_before, retained_after, list_calls = asyncio.run(scenario())
    assert retained_before == 1
    assert retained_after == 1
    assert list_calls == 1
    assert client._cleanup_in_flight == {}
    assert client._factory_metadata_lifecycle_tasks() == ()


def test_metadata_waiter_cleanup_survives_lock_contention_and_double_cancel(
    tmp_path: Path,
) -> None:
    class ResistantCompensationFactory(FakeSandboxFactory):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.release = asyncio.Event()

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            self.started.set()
            try:
                await self.release.wait()
            except asyncio.CancelledError:
                self.cancelled.set()
                await self.release.wait()
            return 0

    async def scenario() -> OpenSandboxClient:
        fake = ResistantCompensationFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        waiter = asyncio.create_task(client.destroy_by_job_id("job-double-cancel"))
        await fake.started.wait()
        await client._cleanup_lock.acquire()
        try:
            waiter.cancel()
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
        finally:
            client._cleanup_lock.release()
            fake.release.set()
            for _ in range(20):
                await asyncio.sleep(0)
        try:
            await client.drain_cleanup()
        except SandboxDestroyError:
            await client.drain_cleanup()
        assert fake.cancelled.is_set()
        return client

    client = asyncio.run(scenario())
    assert client._cleanup_waiters == {}
    assert client._cleanup_in_flight == {}
    assert client._cleanup_quarantine == set()


def test_resistant_metadata_timeout_retains_job_identity_until_raw_completion(
    tmp_path: Path,
) -> None:
    class ResistantCompensationFactory(FakeSandboxFactory):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = asyncio.Event()
            self.release = asyncio.Event()
            self.count = 0
            self.active = 0
            self.max_active = 0

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            self.count += 1
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            self.started.set()
            try:
                try:
                    await self.release.wait()
                except asyncio.CancelledError:
                    self.cancelled.set()
                    await self.release.wait()
                return 0
            finally:
                self.active -= 1

    async def scenario() -> tuple[OpenSandboxClient, ResistantCompensationFactory, int]:
        fake = ResistantCompensationFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        client._cleanup_hard_timeout_seconds = 0.02
        client._cleanup_drain_margin_seconds = 0.05
        try:
            with pytest.raises(SandboxDestroyError, match="cleanup failed"):
                await client.destroy_by_job_id("job-resistant")
            with pytest.raises(SandboxDestroyError, match="cleanup failed") as second:
                await client.destroy_by_job_id("job-resistant")
            assert "private" not in str(second.value)
            retained = len(client._cleanup_in_flight)
        finally:
            fake.release.set()
            for _ in range(10):
                await asyncio.sleep(0)
        with pytest.raises(SandboxDestroyError, match="cleanup failed"):
            await client.drain_cleanup()
        await client.drain_cleanup()
        return client, fake, retained

    client, fake, retained = asyncio.run(scenario())
    assert fake.cancelled.is_set()
    assert fake.count == 1
    assert fake.max_active == 1
    assert retained == 1
    assert client._cleanup_in_flight == {}
    assert client._cleanup_waiters == {}
    assert client._cleanup_quarantine == set()


def test_destroy_by_job_id_is_hard_bounded_and_visible_to_drain(
    tmp_path: Path,
) -> None:
    class SlowCompensationFactory(FakeSandboxFactory):
        def __init__(self) -> None:
            super().__init__()
            self.started = asyncio.Event()
            self.cancelled = False

        async def destroy_by_job_id(self, job_id: str) -> int:
            del job_id
            self.started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise

    async def scenario() -> tuple[OpenSandboxClient, SlowCompensationFactory]:
        fake = SlowCompensationFactory()
        client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
        client._cleanup_hard_timeout_seconds = 0.02
        client._cleanup_drain_margin_seconds = 0.05
        cleanup = asyncio.create_task(client.destroy_by_job_id("job-slow"))
        await fake.started.wait()
        with pytest.raises(SandboxDestroyError, match="cleanup failed"):
            await cleanup
        with pytest.raises(SandboxDestroyError, match="cleanup failed"):
            await client.drain_cleanup()
        await client.drain_cleanup()
        return client, fake

    client, fake = asyncio.run(scenario())
    assert fake.cancelled is True
    assert client._cleanup_in_flight == {}


def test_official_reconciliation_lists_only_fixed_job_metadata_then_kills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    filters: list[dict[str, object]] = []
    killed: list[str] = []

    class ConnectionConfig:
        def __init__(self, **_values: object) -> None:
            pass

    class SandboxFilter:
        def __init__(self, **values: object) -> None:
            filters.append(values)

    class Manager:
        @classmethod
        async def create(cls, **_values: object) -> "Manager":
            return cls()

        async def list_sandbox_infos(self, _filter: object) -> object:
            return SimpleNamespace(
                sandbox_infos=[
                    SimpleNamespace(
                        id="sandbox-1",
                        metadata={
                            "medchat.operation": "molecular_docking",
                            "medchat.job_id": "job-1",
                        },
                    )
                ]
            )

        async def kill_sandbox(self, sandbox_id: str) -> None:
            killed.append(sandbox_id)

        async def close(self) -> None:
            pass

    sdk = opensandbox_client_module._OfficialSDK(
        ConnectionConfig=ConnectionConfig,
        Sandbox=object,
        SandboxManager=Manager,
        WriteEntry=object,
        SearchEntry=object,
        ExecutionHandlers=object,
        SandboxFilter=SandboxFilter,
    )
    monkeypatch.setattr(opensandbox_client_module, "_load_official_sdk", lambda: sdk)

    destroyed = asyncio.run(OpenSandboxClient(config(tmp_path)).destroy_by_job_id("job-1"))

    assert destroyed == 1
    assert filters == [
        {
            "metadata": {
                "medchat.operation": "molecular_docking",
                "medchat.job_id": "job-1",
            },
            "page": 1,
            "page_size": 100,
        }
    ]
    assert killed == ["sandbox-1"]


def test_official_metadata_observation_retries_once_then_records_success(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retries: list[tuple[str, str]] = []
    sleeps: list[float] = []

    class Telemetry:
        def record_retry(self, operation: str, outcome: str) -> None:
            retries.append((operation, outcome))

    class Manager:
        list_calls = 0
        killed: list[str] = []
        close_calls = 0

        @classmethod
        async def create(cls, **_values: object) -> "Manager":
            return cls()

        async def list_sandbox_infos(self, _filter: object) -> object:
            type(self).list_calls += 1
            if type(self).list_calls == 1:
                raise ConnectionError("private metadata transport detail")
            return SimpleNamespace(
                sandbox_infos=[
                    SimpleNamespace(
                        id="sandbox-1",
                        metadata={
                            "medchat.operation": "molecular_docking",
                            "medchat.job_id": "job-1",
                        },
                    )
                ]
            )

        async def kill_sandbox(self, sandbox_id: str) -> None:
            type(self).killed.append(sandbox_id)

        async def close(self) -> None:
            type(self).close_calls += 1

    async def sleep(delay: float) -> None:
        sleeps.append(delay)

    monkeypatch.setattr(
        opensandbox_client_module,
        "_load_official_sdk",
        lambda: _reconciliation_sdk(Manager),
    )
    client = OpenSandboxClient(
        config(tmp_path),
        telemetry=Telemetry(),
        metadata_retry_sleep=sleep,
        metadata_retry_backoff=lambda _attempt: 0.0,
    )

    destroyed = asyncio.run(client.destroy_by_job_id("job-1"))

    assert destroyed == 1
    assert Manager.list_calls == 2
    assert Manager.killed == ["sandbox-1"]
    assert Manager.close_calls == 2
    assert sleeps == [0.0]
    assert retries == [("metadata", "attempted"), ("metadata", "succeeded")]


def test_official_metadata_observation_exhaustion_is_sanitized_and_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retries: list[tuple[str, str]] = []

    class Telemetry:
        def record_retry(self, operation: str, outcome: str) -> None:
            retries.append((operation, outcome))

    class Manager:
        list_calls = 0
        close_calls = 0

        @classmethod
        async def create(cls, **_values: object) -> "Manager":
            return cls()

        async def list_sandbox_infos(self, _filter: object) -> object:
            type(self).list_calls += 1
            raise ConnectionError("private-marker C:\\private\\metadata")

        async def close(self) -> None:
            type(self).close_calls += 1

    monkeypatch.setattr(
        opensandbox_client_module,
        "_load_official_sdk",
        lambda: _reconciliation_sdk(Manager),
    )
    client = OpenSandboxClient(
        config(tmp_path),
        telemetry=Telemetry(),
        metadata_retry_sleep=lambda _delay: asyncio.sleep(0),
        metadata_retry_backoff=lambda _attempt: 0.0,
    )

    with pytest.raises(SandboxListError) as raised:
        asyncio.run(client.destroy_by_job_id("job-1"))

    assert Manager.list_calls == 2
    assert Manager.close_calls == 2
    assert retries == [("metadata", "attempted"), ("metadata", "exhausted")]
    assert raised.value.operation == "metadata"
    assert raised.value.failure_class is FailureClass.CONNECTION_FAILED
    assert "private-marker" not in str(raised.value)
    assert "private-marker" not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize(
    ("failure", "expected_error"),
    [
        (
            SandboxInputError("invalid metadata input", operation="metadata"),
            SandboxDestroyError,
        ),
        (
            SandboxProtocolError("invalid metadata", operation="metadata"),
            SandboxProtocolError,
        ),
        (
            SandboxDependencyUnavailableError(
                "dependency unavailable", operation="metadata"
            ),
            SandboxDependencyUnavailableError,
        ),
    ],
)
def test_official_metadata_protocol_and_dependency_failures_are_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
    expected_error: type[Exception],
) -> None:
    class Manager:
        list_calls = 0
        close_calls = 0

        @classmethod
        async def create(cls, **_values: object) -> "Manager":
            return cls()

        async def list_sandbox_infos(self, _filter: object) -> object:
            type(self).list_calls += 1
            raise failure

        async def close(self) -> None:
            type(self).close_calls += 1

    monkeypatch.setattr(
        opensandbox_client_module,
        "_load_official_sdk",
        lambda: _reconciliation_sdk(Manager),
    )
    client = OpenSandboxClient(
        config(tmp_path),
        metadata_retry_sleep=lambda _delay: asyncio.sleep(0),
        metadata_retry_backoff=lambda _attempt: 0.0,
    )

    with pytest.raises(expected_error):
        asyncio.run(client.destroy_by_job_id("job-1"))

    assert Manager.list_calls == 1
    assert Manager.close_calls == 1


def test_official_metadata_retry_sleep_cancellation_propagates_and_closes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Manager:
        list_calls = 0
        close_calls = 0

        @classmethod
        async def create(cls, **_values: object) -> "Manager":
            return cls()

        async def list_sandbox_infos(self, _filter: object) -> object:
            type(self).list_calls += 1
            raise ConnectionError("metadata transport failed")

        async def close(self) -> None:
            type(self).close_calls += 1

    async def cancelled_sleep(_delay: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(
        opensandbox_client_module,
        "_load_official_sdk",
        lambda: _reconciliation_sdk(Manager),
    )
    client = OpenSandboxClient(
        config(tmp_path),
        metadata_retry_sleep=cancelled_sleep,
        metadata_retry_backoff=lambda _attempt: 0.0,
    )

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(client.destroy_by_job_id("job-1"))

    assert Manager.list_calls == 1
    assert Manager.close_calls == 1


def test_official_metadata_deadline_prevents_second_observation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    retries: list[tuple[str, str]] = []
    sleep_called = False

    class Telemetry:
        def record_retry(self, operation: str, outcome: str) -> None:
            retries.append((operation, outcome))

    class Manager:
        list_calls = 0

        @classmethod
        async def create(cls, **_values: object) -> "Manager":
            return cls()

        async def list_sandbox_infos(self, _filter: object) -> object:
            type(self).list_calls += 1
            raise ConnectionError("metadata transport failed")

        async def close(self) -> None:
            pass

    async def sleep(_delay: float) -> None:
        nonlocal sleep_called
        sleep_called = True

    monkeypatch.setattr(
        opensandbox_client_module,
        "_load_official_sdk",
        lambda: _reconciliation_sdk(Manager),
    )
    monkeypatch.setattr(opensandbox_client_module, "_SDK_REQUEST_TIMEOUT_SECONDS", 0.1)
    client = OpenSandboxClient(
        config(tmp_path),
        telemetry=Telemetry(),
        metadata_retry_sleep=sleep,
        metadata_retry_backoff=lambda _attempt: 0.15,
    )

    with pytest.raises(SandboxListError):
        asyncio.run(client.destroy_by_job_id("job-1"))

    assert Manager.list_calls == 1
    assert sleep_called is False
    assert retries == [("metadata", "attempted"), ("metadata", "exhausted")]


def test_official_metadata_telemetry_failure_does_not_change_retry_outcome(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Telemetry:
        def record_retry(self, operation: str, outcome: str) -> None:
            del operation, outcome
            raise RuntimeError("telemetry unavailable")

    class Manager:
        list_calls = 0

        @classmethod
        async def create(cls, **_values: object) -> "Manager":
            return cls()

        async def list_sandbox_infos(self, _filter: object) -> object:
            type(self).list_calls += 1
            if type(self).list_calls == 1:
                raise ConnectionResetError("metadata transport failed")
            return SimpleNamespace(sandbox_infos=[])

        async def close(self) -> None:
            pass

    monkeypatch.setattr(
        opensandbox_client_module,
        "_load_official_sdk",
        lambda: _reconciliation_sdk(Manager),
    )
    client = OpenSandboxClient(
        config(tmp_path),
        telemetry=Telemetry(),
        metadata_retry_sleep=lambda _delay: asyncio.sleep(0),
        metadata_retry_backoff=lambda _attempt: 0.0,
    )

    destroyed = asyncio.run(client.destroy_by_job_id("job-1"))

    assert destroyed == 0
    assert Manager.list_calls == 2


def test_official_kill_failure_is_not_retried(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Manager:
        list_calls = 0
        kill_calls = 0
        close_calls = 0

        @classmethod
        async def create(cls, **_values: object) -> "Manager":
            return cls()

        async def list_sandbox_infos(self, _filter: object) -> object:
            type(self).list_calls += 1
            return SimpleNamespace(
                sandbox_infos=[
                    SimpleNamespace(
                        id="sandbox-1",
                        metadata={
                            "medchat.operation": "molecular_docking",
                            "medchat.job_id": "job-1",
                        },
                    )
                ]
            )

        async def kill_sandbox(self, _sandbox_id: str) -> None:
            type(self).kill_calls += 1
            raise ConnectionError("kill failed")

        async def close(self) -> None:
            type(self).close_calls += 1

    monkeypatch.setattr(
        opensandbox_client_module,
        "_load_official_sdk",
        lambda: _reconciliation_sdk(Manager),
    )
    client = OpenSandboxClient(
        config(tmp_path),
        metadata_retry_sleep=lambda _delay: asyncio.sleep(0),
        metadata_retry_backoff=lambda _attempt: 0.0,
    )

    with pytest.raises(SandboxDestroyError) as raised:
        asyncio.run(client.destroy_by_job_id("job-1"))

    assert Manager.list_calls == 1
    assert Manager.kill_calls == 1
    assert Manager.close_calls == 1
    assert "kill failed" not in repr(raised.value)
    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


@pytest.mark.parametrize("blocked_stage", ["create", "list", "close"])
def test_official_metadata_lifecycle_deadline_tracks_suppressed_cancellation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blocked_stage: str,
) -> None:
    async def scenario() -> tuple[
        SandboxClientError,
        float,
        int,
        int,
        int,
        int,
        bool,
        list[tuple[str, str]],
    ]:
        started = asyncio.Event()
        cancelled = asyncio.Event()
        release = asyncio.Event()
        retries: list[tuple[str, str]] = []

        class Telemetry:
            def record_retry(self, operation: str, outcome: str) -> None:
                retries.append((operation, outcome))

        async def block(stage: str) -> None:
            if blocked_stage != stage:
                return
            started.set()
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()

        class Manager:
            create_calls = 0
            list_calls = 0
            close_calls = 0

            @classmethod
            async def create(cls, **_values: object) -> "Manager":
                cls.create_calls += 1
                await block("create")
                return cls()

            async def list_sandbox_infos(self, _filter: object) -> object:
                type(self).list_calls += 1
                await block("list")
                return SimpleNamespace(sandbox_infos=[])

            async def close(self) -> None:
                type(self).close_calls += 1
                await block("close")

        monkeypatch.setattr(opensandbox_client_module, "_SDK_REQUEST_TIMEOUT_SECONDS", 0.02)
        monkeypatch.setattr(
            opensandbox_client_module,
            "_load_official_sdk",
            lambda: _reconciliation_sdk(Manager),
        )
        client = OpenSandboxClient(
            config(tmp_path),
            telemetry=Telemetry(),
            metadata_retry_sleep=lambda _delay: asyncio.sleep(0),
            metadata_retry_backoff=lambda _attempt: 0.0,
        )
        client._cleanup_hard_timeout_seconds = 0.08
        started_at = asyncio.get_running_loop().time()
        try:
            await client.destroy_by_job_id("job-1")
        except SandboxClientError as raised:
            failure = raised
        else:
            raise AssertionError("metadata lifecycle did not hit its deadline")
        elapsed = asyncio.get_running_loop().time() - started_at
        await cancelled.wait()
        lifecycle_tasks = getattr(client._factory, "_metadata_lifecycle_tasks", set())
        tracked = len(lifecycle_tasks)
        release.set()
        for _ in range(20):
            await asyncio.sleep(0)
            lifecycle_tasks = getattr(
                client._factory, "_metadata_lifecycle_tasks", set()
            )
            if Manager.close_calls and not lifecycle_tasks:
                break
        with pytest.raises(SandboxDestroyError):
            await client.drain_cleanup()
        await client.drain_cleanup()
        return (
            failure,
            elapsed,
            Manager.create_calls,
            Manager.list_calls,
            Manager.close_calls,
            tracked,
            not getattr(client._factory, "_metadata_lifecycle_tasks", set()),
            retries,
        )

    failure, elapsed, creates, lists, closes, tracked, drained, retries = asyncio.run(
        scenario()
    )
    assert isinstance(failure, SandboxListError)
    assert failure.operation == "metadata"
    assert elapsed < 0.06
    assert creates == 1
    assert lists <= 1
    assert closes == 1
    assert tracked == 1
    assert drained is True
    assert retries == [("metadata", "exhausted")]


def test_official_metadata_caller_cancellation_is_preserved_and_drained(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def scenario() -> tuple[int, int, int, bool]:
        list_started = asyncio.Event()
        list_cancelled = asyncio.Event()
        release = asyncio.Event()

        class Manager:
            list_calls = 0
            close_calls = 0

            @classmethod
            async def create(cls, **_values: object) -> "Manager":
                return cls()

            async def list_sandbox_infos(self, _filter: object) -> object:
                type(self).list_calls += 1
                list_started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    list_cancelled.set()
                    await release.wait()
                return SimpleNamespace(sandbox_infos=[])

            async def close(self) -> None:
                type(self).close_calls += 1

        monkeypatch.setattr(
            opensandbox_client_module,
            "_load_official_sdk",
            lambda: _reconciliation_sdk(Manager),
        )
        client = OpenSandboxClient(config(tmp_path))
        task = asyncio.create_task(client.destroy_by_job_id("job-1"))
        await list_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        try:
            await asyncio.wait_for(list_cancelled.wait(), timeout=0.05)
            cancellation_preserved = True
        except asyncio.TimeoutError:
            cancellation_preserved = False
        tracked = len(
            getattr(client._factory, "_metadata_lifecycle_tasks", set())
        )
        release.set()
        for _ in range(20):
            await asyncio.sleep(0)
            if not getattr(client._factory, "_metadata_lifecycle_tasks", set()):
                break
        try:
            await client.drain_cleanup()
        except SandboxDestroyError:
            await client.drain_cleanup()
        return (
            Manager.list_calls,
            Manager.close_calls,
            tracked,
            cancellation_preserved
            and not getattr(client._factory, "_metadata_lifecycle_tasks", set()),
        )

    lists, closes, tracked, drained = asyncio.run(scenario())
    assert lists == 1
    assert closes == 1
    assert tracked == 1
    assert drained is True


def test_official_metadata_list_failure_precedes_close_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Manager:
        create_calls = 0
        list_calls = 0
        close_calls = 0

        @classmethod
        async def create(cls, **_values: object) -> "Manager":
            cls.create_calls += 1
            return cls()

        async def list_sandbox_infos(self, _filter: object) -> object:
            type(self).list_calls += 1
            raise ConnectionError("private list detail")

        async def close(self) -> None:
            type(self).close_calls += 1
            raise RuntimeError("private close detail")

    monkeypatch.setattr(
        opensandbox_client_module,
        "_load_official_sdk",
        lambda: _reconciliation_sdk(Manager),
    )
    client = OpenSandboxClient(
        config(tmp_path),
        metadata_retry_sleep=lambda _delay: asyncio.sleep(0),
        metadata_retry_backoff=lambda _attempt: 0.0,
    )

    with pytest.raises(SandboxListError) as raised:
        asyncio.run(client.destroy_by_job_id("job-1"))

    assert raised.value.failure_class is FailureClass.CONNECTION_FAILED
    assert "private" not in str(raised.value)
    assert Manager.create_calls == Manager.list_calls == Manager.close_calls == 2


@pytest.mark.parametrize(
    "delay",
    [float("nan"), float("inf"), float("-inf"), -0.001, 0.150001, 1, True, "0"],
)
def test_official_metadata_rejects_invalid_retry_delay_without_sleeping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    delay: object,
) -> None:
    sleeps: list[float] = []

    class Manager:
        list_calls = 0

        @classmethod
        async def create(cls, **_values: object) -> "Manager":
            return cls()

        async def list_sandbox_infos(self, _filter: object) -> object:
            type(self).list_calls += 1
            raise ConnectionError("metadata unavailable")

        async def close(self) -> None:
            pass

    async def sleep(value: float) -> None:
        sleeps.append(value)

    monkeypatch.setattr(
        opensandbox_client_module,
        "_load_official_sdk",
        lambda: _reconciliation_sdk(Manager),
    )
    client = OpenSandboxClient(
        config(tmp_path),
        metadata_retry_sleep=sleep,
        metadata_retry_backoff=lambda _attempt: delay,  # type: ignore[return-value]
    )

    with pytest.raises(SandboxListError):
        asyncio.run(client.destroy_by_job_id("job-1"))

    assert Manager.list_calls == 1
    assert sleeps == []


def test_official_metadata_accepts_exact_maximum_retry_delay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleeps: list[float] = []

    class Manager:
        list_calls = 0

        @classmethod
        async def create(cls, **_values: object) -> "Manager":
            return cls()

        async def list_sandbox_infos(self, _filter: object) -> object:
            type(self).list_calls += 1
            if type(self).list_calls == 1:
                raise ConnectionError("metadata unavailable")
            return SimpleNamespace(sandbox_infos=[])

        async def close(self) -> None:
            pass

    async def sleep(value: float) -> None:
        sleeps.append(value)

    monkeypatch.setattr(
        opensandbox_client_module,
        "_load_official_sdk",
        lambda: _reconciliation_sdk(Manager),
    )
    client = OpenSandboxClient(
        config(tmp_path),
        metadata_retry_sleep=sleep,
        metadata_retry_backoff=lambda _attempt: 0.15,
    )

    destroyed = asyncio.run(client.destroy_by_job_id("job-1"))

    assert destroyed == 0
    assert Manager.list_calls == 2
    assert sleeps == [0.15]


def test_all_domain_errors_share_one_narrow_base() -> None:
    assert all(
        issubclass(error, SandboxClientError)
        for error in (
            SandboxInputError,
            SandboxDependencyUnavailableError,
            SandboxCreateError,
            SandboxUploadError,
            SandboxReadError,
            SandboxListError,
            SandboxRunError,
            SandboxDestroyError,
            SandboxProtocolError,
        )
    )


def test_destroy_error_completion_certainty_defaults_fail_closed() -> None:
    failure = SandboxDestroyError(
        "sandbox cleanup failed",
        operation="destroy",
        failure_class=FailureClass.CONNECTION_FAILED,
    )

    assert failure.completion_known is False
    with pytest.raises(AttributeError):
        failure.completion_known = True  # type: ignore[misc]
    with pytest.raises((TypeError, ValueError)):
        SandboxDestroyError(
            "sandbox cleanup failed",
            operation="destroy",
            completion_known=1,  # type: ignore[arg-type]
        )


def test_terminal_underlying_destroy_failure_marks_completion_known(
    tmp_path: Path,
) -> None:
    fake = FakeSandboxFactory()
    fake.failures["destroy"] = ConnectionError("private destroy transport")
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    with pytest.raises(SandboxDestroyError) as raised:
        asyncio.run(client.destroy(SandboxHandle("sandbox-1", fake.raw)))

    assert raised.value.completion_known is True
    assert raised.value.failure_class is FailureClass.CONNECTION_FAILED
    assert "private destroy transport" not in str(raised.value)
