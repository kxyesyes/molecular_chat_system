from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
import os
import re
import signal
import stat
import subprocess
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Callable

import pytest

from src.sandbox_broker.validation import validate_scientific_output
from src.sandbox_broker.models import DockingParameters


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
    "setuptools",
}
TARGET_PACKAGE_PINS = {
    "python": "3.10.14=hd12c33a_0_cpython",
    "vina": "1.2.5=py310hb68b39e_3",
    "meeko": "0.7.1=pyhd8ed1ab_1",
    "rdkit": "2023.09.6=py310hb79e901_1",
    "prody": "2.4.1=pyh5e1b82b_2",
    "numpy": "1.26.4=py310hb13e2d6_0",
    "openbabel": "3.1.1=py310hbff9852_9",
    "setuptools": "81.0.0=pyh332efcf_0",
}
MINIMAL_ENV_KEYS = {"PATH", "HOME", "TMPDIR", "LANG", "LC_ALL"}
VALID_MOL = b"""ethanol
  MedChat        2D

  3  2  0  0  0  0  0  0  0  0999 V2000
    0.0000    0.0000    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    1.2990    0.7500    0.0000 C   0  0  0  0  0  0  0  0  0  0  0  0
    2.5981    0.0000    0.0000 O   0  0  0  0  0  0  0  0  0  0  0  0
  1  2  1  0
  2  3  1  0
M  END
"""
VALID_SDF = VALID_MOL + b"$$$$\n"


def _load_runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location("task8_run_docking", RUNNER)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _sha(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _parameters(**updates: object) -> DockingParameters:
    values: dict[str, object] = {
        "center": [1.0, 2.0, 3.0],
        "size": [20.0, 21.0, 22.0],
        "exhaustiveness": 8,
        "num_modes": 2,
    }
    values.update(updates)
    return DockingParameters(**values)


def _request(
    receptor: bytes = b"RECEPTOR\n",
    ligand: bytes = VALID_SDF,
    *,
    parameters: DockingParameters | None = None,
    ligand_suffix: str = ".sdf",
) -> dict[str, object]:
    return {
        "schema_version": 1,
        "parameters": (parameters or _parameters()).model_dump(mode="json"),
        "receptor_path": "/workspace/input/receptor.pdb",
        "ligand_path": f"/workspace/input/ligand{ligand_suffix}",
        "receptor_sha256": _sha(receptor),
        "ligand_sha256": _sha(ligand),
    }


def _write_inputs(
    input_root: Path,
    payload: dict[str, object] | None = None,
    *,
    receptor: bytes = b"RECEPTOR\n",
    ligand: bytes = VALID_SDF,
    ligand_suffix: str = ".sdf",
    raw_request: bytes | None = None,
) -> None:
    input_root.mkdir(mode=0o700)
    receptor_path = input_root / "receptor.pdb"
    ligand_path = input_root / f"ligand{ligand_suffix}"
    receptor_path.write_bytes(receptor)
    ligand_path.write_bytes(ligand)
    receptor_path.chmod(0o600)
    ligand_path.chmod(0o600)
    request_path = input_root / "request.json"
    request_path.write_bytes(
        raw_request
        if raw_request is not None
        else json.dumps(
            payload or _request(receptor, ligand, ligand_suffix=ligand_suffix),
            allow_nan=False,
        ).encode("utf-8")
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
    unexpected_nested_tool: str | None = None,
) -> None:
    class FakeProcess:
        next_pid = 20000

        def __init__(self, argv: list[str], **kwargs: object) -> None:
            self.argv = list(argv)
            self.kwargs = dict(kwargs)
            self.pid = FakeProcess.next_pid
            FakeProcess.next_pid += 1
            self.returncode: int | None = None
            self.terminated = False
            self.killed = False
            calls.append((self.argv, self.kwargs))
            tool = self.argv[0]
            if tool == "mk_prepare_receptor.py":
                Path(self.argv[4]).write_bytes(b"RECEPTOR PDBQT\n")
            elif tool == "obabel":
                Path(self.argv[self.argv.index("-O") + 1]).write_bytes(VALID_SDF)
            elif tool == "mk_prepare_ligand.py":
                Path(self.argv[4]).write_bytes(b"LIGAND PDBQT\n")
            elif tool == "vina":
                Path(self.argv[-1]).write_bytes(pose)
            else:  # pragma: no cover - a contract regression should show the argv
                raise AssertionError(self.argv)
            if tool == unexpected_nested_tool:
                parent = Path(self.argv[-1]).parent
                nested = parent / "unexpected" / "nested"
                nested.mkdir(parents=True)
                (nested / "probe.txt").write_bytes(b"unexpected")

        def wait(self, timeout: float | None = None) -> int:
            self.kwargs["wait_timeout"] = timeout
            if self.argv[0] == timeout_tool and not (self.terminated or self.killed):
                raise subprocess.TimeoutExpired(self.argv, timeout)
            if self.returncode is None:
                self.returncode = 9 if self.argv[0] == fail_tool else 0
            return self.returncode

        def poll(self) -> int | None:
            return self.returncode

        def terminate(self) -> None:
            self.terminated = True
            self.returncode = -int(signal.SIGTERM)

        def kill(self) -> None:
            self.killed = True
            self.returncode = -int(signal.SIGKILL)

    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)

    def fake_version(distribution: str) -> str:
        return {"vina": "1.2.5", "meeko": "0.7.1"}[distribution]

    monkeypatch.setattr(module.metadata, "version", fake_version)


def _run_valid(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    pose: bytes | None = None,
    parameters: DockingParameters | None = None,
) -> tuple[ModuleType, Path, list[tuple[list[str], dict[str, object]]], int]:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(input_root, _request(parameters=parameters))
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
    unexpected_nested_tool: str | None = None,
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
        unexpected_nested_tool=unexpected_nested_tool,
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
        "deployment/opensandbox/environment-lock.yml /tmp/environment-exact.yml"
    ) in lines
    assert "RUN micromamba config set channel_priority strict" in lines
    assert (
        "RUN micromamba install -y -n base -f /tmp/environment-exact.yml "
        "&& micromamba clean --all --yes"
    ) in lines
    assert 'RUN /opt/conda/bin/python -c "import pkg_resources, prody"' in lines
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
    resolved = {
        item.split("=", 1)[0]: item.split("=", 1)[1] for item in dependencies
    }
    assert {name: resolved[name] for name in TARGET_PACKAGE_PINS} == TARGET_PACKAGE_PINS


@pytest.mark.skipif(
    not os.environ.get("MICROMAMBA_EXE"),
    reason="set MICROMAMBA_EXE to opt in to the real 2.3.2 consumption test",
)
def test_lock_is_consumed_by_opt_in_micromamba_232(tmp_path: Path) -> None:
    binary = Path(os.environ["MICROMAMBA_EXE"])
    exact_file = tmp_path / "environment-exact.yml"
    exact_file.write_bytes(LOCK.read_bytes())
    version = subprocess.run(
        [str(binary), "--version"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert version.stdout.strip() == "2.3.2"
    environment = {
        "MAMBA_ROOT_PREFIX": str(tmp_path / "root"),
        "MAMBA_NO_RC": "true",
        "PATH": os.environ.get("PATH", ""),
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", ""),
        "HOME": str(tmp_path),
        "USERPROFILE": str(tmp_path),
        "TEMP": str(tmp_path),
        "TMP": str(tmp_path),
    }
    consumed = subprocess.run(
        [
            str(binary),
            "install",
            "--dry-run",
            "-y",
            "-n",
            "base",
            "-f",
            str(exact_file),
            "--platform",
            "linux-64",
            "--strict-channel-priority",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=180,
        env=environment,
    )
    assert consumed.returncode == 0, consumed.stderr[-2000:]
    assert "Dry run" in consumed.stdout


def test_wrapper_ast_forbids_dynamic_execution_and_uses_safe_subprocess() -> None:
    source = RUNNER.read_text(encoding="utf-8")
    tree = ast.parse(source, feature_version=(3, 10))
    calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)]
    forbidden_names = {"eval", "exec"}
    assert not any(isinstance(node.func, ast.Name) and node.func.id in forbidden_names for node in calls)
    assert not any(
        isinstance(node.func, ast.Attribute)
        and node.func.attr in {"system", "popen"}
        for node in calls
    )
    popen_calls = [
        node
        for node in calls
        if isinstance(node.func, ast.Attribute) and node.func.attr == "Popen"
    ]
    assert popen_calls
    assert all(node.args and not isinstance(node.args[0], ast.Constant) for node in popen_calls)
    assert all(
        any(keyword.arg == "shell" and isinstance(keyword.value, ast.Constant) and keyword.value.value is False
            for keyword in node.keywords)
        for node in popen_calls
    )
    assert all(
        any(keyword.arg == "start_new_session" and isinstance(keyword.value, ast.Constant) and keyword.value.value is True
            for keyword in node.keywords)
        for node in popen_calls
    )
    assert 'Path("/workspace/input")' in source
    assert 'Path("/workspace/output")' in source
    assert "allow_nan=False" in source
    assert "object_pairs_hook" in source and "parse_constant" in source
    assert "PR_SET_CHILD_SUBREAPER" in source
    assert "ctypes.CDLL" in source
    assert any(
        isinstance(node.func, ast.Attribute) and node.func.attr == "waitpid"
        for node in calls
    )


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
        "ligand_sha256": _sha(VALID_SDF),
        "pose_count": 2,
        "best_energy": -8.2,
        "pose_files": ["poses/result.pdbqt"],
        "vina_version": "1.2.5",
        "meeko_version": "0.7.1",
        "warnings": ["ligand_hydrogens_and_3d_coordinates_prepared"],
    }
    validated = validate_scientific_output(
        output_root,
        _sha(b"RECEPTOR\n"),
        _sha(VALID_SDF),
        20 * 1024 * 1024,
    )
    assert validated.pose_count == 2
    assert validated.best_energy == -8.2
    assert [call[0][0] for call in calls] == [
        "mk_prepare_receptor.py",
        "obabel",
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
    ligand_prepared = output_root / ".work" / "ligand.prepared.sdf"
    assert calls[1][0] == [
        "obabel",
        str(tmp_path / "input" / "ligand.sdf"),
        "-O",
        str(ligand_prepared),
        "-h",
        "--gen3d",
    ]
    assert calls[2][0] == [
        "mk_prepare_ligand.py",
        "-i",
        str(ligand_prepared),
        "-o",
        str(ligand_pdbqt),
    ]
    assert calls[3][0] == [
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
        "3.0",
        "--out",
        str(output_root / "poses" / "result.pdbqt"),
    ]
    assert not (output_root / ".work").exists()


def test_legacy_encoded_pdb_header_is_normalized_without_changing_coordinates_or_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receptor = (
        b"HEADER    LEGACY ENCODED SAMPLE\n"
        b"COMPND    D:\\Desktop\\\xc9\xa3\xc0\xcf\xca\xa6\xb7\xd6\xd7\xd3\xb6\xd4\xbd\xd3\\MAGL.pdbqt\n"
        b"ATOM      1  N   GLY A   1      11.104  13.207   9.154  1.00 20.00           N  \n"
        b"END\n"
    )
    coordinate = next(
        line for line in receptor.splitlines(keepends=True) if line.startswith(b"ATOM  ")
    )
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(
        input_root,
        _request(receptor=receptor),
        receptor=receptor,
    )
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)
    original_run_tool = module._run_tool
    prepared_inputs: list[bytes] = []

    def require_utf8_receptor(
        argv: list[str], phase: str, deadline: float, cleanup_deadline: float
    ) -> None:
        if argv[0] == "mk_prepare_receptor.py":
            prepared = Path(argv[2]).read_bytes()
            prepared.decode("utf-8", errors="strict")
            prepared_inputs.append(prepared)
        original_run_tool(argv, phase, deadline, cleanup_deadline)

    monkeypatch.setattr(module, "_run_tool", require_utf8_receptor)

    assert module.main(REQUEST_ARGUMENTS) == 0
    assert len(prepared_inputs) == 1
    assert coordinate in prepared_inputs[0]
    assert all(value < 128 for value in prepared_inputs[0])
    assert (input_root / "receptor.pdb").read_bytes() == receptor
    payload = _result(output_root)
    assert payload["receptor_sha256"] == _sha(receptor)
    assert payload["warnings"] == [
        "receptor_header_encoding_normalized",
        "ligand_hydrogens_and_3d_coordinates_prepared",
    ]
    assert calls[0][0][2] != str(input_root / "receptor.pdb")
    assert not (output_root / ".work").exists()


def test_unlabelled_duplicate_receptor_atoms_select_first_conformer_with_warning(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    first_ca = (
        b"ATOM      2  CA  SER A  13      11.000  12.000  13.000  1.00 20.00           C  \n"
    )
    second_ca = (
        b"ATOM      3  CA  SER A  13      21.000  22.000  23.000  1.00 20.00           C  \n"
    )
    receptor = (
        b"HEADER    UNLABELLED ALTERNATE CONFORMERS\n"
        b"ATOM      1  N   SER A  13      10.000  12.000  13.000  1.00 20.00           N  \n"
        + first_ca
        + second_ca
        + b"ATOM      4  C   SER A  13      12.000  12.000  13.000  1.00 20.00           C  \n"
        + b"END\n"
    )
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(
        input_root,
        _request(receptor=receptor),
        receptor=receptor,
    )
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)
    original_run_tool = module._run_tool
    prepared_inputs: list[bytes] = []

    def capture_receptor(
        argv: list[str], phase: str, deadline: float, cleanup_deadline: float
    ) -> None:
        if argv[0] == "mk_prepare_receptor.py":
            prepared_inputs.append(Path(argv[2]).read_bytes())
        original_run_tool(argv, phase, deadline, cleanup_deadline)

    monkeypatch.setattr(module, "_run_tool", capture_receptor)

    assert module.main(REQUEST_ARGUMENTS) == 0
    assert len(prepared_inputs) == 1
    assert first_ca in prepared_inputs[0]
    assert second_ca not in prepared_inputs[0]
    assert (input_root / "receptor.pdb").read_bytes() == receptor
    payload = _result(output_root)
    assert payload["receptor_sha256"] == _sha(receptor)
    assert payload["warnings"] == [
        "receptor_duplicate_atoms_first_conformer_selected",
        "ligand_hydrogens_and_3d_coordinates_prepared",
    ]
    assert calls[0][0][2] != str(input_root / "receptor.pdb")
    assert not (output_root / ".work").exists()


@pytest.mark.parametrize(
    "parameters",
    [
        _parameters(),
        _parameters(
            center=[1.0e300, -1.0e300, 0.0],
            size=[80.0, 0.1, 1.0],
            exhaustiveness=64,
            num_modes=20,
            energy_range=20,
        ),
        _parameters(energy_range=0),
    ],
    ids=["defaults", "upper_bounds_and_unbounded_center", "zero_energy_range"],
)
def test_wrapper_accepts_real_docking_parameter_payloads(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parameters: DockingParameters,
) -> None:
    _, _, calls, exit_code = _run_valid(
        tmp_path, monkeypatch, parameters=parameters
    )
    assert exit_code == 0
    vina = calls[-1][0]
    assert vina[vina.index("--energy_range") + 1] == str(parameters.energy_range)


def test_subprocess_has_cumulative_deadline_silent_streams_and_secret_free_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "sk-secret-value")
    monkeypatch.setenv("MEDCHAT_PRIVATE_TOKEN", "token-secret-value")
    _, _, calls, exit_code = _run_valid(tmp_path, monkeypatch)
    assert exit_code == 0
    timeouts = [float(kwargs["wait_timeout"]) for _, kwargs in calls]
    assert all(0 < value <= 270 for value in timeouts)
    assert timeouts == sorted(timeouts, reverse=True)
    for _, kwargs in calls:
        assert kwargs["shell"] is False
        assert kwargs["stdin"] is subprocess.DEVNULL
        assert kwargs["stdout"] is subprocess.DEVNULL
        assert kwargs["stderr"] is subprocess.DEVNULL
        assert kwargs["start_new_session"] is True
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
        lambda data: data["parameters"].__setitem__("size", [80.1, 1.0, 1.0]),  # type: ignore[union-attr]
        lambda data: data["parameters"].__setitem__("center", [0.0, True, 0.0]),  # type: ignore[union-attr]
        lambda data: data["parameters"].__setitem__("exhaustiveness", 65),  # type: ignore[union-attr]
        lambda data: data["parameters"].__setitem__("num_modes", 21),  # type: ignore[union-attr]
        lambda data: data["parameters"].__setitem__("energy_range", 20.1),  # type: ignore[union-attr]
        lambda data: data["parameters"].__setitem__("energy_range", True),  # type: ignore[union-attr]
        lambda data: data.__setitem__("receptor_path", "/workspace/input/../receptor.pdb"),
        lambda data: data.__setitem__("ligand_path", "/workspace/input/ligand.xyz"),
        lambda data: data.__setitem__("receptor_path", "/workspace/input/receptor.pdbqt"),
        lambda data: data.__setitem__("ligand_path", "/workspace/input/ligand.pdb"),
        lambda data: data.__setitem__("ligand_path", "/workspace/input/ligand.pdbqt"),
        lambda data: data.__setitem__("receptor_sha256", "A" * 64),
        lambda data: data.__setitem__("ligand_sha256", "0" * 64),
    ],
    ids=[
        "extra",
        "bool_schema",
        "parameter_extra",
        "bool_integer",
        "oversized_box",
        "bool_center",
        "exhaustiveness_upper_bound",
        "num_modes_upper_bound",
        "energy_upper_bound",
        "bool_energy",
        "traversal",
        "suffix",
        "unsupported_receptor_pdbqt",
        "unsupported_ligand_pdb",
        "unsupported_ligand_pdbqt",
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


@pytest.mark.parametrize(
    ("role", "suffix"),
    [("receptor", ".pdbqt"), ("ligand", ".pdb"), ("ligand", ".pdbqt")],
)
def test_wrapper_rejects_existing_but_meeko_unsupported_input_suffixes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    role: str,
    suffix: str,
) -> None:
    payload = _request()
    payload[f"{role}_path"] = f"/workspace/input/{role}{suffix}"

    def rename_input(input_root: Path) -> None:
        original = input_root / ("receptor.pdb" if role == "receptor" else "ligand.sdf")
        original.rename(input_root / f"{role}{suffix}")

    exit_code, result, _, calls = _run_failure(
        tmp_path,
        monkeypatch,
        payload=payload,
        mutate=rename_input,
    )
    assert exit_code != 0
    assert result["error_code"] == "invalid_input"
    assert calls == []


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
        now[0] = 265.0
        return parsed

    monkeypatch.setattr(module, "_parse_request", slow_parse)
    assert module.main(REQUEST_ARGUMENTS) != 0
    result = _result(output_root)
    assert result["phase"] == "receptor_preparation"
    assert result["error_code"] == "tool_timeout"
    assert calls == []


def test_deadline_layers_reserve_cleanup_and_result_publication() -> None:
    module = _load_runner()
    started = 1000.0
    tool_deadline, cleanup_deadline, wrapper_deadline = module._deadline_layers(started)

    assert started < tool_deadline < cleanup_deadline < wrapper_deadline
    assert module._OUTER_DEADLINE_SECONDS == 270.0
    assert module._WRAPPER_DEADLINE_SECONDS == 267.0
    assert wrapper_deadline == started + 267.0
    assert wrapper_deadline < started + module._OUTER_DEADLINE_SECONDS
    assert cleanup_deadline - tool_deadline >= 0.5
    assert wrapper_deadline - cleanup_deadline >= 0.5


def test_windows_snapshot_identity_ignores_ctime_but_keeps_content_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_runner()

    def metadata(ctime: int) -> SimpleNamespace:
        return SimpleNamespace(
            st_dev=1,
            st_ino=2,
            st_mode=stat.S_IFREG | 0o600,
            st_file_attributes=0,
            st_nlink=1,
            st_size=3,
            st_mtime_ns=10,
            st_ctime_ns=ctime,
        )

    values = iter([metadata(1), metadata(3)])

    class FakePath:
        def lstat(self) -> SimpleNamespace:
            return next(values)

    fstats = iter([metadata(2), metadata(4)])
    reads = iter([b"abc", b""])
    monkeypatch.setattr(module.os, "name", "nt")
    monkeypatch.setattr(module.os, "open", lambda *_args, **_kwargs: 9)
    monkeypatch.setattr(module.os, "fstat", lambda _descriptor: next(fstats))
    monkeypatch.setattr(module.os, "read", lambda _descriptor, _size: next(reads))
    monkeypatch.setattr(module.os, "close", lambda _descriptor: None)

    content, digest = module._read_regular_snapshot(FakePath(), 10)

    assert content == b"abc"
    assert digest == _sha(b"abc")


@pytest.mark.parametrize(
    ("suffix", "ligand"),
    [
        (".sdf", b"$$$$\n"),
        (".sdf", b"not a molecule\n$$$$\n"),
        (".sdf", VALID_SDF + VALID_SDF),
        (".mol", b"not a molecule\n"),
    ],
    ids=["zero-record-sdf", "invalid-sdf", "multi-record-sdf", "invalid-mol"],
)
def test_ligand_must_be_exactly_one_valid_rdkit_molecule_before_meeko(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    suffix: str,
    ligand: bytes,
) -> None:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    payload = _request(ligand=ligand, ligand_suffix=suffix)
    _write_inputs(
        input_root,
        payload,
        ligand=ligand,
        ligand_suffix=suffix,
    )
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)

    assert module.main(REQUEST_ARGUMENTS) != 0
    assert _result(output_root) == {
        "schema_version": 1,
        "status": "failed",
        "phase": "input_validation",
        "error_code": "invalid_input",
        "warnings": [],
    }
    assert calls == []


def test_valid_single_record_mol_reaches_geometry_preparation_then_meeko(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    payload = _request(ligand=VALID_MOL, ligand_suffix=".mol")
    _write_inputs(
        input_root,
        payload,
        ligand=VALID_MOL,
        ligand_suffix=".mol",
    )
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)

    assert module.main(REQUEST_ARGUMENTS) == 0
    assert _result(output_root)["status"] == "succeeded"
    assert calls[1][0][0] == "obabel"
    assert calls[1][0][1] == str(input_root / "ligand.mol")
    assert calls[2][0][0] == "mk_prepare_ligand.py"
    assert calls[2][0][2] == str(output_root / ".work" / "ligand.prepared.sdf")


def test_cli_subreaper_failure_is_stable_and_prevents_tool_spawn(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(input_root, _request())
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)
    monkeypatch.setattr(
        module,
        "_enable_child_subreaper",
        lambda: module._fail("environment_setup", "environment_unavailable"),
    )
    monkeypatch.setattr(module.sys, "argv", [str(RUNNER), *REQUEST_ARGUMENTS])

    assert module.main() != 0
    assert calls == []
    assert _result(output_root) == {
        "schema_version": 1,
        "status": "failed",
        "phase": "environment_setup",
        "error_code": "environment_unavailable",
        "warnings": [],
    }


@pytest.mark.parametrize("return_code", [0, -1])
def test_subreaper_prctl_uses_exact_operation_and_has_stable_failure(
    monkeypatch: pytest.MonkeyPatch, return_code: int
) -> None:
    module = _load_runner()
    calls: list[tuple[int, int, int, int, int]] = []

    class FakePrctl:
        argtypes: object = None
        restype: object = None

        def __call__(self, *arguments: int) -> int:
            calls.append(arguments)  # type: ignore[arg-type]
            return return_code

    library = type("Library", (), {"prctl": FakePrctl()})()
    monkeypatch.setattr(module.sys, "platform", "linux")
    monkeypatch.setattr(module.ctypes, "CDLL", lambda *args, **kwargs: library)
    if return_code == 0:
        module._enable_child_subreaper()
        assert module._SUBREAPER_ENABLED is True
    else:
        with pytest.raises(module.DockingFailure) as raised:
            module._enable_child_subreaper()
        assert raised.value.phase == "environment_setup"
        assert raised.value.error_code == "environment_unavailable"
        assert module._SUBREAPER_ENABLED is False
    assert calls == [(36, 1, 0, 0, 0)]


def test_timeout_cleanup_uses_absolute_budget_and_kill_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_runner()
    events: list[tuple[str, int]] = []

    class FakeProcess:
        pid = 41000
        returncode: int | None = None
        waits = 0

        def wait(self, timeout: float | None = None) -> int:
            del timeout
            self.waits += 1
            if self.waits == 1:
                raise subprocess.TimeoutExpired(["tool"], 0.1)
            self.returncode = -int(signal.SIGKILL)
            events.append(("direct_reaped", self.pid))
            return self.returncode

        def poll(self) -> int | None:
            return self.returncode

    process = FakeProcess()

    def fake_killpg(process_group: int, value: int) -> None:
        events.append(("signal", value))

    monkeypatch.setattr(module.os, "killpg", fake_killpg, raising=False)
    monkeypatch.setattr(module.signal, "SIGKILL", 9, raising=False)
    monkeypatch.setattr(module, "_PROCESS_STOP_GRACE_SECONDS", 0.001)
    monkeypatch.setattr(
        module,
        "_manage_adopted_descendants",
        lambda _baseline, _references, value: (
            events.append(("descendant_signal", int(value))) or True,
            True,
        ),
    )
    cleanup_deadline = module.time.monotonic() + 1.0

    assert module._terminate_and_reap_invocation(
        process, frozenset(), cleanup_deadline
    ) is True
    assert events[0] == ("signal", int(signal.SIGTERM))
    kill_group = events.index(("signal", 9))
    direct_reap = events.index(("direct_reaped", process.pid))
    kill_descendant = events.index(("descendant_signal", 9))
    assert 0 < kill_group < direct_reap < kill_descendant
    assert all(
        event == ("descendant_signal", int(signal.SIGTERM))
        for event in events[1:kill_group]
    )


def test_explicit_unit_main_does_not_enable_subreaper_in_pytest_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_runner()
    input_root, _ = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(input_root, _request())
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)
    monkeypatch.setattr(
        module,
        "_enable_child_subreaper",
        lambda: (_ for _ in ()).throw(AssertionError("pytest became subreaper")),
    )
    assert module.main(REQUEST_ARGUMENTS) == 0


def _linux_process_tree_probe(tmp_path: Path, mode: str) -> list[int]:
    pid_file = tmp_path / f"{mode}-process-tree-pids.json"
    output_root = tmp_path / f"{mode}-output"
    output_root.mkdir(mode=0o700)
    child_tail = "time.sleep(30)" if mode == "timeout" else "sys.exit(0)"
    child_code = (
        "import json,os,subprocess,sys,time;"
        "g=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],"
        "preexec_fn=os.setsid);"
        "open(sys.argv[1],'w').write(json.dumps([os.getpid(),g.pid]));"
        + child_tail
    )
    helper_code = """
import importlib.util
import json
import os
import signal
import sys
import time
from pathlib import Path

spec = importlib.util.spec_from_file_location("runner_probe", sys.argv[1])
m = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = m
spec.loader.exec_module(m)
m.OUTPUT_ROOT = Path(sys.argv[2])
m._enable_child_subreaper()
m._INVOCATION_BASELINE_CHILDREN = m._linux_children()
expected = "tool_timeout" if sys.argv[3] == "timeout" else "tool_failed"
deadline = time.monotonic() + (0.5 if sys.argv[3] == "timeout" else 3.0)
cleanup_deadline = deadline + 0.75
accepted = False
try:
    m._run_tool(
        [sys.executable, "-c", sys.argv[4], sys.argv[5]],
        "docking",
        deadline,
        cleanup_deadline,
    )
except m.DockingFailure as failure:
    accepted = failure.error_code == expected
pids = json.loads(Path(sys.argv[5]).read_text(encoding="utf-8"))
remaining = [pid for pid in pids if Path(f"/proc/{pid}").exists()]
if remaining:
    for pid in remaining:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    stop = time.monotonic() + 1.0
    while time.monotonic() < stop:
        try:
            child, _ = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        if child == 0:
            time.sleep(0.01)
    remaining = [pid for pid in pids if Path(f"/proc/{pid}").exists()]
if not accepted:
    raise SystemExit(20)
if remaining:
    raise SystemExit(21)
"""
    compile(helper_code, "<linux-process-tree-probe>", "exec")
    helper = subprocess.Popen(
        [
            sys.executable,
            "-c",
            helper_code,
            str(RUNNER),
            str(output_root),
            mode,
            child_code,
            str(pid_file),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    pids: list[int] = []
    try:
        assert helper.wait(timeout=8) == 0
        pids = json.loads(pid_file.read_text(encoding="utf-8"))
        assert all(not Path(f"/proc/{pid}").exists() for pid in pids)
        return pids
    finally:
        if helper.poll() is None:
            os.killpg(helper.pid, signal.SIGKILL)
            helper.wait(timeout=1)
        if pid_file.exists():
            pids = json.loads(pid_file.read_text(encoding="utf-8"))
        for pid in pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux subreaper contract"
)
def test_timeout_fully_reaps_real_child_and_grandchild(tmp_path: Path) -> None:
    _linux_process_tree_probe(tmp_path, "timeout")


@pytest.mark.skipif(
    not sys.platform.startswith("linux"), reason="Linux subreaper contract"
)
def test_success_with_background_descendant_is_cleaned_and_fails(
    tmp_path: Path,
) -> None:
    _linux_process_tree_probe(tmp_path, "background")


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


def test_unsorted_pose_energies_are_rejected_without_scientific_values(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pose = (
        b"REMARK VINA RESULT: -6.0 0.0 0.0\n"
        b"REMARK VINA RESULT: -7.0 0.0 0.0\n"
    )
    exit_code, result, output_root, _ = _run_failure(
        tmp_path, monkeypatch, pose=pose
    )
    assert exit_code != 0
    assert result["phase"] == "output_validation"
    assert result["error_code"] == "invalid_output"
    assert "best_energy" not in result and "pose_count" not in result
    assert not (output_root / "poses").exists()


@pytest.mark.parametrize(
    "tool", ["mk_prepare_receptor.py", "mk_prepare_ligand.py", "vina"]
)
def test_unexpected_nested_tool_artifacts_fail_and_are_fully_removed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, tool: str
) -> None:
    exit_code, result, output_root, _ = _run_failure(
        tmp_path,
        monkeypatch,
        unexpected_nested_tool=tool,
    )
    assert exit_code != 0
    assert result["status"] == "failed"
    assert result["phase"] == "output_validation"
    assert result["error_code"] == "invalid_output"
    assert "best_energy" not in result and "pose_count" not in result
    assert not (output_root / ".work").exists()
    assert not (output_root / "poses").exists()


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="POSIX dir-fd race")
def test_cleanup_quarantine_preserves_swapped_replacement(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_runner()
    _, output_root = _configure_roots(module, monkeypatch, tmp_path)
    work = output_root / ".work"
    work.mkdir(mode=0o700)
    (work / "owned.txt").write_text("owned", encoding="ascii")
    identity = module._check_private_directory(work)
    replacement = output_root / "replacement"
    replacement.mkdir(mode=0o700)
    (replacement / "sentinel.txt").write_text("preserve", encoding="ascii")
    saved = output_root / "saved-original"
    original_rename = module.os.rename
    injected = False

    def racing_rename(source: object, destination: object, **kwargs: object) -> None:
        nonlocal injected
        if not injected and source == ".work":
            injected = True
            original_rename(work, saved)
            original_rename(replacement, work)
        original_rename(source, destination, **kwargs)

    monkeypatch.setattr(module.os, "rename", racing_rename)

    assert module._clean_created_directory(work, identity) is False
    assert injected is True
    assert (work / "sentinel.txt").read_text(encoding="ascii") == "preserve"
    assert (saved / "owned.txt").read_text(encoding="ascii") == "owned"


def test_cleanup_failure_is_stable_non_scientific_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(input_root, _request())
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)
    original_cleanup = module._clean_created_directory

    def fail_work_cleanup(
        path: Path, identity: object, deadline: float | None = None
    ) -> bool:
        if path.name == ".work":
            return False
        return bool(original_cleanup(path, identity, deadline))

    monkeypatch.setattr(module, "_clean_created_directory", fail_work_cleanup)
    assert module.main(REQUEST_ARGUMENTS) != 0
    result = _result(output_root)
    assert result == {
        "schema_version": 1,
        "status": "failed",
        "phase": "cleanup",
        "error_code": "cleanup_failed",
        "warnings": [],
    }


def test_result_publish_is_no_replace_atomic_mode_0600_and_preserves_unrelated_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    unrelated = output_root / "keep.txt"
    unrelated.write_text("owner-data", encoding="utf-8")
    _write_inputs(input_root, _request())
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)
    links: list[tuple[object, object, dict[str, object]]] = []
    original_link = module.os.link

    def recording_link(
        source: object, destination: object, **kwargs: object
    ) -> None:
        source_path = (
            output_root / str(source)
            if kwargs.get("src_dir_fd") is not None
            else Path(source)
        )
        if os.name == "posix":
            assert stat.S_IMODE(source_path.stat().st_mode) == 0o600
        links.append((source, destination, dict(kwargs)))
        original_link(source, destination, **kwargs)

    monkeypatch.setattr(module.os, "link", recording_link)
    assert module.main(REQUEST_ARGUMENTS) == 0
    assert links
    assert str(links[-1][1]).endswith("result.json")
    assert unrelated.read_text(encoding="utf-8") == "owner-data"
    assert not list(output_root.glob(".result.*.tmp"))
    if os.name == "posix":
        assert stat.S_IMODE((output_root / "result.json").stat().st_mode) == 0o600


def test_result_publish_race_preserves_competing_sentinel(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_runner()
    input_root, output_root = _configure_roots(module, monkeypatch, tmp_path)
    _write_inputs(input_root, _request())
    calls: list[tuple[list[str], dict[str, object]]] = []
    _fake_tools(monkeypatch, module, calls)
    sentinel = b"competing-owner-result"

    def competing_link(
        source: object, destination: object, **kwargs: object
    ) -> None:
        del source
        final = (
            output_root / str(destination)
            if kwargs.get("dst_dir_fd") is not None
            else Path(destination)
        )
        final.write_bytes(sentinel)
        raise FileExistsError

    monkeypatch.setattr(module.os, "link", competing_link)
    assert module.main(REQUEST_ARGUMENTS) != 0
    assert (output_root / "result.json").read_bytes() == sentinel
    assert not list(output_root.glob(".result.*.tmp"))


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
