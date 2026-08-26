from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Callable

import pytest

from src.sandbox_broker.validation import validate_scientific_output


ROOT = Path(__file__).parents[2]
DEPLOYMENT = ROOT / "deployment" / "opensandbox"
DOCKERFILE = DEPLOYMENT / "Dockerfile.docking"
RUNNER = DEPLOYMENT / "run_docking.py"
LOCK = DEPLOYMENT / "environment-lock.yml"
REQUEST_ARGUMENTS = [
    "--request",
    "/workspace/input/request.json",
    "--output",
    "/workspace/output",
]
SUCCESS_FIELDS = {
    "schema_version",
    "status",
    "receptor_sha256",
    "ligand_sha256",
    "pose_count",
    "best_energy",
    "pose_files",
    "vina_version",
    "meeko_version",
    "warnings",
}
TARGET_PACKAGES = {
    "python",
    "vina",
    "meeko",
    "rdkit",
    "prody",
    "numpy",
    "openbabel",
}
MINIMAL_ENV_KEYS = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("task8_run_docking", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _request(receptor: bytes = b"RECEPTOR\n", ligand: bytes = b"LIGAND\n") -> dict[str, object]:
    return {
        "schema_version": 1,
        "parameters": {
            "center": [1.0, 2.0, 3.0],
            "size": [20.0, 21.0, 22.0],
            "exhaustiveness": 8,
            "num_modes": 2,
            "energy_range": 3,
        },
        "receptor_path": "/workspace/input/receptor.pdb",
        "ligand_path": "/workspace/input/ligand.sdf",
        "receptor_sha256": _sha(receptor),
        "ligand_sha256": _sha(ligand),
    }


def _write_inputs(
    input_root: Path,
    payload: dict[str, object] | None = None,
    *,
    receptor: bytes = b"RECEPTOR\n",
    ligand: bytes = b"LIGAND\n",
    raw_request: bytes | None = None,
) -> None:
    input_root.mkdir(mode=0o700)
    receptor_path = input_root / "receptor.pdb"
    ligand_path = input_root / "ligand.sdf"
    receptor_path.write_bytes(receptor)
    ligand_path.write_bytes(ligand)
    receptor_path.chmod(0o600)
    ligand_path.chmod(0o600)
    request_path = input_root / "request.json"
    request_path.write_bytes(
        raw_request
        if raw_request is not None
        else json.dumps(payload or _request(receptor, ligand), allow_nan=False).encode("utf-8")
    )
    request_path.chmod(0o600)


def _configure_roots(
    module: ModuleType, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> tuple[Path, Path]:
    input_root = tmp_path / "input"
    output_root = tmp_path / "output"
    output_root.mkdir(mode=0o700)
    monkeypatch.setattr(module, "INPUT_ROOT", input_root)
    monkeypatch.setattr(module, "OUTPUT_ROOT", output_root)
    return input_root, output_root


def _result(output_root: Path) -> dict[str, object]:
    return json.loads((output_root / "result.json").read_text(encoding="ascii"))


def _fake_tools(
    monkeypatch: pytest.MonkeyPatch,
    module: ModuleType,
    calls: list[tuple[list[str], dict[str, object]]],
    *,
    pose: bytes = (
        b"MODEL 1\nREMARK VINA RESULT: -8.2 0.0 0.0\nENDMDL\n"
        b"MODEL 2\nREMARK VINA RESULT: -7.1 1.0 2.0\nENDMDL\n"
    ),
    fail_tool: str | None = None,
    timeout_tool: str | None = None,
) -> None:
    def fake_run(argv: list[str], **kwargs: object) -> SimpleNamespace:
        copied = list(argv)
        calls.append((copied, dict(kwargs)))
        tool = copied[0]
        if tool == timeout_tool:
            raise subprocess.TimeoutExpired(copied, kwargs["timeout"])
        if tool == fail_tool:
            return SimpleNamespace(returncode=9)
        if tool == "mk_prepare_receptor.py":
            Path(copied[4]).write_bytes(b"RECEPTOR PDBQT\n")
        elif tool == "mk_prepare_ligand.py":
            Path(copied[4]).write_bytes(b"LIGAND PDBQT\n")
        elif tool == "vina":
            Path(copied[-1]).write_bytes(pose)
        else:  # pragma: no cover - a contract regression should show the argv
            raise AssertionError(copied)
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    def fake_version(distribution: str) -> str:
        return {"vina": "1.2.7", "meeko": "0.7.1"}[distribution]

    monkeypatch.setattr(module.metadata, "version", fake_version)


def _run_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pose: bytes | None = None,
) -> tuple[ModuleType, Path, list[tuple[list[str], dict[str, object]]], int]:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(input_root, _request())
    calls: list[tuple[list[str], dict[str, object]]] = []
    options = {} if pose is None else {"pose": pose}
    _fake_tools(monkeypatch, module, calls, **options)
    return module, output_root, calls, module.main(REQUEST_ARGUMENTS)


def _run_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    payload: dict[str, object] | None = None,
    raw_request: bytes | None = None,
    pose: bytes | None = None,
    fail_tool: str | None = None,
    timeout_tool: str | None = None,
    mutate: Callable[[Path], None] | None = None,
) -> tuple[int, dict[str, object], Path, list[tuple[list[str], dict[str, object]]]]:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(input_root, payload or _request(), raw_request=raw_request)
    if mutate is not None:
        mutate(input_root)
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(
        monkeypatch,
        module,
        calls,
        **({} if pose is None else {"pose": pose}),
        fail_tool=fail_tool,
        timeout_tool=timeout_tool,
    )
    exit_code = module.main(REQUEST_ARGUMENTS)
    return exit_code, _result(output_root), output_root, calls


def test_all_task8_assets_exist() -> None:
    assert DOCKERFILE.is_file()
    assert RUNNER.is_file()
    assert LOCK.is_file()


def test_dockerfile_is_exact_non_root_and_has_narrow_copy_scope() -> None:
    text = DOCKERFILE.read_text(encoding="utf-8")
    lines = [line.rstrip() for line in text.splitlines() if line.strip()]
    assert lines[0] == "FROM mambaorg/micromamba:2.3.2"
    assert (
        "COPY --chown=$MAMBA_USER:$MAMBA_USER "
        "deployment/opensandbox/environment-lock.yml /tmp/environment-lock.yml"
    ) in lines
    assert "RUN micromamba config set channel_priority strict" in lines
    assert (
        "RUN micromamba install -y -n base -f /tmp/environment-lock.yml "
        "&& micromamba clean --all --yes"
    ) in lines
    assert "USER root" in lines
    assert any(
        "install -d -o 65532 -g 65532 -m 0700 /workspace/input /workspace/output" in line
        for line in lines
    )
    assert any("install -d -o root -g root -m 0555 /opt/medchat" in line for line in lines)
    assert (
        "COPY deployment/opensandbox/run_docking.py /opt/medchat/run_docking.py"
    ) in lines
    assert any(
        "chown root:root /opt/medchat/run_docking.py" in line
        and "chmod 0555 /opt/medchat/run_docking.py" in line
        for line in lines
    )
    assert lines[-3:] == [
        "USER 65532:65532",
        "WORKDIR /workspace",
        'ENTRYPOINT ["sleep", "infinity"]',
    ]
    forbidden = ("COPY .", "ADD ", "curl ", "src/", "requirements.txt")
    assert not any(token in text for token in forbidden)


def test_lock_is_explicit_linux64_strict_and_fully_build_pinned() -> None:
    text = LOCK.read_text(encoding="utf-8")
    lines = text.splitlines()
    assert lines[:3] == [
        "name: base",
        "channels:",
        "  - conda-forge",
    ]
    assert not any(line.startswith(("channel_priority:", "subdir:")) for line in lines)
    assert "--platform linux-64" in text
    assert "--strict-channel-priority" in text
    assert not re.search(r"^\s+- pip:\s*$", text, re.MULTILINE)
    dependencies = [
        line.strip()[2:]
        for line in lines
        if line.startswith("  - ") and line.strip()[2:] != "conda-forge"
    ]
    assert len(dependencies) >= 40
    assert len(dependencies) == len(set(dependencies))
    assert all(re.fullmatch(r"[a-z0-9_.+-]+=[^=\s*]+=[^=\s*]+", item) for item in dependencies)
    names = {item.split("=", 1)[0] for item in dependencies}
    assert TARGET_PACKAGES <= names


def test_wrapper_ast_forbids_dynamic_execution_and_uses_safe_subprocess() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 10))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    forbidden_names = {"eval", "exec"}
    assert not any(isinstance(node.func, ast.Name) and node.func.id in forbidden_names for node in calls)
    assert not any(
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"system", "popen", "Popen"}
        for node in calls
    )
    run_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "run"
    ]
    assert run_calls
    assert all(node.args and not isinstance(node.args[0], ast.Constant) for node in run_calls)
    assert all(
        any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            for keyword in node.keywords)
        for node in run_calls
    )
    assert 'Path("/workspace/input")' in source
    assert 'Path("/workspace/output")' in source
    assert "allow_nan=False" in source
    assert "object_pairs_hook" in source and "parse_constant" in source


def test_valid_fake_tools_execute_exact_sequence_and_emit_validator_schema(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, output_root, calls, exit_code = _run_valid(tmp_path, monkeypatch)
    assert exit_code == 0
    payload = _result(output_root)
    assert set(payload) == SUCCESS_FIELDS
    assert payload == {
        "schema_version": 1,
        "status": "succeeded",
        "receptor_sha256": _sha(b"RECEPTOR\n"),
        "ligand_sha256": _sha(b"LIGAND\n"),
        "pose_count": 2,
        "best_energy": -8.2,
        "pose_files": ["poses/result.pdbqt"],
        "vina_version": "1.2.7",
        "meeko_version": "0.7.1",
        "warnings": [],
    }
    validated = validate_scientific_output(
        output_root,
        _sha(b"RECEPTOR\n"),
        _sha(b"LIGAND\n"),
        20 * 1024 * 1024,
    )
    assert validated.pose_count == 2
    assert validated.best_energy == -8.2
    assert [call[0][0] for call in calls] == [
        "mk_prepare_receptor.py",
        "mk_prepare_ligand.py",
        "vina",
    ]
    receptor_pdbqt = output_root / ".work" / "receptor.pdbqt"
    ligand_pdbqt = output_root / ".work" / "ligand.pdbqt"
    assert calls[0][0] == [
        "mk_prepare_receptor.py",
        "-i",
        str(tmp_path / "input" / "receptor.pdb"),
        "--write_pdbqt",
        str(receptor_pdbqt),
    ]
    assert calls[1][0] == [
        "mk_prepare_ligand.py",
        "-i",
        str(tmp_path / "input" / "ligand.sdf"),
        "-o",
        str(ligand_pdbqt),
    ]
    assert calls[2][0] == [
        "vina",
        "--receptor",
        str(receptor_pdbqt),
        "--ligand",
        str(ligand_pdbqt),
        "--center_x",
        "1.0",
        "--center_y",
        "2.0",
        "--center_z",
        "3.0",
        "--size_x",
        "20.0",
        "--size_y",
        "21.0",
        "--size_z",
        "22.0",
        "--exhaustiveness",
        "8",
        "--num_modes",
        "2",
        "--energy_range",
        "3",
        "--out",
        str(output_root / "poses" / "result.pdbqt"),
    ]
    assert not (output_root / ".work").exists()


def test_subprocess_has_cumulative_deadline_silent_streams_and_secret_free_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setenv("MEDCHAT_PRIVATE_TOKEN", "token-secret-value")
    _, _, calls, exit_code = _run_valid(tmp_path, monkeypatch)
    assert exit_code == 0
    timeouts = [float(kwargs["timeout"]) for _, kwargs in calls]
    assert all(0 < value <= 180 for value in timeouts)
    assert timeouts == sorted(timeouts, reverse=True)
    for _, kwargs in calls:
        assert kwargs["shell"] is False
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        env = kwargs["env"]
        assert isinstance(env, dict)
        assert set(env) == MINIMAL_ENV_KEYS
        assert "sk-secret-value" not in repr(env)
        assert "token-secret-value" not in repr(env)
        assert env["PATH"].startswith("/opt/conda/bin:")


@pytest.mark.parametrize(
    "raw_request",
    [
        b'{"schema_version":1,"schema_version":1}',
        b'{"schema_version":NaN}',
        b'{"schema_version":Infinity}',
        b"\xff\xfe",
        b'{"schema_version":1,"receptor_path":"abc\\u0000def"}',
        b"{" + b'\"x\":\"' + (b"a" * (1024 * 1024)) + b'\"}',
    ],
    ids=["duplicate", "nan", "infinity", "non_utf8", "nul", "oversized"],
)
def test_invalid_json_is_rejected_with_stable_sanitized_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, raw_request: bytes
) -> None:
    exit_code, result, output_root, calls = _run_failure(
        tmp_path, monkeypatch, raw_request=raw_request
    )
    assert exit_code != 0
    assert calls == []
    assert result == {
        "schema_version": 1,
        "status": "failed",
        "phase": "input_validation",
        "error_code": "invalid_input",
        "warnings": [],
    }
    assert not (output_root / "poses").exists()


@pytest.mark.parametrize(
    "mutation",
    [
        lambda data: data.__setitem__("extra", 1),
        lambda data: data.__setitem__("schema_version", True),
        lambda data: data["parameters"].__setitem__("extra", 1),  # type: ignore[union-attr]
        lambda data: data["parameters"].__setitem__("exhaustiveness", True),  # type: ignore[union-attr]
        lambda data: data["parameters"].__setitem__("center", [0.0, 1_000_001.0, 0.0]),  # type: ignore[union-attr]
        lambda data: data.__setitem__("receptor_path", "/workspace/input/../receptor.pdb"),
        lambda data: data.__setitem__("ligand_path", "/workspace/input/ligand.xyz"),
        lambda data: data.__setitem__("receptor_sha256", "A" * 64),
        lambda data: data.__setitem__("ligand_sha256", "0" * 64),
    ],
    ids=[
        "extra",
        "bool_schema",
        "parameter_extra",
        "bool_integer",
        "out_of_range_vector",
        "traversal",
        "suffix",
        "uppercase_hash",
        "hash_mismatch",
    ],
)
def test_invalid_request_contract_is_rejected_before_tools(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    mutation: Callable[[dict[str, object]], None],
) -> None:
    payload = _request()
    mutation(payload)
    exit_code, result, _, calls = _run_failure(tmp_path, monkeypatch, payload=payload)
    assert exit_code != 0
    assert result["error_code"] == "invalid_input"
    assert "best_energy" not in result and "pose_count" not in result
    assert calls == []


def test_symlinked_input_is_rejected_without_reading_target(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_path / "outside-secret.pdb"
    target.write_bytes(b"RECEPTOR\n")

    def replace_with_link(input_root: Path) -> None:
        receptor = input_root / "receptor.pdb"
        receptor.unlink()
        try:
            receptor.symlink_to(target)
        except (NotImplementedError, OSError) as exc:
            pytest.skip(f"symlinks unavailable: {type(exc).__name__}")

    exit_code, result, _, calls = _run_failure(
        tmp_path, monkeypatch, mutate=replace_with_link
    )
    assert exit_code != 0
    assert result["error_code"] == "invalid_input"
    assert calls == []
    assert "outside-secret" not in json.dumps(result)


def test_input_hash_drift_between_validation_and_execution_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(input_root, _request())
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)
    original_parse = module._parse_request

    def parse_then_replace() -> object:
        parsed = original_parse()
        receptor = input_root / "receptor.pdb"
        receptor.write_bytes(b"CHANGED\n")
        receptor.chmod(0o600)
        return parsed

    monkeypatch.setattr(module, "_parse_request", parse_then_replace)
    assert module.main(REQUEST_ARGUMENTS) != 0
    result = _result(output_root)
    assert result["phase"] == "input_validation"
    assert result["error_code"] == "invalid_input"
    assert "best_energy" not in result and "pose_count" not in result
    assert calls == []


def test_absolute_deadline_includes_input_validation_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(input_root, _request())
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)
    now = [0.0]
    monkeypatch.setattr(module.time, "monotonic", lambda: now[0])
    original_parse = module._parse_request

    def slow_parse() -> object:
        parsed = original_parse()
        now[0] = 181.0
        return parsed

    monkeypatch.setattr(module, "_parse_request", slow_parse)
    assert module.main(REQUEST_ARGUMENTS) != 0
    result = _result(output_root)
    assert result["phase"] == "receptor_preparation"
    assert result["error_code"] == "tool_timeout"
    assert calls == []


@pytest.mark.parametrize(
    ("kind", "tool", "expected_code"),
    [
        ("nonzero", "mk_prepare_receptor.py", "tool_failed"),
        ("nonzero", "mk_prepare_ligand.py", "tool_failed"),
        ("nonzero", "vina", "tool_failed"),
        ("timeout", "mk_prepare_receptor.py", "tool_timeout"),
        ("timeout", "vina", "tool_timeout"),
    ],
)
def test_timeout_and_nonzero_are_non_scientific_failures(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    kind: str,
    tool: str,
    expected_code: str,
) -> None:
    options = {"fail_tool": tool} if kind == "nonzero" else {"timeout_tool": tool}
    exit_code, result, output_root, _ = _run_failure(
        tmp_path, monkeypatch, **options
    )
    assert exit_code != 0
    assert result["status"] == "failed"
    assert result["error_code"] == expected_code
    assert result["phase"] in {
        "receptor_preparation",
        "ligand_preparation",
        "docking",
    }
    assert "best_energy" not in result and "pose_count" not in result
    assert not (output_root / "poses" / "result.pdbqt").exists()
    assert not (output_root / ".work").exists()


@pytest.mark.parametrize(
    "pose",
    [
        b"REMARK VINA RESULT: malformed\n",
        b"MODEL 1\nENDMDL\n",
        b"REMARK VINA RESULT: NaN 0.0 0.0\n",
        b"REMARK VINA RESULT: -7.0 0.0\n",
        b"REMARK VINA RESULT: -7.0 0.0 0.0 trailing\n",
        b"REMARK VINA RESULT: -7.0 0.0 0.0\x0b",
        b"A" * 4097 + b"\nREMARK VINA RESULT: -7.0 0.0 0.0\n",
        (
            b"REMARK VINA RESULT: -8.0 0.0 0.0\n"
            b"REMARK VINA RESULT: -7.0 0.0 0.0\n"
            b"REMARK VINA RESULT: -6.0 0.0 0.0\n"
        ),
    ],
    ids=[
        "malformed",
        "no_energy",
        "nan_energy",
        "missing_rmsd",
        "trailing_claim",
        "control_byte",
        "overlong_line",
        "too_many_modes",
    ],
)
def test_malformed_pose_claims_never_publish_science(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pose: bytes
) -> None:
    exit_code, result, output_root, _ = _run_failure(
        tmp_path, monkeypatch, pose=pose
    )
    assert exit_code != 0
    assert result["phase"] == "output_validation"
    assert result["error_code"] == "invalid_output"
    assert "best_energy" not in result and "pose_count" not in result
    assert not (output_root / "poses" / "result.pdbqt").exists()


def test_oversized_pose_is_rejected_and_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pose = b"REMARK VINA RESULT: -8.0 0.0 0.0\n" + b"X" * (16 * 1024 * 1024)
    exit_code, result, output_root, _ = _run_failure(tmp_path, monkeypatch, pose=pose)
    assert exit_code != 0
    assert result["error_code"] == "invalid_output"
    assert not (output_root / "poses" / "result.pdbqt").exists()


def test_result_publish_is_atomic_mode_0600_and_preserves_unrelated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    unrelated = output_root / "keep.txt"
    unrelated.write_text("owner-data", encoding="utf-8")
    _write_inputs(input_root, _request())
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)
    replaces: list[tuple[Path, Path]] = []
    original_replace = module.os.replace

    def recording_replace(source: object, destination: object) -> None:
        source_path = Path(source)
        if os.name == "posix":
            assert stat.S_IMODE(source_path.stat().st_mode) == 0o600
        replaces.append((source_path, Path(destination)))
        original_replace(source, destination)

    monkeypatch.setattr(module.os, "replace", recording_replace)
    assert module.main(REQUEST_ARGUMENTS) == 0
    assert replaces and replaces[-1][1] == output_root / "result.json"
    assert unrelated.read_text(encoding="utf-8") == "owner-data"
    assert not list(output_root.glob(".result.*.tmp"))
    if os.name == "posix":
        assert stat.S_IMODE((output_root / "result.json").stat().st_mode) == 0o600


def test_existing_result_is_never_overwritten(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(input_root, _request())
    result = output_root / "result.json"
    result.write_bytes(b"owner-result")
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)
    assert module.main(REQUEST_ARGUMENTS) != 0
    assert result.read_bytes() == b"owner-result"
    assert calls == []


@pytest.mark.parametrize(
    "argv",
    [
        [],
        REQUEST_ARGUMENTS[:-1],
        ["--output", "/workspace/output", "--request", "/workspace/input/request.json"],
        REQUEST_ARGUMENTS + ["extra"],
        REQUEST_ARGUMENTS + ["--output", "/workspace/output"],
        ["--request", "request.json", "--output", "/workspace/output"],
        ["--request", "/workspace/input/request.json", "--output", "/workspace/output/"],
    ],
)
def test_cli_rejects_missing_reordered_extra_duplicate_or_nonexact_values(
    argv: list[str],
) -> None:
    module = _load_runner()
    with pytest.raises(SystemExit) as caught:
        module.main(argv)
    assert caught.value.code == 2


def test_runner_source_parses_as_python_310() -> None:
    ast.parse(RUNNER.read_text(encoding="utf-8"), feature_version=(3, 10))
