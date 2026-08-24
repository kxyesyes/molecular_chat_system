from __future__ import annotations

import errno
import hashlib
import importlib.util
import json
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

import pytest


ROOT = Path(__file__).resolve().parents[1]
DEPLOY = ROOT / "deployment" / "temporal"
COMPOSE = DEPLOY / "docker-compose.yml"
ENV_EXAMPLE = DEPLOY / "env.example"
ROLE_SCRIPT = DEPLOY / "postgres-init" / "010-exporter.sh"
SCHEMA_SCRIPT = DEPLOY / "scripts" / "temporal-schema-setup.sh"
SERVER_SCRIPT = DEPLOY / "scripts" / "temporal-server-entrypoint.sh"
NAMESPACE_SCRIPT = DEPLOY / "scripts" / "temporal-namespace-setup.sh"
ROTATE_SCRIPT = DEPLOY / "scripts" / "rotate-exporter-password.sh"
PLAN = ROOT / "docs/superpowers/plans/2026-08-21-temporal-production-canary-readiness.md"
DESIGN = ROOT / "docs/superpowers/specs/2026-08-21-temporal-production-canary-readiness-design.md"
INTEGRATION_TEST = ROOT / "tests/test_temporal_deployment_integration.py"
PROMETHEUS_CONFIG = DEPLOY / "prometheus" / "prometheus.yml"
PROMETHEUS_RULES = (
    DEPLOY / "prometheus" / "rules" / "medchat-temporal.yml"
)
PROMETHEUS_RULE_TEST = (
    DEPLOY / "prometheus" / "tests" / "medchat-temporal.test.yml"
)
GRAFANA_DATASOURCE = (
    DEPLOY / "grafana" / "provisioning" / "datasources" / "prometheus.yml"
)
GRAFANA_PROVIDER = (
    DEPLOY / "grafana" / "provisioning" / "dashboards" / "dashboard.yml"
)
GRAFANA_DASHBOARD = (
    DEPLOY / "grafana" / "dashboards" / "medchat-temporal-docking.json"
)
RELAY_TEMPLATE = ROOT / "deployment" / "nginx-medchat-temporal-metrics.conf.template"
RELAY_SCRIPT = ROOT / "scripts" / "configure_temporal_metrics_relay.py"
WEB_SYSTEMD_UNIT = ROOT / "deployment" / "medchat.service"
WORKER_SYSTEMD_UNIT = ROOT / "deployment" / "medchat-temporal-worker.service"
PREPARE_SYSTEMD_UNIT = (
    ROOT / "deployment" / "medchat-temporal-worker-prepare.service"
)
WORKER_TMPFILES = (
    ROOT / "deployment" / "tmpfiles" / "medchat-temporal-worker.conf"
)
OLD_WORKER_TMPFILES = ROOT / "deployment" / "medchat-temporal-worker.tmpfiles"
WORKER_ENV_EXAMPLE = ROOT / "deployment" / "temporal-worker.env.example"
WORKER_INSTALLER = ROOT / "deployment" / "install-temporal-worker.sh"
BUNDLE_INSTALLER = (
    ROOT / "deployment" / "libexec" / "install-temporal-worker-bundle.py"
)
WORKER_ACTIVATOR = ROOT / "deployment" / "activate-temporal-worker-generation.sh"
WORKER_HELPER = (
    ROOT / "deployment" / "libexec" / "validate-temporal-worker-env.py"
)
WORKER_DIRECTORY_HELPER = (
    ROOT / "deployment" / "libexec" / "prepare-temporal-worker-directories.py"
)

LONG_RUNNING = {
    "postgres", "postgres-exporter", "temporal", "temporal-ui", "prometheus", "grafana"
}
JOBS = {"temporal-schema", "temporal-namespace", "temporal-exporter-role-sync"}
IMAGES = {
    "postgres": "postgres:16.14-alpine3.23",
    "temporal-schema": "temporalio/admin-tools:1.29.7",
    "temporal": "temporalio/server:1.29.7",
    "temporal-namespace": "temporalio/admin-tools:1.29.7",
    "temporal-exporter-role-sync": "postgres:16.14-alpine3.23",
    "postgres-exporter": "ghcr.io/prometheus-community/postgres-exporter:v0.20.1",
    "temporal-ui": "temporalio/ui:2.53.1",
    "prometheus": "prom/prometheus:v3.13.2",
    "grafana": "grafana/grafana:12.4.8-ubuntu",
}
PORTS = {
    "postgres": ["127.0.0.1:${TEMPORAL_POSTGRES_HOST_PORT:-5433}:5432"],
    "temporal-schema": [],
    "temporal": ["127.0.0.1:${TEMPORAL_GRPC_HOST_PORT:-7233}:7233"],
    "temporal-namespace": [],
    "temporal-exporter-role-sync": [],
    "postgres-exporter": [],
    "temporal-ui": ["127.0.0.1:${TEMPORAL_UI_HOST_PORT:-8233}:8080"],
    "prometheus": ["127.0.0.1:${PROMETHEUS_HOST_PORT:-9090}:9090"],
    "grafana": ["127.0.0.1:${GRAFANA_HOST_PORT:-3000}:3000"],
}
NETWORKS = {
    "postgres": {"database"},
    "temporal-schema": {"database"},
    "temporal": {"database", "temporal", "temporal-metrics"},
    "temporal-namespace": {"temporal"},
    "temporal-exporter-role-sync": {"database"},
    "postgres-exporter": {"database", "postgres-metrics"},
    "temporal-ui": {"temporal"},
    "prometheus": {"temporal-metrics", "postgres-metrics", "prometheus-grafana"},
    "grafana": {"prometheus-grafana"},
}
MOUNTS = {
    "postgres": ["temporal-postgres-data:/var/lib/postgresql/data"],
    "temporal-schema": [
        "./scripts/temporal-schema-setup.sh:/opt/medchat/temporal-schema-setup.sh:ro"
    ],
    "temporal": [
        "./scripts/temporal-server-entrypoint.sh:/opt/medchat/temporal-server-entrypoint.sh:ro"
    ],
    "temporal-namespace": [
        "./scripts/temporal-namespace-setup.sh:/opt/medchat/temporal-namespace-setup.sh:ro"
    ],
    "temporal-exporter-role-sync": [
        "./postgres-init/010-exporter.sh:/opt/medchat/010-exporter.sh:ro"
    ],
    "postgres-exporter": [],
    "temporal-ui": [],
    "prometheus": [
        "prometheus-data:/prometheus",
        "./prometheus:/etc/prometheus:ro",
    ],
    "grafana": [
        "grafana-data:/var/lib/grafana",
        "./grafana/provisioning:/etc/grafana/provisioning:ro",
        "./grafana/dashboards:/var/lib/grafana/dashboards:ro",
    ],
}


def parse_systemd_directives(path: Path) -> dict[str, dict[str, list[str]]]:
    parsed: dict[str, dict[str, list[str]]] = {}
    section: str | None = None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith(("#", ";")):
            continue
        if line.startswith("[") and line.endswith("]"):
            section = line[1:-1]
            parsed.setdefault(section, {})
            continue
        assert section is not None and "=" in line
        name, value = line.split("=", 1)
        parsed[section].setdefault(name, []).append(value)
    return parsed


def test_temporal_worker_systemd_unit_has_full_hardened_contract() -> None:
    assert parse_systemd_directives(WORKER_SYSTEMD_UNIT) == {
        "Unit": {
            "Description": ["MedChat Temporal Docking Worker"],
            "After": [
                "network-online.target docker.service "
                "medchat-temporal-worker-prepare.service"
            ],
            "Wants": ["network-online.target"],
            "Requires": ["medchat-temporal-worker-prepare.service"],
        },
        "Service": {
            "Type": ["simple"],
            "User": ["medchat"],
            "Group": ["medchat"],
            "WorkingDirectory": ["/opt/medchat/molecular_chat_system"],
            "EnvironmentFile": ["/etc/medchat/temporal-worker.env"],
            "Environment": [
                "PATH=/opt/conda/envs/medchat/bin:/usr/local/sbin:"
                "/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
                "PYTHONDONTWRITEBYTECODE=1",
            ],
            "ExecStartPre": [
                "/opt/conda/envs/medchat/bin/python "
                "scripts/validate_temporal_worker_production.py --check-config",
            ],
            "ExecStart": [
                "/opt/conda/envs/medchat/bin/python "
                "scripts/run_temporal_docking_worker.py"
            ],
            "Restart": ["on-failure"],
            "RestartSec": ["5"],
            "TimeoutStartSec": ["60"],
            "TimeoutStopSec": ["90"],
            "KillMode": ["mixed"],
            "KillSignal": ["SIGTERM"],
            "SendSIGKILL": ["yes"],
            "UMask": ["0077"],
            "PrivateTmp": ["true"],
            "NoNewPrivileges": ["true"],
            "ProtectSystem": ["strict"],
            "ReadWritePaths": [
                "/opt/medchat/molecular_chat_system/scratch "
                "/opt/medchat/molecular_chat_system/temp_docking"
            ],
            "ReadOnlyPaths": [
                "/opt/medchat/molecular_chat_system "
                "/opt/conda/envs/medchat"
            ],
        },
        "Install": {"WantedBy": ["multi-user.target"]},
    }

    worker = parse_systemd_directives(WORKER_SYSTEMD_UNIT)
    assert all(
        not value.startswith(("+", "!"))
        for key, values in worker["Service"].items()
        if key.startswith("Exec")
        for value in values
    )


def test_temporal_worker_prepare_unit_is_root_only_and_environment_free() -> None:
    prepare = parse_systemd_directives(PREPARE_SYSTEMD_UNIT)
    assert prepare == {
        "Unit": {
            "Description": [
                "Prepare MedChat Temporal Docking Worker Trust Boundary"
            ],
            "Before": ["medchat-temporal-worker.service"],
            "PartOf": ["medchat-temporal-worker.service"],
        },
        "Service": {
            "Type": ["oneshot"],
            "ExecStart": [
                "/usr/lib/medchat/temporal-worker/current/libexec/"
                "validate-temporal-worker-env",
                "/usr/lib/medchat/temporal-worker/current/libexec/"
                "prepare-temporal-worker-directories",
            ],
            "RemainAfterExit": ["yes"],
        },
    }
    assert not ({"User", "Environment", "EnvironmentFile"} & prepare["Service"].keys())
    combined = PREPARE_SYSTEMD_UNIT.read_text(encoding="utf-8").lower()
    for forbidden in (
        "/opt/medchat",
        "/opt/conda",
        "docker",
        "compose",
        "temporal address",
        "vina",
        "taskstore",
    ):
        assert forbidden not in combined


def test_web_systemd_unit_preserves_behavior_with_narrow_environment_source() -> None:
    assert parse_systemd_directives(WEB_SYSTEMD_UNIT) == {
        "Unit": {
            "Description": ["MedChat Web Service"],
            "After": ["network.target"],
        },
        "Service": {
            "Type": ["simple"],
            "User": ["medchat"],
            "Group": ["medchat"],
            "WorkingDirectory": ["/opt/medchat/molecular_chat_system"],
            "EnvironmentFile": ["/etc/medchat/medchat.env"],
            "ExecStart": [
                "/opt/conda/envs/medchat/bin/python main.py --no-reload"
            ],
            "Restart": ["always"],
            "RestartSec": ["3"],
            "TimeoutStartSec": ["120"],
            "TimeoutStopSec": ["30"],
        },
        "Install": {"WantedBy": ["multi-user.target"]},
    }


def test_web_and_temporal_worker_systemd_units_are_independent_processes() -> None:
    web = parse_systemd_directives(WEB_SYSTEMD_UNIT)
    worker = parse_systemd_directives(WORKER_SYSTEMD_UNIT)
    assert "Requires" not in web["Unit"]
    assert "PartOf" not in web["Unit"]
    assert "medchat.service" not in " ".join(
        value
        for values in worker["Unit"].values()
        for value in values
    )
    assert web["Service"]["ExecStart"] != worker["Service"]["ExecStart"]
    worker_commands = [
        *worker["Service"]["ExecStartPre"],
        *worker["Service"]["ExecStart"],
    ]
    assert all("docker " not in command.lower() for command in worker_commands)
    assert all("compose" not in command.lower() for command in worker_commands)


def test_systemd_units_have_no_shell_interpolation_or_embedded_credentials() -> None:
    units = (WEB_SYSTEMD_UNIT, WORKER_SYSTEMD_UNIT, PREPARE_SYSTEMD_UNIT)
    embedded_credential = re.compile(
        r"(?i)(?:https?|postgres(?:ql)?|mysql|redis)://[^/\s:@]+:[^/\s@]+@"
    )
    secret_assignment = re.compile(
        r"(?i)(?:api[_-]?key|password|secret|token)\s*=\s*[^\s\"']+"
    )
    shell_interpolation = re.compile(
        r"(?:\$\(|\$\{|`|(?:^|\s)(?:/bin/)?(?:ba)?sh\s+-c(?:\s|$))"
    )
    for path in units:
        text = path.read_text(encoding="utf-8")
        parsed = parse_systemd_directives(path)
        executable_values = [
            value
            for key, values in parsed["Service"].items()
            if key.startswith("Exec")
            for value in values
        ]
        assert not embedded_credential.search(text), path.name
        assert not secret_assignment.search(text), path.name
        assert all(not shell_interpolation.search(value) for value in executable_values)
        environment = parsed["Service"].get("Environment", [])
        assert all("KEY=" not in value and "TOKEN=" not in value for value in environment)


def test_temporal_worker_tmpfiles_is_removed_in_favor_of_directory_helper() -> None:
    assert not OLD_WORKER_TMPFILES.exists()
    assert not WORKER_TMPFILES.exists()
    assert WORKER_DIRECTORY_HELPER.is_file()


def test_temporal_worker_environment_example_is_exact_non_secret_allowlist() -> None:
    assignments = {
        line.split("=", 1)[0]: line.split("=", 1)[1]
        for line in WORKER_ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
        if line and not line.startswith(("#", ";"))
    }
    assert set(assignments) == {
        "MEDCHAT_TASK_BACKEND",
        "MEDCHAT_TEMPORAL_CANARY_PERCENT",
        "MEDCHAT_TEMPORAL_ADDRESS",
        "MEDCHAT_TEMPORAL_NAMESPACE",
        "MEDCHAT_TEMPORAL_DOCKING_QUEUE",
        "MEDCHAT_TEMPORAL_DOCKING_CONCURRENCY",
        "MEDCHAT_TASK_STAGING_ROOT",
        "MEDCHAT_TASK_DB_PATH",
        "MEDCHAT_TEMPORAL_WORKER_METRICS_ADDRESS",
        "MEDCHAT_TEMPORAL_WORKER_METRICS_PORT",
        "MEDCHAT_TEMPORAL_BASELINE_P95_SECONDS",
        "MEDCHAT_TEMPORAL_BACKUP_STATE",
        "MOLECULAR_DOCKING_ROOT",
        "MOLECULAR_DOCKING_VINA",
        "MOLECULAR_DOCKING_ADFR_BIN",
        "MOLECULAR_DOCKING_PREPARE_RECEPTOR",
        "MOLECULAR_DOCKING_PREPARE_LIGAND",
        "MOLECULAR_DOCKING_VINA_TIMEOUT_SECONDS",
    }
    assert all(value and not re.search(r"(?i)(secret|password|token|api.?key)", value) for value in assignments.values())
    assert not ({"PATH", "PYTHONPATH", "PYTHONHOME", "LD_PRELOAD"} & assignments.keys())
    assert all("RELAY" not in key and "PROMETHEUS_SCRAPE" not in key for key in assignments)


def _native_posix_repository(*, require_root: bool = False) -> str:
    if sys.platform != "linux":
        pytest.skip(
            "native Linux POSIX execution is required; Windows does not dispatch WSL"
        )
    if shutil.which("sh") is None:
        pytest.skip("native POSIX shell is unavailable")
    if require_root and os.geteuid() != 0:
        pytest.skip("native Linux root is required")
    return ROOT.resolve().as_posix()


def _run_native_posix(
    command: str,
    *,
    timeout: float,
) -> subprocess.CompletedProcess[str]:
    _native_posix_repository()
    return subprocess.run(
        ["sh", "-lc", command],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )


def test_posix_deployment_tests_never_dispatch_wsl_on_windows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[list[str]] = []

    def forbidden_run(command, *args, **kwargs):
        del args, kwargs
        calls.append(command)
        raise AssertionError("a POSIX subprocess was started")

    monkeypatch.setattr(subprocess, "run", forbidden_run)
    if os.name == "nt":
        with pytest.raises(pytest.skip.Exception):
            _native_posix_repository()
        assert calls == []
    else:
        pytest.skip("Windows dispatch guard is exercised only on Windows")


def _load_bundle_installer():
    spec = importlib.util.spec_from_file_location(
        "temporal_bundle_installer_test",
        BUNDLE_INSTALLER,
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_temporal_bundle_v1_digest_and_manifest_are_exact() -> None:
    installer = _load_bundle_installer()
    assets = {
        "units/medchat-temporal-worker.service": (0o644, b"worker\n"),
        "units/medchat-temporal-worker-prepare.service": (0o644, b"prepare\n"),
        "libexec/validate-temporal-worker-env": (0o755, b"validate\n"),
        "libexec/prepare-temporal-worker-directories": (0o755, b"prepare-dirs\n"),
    }
    framed = bytearray(b"MEDCHAT_TEMPORAL_BUNDLE_V1\0")
    for path in sorted(assets, key=lambda value: value.encode("ascii")):
        mode, payload = assets[path]
        encoded = path.encode("ascii")
        framed.extend(struct.pack(">I", len(encoded)))
        framed.extend(encoded)
        framed.extend(struct.pack(">I", mode))
        framed.extend(struct.pack(">Q", len(payload)))
        framed.extend(payload)
    expected = hashlib.sha256(framed).hexdigest()
    assert installer.compute_bundle_digest(assets) == expected
    manifest = installer.build_manifest(assets, expected)
    assert manifest.startswith(f"MEDCHAT_TEMPORAL_BUNDLE_V1 {expected}\n".encode())
    assert manifest.endswith(b"\n") and not manifest.endswith(b"\n\n")
    parsed = installer.parse_manifest(manifest)
    assert parsed.bundle_digest == expected
    assert parsed.assets == {
        path: (mode, len(payload), hashlib.sha256(payload).hexdigest())
        for path, (mode, payload) in assets.items()
    }


def _make_manifest_size_noncanonical(payload: bytes) -> bytes:
    lines = payload.splitlines(keepends=True)
    fields = lines[1].split(b" ", 3)
    fields[2] = b"0" + fields[2]
    lines[1] = b" ".join(fields)
    return b"".join(lines)


@pytest.mark.parametrize(
    "mutation",
    [
        lambda payload: payload.replace(b"\n", b"\r\n", 1),
        lambda payload: payload + b"\n",
        lambda payload: payload.replace(b"0644", b"644", 1),
        _make_manifest_size_noncanonical,
        lambda payload: payload.replace(
            b"units/medchat-temporal-worker.service",
            b"../medchat-temporal-worker.service",
            1,
        ),
    ],
    ids=["cr", "blank", "mode", "size", "dot-component"],
)
def test_temporal_bundle_manifest_parser_rejects_noncanonical_content(mutation) -> None:
    installer = _load_bundle_installer()
    assets = {
        path: (mode, path.encode("ascii"))
        for path, mode, _source in installer.ASSET_SPECS
    }
    digest = installer.compute_bundle_digest(assets)
    with pytest.raises(installer.BundleInstallError):
        installer.parse_manifest(mutation(installer.build_manifest(assets, digest)))


def test_temporal_worker_shell_execs_the_bundle_writer() -> None:
    source = WORKER_INSTALLER.read_text(encoding="utf-8")
    assert "exec /usr/bin/python3" in source
    assert "install-temporal-worker-bundle.py" in source
    assert "from __future__ import annotations" not in source
    assert "systemctl" not in source
    assert "current" not in source


def test_temporal_worker_activation_wrapper_is_fixed_and_execs_writer() -> None:
    source = WORKER_ACTIVATOR.read_text(encoding="utf-8")
    assert source.startswith("#!/bin/sh\nset -eu\n")
    assert "PATH=/usr/sbin:/usr/bin:/sbin:/bin\nexport PATH\n" in source
    assert "exec /usr/bin/python3" in source
    assert "install-temporal-worker-bundle.py" in source
    assert "--activate" in source
    assert "--migrate-legacy" in source
    assert "--destdir" not in source
    assert "eval" not in source


def test_temporal_worker_activation_operator_has_transactional_contract() -> None:
    source = BUNDLE_INSTALLER.read_text(encoding="utf-8")
    for required in (
        "activate_generation(",
        "migrate_legacy_generation(",
        "BOOTSTRAP_LINKS",
        'f"releases/{digest}"',
        "start_new_session=True",
        "os.killpg",
        "daemon-reload",
        "FragmentPath",
        "PHASE_SWITCHED",
        "PHASE_WORKER_STARTING",
    ):
        assert required in source


def test_temporal_worker_bootstrap_symlink_targets_are_exact() -> None:
    installer = _load_bundle_installer()
    assert installer.BOOTSTRAP_LINKS == {
        "etc/systemd/system/medchat-temporal-worker.service": (
            "/usr/lib/medchat/temporal-worker/current/units/"
            "medchat-temporal-worker.service"
        ),
        "etc/systemd/system/medchat-temporal-worker-prepare.service": (
            "/usr/lib/medchat/temporal-worker/current/units/"
            "medchat-temporal-worker-prepare.service"
        ),
    }


def test_temporal_worker_legacy_allowlist_matches_flat_predecessor() -> None:
    installer = _load_bundle_installer()
    predecessor = {
        "etc/systemd/system/medchat-temporal-worker.service": (
            "deployment/medchat-temporal-worker.service",
            0o644,
        ),
        "etc/systemd/system/medchat-temporal-worker-prepare.service": (
            "deployment/medchat-temporal-worker-prepare.service",
            0o644,
        ),
        "usr/libexec/medchat/validate-temporal-worker-env": (
            "deployment/libexec/validate-temporal-worker-env.py",
            0o755,
        ),
        "usr/lib/tmpfiles.d/medchat-temporal-worker.conf": (
            "deployment/tmpfiles/medchat-temporal-worker.conf",
            0o644,
        ),
    }
    expected = {}
    for installed, (repository_path, mode) in predecessor.items():
        payload = subprocess.run(
            ["git", "show", f"0c54b2f:{repository_path}"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout
        expected[installed] = (mode, hashlib.sha256(payload).hexdigest())
    assert installer.LEGACY_ASSETS == expected
    assert installer.LEGACY_TMPFILES_RELATIVE == (
        "usr/lib/tmpfiles.d/medchat-temporal-worker.conf"
    )
    assert installer.LEGACY_QUARANTINE_RELATIVE == (
        "usr/lib/medchat/temporal-worker/quarantine/legacy-tmpfiles.disabled"
    )


def test_temporal_bundle_installer_stages_immutable_generation() -> None:
    repository = _native_posix_repository()
    command = f"""
set -eu
cd '{repository}'
destination=$(mktemp -d)
cleanup() {{
    chmod -R u+w "$destination" 2>/dev/null || true
    rm -rf -- "$destination"
}}
trap cleanup EXIT HUP INT TERM
mkdir "$destination/run"
sh deployment/install-temporal-worker.sh --destdir "$destination" > "$destination/result"
digest=$(sed -n 's/^temporal_worker_bundle_installation=passed digest=//p' "$destination/result")
test "$(printf %s "$digest" | wc -c)" -eq 64
sh deployment/install-temporal-worker.sh --destdir "$destination" > "$destination/result-again"
test "$(cat "$destination/result-again")" = "temporal_worker_bundle_installation=passed digest=$digest"
generation="$destination/usr/lib/medchat/temporal-worker/releases/$digest"
test -d "$generation"
test ! -e "$destination/usr/lib/medchat/temporal-worker/current"
test -f "$generation/manifest.sha256"
test -f "$generation/units/medchat-temporal-worker.service"
test -f "$generation/units/medchat-temporal-worker-prepare.service"
test -x "$generation/libexec/validate-temporal-worker-env"
test -x "$generation/libexec/prepare-temporal-worker-directories"
test "$(stat -c %a "$destination/run/medchat-temporal-worker")" = 700
test "$(stat -c %a "$destination/run/medchat-temporal-worker/install.lock")" = 600
test "$(find "$destination/usr/lib/medchat/temporal-worker/releases" -mindepth 1 -maxdepth 1 -type d | wc -l)" -eq 1
test -z "$(find "$destination" -name '.staging-*' -o -name '.medchat-install-*')"
"""
    result = _run_native_posix(command, timeout=90)
    assert result.returncode == 0, result.stderr


def test_temporal_bundle_native_trust_and_generation_contracts() -> None:
    repository = _native_posix_repository()
    command = r'''
set -eu
cd '__REPOSITORY__'
test_root=$(mktemp -d)
cleanup() {
    chmod -R u+w "$test_root" 2>/dev/null || true
    rm -rf -- "$test_root"
}
trap cleanup EXIT HUP INT TERM
cp -R deployment "$test_root/source"
python3 - "$test_root/source" "$test_root" <<'PY'
import importlib.util
import os
from pathlib import Path
import stat
import sys

source = Path(sys.argv[1])
root = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location(
    "bundle_installer", source / "libexec/install-temporal-worker-bundle.py"
)
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)

def destination(name):
    path = root / name
    path.mkdir(mode=0o700)
    return path

def expect(code, operation):
    try:
        operation()
    except module.BundleInstallError as exc:
        assert exc.code == code, (exc.code, code)
    else:
        raise AssertionError("operation unexpectedly succeeded")

first = destination("valid")
digest = module.install_bundle(str(source), str(first), False)
generation = first / "usr/lib/medchat/temporal-worker/releases" / digest
assert len(digest) == 64 and generation.is_dir()
assert not (first / "usr/lib/medchat/temporal-worker/current").exists()
expected_owner = (os.geteuid(), os.getegid())
for directory in (generation, generation / "units", generation / "libexec"):
    metadata = os.lstat(directory)
    assert stat.S_ISDIR(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == 0o555
    assert (metadata.st_uid, metadata.st_gid) == expected_owner
for logical, mode, _source_name in module.ASSET_SPECS:
    installed = generation / logical
    metadata = os.lstat(installed)
    assert stat.S_ISREG(metadata.st_mode) and not stat.S_ISLNK(metadata.st_mode)
    assert stat.S_IMODE(metadata.st_mode) == mode
    assert (metadata.st_uid, metadata.st_gid) == expected_owner
manifest_meta = os.lstat(generation / "manifest.sha256")
assert stat.S_ISREG(manifest_meta.st_mode)
assert stat.S_IMODE(manifest_meta.st_mode) == 0o644
module.verify_generation(
    os.open(first / "usr/lib/medchat/temporal-worker/releases", module._DIRECTORY_FLAGS),
    digest,
    *expected_owner,
)

leaf = source / "medchat-temporal-worker.service"
leaf_real = source / "worker.real"
leaf.rename(leaf_real)
leaf.symlink_to(leaf_real.name)
expect("bundle_source_untrusted", lambda: module.install_bundle(str(source), str(destination("leaf-link")), False))
leaf.unlink()
leaf_real.rename(leaf)

libexec = source / "libexec"
libexec_real = source / "libexec.real"
libexec.rename(libexec_real)
libexec.symlink_to(libexec_real.name, target_is_directory=True)
expect("bundle_source_untrusted", lambda: module.install_bundle(str(source), str(destination("parent-link")), False))
libexec.unlink()
libexec_real.rename(libexec)

prepare = source / "medchat-temporal-worker-prepare.service"
prepare_real = source / "prepare.real"
prepare.rename(prepare_real)
os.mkfifo(prepare)
expect("bundle_source_untrusted", lambda: module.install_bundle(str(source), str(destination("fifo")), False))
prepare.unlink()
prepare_real.rename(prepare)

oversize = source / "medchat-temporal-worker.service"
original = oversize.read_bytes()
oversize.write_bytes(b"x" * (module.MAX_ASSET_BYTES + 1))
expect("bundle_source_too_large", lambda: module.install_bundle(str(source), str(destination("oversize")), False))
oversize.write_bytes(original)

original_read_all = module._read_all
mutated = False
def mutate_during_read(descriptor, limit, code):
    global mutated
    if not mutated:
        mutated = True
        with open(f"/proc/self/fd/{descriptor}", "ab", buffering=0) as writable:
            writable.write(b"changed")
    return original_read_all(descriptor, limit, code)
module._read_all = mutate_during_read
expect("bundle_source_changed", lambda: module.install_bundle(str(source), str(destination("mutation")), False))
module._read_all = original_read_all
oversize.write_bytes(original)

lock_destination = destination("lock-link")
(lock_destination / "run/medchat-temporal-worker").mkdir(parents=True, mode=0o700)
sentinel = root / "sentinel"
sentinel.write_text("keep", encoding="ascii")
(lock_destination / "run/medchat-temporal-worker/install.lock").symlink_to(sentinel)
expect("bundle_lock_untrusted", lambda: module.install_bundle(str(source), str(lock_destination), False))
assert sentinel.read_text(encoding="ascii") == "keep"

parent_destination = destination("release-parent-link")
release_parent = parent_destination / "usr/lib/medchat/temporal-worker"
release_parent.mkdir(parents=True)
outside = root / "outside"
outside.mkdir()
(release_parent / "releases").symlink_to(outside, target_is_directory=True)
expect("bundle_destination_untrusted", lambda: module.install_bundle(str(source), str(parent_destination), False))
assert not any(outside.iterdir())
PY
'''.replace("__REPOSITORY__", repository)
    result = _run_native_posix(command, timeout=120)
    assert result.returncode == 0, result.stderr


def test_temporal_activation_first_upgrade_rollback_and_failure_are_atomic() -> None:
    repository = _native_posix_repository()
    command = r'''
set -eu
cd '__REPOSITORY__'
test_root=$(mktemp -d)
cleanup() {
    chmod -R u+w "$test_root" 2>/dev/null || true
    rm -rf -- "$test_root"
}
trap cleanup EXIT HUP INT TERM
cp -R deployment "$test_root/source"
cat > "$test_root/systemctl" <<'SH'
#!/bin/sh
set -eu
command=$1
shift
state=$TEST_TEMPORAL_STATE
case "$command" in
    is-active)
        [ "$1" = --quiet ]
        grep -qx "$2" "$state" 2>/dev/null && exit 0
        exit 3
        ;;
    stop)
        unit=$1
        if [ -f "$state" ]; then grep -vx "$unit" "$state" > "$state.next" || true; mv "$state.next" "$state"; fi
        ;;
    start)
        unit=$1
        grep -qx "$unit" "$state" 2>/dev/null || printf '%s\n' "$unit" >> "$state"
        ;;
    daemon-reload)
        if [ -f "$TEST_TEMPORAL_FAIL_RELOAD" ]; then rm "$TEST_TEMPORAL_FAIL_RELOAD"; exit 1; fi
        ;;
    show)
        unit=$1
        printf '/etc/systemd/system/%s\n' "$unit"
        ;;
    cat)
        [ "$1" = --no-pager ]
        unit=$2
        cat "$TEST_TEMPORAL_ROOT/usr/lib/medchat/temporal-worker/current/units/$unit"
        ;;
    *) exit 2 ;;
esac
SH
chmod 755 "$test_root/systemctl"
python3 - "$test_root/source" "$test_root" <<'PY'
import importlib.util
import os
from pathlib import Path
import sys

source = Path(sys.argv[1])
test_root = Path(sys.argv[2])
spec = importlib.util.spec_from_file_location("bundle", source / "libexec/install-temporal-worker-bundle.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
systemctl = str(test_root / "systemctl")
state = test_root / "state"
fail_reload = test_root / "fail-reload"
os.environ["TEST_TEMPORAL_STATE"] = str(state)
os.environ["TEST_TEMPORAL_FAIL_RELOAD"] = str(fail_reload)

def make_root(name):
    path = test_root / name
    path.mkdir(mode=0o700)
    return path

def current(root):
    return os.readlink(root / "usr/lib/medchat/temporal-worker/current")

root = make_root("root")
os.environ["TEST_TEMPORAL_ROOT"] = str(root)
first = module.install_bundle(str(source), str(root), False)
module.activate_generation(first, root_path=str(root), systemctl=systemctl)
assert current(root) == f"releases/{first}"
for relative, target in module.BOOTSTRAP_LINKS.items():
    installed = root / relative
    assert installed.is_symlink() and os.readlink(installed) == target
assert set(state.read_text().splitlines()) == {module.PREPARE_UNIT, module.WORKER_UNIT}

module.activate_generation(first, root_path=str(root), systemctl=systemctl)
assert current(root) == f"releases/{first}"

worker_source = source / "medchat-temporal-worker.service"
worker_source.write_bytes(worker_source.read_bytes() + b"\n# upgrade\n")
second = module.install_bundle(str(source), str(root), False)
assert second != first
module.activate_generation(second, root_path=str(root), systemctl=systemctl)
assert current(root) == f"releases/{second}"
module.activate_generation(first, root_path=str(root), systemctl=systemctl)
assert current(root) == f"releases/{first}"

fail_reload.touch()
try:
    module.activate_generation(second, root_path=str(root), systemctl=systemctl)
except module.BundleInstallError as exc:
    assert exc.code == "activation_systemctl_failed"
else:
    raise AssertionError("reload failure unexpectedly succeeded")
assert current(root) == f"releases/{first}"
assert set(state.read_text().splitlines()) == {module.PREPARE_UNIT, module.WORKER_UNIT}

unexpected = make_root("unexpected")
os.environ["TEST_TEMPORAL_ROOT"] = str(unexpected)
unexpected_digest = module.install_bundle(str(source), str(unexpected), False)
entry = unexpected / "etc/systemd/system/medchat-temporal-worker.service"
entry.parent.mkdir(parents=True)
entry.write_text("keep", encoding="ascii")
try:
    module.activate_generation(unexpected_digest, root_path=str(unexpected), systemctl=systemctl)
except module.BundleInstallError as exc:
    assert exc.code == "activation_bootstrap_invalid"
else:
    raise AssertionError("unexpected bootstrap entry was accepted")
assert entry.read_text(encoding="ascii") == "keep" and not entry.is_symlink()
assert not (unexpected / "usr/lib/medchat/temporal-worker/current").exists()
PY
'''.replace("__REPOSITORY__", repository)
    result = _run_native_posix(command, timeout=180)
    assert result.returncode == 0, result.stderr


def test_temporal_legacy_migration_quarantines_dangerous_policy(
    tmp_path: Path,
) -> None:
    _native_posix_repository(require_root=True)
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    predecessor = {
        "worker": "deployment/medchat-temporal-worker.service",
        "prepare": "deployment/medchat-temporal-worker-prepare.service",
        "env-helper": "deployment/libexec/validate-temporal-worker-env.py",
        "tmpfiles": "deployment/tmpfiles/medchat-temporal-worker.conf",
    }
    for name, repository_path in predecessor.items():
        payload = subprocess.run(
            ["git", "show", f"0c54b2f:{repository_path}"],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        ).stdout
        (legacy / name).write_bytes(payload)
    repository = ROOT.resolve().as_posix()
    legacy_wsl = legacy.resolve().as_posix()
    command = r'''
set -eu
cd '__REPOSITORY__'
test_root=$(mktemp -d)
cleanup() {
    chmod -R u+w "$test_root" 2>/dev/null || true
    rm -rf -- "$test_root"
}
trap cleanup EXIT HUP INT TERM
cp -R deployment "$test_root/source"
cat > "$test_root/systemctl" <<'SH'
#!/bin/sh
set -eu
command=$1
shift
state=$TEST_TEMPORAL_STATE
case "$command" in
    is-active) [ "$1" = --quiet ]; grep -qx "$2" "$state" 2>/dev/null && exit 0; exit 3 ;;
    stop) unit=$1; grep -vx "$unit" "$state" > "$state.next" 2>/dev/null || true; mv "$state.next" "$state" ;;
    start) unit=$1; grep -qx "$unit" "$state" 2>/dev/null || printf '%s\n' "$unit" >> "$state" ;;
    daemon-reload) [ ! -f "$TEST_TEMPORAL_FAIL_RELOAD" ] || { rm "$TEST_TEMPORAL_FAIL_RELOAD"; exit 1; } ;;
    show) printf '/etc/systemd/system/%s\n' "$1" ;;
    cat) [ "$1" = --no-pager ]; cat "$TEST_TEMPORAL_ROOT/usr/lib/medchat/temporal-worker/current/units/$2" ;;
    *) exit 2 ;;
esac
SH
chmod 755 "$test_root/systemctl"
python3 - "$test_root/source" "$test_root" '__LEGACY__' <<'PY'
import hashlib
import importlib.util
import os
from pathlib import Path
import shutil
import stat
import subprocess
import sys

source, test_root, legacy = map(Path, sys.argv[1:])
spec = importlib.util.spec_from_file_location("bundle", source / "libexec/install-temporal-worker-bundle.py")
module = importlib.util.module_from_spec(spec)
assert spec.loader is not None
spec.loader.exec_module(module)
systemctl = str(test_root / "systemctl")

def make_root(name):
    root = test_root / name
    root.mkdir(mode=0o700)
    digest = module.install_bundle(str(source), str(root), False)
    paths = {
        "worker": (root / "etc/systemd/system/medchat-temporal-worker.service", 0o644),
        "prepare": (root / "etc/systemd/system/medchat-temporal-worker-prepare.service", 0o644),
        "env-helper": (root / "usr/libexec/medchat/validate-temporal-worker-env", 0o755),
        "tmpfiles": (root / "usr/lib/tmpfiles.d/medchat-temporal-worker.conf", 0o644),
    }
    for fixture, (target, mode) in paths.items():
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(legacy / fixture, target)
        target.chmod(mode)
    return root, digest

def configure(root, name, active=True):
    state = test_root / f"{name}.state"
    if active:
        state.write_text(f"{module.PREPARE_UNIT}\n{module.WORKER_UNIT}\n", encoding="ascii")
    else:
        state.touch()
    os.environ["TEST_TEMPORAL_ROOT"] = str(root)
    os.environ["TEST_TEMPORAL_STATE"] = str(state)
    os.environ["TEST_TEMPORAL_FAIL_RELOAD"] = str(test_root / f"{name}.fail")
    return state

root, digest = make_root("success")
state = configure(root, "success")
module.migrate_legacy_generation(digest, root_path=str(root), systemctl=systemctl)
tmpfiles = root / module.LEGACY_TMPFILES_RELATIVE
quarantine = root / module.LEGACY_QUARANTINE_RELATIVE
assert not tmpfiles.exists()
metadata = os.lstat(quarantine)
assert stat.S_ISREG(metadata.st_mode) and stat.S_IMODE(metadata.st_mode) == 0o600
assert hashlib.sha256(quarantine.read_bytes()).hexdigest() == module.LEGACY_ASSETS[module.LEGACY_TMPFILES_RELATIVE][1]
for relative, target in module.BOOTSTRAP_LINKS.items():
    assert os.path.islink(root / relative) and os.readlink(root / relative) == target
assert os.readlink(root / "usr/lib/medchat/temporal-worker/current") == f"releases/{digest}"
assert set(state.read_text().splitlines()) == {module.PREPARE_UNIT, module.WORKER_UNIT}
module.migrate_legacy_generation(digest, root_path=str(root), systemctl=systemctl)

unknown, unknown_digest = make_root("unknown")
unknown_state = configure(unknown, "unknown")
unit = unknown / "etc/systemd/system/medchat-temporal-worker.service"
unit.write_bytes(unit.read_bytes() + b"unknown")
try:
    module.migrate_legacy_generation(unknown_digest, root_path=str(unknown), systemctl=systemctl)
except module.BundleInstallError as exc:
    assert exc.code == "legacy_migration_unsupported"
else:
    raise AssertionError("unknown legacy hash was accepted")
assert (unknown / module.LEGACY_TMPFILES_RELATIVE).is_file()
assert not (unknown / module.LEGACY_QUARANTINE_RELATIVE).exists()
assert not (unknown / "usr/lib/medchat/temporal-worker/current").exists()
assert unknown_state.read_text() == ""

failed, failed_digest = make_root("failed")
failed_state = configure(failed, "failed")
sentinel = test_root / "sentinel"
sentinel.write_text("keep", encoding="ascii")
sentinel.chmod(0o640)
scratch = failed / "opt/medchat/molecular_chat_system/scratch"
scratch.parent.mkdir(parents=True)
scratch.symlink_to(sentinel)
before = os.lstat(sentinel)
(test_root / "failed.fail").touch()
try:
    module.migrate_legacy_generation(failed_digest, root_path=str(failed), systemctl=systemctl)
except module.BundleInstallError as exc:
    assert exc.code == "legacy_migration_manual_recovery_required"
else:
    raise AssertionError("reload failure unexpectedly succeeded")
assert not (failed / module.LEGACY_TMPFILES_RELATIVE).exists()
assert (failed / module.LEGACY_QUARANTINE_RELATIVE).is_file()
assert not (failed / "usr/lib/medchat/temporal-worker/current").exists()
assert failed_state.read_text() == ""
if Path("/usr/bin/systemd-tmpfiles").is_file():
    subprocess.run(["/usr/bin/systemd-tmpfiles", "--create", f"--root={failed}"], check=True)
after = os.lstat(sentinel)
assert (after.st_dev, after.st_ino, stat.S_IMODE(after.st_mode), sentinel.read_text()) == (
    before.st_dev, before.st_ino, stat.S_IMODE(before.st_mode), "keep"
)
PY
'''.replace("__REPOSITORY__", repository).replace("__LEGACY__", legacy_wsl)
    result = _run_native_posix(command, timeout=240)
    assert result.returncode == 0, result.stderr


def test_temporal_worker_installer_has_live_source_guard_and_no_service_actions() -> None:
    source = WORKER_INSTALLER.read_text(encoding="utf-8")
    writer = BUNDLE_INSTALLER.read_text(encoding="utf-8")
    assert source.startswith("#!/bin/sh\nset -eu\n")
    assert "PATH=/usr/sbin:/usr/bin:/sbin:/bin\nexport PATH\n" in source
    assert "--destdir" in source
    assert "/usr/bin/python3" in source
    assert "hashlib" in writer
    assert "O_NOFOLLOW" in writer
    assert "os.rename" in writer
    assert "os.fstat" in writer
    assert "id -u" in source
    assert "stat -c %u" in source
    assert "022" in source
    assert "source_not_root_owned" in source
    assert "/etc/medchat/temporal-worker.env" not in source
    assert not re.search(r"systemctl\s+(?:start|restart|reload|enable)", source)


def test_temporal_worker_installer_routes_root_aliases_through_live_guard() -> None:
    repository = _native_posix_repository()
    if os.geteuid() == 0:
        pytest.skip("native non-root execution is required")
    command = f"""
set -eu
cd '{repository}'
if [ "$(id -u)" -eq 0 ]; then exit 77; fi
for destination in / /./ /tmp/.. ///
do
    set +e
    output=$(sh deployment/install-temporal-worker.sh --destdir "$destination" 2>&1)
    result=$?
    set -e
    test "$result" -eq 1
    test "$output" = 'temporal_worker_install=failed code=root_required'
done
"""
    result = _run_native_posix(command, timeout=60)
    if result.returncode == 77:
        pytest.skip("native non-root execution is required")
    assert result.returncode == 0, result.stderr


@pytest.mark.skip(reason="superseded by immutable bundle native trust coverage")
def test_temporal_worker_installer_does_not_follow_leaf_or_parent_symlinks() -> None:
    repository = _native_posix_repository()
    command = f"""
set -eu
cd '{repository}'
destination=$(mktemp -d)
outside=$(mktemp -d)
parent_destination=$(mktemp -d)
parent_outside=$(mktemp -d)
trap 'rm -rf -- "$destination" "$outside" "$parent_destination" "$parent_outside"' EXIT
mkdir -p "$destination/etc/systemd/system"
printf '%s\n' keep > "$outside/marker"
ln -s "$outside/marker" "$destination/etc/systemd/system/medchat-temporal-worker.service"
sh deployment/install-temporal-worker.sh --destdir "$destination"
test ! -L "$destination/etc/systemd/system/medchat-temporal-worker.service"
cmp deployment/medchat-temporal-worker.service "$destination/etc/systemd/system/medchat-temporal-worker.service"
test "$(cat "$outside/marker")" = keep
ln -s "$parent_outside" "$parent_destination/etc"
set +e
sh deployment/install-temporal-worker.sh --destdir "$parent_destination" >/dev/null 2>&1
result=$?
set -e
test "$result" -ne 0
test ! -e "$parent_outside/systemd"
"""
    result = _run_native_posix(command, timeout=60)
    assert result.returncode == 0, result.stderr


def test_temporal_worker_live_install_arms_cleanup_before_install() -> None:
    command = _live_root_install_command("/mnt/d/repository")
    absence_check = command.index("for target in")
    cleanup_arm = command.index("cleanup_armed=1")
    installer_call = command.index(
        'sh "$release/deployment/install-temporal-worker.sh" --destdir /'
    )
    absence_check_end = command.index(
        "done\nif [ ! -d /usr/libexec/medchat ]; then"
    )
    assert absence_check < absence_check_end < cleanup_arm < installer_call
    assert 'if [ "$cleanup_armed" -eq 1 ]; then' in command
    assert "trap cleanup EXIT" in command
    assert "trap cleanup_on_signal HUP INT TERM" in command


@pytest.mark.skip(reason="superseded by immutable generation boundary coverage")
def test_temporal_worker_installer_detects_post_replace_target_replacement() -> None:
    repository = _native_posix_repository()
    command = f"""
set -eu
cd '{repository}'
release=$(mktemp -d)
destination=$(mktemp -d)
trap 'rm -rf -- "$release" "$destination"' EXIT HUP INT TERM
cp -R deployment "$release/deployment"
python3 - "$release/deployment/install-temporal-worker.sh" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
source = path.read_text(encoding="utf-8")
needle = '''        os.replace(
            temporary_name,
            leaf_name,
            src_dir_fd=parent_descriptor,
            dst_dir_fd=parent_descriptor,
        )
        os.fsync(parent_descriptor)
'''
replacement = needle + '''        if relative_path == "etc/systemd/system/medchat-temporal-worker.service":
            attack_name = ".medchat-test-post-replace-attack"
            attack_payload = b"tampered temporal worker unit\\\\n"
            try:
                os.unlink(attack_name, dir_fd=parent_descriptor)
            except FileNotFoundError:
                pass
            attack_descriptor = os.open(
                attack_name,
                CREATE_FLAGS,
                mode,
                dir_fd=parent_descriptor,
            )
            try:
                attack_offset = 0
                while attack_offset < len(attack_payload):
                    attack_written = os.write(
                        attack_descriptor,
                        attack_payload[attack_offset:],
                    )
                    if attack_written <= 0:
                        raise RuntimeError("test attack write made no progress")
                    attack_offset += attack_written
                os.fchmod(attack_descriptor, mode)
                os.fsync(attack_descriptor)
            finally:
                os.close(attack_descriptor)
            os.replace(
                attack_name,
                leaf_name,
                src_dir_fd=parent_descriptor,
                dst_dir_fd=parent_descriptor,
            )
            os.fsync(parent_descriptor)
'''
if source.count(needle) != 1:
    raise SystemExit("installer replacement anchor changed")
path.write_text(source.replace(needle, replacement), encoding="utf-8", newline="\\n")
PY
set +e
output=$(sh "$release/deployment/install-temporal-worker.sh" --destdir "$destination" 2>&1)
result=$?
set -e
test "$result" -eq 1
test "$output" = 'temporal_worker_install=failed code=asset_installation_failed'
test ! -L "$destination/etc/systemd/system/medchat-temporal-worker.service"
test "$(cat "$destination/etc/systemd/system/medchat-temporal-worker.service")" = 'tampered temporal worker unit'
test ! -e "$destination/usr/share/medchat/temporal-worker-assets.sha256"
"""
    result = _run_native_posix(command, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.skip(reason="superseded by public-PID immutable staging signal coverage")
@pytest.mark.parametrize("signal_name", ["HUP", "INT", "TERM"])
def test_temporal_worker_installer_cleans_temporary_file_on_signal(
    signal_name: str,
) -> None:
    repository = _native_posix_repository()
    command = f"""
set -eu
command -v setsid >/dev/null 2>&1 || exit 77
cd '{repository}'
test_root=$(mktemp -d)
release="$test_root/release"
destination="$test_root/destination"
sync_file="$test_root/sync"
output_file="$test_root/output"
leader=
leader_starttime=
python_pid=
python_starttime=
python_pgid=
process_identity_matches() {{
    python3 - "$python_pid" "$python_starttime" "$python_pgid" <<'PY'
import os
import sys

pid, expected_starttime, expected_pgid = map(int, sys.argv[1:])
try:
    payload = open(f"/proc/{{pid}}/stat", encoding="ascii").read()
    closing_parenthesis = payload.rfind(")")
    starttime = int(payload[closing_parenthesis + 2 :].split()[19])
    pgid = os.getpgid(pid)
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if (starttime, pgid) == (expected_starttime, expected_pgid) else 1)
PY
}}
leader_identity_matches() {{
    python3 - "$leader" "$leader_starttime" <<'PY'
import os
import sys

pid, expected_starttime = map(int, sys.argv[1:])
try:
    payload = open(f"/proc/{{pid}}/stat", encoding="ascii").read()
    closing_parenthesis = payload.rfind(")")
    starttime = int(payload[closing_parenthesis + 2 :].split()[19])
    pgid = os.getpgid(pid)
except (OSError, ValueError):
    raise SystemExit(1)
raise SystemExit(0 if (starttime, pgid) == (expected_starttime, pid) else 1)
PY
}}
kill_process_group() {{
    python3 - "$1" <<'PY'
import os
import signal
import sys

os.killpg(int(sys.argv[1]), signal.SIGKILL)
PY
}}
cleanup() {{
    killed_group=0
    if [ -n "$python_pid" ] && process_identity_matches; then
        if kill_process_group "$python_pgid"; then killed_group=1; fi
    elif [ -n "$leader" ] && [ -n "$leader_starttime" ] && leader_identity_matches; then
        if kill_process_group "$leader"; then killed_group=1; fi
    fi
    if [ -n "$leader" ]; then
        if [ "$killed_group" -eq 1 ] || ! kill -0 "$leader" 2>/dev/null; then
            wait "$leader" 2>/dev/null || true
        fi
    fi
    rm -rf -- "$test_root"
}}
cleanup_on_signal() {{
    cleanup
    trap - EXIT HUP INT TERM
    exit 1
}}
trap cleanup EXIT
trap cleanup_on_signal HUP INT TERM
mkdir -p "$release" "$destination"
printf '%s\n' keep > "$destination/sentinel"
cp -R deployment "$release/deployment"
python3 - "$release/deployment/install-temporal-worker.sh" "$sync_file" <<'PY'
from pathlib import Path
import sys

path = Path(sys.argv[1])
sync_path = sys.argv[2]
source = path.read_text(encoding="utf-8")
needle = '''        descriptor = os.open(
            temporary_name,
            CREATE_FLAGS,
            mode,
            dir_fd=parent_descriptor,
        )
        offset = 0
'''
injection = '''        sync_descriptor = os.open(
            %r,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        try:
            process_stat = open("/proc/self/stat", encoding="ascii").read()
            closing_parenthesis = process_stat.rfind(")")
            process_starttime = int(
                process_stat[closing_parenthesis + 2 :].split()[19]
            )
            sync_payload = ("%%d %%d %%d\\\\n" %% (
                os.getpid(),
                os.getpgrp(),
                process_starttime,
            )).encode("ascii")
            os.write(sync_descriptor, sync_payload)
            os.fsync(sync_descriptor)
        finally:
            os.close(sync_descriptor)
        __import__("signal").pause()
''' % (sync_path,)
if source.count(needle) != 1:
    raise SystemExit("installer temporary-file synchronization anchor changed")
replacement = needle.replace("        offset = 0\\n", injection + "        offset = 0\\n")
path.write_text(source.replace(needle, replacement), encoding="utf-8", newline="\\n")
PY
setsid sh "$release/deployment/install-temporal-worker.sh" \
    --destdir "$destination" >"$output_file" 2>&1 &
leader=$!
leader_starttime=$(python3 - "$leader" <<'PY'
import sys

payload = open(f"/proc/{{sys.argv[1]}}/stat", encoding="ascii").read()
closing_parenthesis = payload.rfind(")")
print(int(payload[closing_parenthesis + 2 :].split()[19]))
PY
)
for attempt in $(seq 1 200)
do
    if [ -s "$sync_file" ]; then break; fi
    if ! kill -0 "$leader" 2>/dev/null; then break; fi
    sleep 0.025
done
test -s "$sync_file"
read -r python_pid python_pgid python_starttime < "$sync_file"
test "$python_pgid" = "$leader"
process_identity_matches
kill -s '{signal_name}' "$python_pid"
for attempt in $(seq 1 200)
do
    if ! kill -0 "$leader" 2>/dev/null; then break; fi
    sleep 0.025
done
if kill -0 "$leader" 2>/dev/null; then exit 90; fi
set +e
wait "$leader"
result=$?
set -e
leader=
python_pid=
test "$result" -ne 0
test "$(cat "$output_file")" = 'temporal_worker_install=failed code=asset_installation_failed'
test "$(cat "$destination/sentinel")" = keep
test -z "$(find "$destination" -name '.medchat-install-*' -print -quit)"
test ! -e "$destination/etc/systemd/system/medchat-temporal-worker.service"
test ! -e "$destination/etc/systemd/system/medchat-temporal-worker-prepare.service"
test ! -e "$destination/usr/libexec/medchat/validate-temporal-worker-env"
test ! -e "$destination/usr/lib/tmpfiles.d/medchat-temporal-worker.conf"
test ! -e "$destination/usr/share/medchat/temporal-worker-assets.sha256"
rm -rf -- "$release"
test ! -e "$release"
"""
    result = _run_native_posix(command, timeout=45)
    if result.returncode == 77:
        pytest.skip("native setsid is unavailable")
    assert result.returncode == 0, result.stderr


def test_temporal_worker_live_root_validation_is_a_release_blocker() -> None:
    pytest.skip(
        "release blocker: isolated live-root runner is intentionally not implemented"
    )


def _load_bundle_installer_from(path: Path):
    spec = importlib.util.spec_from_file_location(
        f"temporal_bundle_installer_{path.parent.name}", path
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _make_native_destination(path: Path) -> Path:
    path.mkdir(mode=0o700)
    (path / "sentinel").write_text("keep", encoding="ascii")
    return path


def _unlock_native_tree(path: Path) -> None:
    for directory in sorted(
        (entry for entry in path.rglob("*") if entry.is_dir()),
        key=lambda entry: len(entry.parts),
    ):
        directory.chmod(0o700)


def test_temporal_bundle_failures_leave_only_complete_generations(
    tmp_path: Path,
) -> None:
    _native_posix_repository()
    baseline_source = tmp_path / "baseline"
    upgrade_source = tmp_path / "upgrade"
    shutil.copytree(ROOT / "deployment", baseline_source)
    shutil.copytree(ROOT / "deployment", upgrade_source)
    worker = upgrade_source / "medchat-temporal-worker.service"
    worker.write_bytes(worker.read_bytes() + b"\n# failure-boundary-upgrade\n")
    installer = _load_bundle_installer_from(
        baseline_source / "libexec/install-temporal-worker-bundle.py"
    )
    original_write = installer._write_file
    original_rename = installer.os.rename

    for fail_after in range(1, 6):
        destination = _make_native_destination(tmp_path / f"write-{fail_after}")
        try:
            baseline = installer.install_bundle(
                str(baseline_source), str(destination), False
            )
            writes = 0

            def fail_after_write(*args, **kwargs):
                nonlocal writes
                original_write(*args, **kwargs)
                writes += 1
                if writes == fail_after:
                    raise installer.BundleInstallError("bundle_publish_failed")

            installer._write_file = fail_after_write
            with pytest.raises(installer.BundleInstallError):
                installer.install_bundle(str(upgrade_source), str(destination), False)
            releases = destination / installer.RELEASES_RELATIVE
            assert {entry.name for entry in releases.iterdir()} == {baseline}
            assert (destination / "sentinel").read_text(encoding="ascii") == "keep"
            assert not (destination / "usr/lib/medchat/temporal-worker/current").exists()
            assert not list(destination.rglob(".staging-*"))
        finally:
            installer._write_file = original_write
            _unlock_native_tree(destination)

    destination = _make_native_destination(tmp_path / "post-publish")
    try:
        baseline = installer.install_bundle(
            str(baseline_source), str(destination), False
        )

        def publish_then_fail(*args, **kwargs):
            original_rename(*args, **kwargs)
            raise installer.BundleInstallError("bundle_publish_failed")

        installer.os.rename = publish_then_fail
        with pytest.raises(installer.BundleInstallError):
            installer.install_bundle(str(upgrade_source), str(destination), False)
        releases_path = destination / installer.RELEASES_RELATIVE
        releases = {entry.name for entry in releases_path.iterdir()}
        assert baseline in releases and len(releases) == 2
        releases_fd = os.open(releases_path, installer._DIRECTORY_FLAGS)
        try:
            for digest in releases:
                installer.verify_generation(
                    releases_fd, digest, os.geteuid(), os.getegid()
                )
        finally:
            os.close(releases_fd)
        assert not list(destination.rglob(".staging-*"))
        assert (destination / "sentinel").read_text(encoding="ascii") == "keep"
    finally:
        installer.os.rename = original_rename
        _unlock_native_tree(destination)


@pytest.mark.parametrize("signal_name", ["SIGHUP", "SIGINT", "SIGTERM"])
def test_temporal_bundle_signal_cleans_private_staging(
    tmp_path: Path,
    signal_name: str,
) -> None:
    _native_posix_repository()
    source = tmp_path / f"source-{signal_name}"
    shutil.copytree(ROOT / "deployment", source)
    destination = _make_native_destination(tmp_path / f"destination-{signal_name}")
    sync = tmp_path / f"sync-{signal_name}"
    helper = source / "libexec/install-temporal-worker-bundle.py"
    payload = helper.read_text(encoding="utf-8")
    anchor = "        staging_created = True\n        generation = os.open("
    injection = (
        "        staging_created = True\n"
        f"        sync_descriptor = os.open({str(sync)!r}, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)\n"
        "        os.close(sync_descriptor)\n"
        "        signal.pause()\n"
        "        generation = os.open("
    )
    assert payload.count(anchor) == 1
    helper.write_text(payload.replace(anchor, injection), encoding="utf-8", newline="\n")
    process = subprocess.Popen(
        ["sh", str(source / "install-temporal-worker.sh"), "--destdir", str(destination)],
        cwd=source,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        deadline = time.monotonic() + 10
        while not sync.exists() and process.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert sync.exists() and process.poll() is None
        os.kill(process.pid, getattr(signal, signal_name))
        stdout, stderr = process.communicate(timeout=10)
        assert process.returncode != 0
        assert stdout == ""
        assert stderr == (
            "temporal_worker_bundle_installation=failed "
            "code=bundle_interrupted\n"
        )
        assert (destination / "sentinel").read_text(encoding="ascii") == "keep"
        assert not list(destination.rglob(".staging-*"))
        releases = destination / "usr/lib/medchat/temporal-worker/releases"
        assert not releases.exists() or not any(releases.iterdir())
    finally:
        if process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait(timeout=5)
        _unlock_native_tree(destination)


def test_temporal_bundle_concurrent_installers_serialize_on_one_lock(
    tmp_path: Path,
) -> None:
    _native_posix_repository()
    first_source = tmp_path / "first-source"
    second_source = tmp_path / "second-source"
    shutil.copytree(ROOT / "deployment", first_source)
    shutil.copytree(ROOT / "deployment", second_source)
    second_worker = second_source / "medchat-temporal-worker.service"
    second_worker.write_bytes(second_worker.read_bytes() + b"\n# concurrent-second\n")
    marker = tmp_path / "first-holds-lock"
    gate = tmp_path / "release-first"
    helper = first_source / "libexec/install-temporal-worker-bundle.py"
    payload = helper.read_text(encoding="utf-8")
    anchor = "        assets = read_source_assets(source, live)\n"
    injection = (
        anchor
        + f"        marker = os.open({str(marker)!r}, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)\n"
        + "        os.close(marker)\n"
        + f"        while not os.path.exists({str(gate)!r}):\n"
        + "            time.sleep(0.02)\n"
    )
    assert payload.count(anchor) == 1
    helper.write_text(payload.replace(anchor, injection), encoding="utf-8", newline="\n")
    destination = _make_native_destination(tmp_path / "concurrent-destination")
    first = subprocess.Popen(
        ["sh", str(first_source / "install-temporal-worker.sh"), "--destdir", str(destination)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    second: subprocess.Popen[str] | None = None
    try:
        deadline = time.monotonic() + 10
        while not marker.exists() and first.poll() is None and time.monotonic() < deadline:
            time.sleep(0.02)
        assert marker.exists() and first.poll() is None
        second = subprocess.Popen(
            ["sh", str(second_source / "install-temporal-worker.sh"), "--destdir", str(destination)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        time.sleep(0.25)
        assert second.poll() is None
        gate.touch()
        first_stdout, first_stderr = first.communicate(timeout=15)
        second_stdout, second_stderr = second.communicate(timeout=15)
        assert first.returncode == 0, first_stderr
        assert second.returncode == 0, second_stderr
        pattern = re.compile(
            r"temporal_worker_bundle_installation=passed digest=([0-9a-f]{64})\n?"
        )
        first_match = pattern.fullmatch(first_stdout)
        second_match = pattern.fullmatch(second_stdout)
        assert first_match is not None and second_match is not None
        assert first_match.group(1) != second_match.group(1)
        releases_path = destination / "usr/lib/medchat/temporal-worker/releases"
        assert {entry.name for entry in releases_path.iterdir()} == {
            first_match.group(1),
            second_match.group(1),
        }
        assert not (destination / "usr/lib/medchat/temporal-worker/current").exists()
        assert not list(destination.rglob(".staging-*"))
    finally:
        for process in (first, second):
            if process is not None and process.poll() is None:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        _unlock_native_tree(destination)


def test_temporal_worker_installer_stages_exact_assets_without_side_effects(
    tmp_path: Path,
) -> None:
    _native_posix_repository()
    destination = tmp_path / "root"
    destination.mkdir(mode=0o700)
    result = subprocess.run(
        ["sh", str(WORKER_INSTALLER), "--destdir", str(destination)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    match = re.fullmatch(
        r"temporal_worker_bundle_installation=passed digest=([0-9a-f]{64})\n?",
        result.stdout,
    )
    assert match is not None
    generation = (
        destination
        / "usr/lib/medchat/temporal-worker/releases"
        / match.group(1)
    )
    try:
        expected = {
            "units/medchat-temporal-worker.service": (WORKER_SYSTEMD_UNIT, 0o644),
            "units/medchat-temporal-worker-prepare.service": (
                PREPARE_SYSTEMD_UNIT,
                0o644,
            ),
            "libexec/validate-temporal-worker-env": (WORKER_HELPER, 0o755),
            "libexec/prepare-temporal-worker-directories": (
                WORKER_DIRECTORY_HELPER,
                0o755,
            ),
        }
        for relative, (source, mode) in expected.items():
            target = generation / relative
            assert not target.is_symlink()
            assert target.read_bytes() == source.read_bytes()
            assert target.stat().st_mode & 0o777 == mode
        assert (generation / "manifest.sha256").stat().st_mode & 0o777 == 0o644
        assert not (destination / "usr/lib/medchat/temporal-worker/current").exists()
        assert not (destination / "etc/medchat/temporal-worker.env").exists()
    finally:
        for directory in sorted(
            (path for path in destination.rglob("*") if path.is_dir()),
            key=lambda path: len(path.parts),
        ):
            directory.chmod(0o700)


def shell_executable() -> Path:
    candidates = [
        shutil.which("sh"),
        r"C:\Program Files\Git\bin\sh.exe",
    ]
    for candidate in candidates:
        if candidate and Path(candidate).is_file():
            return Path(candidate)
    pytest.skip("POSIX shell is unavailable")


def shell_path(shell: Path, path: Path) -> str:
    if os.name != "nt":
        return str(path)
    resolved = path.resolve().as_posix()
    assert re.fullmatch(r"[A-Za-z]:/.*", resolved)
    return f"/{resolved[0].lower()}{resolved[2:]}"


def run_shell_script(
    script: Path,
    tmp_path: Path,
    *,
    environment: dict[str, str],
    stubs: dict[str, str],
) -> subprocess.CompletedProcess[str]:
    shell = shell_executable()
    stub_dir = tmp_path / "bin"
    stub_dir.mkdir()
    for name, content in stubs.items():
        stub = stub_dir / name
        stub.write_text(content, encoding="utf-8", newline="\n")
        stub.chmod(0o755)
    process_environment = os.environ.copy()
    process_environment.update(environment)
    process_environment["PATH"] = (
        f"{shell_path(shell, stub_dir)}:{process_environment.get('PATH', '')}"
    )
    return subprocess.run(
        [str(shell), shell_path(shell, script)],
        cwd=DEPLOY,
        env=process_environment,
        capture_output=True,
        text=True,
        timeout=15,
    )


def identifier_test_environment(tmp_path: Path, *, role_sync: bool) -> dict[str, str]:
    shell = shell_executable()
    postgres_secret = tmp_path / "postgres-password"
    postgres_secret.write_bytes(b"test-postgres-password")
    environment = {
        "POSTGRES_SEEDS": "postgres",
        "POSTGRES_USER": "temporal",
        "DB_PORT": "5432",
        "DBNAME": "temporal",
        "VISIBILITY_DBNAME": "temporal_visibility",
        "PGHOST": "postgres",
        "PGPORT": "5432",
        "PGDATABASE": "temporal",
        "PGUSER": "temporal",
        "TEMPORAL_POSTGRES_PASSWORD_FILE": shell_path(shell, postgres_secret),
    }
    if role_sync:
        exporter_secret = tmp_path / "exporter-password"
        exporter_secret.write_bytes(b"test-exporter-password")
        environment["TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE"] = shell_path(
            shell, exporter_secret
        )
    return environment


def command_recording_stubs() -> dict[str, str]:
    return {
        "wc": """#!/bin/sh
printf 'wc %s\\n' "$*" >> "$STUB_LOG"
exec /usr/bin/wc "$@"
""",
        "cat": """#!/bin/sh
printf 'cat %s\\n' "$*" >> "$STUB_LOG"
exec /usr/bin/cat "$@"
""",
        "psql": """#!/bin/sh
printf 'psql %s\\n' "$*" >> "$STUB_LOG"
exit 0
""",
        "temporal-sql-tool": """#!/bin/sh
printf 'temporal-sql-tool %s\\n' "$*" >> "$STUB_LOG"
exit 0
""",
    }


def load_yaml(path: Path) -> dict[str, Any]:
    yaml = pytest.importorskip("yaml", reason="PyYAML is required for YAML checks")
    value = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(value, dict)
    return value


def env_map(service: dict[str, Any]) -> dict[str, str]:
    environment = service.get("environment", {})
    if isinstance(environment, dict):
        return {str(key): str(value) for key, value in environment.items()}
    result = {}
    for item in environment:
        key, separator, value = str(item).partition("=")
        assert separator
        result[key] = value
    return result


def deps(service: dict[str, Any]) -> dict[str, dict[str, Any]]:
    value = service.get("depends_on", {})
    assert isinstance(value, dict)
    return value


def test_images_services_ports_and_mount_boundaries() -> None:
    compose = load_yaml(COMPOSE)
    services = compose["services"]
    assert set(services) == LONG_RUNNING | JOBS
    assert {name: service["image"] for name, service in services.items()} == IMAGES
    assert "auto-setup" not in COMPOSE.read_text(encoding="utf-8")
    assert all(not image.endswith(":latest") and "@sha256:" not in image for image in IMAGES.values())
    assert set(compose["volumes"]) == {
        "temporal-postgres-data", "prometheus-data", "grafana-data"
    }
    for name, service in services.items():
        assert service.get("ports", []) == PORTS[name]
        assert service.get("volumes", []) == MOUNTS[name]
        assert all(str(port).startswith("127.0.0.1:") for port in service.get("ports", []))
        assert all(not str(mount).startswith("./") or str(mount).endswith(":ro")
                   for mount in service.get("volumes", []))
        assert all(not str(mount).startswith("/") for mount in service.get("volumes", []))
    text = COMPOSE.read_text(encoding="utf-8")
    assert "/docker-entrypoint-initdb.d" not in text
    assert "./postgres-init/010-exporter.sh:/opt/medchat/010-exporter.sh:ro" in text
    assert "/var/run/docker.sock" not in text
    assert "../..:/" not in text


def test_lifecycle_healthchecks_and_bounded_logs() -> None:
    services = load_yaml(COMPOSE)["services"]
    expected_logging = {
        "driver": "json-file",
        "options": {"max-size": "10m", "max-file": "5"},
    }
    for name in LONG_RUNNING:
        service = services[name]
        assert service["restart"] == "unless-stopped", name
        health = service.get("healthcheck")
        assert health and all(health.get(key) for key in (
            "test", "interval", "timeout", "retries", "start_period"
        )), name
    for name in JOBS:
        service = services[name]
        assert re.fullmatch(r"on-failure:[1-9][0-9]*", service["restart"]), name
        assert "healthcheck" not in service
        assert service.get("ports", []) == []
    for name, service in services.items():
        assert service.get("init") is True, name
        assert service.get("security_opt") == ["no-new-privileges:true"], name
        assert service.get("stop_grace_period"), name
        assert service.get("logging") == expected_logging, name

    commands = {
        name: " ".join(map(str, services[name]["healthcheck"]["test"]))
        for name in LONG_RUNNING
    }
    assert "pg_isready" in commands["postgres"]
    assert "127.0.0.1:9187/metrics" in commands["postgres-exporter"]
    assert "wget -qO- -T 4" in commands["postgres-exporter"]
    assert "grep -Eq" in commands["postgres-exporter"]
    assert "pg_up 1" in commands["postgres-exporter"]
    assert "7233" in commands["temporal"]
    assert (
        "temporal operator cluster health" in commands["temporal"]
        or "nc -z" in commands["temporal"]
    )
    assert "127.0.0.1:8080" in commands["temporal-ui"]
    assert "127.0.0.1:9090/-/healthy" in commands["prometheus"]
    assert "127.0.0.1:3000/api/health" in commands["grafana"]


def test_job_dependencies_are_completed_successfully_and_acyclic() -> None:
    services = load_yaml(COMPOSE)["services"]
    expected = {
        "postgres": {},
        "temporal-schema": {"postgres": {"condition": "service_healthy"}},
        "temporal": {"temporal-schema": {"condition": "service_completed_successfully"}},
        "temporal-namespace": {"temporal": {"condition": "service_healthy"}},
        "temporal-exporter-role-sync": {"postgres": {"condition": "service_healthy"}},
        "postgres-exporter": {
            "temporal-exporter-role-sync": {
                "condition": "service_completed_successfully",
                "restart": True,
            }
        },
        "temporal-ui": {
            "temporal-namespace": {"condition": "service_completed_successfully"}
        },
        "prometheus": {
            "postgres-exporter": {"condition": "service_healthy"},
            "temporal": {"condition": "service_healthy"},
        },
        "grafana": {"prometheus": {"condition": "service_healthy"}},
    }
    actual = {name: deps(service) for name, service in services.items()}
    assert actual == expected
    role_sync = services["temporal-exporter-role-sync"]
    assert "profiles" not in role_sync
    assert role_sync["entrypoint"] == ["/opt/medchat/010-exporter.sh"]

    def visit(name: str, ancestors: frozenset[str]) -> None:
        assert name not in ancestors
        for dependency in actual[name]:
            visit(dependency, ancestors | {name})

    for name in services:
        visit(name, frozenset())


def test_named_networks_segment_database_temporal_and_monitoring() -> None:
    compose = load_yaml(COMPOSE)
    assert compose["networks"] == {
        "database": {"internal": True},
        "temporal": None,
        "temporal-metrics": None,
        "postgres-metrics": None,
        "prometheus-grafana": None,
    }
    assert all("name" not in (configuration or {})
               for configuration in compose["networks"].values())
    for name, service in compose["services"].items():
        assert set(service.get("networks", [])) == NETWORKS[name]
    assert NETWORKS["grafana"].isdisjoint(
        NETWORKS["temporal"] | NETWORKS["postgres-exporter"]
    )
    assert "temporal-metrics" not in NETWORKS["temporal-ui"]


def test_secrets_are_file_backed_not_compose_values() -> None:
    compose = load_yaml(COMPOSE)
    services = compose["services"]
    assert compose["secrets"] == {
        "temporal_postgres_password": {
            "file": "${TEMPORAL_POSTGRES_PASSWORD_FILE:?required}"
        },
        "temporal_postgres_exporter_password": {
            "file": "${TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE:?required}"
        },
        "grafana_admin_password": {
            "file": "${GRAFANA_ADMIN_PASSWORD_FILE:?required}"
        },
    }
    assert env_map(services["postgres"])["POSTGRES_PASSWORD_FILE"] == (
        "/run/secrets/temporal_postgres_password"
    )
    assert env_map(services["grafana"])["GF_SECURITY_ADMIN_PASSWORD__FILE"] == (
        "/run/secrets/grafana_admin_password"
    )
    for name in {"postgres", "grafana", "temporal", "temporal-schema",
                 "temporal-exporter-role-sync"}:
        environment = env_map(services[name])
        assert not {"POSTGRES_PASSWORD", "POSTGRES_PWD", "SQL_PASSWORD", "PGPASSWORD"} & set(environment)
        assert "GF_SECURITY_ADMIN_PASSWORD" not in environment
    text = COMPOSE.read_text(encoding="utf-8")
    assert "POSTGRES_PASSWORD=${" not in text
    assert "POSTGRES_PWD=${" not in text
    assert "GF_SECURITY_ADMIN_PASSWORD=${" not in text
    assert "${TEMPORAL_POSTGRES_PASSWORD:" not in text
    assert "${GRAFANA_ADMIN_PASSWORD:" not in text

    expected_service_secrets = {
        "postgres": {"temporal_postgres_password"},
        "temporal-schema": {"temporal_postgres_password"},
        "temporal": {"temporal_postgres_password"},
        "temporal-namespace": set(),
        "temporal-exporter-role-sync": {
            "temporal_postgres_password",
            "temporal_postgres_exporter_password",
        },
        "postgres-exporter": {"temporal_postgres_exporter_password"},
        "temporal-ui": set(),
        "prometheus": set(),
        "grafana": {"grafana_admin_password"},
    }
    for name, service in services.items():
        mounted = {
            item["source"] if isinstance(item, dict) else str(item)
            for item in service.get("secrets", [])
        }
        assert mounted == expected_service_secrets[name]
        for item in service.get("secrets", []):
            if isinstance(item, dict):
                assert {"uid", "gid", "mode"}.isdisjoint(item)


def test_exporter_database_is_dynamic_and_role_sync_precedes_exporter() -> None:
    services = load_yaml(COMPOSE)["services"]
    exporter = services["postgres-exporter"]
    assert env_map(exporter) == {
        "DATA_SOURCE_URI": "postgres:5432/${TEMPORAL_POSTGRES_DB:-temporal}?sslmode=disable",
        "DATA_SOURCE_USER": "temporal_exporter",
        "DATA_SOURCE_PASS_FILE": "/run/secrets/temporal_postgres_exporter_password",
    }
    assert deps(exporter) == {
        "temporal-exporter-role-sync": {
            "condition": "service_completed_successfully",
            "restart": True,
        }
    }
    role_environment = env_map(services["temporal-exporter-role-sync"])
    assert role_environment["PGDATABASE"] == "${TEMPORAL_POSTGRES_DB:-temporal}"
    assert role_environment["VISIBILITY_DBNAME"] == (
        "${TEMPORAL_POSTGRES_VISIBILITY_DB:-temporal_visibility}"
    )


@pytest.mark.parametrize(
    (
        "scenario", "expected_returncode", "expected_stdout", "expected_error",
        "expected_calls",
    ),
    [
        (
            "success", 0, "OK E_EXPORTER_ROTATED\n", "",
            ["version", "up-help", "role-sync", "recreate-wait"],
        ),
        (
            "role-sync-fails", 1, "", "ERROR E_ROLE_SYNC\n",
            ["version", "up-help", "role-sync"],
        ),
        (
            "wait-timeout", 1, "", "ERROR E_EXPORTER_HEALTH\n",
            ["version", "up-help", "role-sync", "recreate-wait"],
        ),
        (
            "wait-unsupported", 1, "", "ERROR E_COMPOSE_WAIT_UNSUPPORTED\n",
            ["version", "up-help"],
        ),
        ("old-compose", 1, "", "ERROR E_COMPOSE_VERSION\n", ["version"]),
    ],
)
def test_exporter_password_rotation_runs_supported_sequence_and_stops_on_failure(
    tmp_path: Path,
    scenario: str,
    expected_returncode: int,
    expected_stdout: str,
    expected_error: str,
    expected_calls: list[str],
) -> None:
    secret = tmp_path / "exporter-password"
    secret.write_bytes(b"rotation-test-password")
    log = tmp_path / "docker.log"
    docker_stub = """#!/bin/sh
set -eu
case "$*" in
  "compose version --short")
    printf '%s\\n' version >> "$STUB_LOG"
    if [ "$STUB_SCENARIO" = "old-compose" ]; then
      printf '%s\\n' '2.16.9'
    else
      printf '%s\\n' '2.17.0'
    fi
    ;;
  "compose up --help")
    printf '%s\\n' up-help >> "$STUB_LOG"
    if [ "$STUB_SCENARIO" = "wait-unsupported" ]; then
      printf '%s\\n' 'Usage: docker compose up'
    else
      printf '%s\\n' '  --wait  Wait for services to be running|healthy'
      printf '%s\\n' '  --wait-timeout int  Maximum duration in seconds'
    fi
    ;;
  *" run --rm temporal-exporter-role-sync")
    printf '%s\\n' role-sync >> "$STUB_LOG"
    [ "$STUB_SCENARIO" != "role-sync-fails" ] || exit 7
    ;;
  *" up -d --wait --wait-timeout 120 --no-deps --force-recreate postgres-exporter")
    printf '%s\\n' recreate-wait >> "$STUB_LOG"
    [ "$STUB_SCENARIO" != "wait-timeout" ] || exit 8
    ;;
  *) exit 93 ;;
esac
"""
    result = run_shell_script(
        ROTATE_SCRIPT,
        tmp_path,
        environment={
            "TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE": shell_path(
                shell_executable(), secret
            ),
            "STUB_LOG": shell_path(shell_executable(), log),
            "STUB_SCENARIO": scenario,
        },
        stubs={"docker": docker_stub},
    )
    calls = log.read_text(encoding="utf-8").splitlines() if log.exists() else []
    assert result.returncode == expected_returncode
    assert result.stdout == expected_stdout
    assert result.stderr == expected_error
    assert calls == expected_calls
    assert "rotation-test-password" not in result.stdout + result.stderr
    if expected_returncode:
        assert "OK" not in result.stdout


def test_exporter_rotation_rejects_unsupported_compose_and_unsafe_secret_file(
    tmp_path: Path,
) -> None:
    script = ROTATE_SCRIPT.read_text(encoding="utf-8")
    assert "2.17" in script
    assert "compose up --help" in script
    assert "--wait-timeout 120" in script
    assert "E_COMPOSE_WAIT_UNSUPPORTED" in script
    assert '-L "$secret_file"' in script
    assert "maximum_secret_bytes=1024" in script
    assert "wc -l" in script
    assert "set -x" not in script


def test_exporter_rotation_rejects_host_secret_symlink_before_docker(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target"
    target.write_bytes(b"rotation-test-password")
    link = tmp_path / "secret-link"
    try:
        link.symlink_to(target)
    except OSError as error:
        known_windows_capability_error = os.name == "nt" and (
            error.errno in {errno.EPERM, errno.EACCES}
            or getattr(error, "winerror", None) == 1314
        )
        if known_windows_capability_error:
            pytest.skip(
                "host symlink creation unavailable due to Windows privilege"
            )
        raise
    log = tmp_path / "docker.log"
    result = run_shell_script(
        ROTATE_SCRIPT,
        tmp_path,
        environment={
            "TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE": shell_path(
                shell_executable(), link
            ),
            "STUB_LOG": shell_path(shell_executable(), log),
        },
        stubs={
            "docker": "#!/bin/sh\nprintf '%s\\n' called >> \"$STUB_LOG\"\nexit 0\n"
        },
    )
    assert result.returncode == 1
    assert result.stderr == "ERROR E_SECRET_FILE\n"
    assert not log.exists()


def test_schema_job_is_idempotent_and_uses_official_schema_tools() -> None:
    script = SCHEMA_SCRIPT.read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n") and "set -eu" in script
    assert "/run/secrets/temporal_postgres_password" in script
    assert "export SQL_PASSWORD=" in script and "export PGPASSWORD=" in script
    assert "WHERE NOT EXISTS" in script and "format('CREATE DATABASE %I'" in script
    assert "\\gexec" in script
    assert script.count("setup-schema -v 0.0") == 2
    assert script.count("update-schema -d") == 2
    assert "/etc/temporal/schema/postgresql/v12/temporal/versioned" in script
    assert "/etc/temporal/schema/postgresql/v12/visibility/versioned" in script
    assert not re.search(r"temporal-sql-tool[^\n]*\bcreate\b", script)
    assert "--password" not in script and "--pw" not in script


@pytest.mark.parametrize("role_sync", [False, True], ids=["schema", "role-sync"])
def test_postgres_identifiers_reject_invalid_values_before_external_commands(
    tmp_path: Path,
    role_sync: bool,
) -> None:
    script = ROLE_SCRIPT if role_sync else SCHEMA_SCRIPT
    database_key = "PGDATABASE" if role_sync else "DBNAME"
    user_key = "PGUSER" if role_sync else "POSTGRES_USER"
    cases = {
        "empty-database": {database_key: ""},
        "unicode": {database_key: "témporal"},
        "slash": {database_key: "bad/name"},
        "question": {database_key: "bad?name"},
        "hash": {database_key: "bad#name"},
        "control": {database_key: "bad\nname"},
        "leading-digit": {database_key: "1temporal"},
        "too-long": {database_key: "a" * 64},
        "invalid-user": {user_key: "bad-user"},
        "same-databases": {
            database_key: "temporal",
            "VISIBILITY_DBNAME": "temporal",
        },
    }
    for name, changes in cases.items():
        case_dir = tmp_path / name
        case_dir.mkdir()
        log = case_dir / "commands.log"
        environment = identifier_test_environment(case_dir, role_sync=role_sync)
        environment.update(changes)
        environment["STUB_LOG"] = shell_path(shell_executable(), log)
        result = run_shell_script(
            script,
            case_dir,
            environment=environment,
            stubs=command_recording_stubs(),
        )
        calls = log.read_text(encoding="utf-8") if log.exists() else ""
        assert result.returncode == 1, (name, result.stderr)
        assert result.stderr == "ERROR E_IDENTIFIER\n", name
        assert calls == "", name


@pytest.mark.parametrize("role_sync", [False, True], ids=["schema", "role-sync"])
def test_postgres_identifiers_accept_ascii_63_byte_boundary(
    tmp_path: Path,
    role_sync: bool,
) -> None:
    script = ROLE_SCRIPT if role_sync else SCHEMA_SCRIPT
    database_key = "PGDATABASE" if role_sync else "DBNAME"
    log = tmp_path / "commands.log"
    environment = identifier_test_environment(tmp_path, role_sync=role_sync)
    environment.update({
        database_key: "a" * 63,
        "VISIBILITY_DBNAME": "_",
        "PGUSER" if role_sync else "POSTGRES_USER": "Z" + "0" * 62,
        "STUB_LOG": shell_path(shell_executable(), log),
    })
    result = run_shell_script(
        script,
        tmp_path,
        environment=environment,
        stubs=command_recording_stubs(),
    )
    calls = log.read_text(encoding="utf-8") if log.exists() else ""
    assert result.returncode == 0, result.stderr
    assert "psql " in calls
    if not role_sync:
        assert calls.count("temporal-sql-tool ") == 4


@pytest.mark.parametrize(
    ("failing_command", "expected_error"),
    [
        ("psql", "ERROR E_SCHEMA_DATABASES\n"),
        ("temporal-sql-tool", "ERROR E_SCHEMA_MIGRATION\n"),
    ],
)
def test_schema_external_failures_are_stable_and_hide_tool_output(
    tmp_path: Path,
    failing_command: str,
    expected_error: str,
) -> None:
    log = tmp_path / "commands.log"
    environment = identifier_test_environment(tmp_path, role_sync=False)
    environment["STUB_LOG"] = shell_path(shell_executable(), log)
    stubs = command_recording_stubs()
    stubs[failing_command] = f"""#!/bin/sh
printf '{failing_command} %s\\n' "$*" >> "$STUB_LOG"
printf '%s\\n' 'tool-payload /absolute/internal/path' >&2
exit 7
"""
    result = run_shell_script(
        SCHEMA_SCRIPT,
        tmp_path,
        environment=environment,
        stubs=stubs,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert result.stderr == expected_error
    assert "tool-payload" not in result.stdout + result.stderr
    assert "/absolute/internal/path" not in result.stdout + result.stderr


def test_namespace_job_is_bounded_idempotent_and_typo_free() -> None:
    script = NAMESPACE_SCRIPT.read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n") and "set -eu" in script
    assert "MAX_ATTEMPTS" in script and "MAX_ATTdMPTS" not in script
    assert "temporal operator cluster health" in script
    assert script.count("temporal operator namespace describe") >= 1
    assert "temporal operator namespace create" in script
    assert "temporal operator namespace update" in script
    assert '--retention "$DEFAULT_NAMESPACE_RETENTION"' in script
    assert '[ "$attempt" -ge "$MAX_ATTEMPTS" ]' in script


def test_namespace_missing_configuration_fails_with_stable_error_before_cli(
    tmp_path: Path,
) -> None:
    log = tmp_path / "temporal.log"
    result = run_shell_script(
        NAMESPACE_SCRIPT,
        tmp_path,
        environment={
            "TEMPORAL_ADDRESS": "",
            "DEFAULT_NAMESPACE": "default",
            "DEFAULT_NAMESPACE_RETENTION": "72h",
            "MAX_ATTEMPTS": "3",
            "SLEEP_SECONDS": "0",
            "STUB_LOG": shell_path(shell_executable(), log),
        },
        stubs={
            "temporal": "#!/bin/sh\nprintf '%s\\n' called >> \"$STUB_LOG\"\nexit 0\n"
        },
    )
    assert result.returncode == 1
    assert result.stderr == "ERROR E_CONFIGURATION\n"
    assert not log.exists()


@pytest.mark.parametrize(
    ("scenario", "expected_returncode", "expected_create_count"),
    [
        ("existing", 0, 0),
        ("create", 0, 1),
        ("race", 0, 1),
        ("update-fails", 1, 0),
    ],
)
def test_namespace_retention_converges_for_all_creation_paths(
    tmp_path: Path,
    scenario: str,
    expected_returncode: int,
    expected_create_count: int,
) -> None:
    log = tmp_path / "temporal.log"
    state = tmp_path / "state"
    state.mkdir()
    temporal_stub = """#!/bin/sh
set -eu
printf '%s\\n' "$*" >> "$STUB_LOG"
case "$*" in
  "operator cluster health "*) exit 0 ;;
  "operator namespace describe "*)
    case "$STUB_SCENARIO" in
      existing|update-fails) exit 0 ;;
      create) [ -f "$STUB_STATE/created" ] && exit 0 || exit 1 ;;
      race) [ -f "$STUB_STATE/raced" ] && exit 0 || exit 1 ;;
    esac
    ;;
  "operator namespace create "*)
    case "$STUB_SCENARIO" in
      create) : > "$STUB_STATE/created"; exit 0 ;;
      race) : > "$STUB_STATE/raced"; exit 1 ;;
      *) exit 91 ;;
    esac
    ;;
  "operator namespace update "*)
    [ "$STUB_SCENARIO" != "update-fails" ] || exit 9
    exit 0
    ;;
esac
exit 92
"""
    result = run_shell_script(
        NAMESPACE_SCRIPT,
        tmp_path,
        environment={
            "TEMPORAL_ADDRESS": "temporal:7233",
            "DEFAULT_NAMESPACE": "default",
            "DEFAULT_NAMESPACE_RETENTION": "168h",
            "MAX_ATTEMPTS": "3",
            "SLEEP_SECONDS": "0",
            "STUB_LOG": shell_path(shell_executable(), log),
            "STUB_STATE": shell_path(shell_executable(), state),
            "STUB_SCENARIO": scenario,
        },
        stubs={"temporal": temporal_stub},
    )
    calls = log.read_text(encoding="utf-8").splitlines()
    assert result.returncode == expected_returncode, result.stderr
    assert sum("operator namespace create" in call for call in calls) == expected_create_count
    update_calls = [call for call in calls if "operator namespace update" in call]
    assert len(update_calls) == 1
    assert "--namespace default" in update_calls[0]
    assert "--retention 168h" in update_calls[0]
    if scenario == "update-fails":
        assert result.stderr == "ERROR E_NAMESPACE_RETENTION\n"


def test_role_sync_drop_recreates_role_and_delegates_dependency_audit_to_postgres() -> None:
    script = ROLE_SCRIPT.read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n") and "set -eu" in script
    assert "/run/secrets/temporal_postgres_password" in script
    assert "/run/secrets/temporal_postgres_exporter_password" in script
    assert "\\getenv exporter_password TEMPORAL_EXPORTER_PASSWORD" in script
    assert "DROP ROLE IF EXISTS temporal_exporter" in script
    assert "CREATE ROLE temporal_exporter WITH LOGIN PASSWORD" in script
    assert re.search(
        r"NOSUPERUSER\s+NOCREATEDB\s+NOCREATEROLE\s+NOINHERIT\s+"
        r"NOREPLICATION\s+NOBYPASSRLS",
        script,
    )
    for membership_option in ("INHERIT TRUE", "SET FALSE", "ADMIN FALSE"):
        assert (
            "GRANT pg_monitor TO temporal_exporter "
            f"WITH {membership_option}"
        ) in script
    assert "GRANT CONNECT ON DATABASE" not in script
    assert script.index("DROP ROLE IF EXISTS") < script.index("CREATE ROLE temporal_exporter")
    assert "REASSIGN OWNED" not in script
    assert "DROP OWNED" not in script
    for catalog in (
        "pg_shdepend", "pg_auth_members", "pg_database", "pg_namespace",
        "pg_class", "pg_proc", "pg_type", "pg_default_acl", "aclexplode",
    ):
        assert catalog not in script
    assert "fail E_EXPORTER_RESET" in script
    assert "--set=exporter_password" not in script
    psql_argv = script.split("psql", 1)[1].split("<<'SQL'", 1)[0]
    assert "exporter_password" not in psql_argv
    assert "TEMPORAL_EXPORTER_PASSWORD" not in psql_argv


@pytest.mark.parametrize("path", [ROLE_SCRIPT, SCHEMA_SCRIPT, SERVER_SCRIPT])
def test_secret_scripts_bound_input_and_do_not_log_password(path: Path) -> None:
    script = path.read_text(encoding="utf-8")
    assert "maximum_secret_bytes=1024" in script
    assert "wc -c" in script and "wc -l" in script and "printf '\\r'" in script
    assert "set -x" not in script
    assert not re.search(r"(?m)^\s*(?:echo|printf).*password", script)


@pytest.mark.parametrize(
    ("path", "role_sync"),
    [(SCHEMA_SCRIPT, False), (ROLE_SCRIPT, True), (SERVER_SCRIPT, False)],
)
def test_secret_script_errors_are_stable_and_do_not_disclose_paths(
    tmp_path: Path,
    path: Path,
    role_sync: bool,
) -> None:
    environment = identifier_test_environment(tmp_path, role_sync=role_sync)
    missing = tmp_path / "missing-secret"
    environment["TEMPORAL_POSTGRES_PASSWORD_FILE"] = shell_path(
        shell_executable(), missing
    )
    result = run_shell_script(path, tmp_path, environment=environment, stubs={})
    assert result.returncode == 1
    assert result.stderr == "ERROR E_SECRET_FILE\n"
    assert str(missing) not in result.stderr


def test_server_wrapper_exports_secret_then_execs_official_entrypoint() -> None:
    script = SERVER_SCRIPT.read_text(encoding="utf-8")
    temporal = load_yaml(COMPOSE)["services"]["temporal"]
    assert "/run/secrets/temporal_postgres_password" in script
    assert 'export POSTGRES_PWD="$postgres_password"' in script
    assert 'exec /etc/temporal/entrypoint.sh "$@"' in script
    assert temporal["entrypoint"] == ["/opt/medchat/temporal-server-entrypoint.sh"]


def test_env_example_has_only_empty_file_secret_keys() -> None:
    lines = ENV_EXAMPLE.read_text(encoding="utf-8").splitlines()
    pairs = [line.split("=", 1) for line in lines
             if line and not line.lstrip().startswith("#")]
    keys = [key for key, _ in pairs]
    values = dict(pairs)
    assert len(keys) == len(set(keys))
    for key in ("TEMPORAL_POSTGRES_PASSWORD_FILE",
                "TEMPORAL_POSTGRES_EXPORTER_PASSWORD_FILE",
                "GRAFANA_ADMIN_PASSWORD_FILE"):
        assert values[key] == ""
    assert "TEMPORAL_POSTGRES_PASSWORD" not in values
    assert "GRAFANA_ADMIN_PASSWORD" not in values
    assert values["TEMPORAL_POSTGRES_DB"] == "temporal"
    assert values["TEMPORAL_POSTGRES_VISIBILITY_DB"] == "temporal_visibility"
    assert values["TEMPORAL_POSTGRES_USER"] == "temporal"
    assert values["TEMPORAL_NAMESPACE"] == "default"
    assert values["TEMPORAL_NAMESPACE_RETENTION"] == "72h"
    assert values["GRAFANA_ADMIN_USER"] == "admin"
    assert values["MEDCHAT_TEMPORAL_METRICS_RELAY_SUBNET"] == "172.30.95.0/28"
    assert values["MEDCHAT_TEMPORAL_METRICS_RELAY_GATEWAY"] == "172.30.95.1"
    assert values["MEDCHAT_TEMPORAL_PROMETHEUS_SCRAPE_ADDRESS"] == "172.30.95.2"
    assert values["MEDCHAT_TEMPORAL_METRICS_RELAY_PORT"] == "9466"
    env_text = ENV_EXAMPLE.read_text(encoding="utf-8")
    assert "E_RELAY_RUNTIME_MISMATCH" in env_text
    assert "MEDCHAT_TEMPORAL_NAMESPACE=default" in env_text
    assert "MEDCHAT_TEMPORAL_DOCKING_QUEUE=medchat-docking" in env_text
    guidance = ENV_EXAMPLE.read_text(encoding="utf-8").lower()
    assert "never commit real" in guidance
    for contract in (
        "regular file", "symlink", "trailing newline", "1024", "0700", "0444",
        "deployment owner", "bind mount", "uid", "gid", "mode", "printf",
        "rotate-exporter-password.sh", ">= 2.17",
    ):
        assert contract in guidance
    assert "0600" not in guidance


def test_opt_in_integration_uses_host_secret_and_cleanup_contracts() -> None:
    integration = INTEGRATION_TEST.read_text(encoding="utf-8")
    assert "secret_directory.chmod(0o700)" in integration
    assert "path.chmod(0o444)" in integration
    assert ".chmod(0o600)" not in integration
    assert "request.addfinalizer(cleanup)" in integration
    assert '"down", "--volumes", "--remove-orphans"' in integration
    assert "if result.returncode != 0" in integration
    assert "isolated Compose cleanup failed" in integration
    assert integration.index("assert rotation.returncode == 0") < integration.index(
        "assert health_status("
    )


def test_no_dangerous_privileges_version_key_or_unescaped_container_vars() -> None:
    compose = load_yaml(COMPOSE)
    text = COMPOSE.read_text(encoding="utf-8")
    forbidden = {"cap_add", "devices", "network_mode", "pid", "privileged", "userns_mode"}
    assert not re.search(r"(?m)^version:", text)
    assert "$${POSTGRES_USER}" in text and "$${POSTGRES_DB}" in text
    assert not re.search(r'(?<!\$)\$\{POSTGRES_(?:USER|DB)\}', text)
    for name, service in compose["services"].items():
        assert forbidden.isdisjoint(service), name


def test_task6_documentation_matches_runtime_contracts() -> None:
    plan = PLAN.read_text(encoding="utf-8")
    task6 = plan.split("## Task 6:", 1)[1].split("## Task 7:", 1)[0]
    design = DESIGN.read_text(encoding="utf-8")
    boundary = design.split("### 5.2 Docker Compose 边界", 1)[1].split("### 5.3", 1)[0]
    assert "Temporal Server auto-setup" not in plan
    assert "temporalio/auto-setup" not in task6
    assert "temporalio/server:1.29.7" in task6
    assert "temporalio/admin-tools:1.29.7" in task6
    assert all(word in task6 for word in ("schema", "namespace", "role-sync"))
    assert "temporalio/server" in boundary and "temporalio/admin-tools" in boundary
    assert "*_FILE" in boundary
    assert "database" in boundary and "temporal-metrics" in boundary
    for contract in (
        "rotate-exporter-password.sh", ">= 2.17", "pg_up",
        "namespace update", "NOBYPASSRLS", "DROP ROLE", "project", "0700",
        "0444", "bind", "--wait-timeout",
    ):
        assert contract in task6
    assert "0600" not in task6
    assert "0600" not in boundary


def test_monitoring_asset_files_exist() -> None:
    for path in (
        PROMETHEUS_CONFIG,
        PROMETHEUS_RULES,
        GRAFANA_DATASOURCE,
        GRAFANA_PROVIDER,
        GRAFANA_DASHBOARD,
        RELAY_TEMPLATE,
        RELAY_SCRIPT,
        PROMETHEUS_RULE_TEST,
    ):
        assert path.is_file(), path.relative_to(ROOT)


def test_prometheus_intervals_rules_and_scrape_targets_are_fixed() -> None:
    config = load_yaml(PROMETHEUS_CONFIG)
    assert config["global"] == {
        "scrape_interval": "15s",
        "evaluation_interval": "15s",
    }
    assert config["rule_files"] == ["/etc/prometheus/rules/*.yml"]
    jobs = {job["job_name"]: job for job in config["scrape_configs"]}
    expected_targets = {
        "prometheus": ["prometheus:9090"],
        "temporal-server": ["temporal:8000"],
        "temporal-postgres": ["postgres-exporter:9187"],
    }
    assert set(jobs) == set(expected_targets) | {"medchat-temporal-worker"}
    for name, targets in expected_targets.items():
        assert jobs[name]["static_configs"] == [{"targets": targets}]
    worker = jobs["medchat-temporal-worker"]
    assert "static_configs" not in worker
    assert worker["file_sd_configs"] == [{
        "files": ["/etc/prometheus/file_sd/worker-targets.json"],
        "refresh_interval": "30s",
    }]
    assert "host.docker.internal" not in PROMETHEUS_CONFIG.read_text(encoding="utf-8")


def test_compose_exposes_metrics_only_over_segmented_monitoring_networks() -> None:
    compose = load_yaml(COMPOSE)
    services = compose["services"]
    assert env_map(services["temporal"])["PROMETHEUS_ENDPOINT"] == "0.0.0.0:8000"
    assert all(not str(port).endswith(":8000") for port in services["temporal"]["ports"])
    assert "extra_hosts" not in services["prometheus"]
    assert set(services["prometheus"]["networks"]) == {
        "temporal-metrics",
        "postgres-metrics",
        "prometheus-grafana",
    }
    assert set(services["grafana"]["networks"]) == {"prometheus-grafana"}
    assert "temporal-metrics" in services["temporal"]["networks"]
    assert "prometheus-grafana" not in services["temporal"]["networks"]
    assert "monitoring" not in compose["networks"]
    assert "worker-metrics-scrape" not in compose["networks"]
    assert all("worker-metrics-scrape" not in service.get("networks", [])
               for service in services.values())
    assert all("name" not in (value or {}) for value in compose["networks"].values())


def test_nginx_relay_template_has_exact_path_acl_and_loopback_upstream() -> None:
    template = RELAY_TEMPLATE.read_text(encoding="utf-8")
    assert "listen ${RELAY_GATEWAY}:${RELAY_PORT};" in template
    assert "location = /metrics" in template
    assert "allow ${PROMETHEUS_ADDRESS};" in template
    assert "deny all;" in template
    assert "proxy_pass http://127.0.0.1:9465/metrics;" in template
    assert "proxy_connect_timeout 2s;" in template
    assert "proxy_read_timeout 5s;" in template
    assert 'proxy_set_header Authorization "";' in template
    assert 'proxy_set_header Cookie "";' in template
    assert "location / { return 404; }" in template
    assert template.count("location = /metrics") == 1
    assert "location /metrics/" not in template


def _alert_rules() -> dict[str, dict[str, Any]]:
    rules = load_yaml(PROMETHEUS_RULES)
    assert isinstance(rules.get("groups"), list) and rules["groups"]
    flattened = [rule for group in rules["groups"] for rule in group["rules"]]
    assert len(flattened) == 11
    return {rule["alert"]: rule for rule in flattened}


def _compact_promql(expression: Any) -> str:
    return re.sub(r"\s+", "", str(expression))


def test_release_blocking_alerts_have_exact_names_safe_metadata_and_durations() -> None:
    alerts = _alert_rules()
    expected_for = {
        "TemporalWorkerHeartbeatStale": "60s",
        "TemporalQueueBacklogGrowing": "10m",
        "TemporalWorkflowStartUnexpectedErrors": "5m",
        "TemporalDuplicateVinaExecution": None,
        "TemporalMultipleTerminalEvents": None,
        "TemporalArtifactValidationFailure": None,
        "TemporalDockingP95TooHigh": "10m",
        "TemporalDockingP95BaselineMissing": None,
        "TemporalRuntimeFailureRateHigh": "10m",
        "TemporalPostgresUnavailable": "60s",
        "TemporalBackupVerificationStale": "15m",
    }
    assert set(alerts) == set(expected_for)
    forbidden = (
        "task_id", "trace_id", "workflow_id", "prompt", "smiles", "secret",
        "password", "file_path", "c:\\", "/home/", "/users/",
    )
    for name, rule in alerts.items():
        assert rule["labels"] == {"severity": "critical", "release_blocker": "true"}
        assert set(rule["annotations"]) == {"summary", "description"}
        assert all(str(value).strip() for value in rule["annotations"].values())
        serialized = json.dumps(rule, ensure_ascii=True).lower()
        assert all(word not in serialized for word in forbidden)
        if expected_for[name] is None:
            assert "for" not in rule
        else:
            assert rule["for"] == expected_for[name]


def test_alert_promql_uses_pinned_temporal_and_task5_metrics_correctly() -> None:
    alerts = _alert_rules()

    heartbeat = _compact_promql(alerts["TemporalWorkerHeartbeatStale"]["expr"])
    heartbeat_metric = "medchat_temporal_worker_last_heartbeat_timestamp_seconds"
    assert f"time()-{heartbeat_metric}>60" in heartbeat
    assert f"absent({heartbeat_metric})" in heartbeat

    baseline_missing = _compact_promql(
        alerts["TemporalDockingP95BaselineMissing"]["expr"]
    )
    assert "absent(medchat_temporal_baseline_p95_seconds)" in baseline_missing
    assert "max(medchat_temporal_baseline_p95_seconds)<=0" in baseline_missing

    # Temporal v1.29.7 source evidence:
    # https://github.com/temporalio/temporal/blob/v1.29.7/common/metrics/metric_defs.go
    backlog = _compact_promql(alerts["TemporalQueueBacklogGrowing"]["expr"])
    assert (
        'deriv(approximate_backlog_count{namespace="default",'
        'taskqueue="medchat-docking"}[10m])>0'
    ) in backlog
    assert "task_queue" not in backlog
    assert "increase(approximate_backlog_count" not in backlog
    starts = _compact_promql(
        alerts["TemporalWorkflowStartUnexpectedErrors"]["expr"]
    )
    assert (
        'increase(service_errors{service_name="frontend",namespace="default",'
        'operation="StartWorkflowExecution"}[15m])>0'
    ) in starts

    hard_invariants = {
        "TemporalDuplicateVinaExecution": (
            "medchat_temporal_duplicate_vina_execution_total"
        ),
        "TemporalMultipleTerminalEvents": (
            "medchat_temporal_terminal_invariant_violation_total"
        ),
        "TemporalArtifactValidationFailure": (
            "medchat_temporal_artifact_validation_failure_total"
        ),
    }
    for name, metric in hard_invariants.items():
        assert f"increase({metric}[15m])>0" in _compact_promql(alerts[name]["expr"])

    p95 = _compact_promql(alerts["TemporalDockingP95TooHigh"]["expr"])
    quantile = (
        "histogram_quantile(0.95,sumby(le)(rate("
        "medchat_temporal_task_duration_seconds_bucket[15m])))"
    )
    assert (
        f"{quantile}>scalar(max(medchat_temporal_baseline_p95_seconds))*1.5"
        in p95
    )
    assert f"{quantile}>60" in p95
    assert "or" in p95

    failure_rate = _compact_promql(alerts["TemporalRuntimeFailureRateHigh"]["expr"])
    assert 'medchat_temporal_task_terminal_total{status!="succeeded"}' in failure_rate
    assert (
        'medchat_temporal_task_terminal_total{status!="succeeded"}[30m]'
        in failure_rate
    )
    assert "medchat_temporal_task_terminal_total[30m]" in failure_rate
    assert "clamp_min(" in failure_rate and ",1)" in failure_rate
    assert ">0.05" in failure_rate

    unavailable = _compact_promql(alerts["TemporalPostgresUnavailable"]["expr"])
    for job in ("temporal-server", "temporal-postgres"):
        assert f'absent(up{{job="{job}"}})' in unavailable
        assert f'up{{job="{job}"}}!=1' in unavailable
    assert 'absent(pg_up{job="temporal-postgres"})' in unavailable
    assert 'pg_up{job="temporal-postgres"}!=1' in unavailable

    backup = _compact_promql(alerts["TemporalBackupVerificationStale"]["expr"])
    backup_metric = "medchat_temporal_backup_verified_timestamp_seconds"
    assert f"absent({backup_metric})" in backup
    assert f"{backup_metric}==0" in backup
    assert f"time()-{backup_metric}>86400" in backup


def test_pinned_temporal_1_29_7_backlog_metric_contract_is_explicit() -> None:
    # Primary sources pinned to the deployed image tag:
    # https://github.com/temporalio/temporal/blob/v1.29.7/common/metrics/tags.go
    # https://github.com/temporalio/temporal/blob/v1.29.7/common/metrics/metric_defs.go
    # https://github.com/temporalio/temporal/blob/v1.29.7/common/dynamicconfig/constants.go
    pinned_contract = {
        "task_queue_tag": "taskqueue",
        "namespace_tag": "namespace",
        "service_name_tag": "service_name",
        "backlog_metric": "approximate_backlog_count",
        "metrics.breakdownByTaskQueue": True,
        "metrics.breakdownByPartition": True,
    }
    assert pinned_contract["task_queue_tag"] == "taskqueue"
    assert pinned_contract["metrics.breakdownByTaskQueue"] is True
    assert pinned_contract["metrics.breakdownByPartition"] is True
    temporal = load_yaml(COMPOSE)["services"]["temporal"]
    assert temporal["image"] == "temporalio/server:1.29.7"
    assert "DYNAMIC_CONFIG_FILE_PATH" not in env_map(temporal)
    serialized_rules = PROMETHEUS_RULES.read_text(encoding="utf-8")
    assert 'taskqueue="medchat-docking"' in serialized_rules
    assert "task_queue=" not in serialized_rules


def test_grafana_provisioning_is_stable_read_only_and_prometheus_backed() -> None:
    datasource = load_yaml(GRAFANA_DATASOURCE)
    assert datasource["apiVersion"] == 1
    assert datasource.get("deleteDatasources", []) == []
    assert datasource["datasources"] == [{
        "name": "Prometheus",
        "type": "prometheus",
        "uid": "medchat-prometheus",
        "access": "proxy",
        "url": "http://prometheus:9090",
        "isDefault": True,
        "editable": False,
    }]
    provider = load_yaml(GRAFANA_PROVIDER)
    assert provider["apiVersion"] == 1
    assert provider["providers"] == [{
        "name": "medchat-temporal",
        "orgId": 1,
        "folder": "MedChat",
        "type": "file",
        "disableDeletion": True,
        "editable": False,
        "updateIntervalSeconds": 30,
        "options": {
            "path": "/var/lib/grafana/dashboards",
            "foldersFromFilesStructure": False,
        },
    }]


def test_dashboard_has_complete_panels_real_metrics_and_no_sensitive_fields() -> None:
    dashboard = json.loads(GRAFANA_DASHBOARD.read_text(encoding="utf-8"))
    assert dashboard["uid"] == "medchat-temporal-docking"
    assert dashboard["refresh"] == "15s"
    assert dashboard["schemaVersion"] >= 39
    assert dashboard["editable"] is False
    panels = dashboard["panels"]
    expected_titles = {
        "Canary level",
        "Worker readiness",
        "Worker heartbeat",
        "Queue backlog",
        "Workflow start requests",
        "Terminal distribution",
        "p50 / p95 latency",
        "Vina attempts",
        "Duplicate prevention",
        "Terminal invariants",
        "Artifact validation",
        "Provenance gate",
        "Scientific gates",
        "Firing release blockers",
    }
    assert {panel["title"] for panel in panels} == expected_titles
    queries = [target["expr"] for panel in panels for target in panel["targets"]]
    assert all(target.get("datasource", {}).get("uid") == "medchat-prometheus"
               for panel in panels for target in panel["targets"])
    allowed_metrics = {
        "approximate_backlog_count",
        "service_requests",
        "ALERTS",
        "medchat_temporal_worker_ready",
        "medchat_temporal_worker_last_heartbeat_timestamp_seconds",
        "medchat_temporal_canary_percent",
        "medchat_temporal_task_terminal_total",
        "medchat_temporal_task_duration_seconds_bucket",
        "medchat_temporal_docking_process_attempts_total",
        "medchat_temporal_duplicate_vina_execution_total",
        "medchat_temporal_terminal_invariant_violation_total",
        "medchat_temporal_artifact_validation_failure_total",
        "medchat_temporal_provenance_validation_failure_total",
    }
    query_text = "\n".join(queries)
    metric_like = set(re.findall(r"\b[A-Za-z][A-Za-z0-9_:]*_[A-Za-z0-9_:]+\b", query_text))
    promql_words = {
        "alertstate", "histogram_quantile", "release_blocker", "service_name",
        "task_type",
    }
    assert metric_like - promql_words <= allowed_metrics
    assert allowed_metrics <= set(re.findall(
        r"\b(?:ALERTS|[A-Za-z][A-Za-z0-9_:]*_[A-Za-z0-9_:]+)\b",
        query_text,
    ))
    queries_by_title = {
        panel["title"]: [target["expr"] for target in panel["targets"]]
        for panel in panels
    }
    assert queries_by_title["Queue backlog"] == [
        'sum(approximate_backlog_count{namespace="default",taskqueue="medchat-docking"})'
    ]
    assert queries_by_title["Workflow start requests"] == [
        'sum(increase(service_requests{service_name="frontend",namespace="default",'
        'operation="StartWorkflowExecution"}[5m]))'
    ]
    assert queries_by_title["Firing release blockers"] == [
        'count(ALERTS{alertstate="firing",release_blocker="true"}) or vector(0)'
    ]
    assert "task_queue" not in query_text
    serialized = json.dumps(dashboard, ensure_ascii=True).lower()
    for forbidden in (
        "smiles", "prompt", "user_id", "task_id", "trace_id", "workflow_id",
        "file_path", "secret", "password", "c:\\", "/home/", "/users/",
    ):
        assert forbidden not in serialized


def test_monitoring_assets_parse_without_embedded_credentials_or_host_paths() -> None:
    for path in (PROMETHEUS_CONFIG, PROMETHEUS_RULES, GRAFANA_DATASOURCE, GRAFANA_PROVIDER):
        assert isinstance(load_yaml(path), dict)
    assert isinstance(json.loads(GRAFANA_DASHBOARD.read_text(encoding="utf-8")), dict)
    combined = "\n".join(
        path.read_text(encoding="utf-8")
        for path in (
            PROMETHEUS_CONFIG,
            PROMETHEUS_RULES,
            GRAFANA_DATASOURCE,
            GRAFANA_PROVIDER,
            GRAFANA_DASHBOARD,
        )
    ).lower()
    for forbidden in (
        "password:", "api_key", "apikey", "bearer_token", "client_secret",
        "c:\\users\\", "/home/", "/users/",
    ):
        assert forbidden not in combined


def test_promtool_fixture_covers_scopes_windows_and_availability_grace() -> None:
    fixture = load_yaml(PROMETHEUS_RULE_TEST)
    assert fixture["rule_files"] == ["../rules/medchat-temporal.yml"]
    names = {case["name"] for case in fixture["tests"]}
    assert names == {
        "labeled baseline p95 fires",
        "missing baseline blocks release",
        "scoped docking queue grows",
        "unrelated queue does not fire",
        "low frequency start error persists",
        "low frequency runtime failure persists",
        "one scrape gap is tolerated",
        "sustained unavailability fires",
    }
    serialized = PROMETHEUS_RULE_TEST.read_text(encoding="utf-8")
    assert 'taskqueue="medchat-docking"' in serialized
    assert 'taskqueue="unrelated"' in serialized
    assert "task_queue=" not in serialized
    assert "medchat_temporal_baseline_p95_seconds" in serialized
    assert "service_errors" in serialized
    assert "medchat_temporal_task_terminal_total" in serialized
    assert 'alertname: TemporalPostgresUnavailable' in serialized


def test_promtool_executes_temporal_rule_fixture_when_available() -> None:
    promtool = shutil.which("promtool")
    if promtool is None:
        pytest.skip("promtool command unavailable")
    result = subprocess.run(
        [promtool, "test", "rules", str(PROMETHEUS_RULE_TEST)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    assert result.returncode == 0, (
        "promtool test rules returned nonzero:\n"
        f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )


def test_metrics_relay_integration_is_explicit_safe_and_test_labeled() -> None:
    integration = INTEGRATION_TEST.read_text(encoding="utf-8")
    assert 'MEDCHAT_RUN_TEMPORAL_DEPLOYMENT_INTEGRATION' in integration
    assert 'sys.platform != "linux"' in integration
    assert "os.geteuid() != 0" in integration
    assert 'shutil.which("nginx")' in integration
    assert 'com.medchat.temporal.metrics-relay-test' in integration
    assert 'up{job="medchat-temporal-worker"}' in integration
    assert "non-Prometheus relay access was not denied" in integration
    assert "localhost relay unexpectedly reachable" in integration
    assert "cleanup refused unlabeled" in integration
    assert "cleanup inspect failed" in integration
    assert "compose.override.yml" in integration
    assert "for relay_port in (9466, 9467)" in integration
    assert "activate_generation(" in integration
    assert "com.docker.compose.project" in integration
    assert "com.docker.compose.network" in integration
    assert "errno.EADDRINUSE" in integration
    assert "loopback worker metrics port unavailable" not in integration
    assert "network_exists = docker_run" in integration
    assert "container_exists = docker_run" in integration
