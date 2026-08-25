from __future__ import annotations

import asyncio
import ast
import inspect
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

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
)


SHA256 = "a" * 64
FIXED_COMMAND = (
    "python /opt/medchat/run_docking.py "
    "--request /workspace/input/request.json --output /workspace/output"
)


@dataclass
class FakeRawSandbox:
    id: str = "sandbox-1"


class FakeSandboxFactory:
    def __init__(self) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.commands: list[str] = []
        self.uploads: list[tuple[str, str]] = []
        self.reads: list[tuple[str, int]] = []
        self.searches: list[tuple[str, str]] = []
        self.destroyed: list[str] = []
        self.destroyed_by_id: list[str] = []
        self.raw = FakeRawSandbox()
        self.read_result: object = ""
        self.list_result: object = []
        self.execution: object = SimpleNamespace(
            exit_code=0,
            logs=SimpleNamespace(stdout=[], stderr=[]),
        )
        self.failures: dict[str, BaseException] = {}

    def _fail(self, operation: str) -> None:
        failure = self.failures.get(operation)
        if failure is not None:
            raise failure

    async def create(self, **policy: Any) -> FakeRawSandbox:
        self._fail("create")
        self.create_calls.append(policy)
        return self.raw

    async def upload_text(self, raw: FakeRawSandbox, path: str, data: str) -> None:
        self._fail("upload")
        del raw
        self.uploads.append((path, data))

    async def read_text(
        self,
        raw: FakeRawSandbox,
        path: str,
        max_bytes: int,
    ) -> object:
        self._fail("read")
        del raw
        self.reads.append((path, max_bytes))
        return self.read_result

    async def list_files(
        self,
        raw: FakeRawSandbox,
        path: str,
        pattern: str,
    ) -> object:
        self._fail("list")
        del raw
        self.searches.append((path, pattern))
        return self.list_result

    async def run(self, raw: FakeRawSandbox, command: str) -> object:
        self._fail("run")
        del raw
        self.commands.append(command)
        return self.execution

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
            "network_default": "deny",
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


def test_dependency_profiles_are_exact() -> None:
    root = Path(__file__).parents[2]
    assert (root / "requirements-opensandbox-broker.txt").read_text(
        encoding="utf-8"
    ).splitlines() == [
        "fastapi==0.104.1",
        "httpx==0.25.2",
        "opensandbox==0.1.15",
        "pydantic==2.5.0",
        "python-multipart==0.0.6",
        "uvicorn[standard]==0.24.0",
    ]
    assert (root / "requirements-opensandbox-server.txt").read_text(
        encoding="utf-8"
    ).splitlines() == ["opensandbox-server==0.2.2"]


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


def test_read_is_bounded_and_rejects_non_text_or_oversized_results(tmp_path: Path) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)

    fake.read_result = b"not text"
    with pytest.raises(SandboxProtocolError):
        asyncio.run(client.read_text(handle, "/workspace/output/result.json"))

    fake.read_result = "x" * (16 * 1024 * 1024 + 1)
    with pytest.raises(SandboxReadError):
        asyncio.run(client.read_text(handle, "/workspace/output/result.json"))

    assert all(limit == 16 * 1024 * 1024 for _, limit in fake.reads)


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

    assert fake.searches == []


def test_list_revalidates_deduplicates_and_bounds_server_paths(tmp_path: Path) -> None:
    fake = FakeSandboxFactory()
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)
    handle = SandboxHandle("sandbox-1", fake.raw)
    valid = "/workspace/output/poses/result.pdbqt"
    fake.list_result = [valid, valid, "/workspace/output/result.json"]

    assert asyncio.run(client.list_files(handle, "/workspace/output", "*")) == [
        valid,
        "/workspace/output/result.json",
    ]

    for unsafe in (
        ["/workspace/output/../input/request.json"],
        ["/workspace/output\\result.json"],
        ["/workspace/outputish/result.json"],
        [1],
        "not-a-list",
        [f"/workspace/output/file-{index}.json" for index in range(257)],
    ):
        fake.list_result = unsafe
        with pytest.raises((SandboxProtocolError, SandboxListError)):
            asyncio.run(client.list_files(handle, "/workspace/output", "*"))


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
        logs=SimpleNamespace(
            stdout=[SimpleNamespace(text=dangerous)],
            stderr=[SimpleNamespace(text=dangerous)],
        ),
    )
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

    if operation == "destroy":
        call = client.destroy(SandboxHandle("sandbox-1", fake.raw))
    else:
        call = client.destroy_by_id("sandbox-1")
    with pytest.raises(type(failure)) as raised:
        asyncio.run(call)

    if not isinstance(failure, asyncio.CancelledError):
        assert raised.value is failure
    assert len(fake.destroyed) + len(fake.destroyed_by_id) == 1


@pytest.mark.parametrize("operation", ["destroy", "destroy_by_id"])
def test_destroy_maps_ordinary_errors_without_retrying(
    tmp_path: Path,
    operation: str,
) -> None:
    fake = FakeSandboxFactory()
    fake.failures[operation] = RuntimeError(
        "key=test-only-credential C:\\Users\\private\\file"
    )
    client = OpenSandboxClient(config(tmp_path), sandbox_factory=fake)

    async def scenario() -> None:
        if operation == "destroy":
            await client.destroy(SandboxHandle("sandbox-1", fake.raw))
        else:
            await client.destroy_by_id("sandbox-1")

    with pytest.raises(SandboxDestroyError) as raised:
        asyncio.run(scenario())
    asyncio.run(scenario())

    assert "test-only-credential" not in str(raised.value)
    assert "Users" not in str(raised.value)
    assert len(fake.destroyed) + len(fake.destroyed_by_id) == 1


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
