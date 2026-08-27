"""Cross-boundary security and scientific failure contracts for OpenSandbox."""

from __future__ import annotations

import asyncio
import ast
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


_PROMETHEUS_SERVER_CALLS = {
    "start_http_server",
    "start_wsgi_server",
}
_LISTENER_CALLS = {
    "asyncio.start_server",
    "asyncio.start_unix_server",
    "http.server.HTTPServer",
    "socket.create_server",
    "socket.socket.bind",
    "socket.socket.listen",
    "socketserver.TCPServer",
    "uvicorn.Config",
    "uvicorn.Server",
    "uvicorn.Server.run",
    "uvicorn.run",
    "wsgiref.simple_server.make_server",
}
_PROTECTED_SOCKET_FAMILY_ATTRIBUTES = {"AF_INET", "AF_UNIX"}


def _normalize_symbol(symbol: str) -> str:
    if symbol.startswith("prometheus_client."):
        leaf = symbol.rsplit(".", 1)[-1]
        if leaf in _PROMETHEUS_SERVER_CALLS:
            return f"prometheus_client.{leaf}"
    return symbol


def _resolved_aliases(tree: ast.Module) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for imported in node.names:
                aliases[imported.asname or imported.name] = imported.name
        elif isinstance(node, ast.ImportFrom) and node.module:
            for imported in node.names:
                aliases[imported.asname or imported.name] = _normalize_symbol(
                    f"{node.module}.{imported.name}"
                )

    def resolve(node: ast.expr) -> str:
        if isinstance(node, ast.Name):
            symbol = node.id
            visited: set[str] = set()
            while symbol in aliases and symbol not in visited:
                visited.add(symbol)
                symbol = aliases[symbol]
            return _normalize_symbol(symbol)
        if isinstance(node, ast.Attribute):
            owner = resolve(node.value)
            symbol = f"{owner}.{node.attr}" if owner else node.attr
            return _normalize_symbol(symbol)
        if isinstance(node, ast.Call):
            return resolve(node.func)
        return ""

    assignments: list[tuple[str, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            assignments.extend(
                (target.id, node.value)
                for target in node.targets
                if isinstance(target, ast.Name)
            )
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assignments.append((node.target.id, node.value))
        elif isinstance(node, ast.ClassDef) and len(node.bases) == 1:
            assignments.append((node.name, node.bases[0]))

    for _ in range(len(assignments) + 1):
        changed = False
        for target, value in assignments:
            resolved = resolve(value)
            if resolved and resolved != target and aliases.get(target) != resolved:
                aliases[target] = resolved
                changed = True
        if not changed:
            break
    return aliases


def _resolve_symbol(node: ast.expr, aliases: dict[str, str]) -> str:
    if isinstance(node, ast.Name):
        symbol = node.id
        visited: set[str] = set()
        while symbol in aliases and symbol not in visited:
            visited.add(symbol)
            symbol = aliases[symbol]
        return _normalize_symbol(symbol)
    if isinstance(node, ast.Attribute):
        owner = _resolve_symbol(node.value, aliases)
        symbol = f"{owner}.{node.attr}" if owner else node.attr
        return _normalize_symbol(symbol)
    if isinstance(node, ast.Call):
        return _resolve_symbol(node.func, aliases)
    return ""


def _resolved_string_constants(tree: ast.Module) -> dict[str, str]:
    constants: dict[str, str] = {}
    assignments: list[tuple[str, ast.expr]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            assignments.extend(
                (target.id, node.value)
                for target in node.targets
                if isinstance(target, ast.Name)
            )
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.value is not None
        ):
            assignments.append((node.target.id, node.value))

    for _ in range(len(assignments) + 1):
        changed = False
        for target, value in assignments:
            resolved: str | None = None
            if isinstance(value, ast.Constant) and type(value.value) is str:
                resolved = value.value
            elif isinstance(value, ast.Name):
                resolved = constants.get(value.id)
            if resolved is not None and constants.get(target) != resolved:
                constants[target] = resolved
                changed = True
        if not changed:
            break
    return constants


def _constant_string(
    node: ast.expr,
    constants: dict[str, str],
) -> str | None:
    if isinstance(node, ast.Constant) and type(node.value) is str:
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id)
    return None


def _namespace_lookup(
    node: ast.expr,
    aliases: dict[str, str],
    constants: dict[str, str],
) -> str | None:
    if not isinstance(node, ast.Subscript):
        return None
    if _resolve_symbol(node.value, aliases) not in {"globals", "locals"}:
        return None
    return _constant_string(node.slice, constants)


def _is_socket_module_reference(
    node: ast.expr,
    aliases: dict[str, str],
    constants: dict[str, str],
) -> bool:
    return (
        _resolve_symbol(node, aliases) == "socket"
        or _namespace_lookup(node, aliases, constants) == "socket"
    )


def _is_socket_namespace(
    node: ast.expr,
    aliases: dict[str, str],
    constants: dict[str, str],
) -> bool:
    if _resolve_symbol(node, aliases) == "socket.__dict__":
        return True
    return bool(
        isinstance(node, ast.Attribute)
        and node.attr == "__dict__"
        and _is_socket_module_reference(node.value, aliases, constants)
    )


def _mutates_socket_family_target(
    target: ast.expr,
    aliases: dict[str, str],
    constants: dict[str, str],
) -> bool:
    if isinstance(target, (ast.Tuple, ast.List)):
        return any(
            _mutates_socket_family_target(element, aliases, constants)
            for element in target.elts
        )
    if isinstance(target, ast.Starred):
        return _mutates_socket_family_target(target.value, aliases, constants)
    if isinstance(target, ast.Attribute):
        return (
            target.attr in _PROTECTED_SOCKET_FAMILY_ATTRIBUTES
            and _is_socket_module_reference(target.value, aliases, constants)
        )
    if not isinstance(target, ast.Subscript):
        return False

    key = _constant_string(target.slice, constants)
    if key in {"_AF_UNIX", "socket"} and _resolve_symbol(
        target.value,
        aliases,
    ) in {"globals", "locals"}:
        return True
    return bool(
        key in _PROTECTED_SOCKET_FAMILY_ATTRIBUTES
        and (
            _is_socket_namespace(target.value, aliases, constants)
            or _is_socket_module_reference(target.value, aliases, constants)
        )
    )


def _mutates_socket_family_call(
    call: ast.Call,
    aliases: dict[str, str],
    constants: dict[str, str],
) -> bool:
    symbol = _resolve_symbol(call.func, aliases)
    leaf = symbol.rsplit(".", 1)[-1]
    if leaf in {"setattr", "delattr"} and len(call.args) >= 2:
        return bool(
            _is_socket_module_reference(call.args[0], aliases, constants)
            and _constant_string(call.args[1], constants)
            in _PROTECTED_SOCKET_FAMILY_ATTRIBUTES
        )
    if leaf in {"__setattr__", "__delattr__"} and call.args:
        owner = call.func.value if isinstance(call.func, ast.Attribute) else None
        return bool(
            owner is not None
            and _is_socket_module_reference(owner, aliases, constants)
            and _constant_string(call.args[0], constants)
            in _PROTECTED_SOCKET_FAMILY_ATTRIBUTES
        )
    return False


def _has_protected_socket_family_mutation(
    tree: ast.Module,
    aliases: dict[str, str],
) -> bool:
    constants = _resolved_string_constants(tree)
    for node in ast.walk(tree):
        targets: list[ast.expr] = []
        if isinstance(node, ast.Assign):
            targets.extend(node.targets)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)):
            targets.append(node.target)
        elif isinstance(node, ast.Delete):
            targets.extend(node.targets)
        if any(
            _mutates_socket_family_target(target, aliases, constants)
            for target in targets
        ):
            return True
        if isinstance(node, ast.Call) and _mutates_socket_family_call(
            node,
            aliases,
            constants,
        ):
            return True
    return False


def _resolved_python_calls(source: str) -> tuple[ast.Module, list[str]]:
    tree = ast.parse(source)
    aliases = _resolved_aliases(tree)
    return tree, [
        _resolve_symbol(node.func, aliases)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
    ]


def _environment_names(tree: ast.Module) -> set[str]:
    aliases = _resolved_aliases(tree)
    constants: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            if isinstance(node.value, ast.Constant) and isinstance(node.value.value, str):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = node.value.value
            elif isinstance(node.value, ast.Name) and node.value.id in constants:
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = constants[node.value.id]

    def constant_string(node: ast.expr) -> str | None:
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            return node.value
        if isinstance(node, ast.Name):
            return constants.get(node.id)
        return None

    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and node.args:
            if _resolve_symbol(node.func, aliases) in {
                "os.environ.get",
                "os.getenv",
            }:
                value = constant_string(node.args[0])
                if value is not None:
                    names.add(value)
        elif isinstance(node, ast.Subscript):
            if _resolve_symbol(node.value, aliases) == "os.environ":
                value = constant_string(node.slice)
                if value is not None:
                    names.add(value)
    return names


def _call_records(
    tree: ast.Module,
    aliases: dict[str, str],
) -> list[tuple[ast.Call, str, str | None]]:
    records: list[tuple[ast.Call, str, str | None]] = []

    class CallVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.functions: list[str] = []

        def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
            self.functions.append(node.name)
            self.generic_visit(node)
            self.functions.pop()

        def visit_Call(self, node: ast.Call) -> None:
            records.append(
                (
                    node,
                    _resolve_symbol(node.func, aliases),
                    self.functions[-1] if self.functions else None,
                )
            )
            self.generic_visit(node)

    CallVisitor().visit(tree)
    return records


def _is_name(node: ast.expr, name: str) -> bool:
    return isinstance(node, ast.Name) and node.id == name


def _is_attribute(node: ast.expr, owner: str, attribute: str) -> bool:
    return (
        isinstance(node, ast.Attribute)
        and node.attr == attribute
        and _is_name(node.value, owner)
    )


def _exact_keywords(call: ast.Call) -> dict[str, ast.expr] | None:
    if any(keyword.arg is None for keyword in call.keywords):
        return None
    values = {keyword.arg: keyword.value for keyword in call.keywords}
    return values if len(values) == len(call.keywords) else None


def _import_bindings(node: ast.AST) -> list[tuple[str, ast.alias]]:
    bindings: list[tuple[str, ast.alias]] = []
    if isinstance(node, ast.Import):
        bindings.extend(
            (imported.asname or imported.name.split(".", 1)[0], imported)
            for imported in node.names
        )
    elif isinstance(node, ast.ImportFrom):
        bindings.extend(
            (imported.asname or imported.name, imported)
            for imported in node.names
        )
    return bindings


def _name_has_unapproved_binding(
    tree: ast.Module,
    name: str,
    *,
    allowed_name_node: ast.Name | None = None,
    allowed_import: ast.alias | None = None,
) -> bool:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Name)
            and node.id == name
            and isinstance(node.ctx, (ast.Store, ast.Del))
            and node is not allowed_name_node
        ):
            return True
        if isinstance(node, ast.arg) and node.arg == name:
            return True
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            if node.name == name:
                return True
        if isinstance(node, (ast.Global, ast.Nonlocal)) and name in node.names:
            return True
        if isinstance(node, ast.ExceptHandler) and node.name == name:
            return True
        if isinstance(node, (ast.MatchAs, ast.MatchStar)) and node.name == name:
            return True
        if isinstance(node, ast.MatchMapping) and node.rest == name:
            return True
        for bound_name, imported in _import_bindings(node):
            if bound_name == name and imported is not allowed_import:
                return True
    return False


def _has_verified_af_unix_binding(tree: ast.Module) -> bool:
    candidates = [
        (index, statement)
        for index, statement in enumerate(tree.body)
        if isinstance(statement, ast.Assign)
        and len(statement.targets) == 1
        and _is_name(statement.targets[0], "_AF_UNIX")
    ]
    if len(candidates) != 1:
        return False
    assignment_index, assignment = candidates[0]
    target = assignment.targets[0]
    if not isinstance(target, ast.Name):  # narrowed by _is_name above
        return False

    value = assignment.value
    if not (
        isinstance(value, ast.Call)
        and _is_name(value.func, "getattr")
        and len(value.args) == 3
        and not value.keywords
        and _is_name(value.args[0], "socket")
        and isinstance(value.args[1], ast.Constant)
        and type(value.args[1].value) is str
        and value.args[1].value == "AF_UNIX"
        and isinstance(value.args[2], ast.Constant)
        and type(value.args[2].value) is int
        and value.args[2].value == 1
    ):
        return False

    socket_imports = [
        (index, imported)
        for index, statement in enumerate(tree.body)
        for bound_name, imported in _import_bindings(statement)
        if bound_name == "socket"
    ]
    if len(socket_imports) != 1:
        return False
    socket_import_index, socket_import = socket_imports[0]
    if not (
        socket_import_index < assignment_index
        and socket_import.name == "socket"
        and socket_import.asname is None
    ):
        return False

    return not any(
        (
            _name_has_unapproved_binding(
                tree,
                "_AF_UNIX",
                allowed_name_node=target,
            ),
            _name_has_unapproved_binding(
                tree,
                "socket",
                allowed_import=socket_import,
            ),
            _name_has_unapproved_binding(tree, "getattr"),
        )
    )


def _allowed_entrypoint_call(
    call: ast.Call,
    symbol: str,
    function: str | None,
) -> bool:
    if symbol == "socket.socket":
        expected_socket_type = {
            "_socket_stale_candidate": "SOCK_STREAM",
            "bind_runtime_socket": "SOCK_STREAM",
            "_notify_systemd_ready": "SOCK_DGRAM",
        }.get(function)
        return bool(
            expected_socket_type
            and _is_attribute(call.func, "socket", "socket")
            and len(call.args) == 2
            and not call.keywords
            and _is_name(call.args[0], "_AF_UNIX")
            and _is_attribute(call.args[1], "socket", expected_socket_type)
        )
    if symbol == "socket.socket.bind":
        return bool(
            function == "bind_runtime_socket"
            and _is_attribute(call.func, "listener", "bind")
            and len(call.args) == 1
            and not call.keywords
            and isinstance(call.args[0], ast.Call)
            and _is_name(call.args[0].func, "str")
            and len(call.args[0].args) == 1
            and _is_name(call.args[0].args[0], "target")
            and not call.args[0].keywords
        )
    if symbol == "uvicorn.Config":
        keywords = _exact_keywords(call)
        return bool(
            function == "serve_application"
            and _is_attribute(call.func, "uvicorn", "Config")
            and len(call.args) == 1
            and _is_name(call.args[0], "application")
            and keywords is not None
            and set(keywords) == {"workers", "access_log", "log_level"}
            and isinstance(keywords["workers"], ast.Constant)
            and keywords["workers"].value == 1
            and isinstance(keywords["access_log"], ast.Constant)
            and keywords["access_log"].value is False
            and isinstance(keywords["log_level"], ast.Constant)
            and keywords["log_level"].value == "info"
        )
    if symbol == "uvicorn.Server":
        keywords = _exact_keywords(call)
        return bool(
            function == "serve_application"
            and _is_name(call.func, "_ReadyServer")
            and len(call.args) == 1
            and _is_name(call.args[0], "configuration")
            and keywords is not None
            and set(keywords) == {"readiness_callback"}
            and _is_name(keywords["readiness_callback"], "_notify_systemd_ready")
        )
    if symbol == "uvicorn.Server.run":
        keywords = _exact_keywords(call)
        return bool(
            function == "serve_application"
            and _is_attribute(call.func, "server", "run")
            and not call.args
            and keywords is not None
            and set(keywords) == {"sockets"}
            and isinstance(keywords["sockets"], ast.List)
            and len(keywords["sockets"].elts) == 1
            and _is_name(keywords["sockets"].elts[0], "listener")
        )
    return False


def _listener_contract_violations(
    source: str,
    *,
    allow_entrypoint_uds: bool = False,
) -> set[str]:
    tree = ast.parse(source)
    aliases = _resolved_aliases(tree)
    violations: set[str] = set()
    if allow_entrypoint_uds and not _has_verified_af_unix_binding(tree):
        violations.add("entrypoint._AF_UNIX")
    if _has_protected_socket_family_mutation(tree, aliases):
        violations.add(
            "entrypoint.socket_family" if allow_entrypoint_uds else "socket_family"
        )
    for call, symbol, function in _call_records(tree, aliases):
        forbidden = (
            symbol in _LISTENER_CALLS
            or symbol in {
                "prometheus_client.start_http_server",
                "prometheus_client.start_wsgi_server",
            }
            or symbol == "socket.socket"
        )
        if forbidden and not (
            allow_entrypoint_uds
            and _allowed_entrypoint_call(call, symbol, function)
        ):
            violations.add(symbol)
    return violations


def _entrypoint_allowlist_counts(source: str) -> dict[str, int]:
    tree = ast.parse(source)
    aliases = _resolved_aliases(tree)
    counts: dict[str, int] = {}
    for call, symbol, function in _call_records(tree, aliases):
        if _allowed_entrypoint_call(call, symbol, function):
            key = f"{function}:{symbol}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def test_broker_metrics_cannot_start_a_second_listener_or_use_tcp_configuration() -> None:
    root = Path(__file__).resolve().parents[2]
    entrypoint = root / "scripts" / "run_sandbox_broker.py"
    broker_sources = [
        *sorted((root / "src" / "sandbox_broker").rglob("*.py")),
        entrypoint,
    ]
    observed_environment: set[str] = set()

    for path in broker_sources:
        if not path.is_file():
            continue
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source)
        violations = _listener_contract_violations(
            source,
            allow_entrypoint_uds=path == entrypoint,
        )
        assert not violations, (path, violations)
        observed_environment.update(_environment_names(tree))

    assert not {
        name
        for name in observed_environment
        if ("METRIC" in name.upper() or "PROMETHEUS" in name.upper())
        and ("HOST" in name.upper() or "PORT" in name.upper())
    }

    assert _entrypoint_allowlist_counts(entrypoint.read_text(encoding="utf-8")) == {
        "_notify_systemd_ready:socket.socket": 1,
        "_socket_stale_candidate:socket.socket": 1,
        "bind_runtime_socket:socket.socket": 1,
        "bind_runtime_socket:socket.socket.bind": 1,
        "serve_application:uvicorn.Config": 1,
        "serve_application:uvicorn.Server": 1,
        "serve_application:uvicorn.Server.run": 1,
    }


@pytest.mark.parametrize(
    ("source", "forbidden"),
    [
        (
            "import prometheus_client as metrics\nmetrics.start_http_server(9000)\n",
            "prometheus_client.start_http_server",
        ),
        (
            "from prometheus_client import start_wsgi_server as serve\nserve(9000)\n",
            "prometheus_client.start_wsgi_server",
        ),
        (
            "import socket as network\nnetwork.socket()\n",
            "socket.socket",
        ),
        (
            "from uvicorn import run as serve\nserve('module:app')\n",
            "uvicorn.run",
        ),
        (
            "from prometheus_client import start_http_server\n"
            "serve = start_http_server\n"
            "alias2 = serve\n"
            "alias2(9000)\n",
            "prometheus_client.start_http_server",
        ),
        (
            "import prometheus_client as metrics\n"
            "serve = metrics.exposition.start_http_server\n"
            "serve(9000)\n",
            "prometheus_client.start_http_server",
        ),
        (
            "from prometheus_client.exposition import start_wsgi_server as serve\n"
            "serve(9000)\n",
            "prometheus_client.start_wsgi_server",
        ),
        (
            "import socket\n"
            "listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "bind = listener.bind\n"
            "alias2 = bind\n"
            "alias2(('0.0.0.0', 9000))\n",
            "socket.socket.bind",
        ),
        (
            "import socket\n"
            "listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "listen = listener.listen\n"
            "alias2 = listen\n"
            "alias2(8)\n",
            "socket.socket.listen",
        ),
        (
            "import asyncio\n"
            "serve = asyncio.start_server\n"
            "alias2 = serve\n"
            "alias2(handler, '127.0.0.1', 9000)\n",
            "asyncio.start_server",
        ),
        (
            "import uvicorn\n"
            "serve = uvicorn.run\n"
            "alias2 = serve\n"
            "alias2('module:app', host='127.0.0.1', port=9000)\n",
            "uvicorn.run",
        ),
    ],
)
def test_listener_static_contract_resolves_import_aliases(
    source: str,
    forbidden: str,
) -> None:
    _, calls = _resolved_python_calls(source)
    assert forbidden in calls


@pytest.mark.parametrize(
    ("source", "environment_name"),
    [
        (
            "import os as operating_system\n"
            "operating_system.getenv('PROMETHEUS_METRICS_PORT')\n",
            "PROMETHEUS_METRICS_PORT",
        ),
        (
            "from os import getenv as read_environment\n"
            "read_environment('METRICS_HOST')\n",
            "METRICS_HOST",
        ),
        (
            "from os import environ as process_environment\n"
            "process_environment.get('METRICS_PORT')\n",
            "METRICS_PORT",
        ),
        (
            "import os\n"
            "read_environment = os.environ.get\n"
            "alias2 = read_environment\n"
            "name = 'PROMETHEUS_METRICS_HOST'\n"
            "alias2(name)\n",
            "PROMETHEUS_METRICS_HOST",
        ),
        (
            "from os import environ\n"
            "environment = environ\n"
            "name = 'METRICS_PORT'\n"
            "environment[name]\n",
            "METRICS_PORT",
        ),
    ],
)
def test_metrics_environment_static_contract_resolves_import_aliases(
    source: str,
    environment_name: str,
) -> None:
    assert environment_name in _environment_names(ast.parse(source))


@pytest.mark.parametrize(
    ("source", "violation"),
    [
        (
            "from prometheus_client import start_http_server\n"
            "serve = start_http_server\n"
            "alias2 = serve\n"
            "alias2(9000)\n",
            "prometheus_client.start_http_server",
        ),
        (
            "import prometheus_client as metrics\n"
            "serve = metrics.exposition.start_wsgi_server\n"
            "serve(9000)\n",
            "prometheus_client.start_wsgi_server",
        ),
        (
            "import socket\n"
            "listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "bind = listener.bind\n"
            "alias2 = bind\n"
            "alias2(('0.0.0.0', 9000))\n",
            "socket.socket.bind",
        ),
        (
            "import socket\n"
            "listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "listen = listener.listen\n"
            "alias2 = listen\n"
            "alias2(8)\n",
            "socket.socket.listen",
        ),
        (
            "import socket\n"
            "create = socket.create_server\n"
            "alias2 = create\n"
            "alias2(('127.0.0.1', 9000))\n",
            "socket.create_server",
        ),
        (
            "import asyncio\n"
            "serve = asyncio.start_server\n"
            "alias2 = serve\n"
            "alias2(handler, '127.0.0.1', 9000)\n",
            "asyncio.start_server",
        ),
        (
            "import uvicorn\n"
            "serve = uvicorn.run\n"
            "alias2 = serve\n"
            "alias2('module:app', host='127.0.0.1', port=9000)\n",
            "uvicorn.run",
        ),
        (
            "import uvicorn\n"
            "configuration = uvicorn.Config(application)\n"
            "build = uvicorn.Server\n"
            "server = build(configuration)\n"
            "launch = server.run\n"
            "launch()\n",
            "uvicorn.Server.run",
        ),
        (
            "import socket\n"
            "_AF_UNIX = socket.AF_UNIX\n"
            "def extra_listener(target):\n"
            "    listener = socket.socket(_AF_UNIX, socket.SOCK_STREAM)\n"
            "    listener.bind(str(target))\n",
            "socket.socket.bind",
        ),
    ],
)
def test_listener_contract_rejects_adversarial_source_fixtures(
    source: str,
    violation: str,
) -> None:
    assert violation in _listener_contract_violations(
        source,
        allow_entrypoint_uds=True,
    )


def test_listener_contract_allows_only_existing_prebound_uds_pattern() -> None:
    source = """
import socket
import uvicorn

_AF_UNIX = getattr(socket, "AF_UNIX", 1)

def _socket_stale_candidate(socket_path):
    client = socket.socket(_AF_UNIX, socket.SOCK_STREAM)

def bind_runtime_socket(socket_path):
    target = socket_path
    listener = socket.socket(_AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(target))
    return listener, (1, 2)

def _notify_systemd_ready():
    notifier = socket.socket(_AF_UNIX, socket.SOCK_DGRAM)

class _ReadyServer(uvicorn.Server):
    pass

def serve_application(application, socket_path):
    listener, bound_identity = bind_runtime_socket(socket_path)
    configuration = uvicorn.Config(
        application,
        workers=1,
        access_log=False,
        log_level="info",
    )
    server = _ReadyServer(
        configuration,
        readiness_callback=_notify_systemd_ready,
    )
    server.run(sockets=[listener])
"""

    assert not _listener_contract_violations(
        source,
        allow_entrypoint_uds=True,
    )
    assert _entrypoint_allowlist_counts(source) == {
        "_notify_systemd_ready:socket.socket": 1,
        "_socket_stale_candidate:socket.socket": 1,
        "bind_runtime_socket:socket.socket": 1,
        "bind_runtime_socket:socket.socket.bind": 1,
        "serve_application:uvicorn.Config": 1,
        "serve_application:uvicorn.Server": 1,
        "serve_application:uvicorn.Server.run": 1,
    }


def _mutate_af_unix_binding(source: str, mutation: str) -> str:
    approved = '_AF_UNIX = getattr(socket, "AF_UNIX", 1)'
    assert source.count(approved) == 1
    if mutation == "reviewer-probe":
        replacement = "_AF_UNIX = socket.AF_INET"
    elif mutation == "literal":
        replacement = "_AF_UNIX = 1"
    elif mutation == "imported-binding":
        replacement = "from socket import AF_UNIX as _AF_UNIX"
    elif mutation == "imported-socket-alias":
        replacement = (
            "import socket as network_socket\n"
            '_AF_UNIX = getattr(network_socket, "AF_UNIX", 1)'
        )
    elif mutation == "reassignment":
        replacement = f"{approved}\n_AF_UNIX = socket.AF_INET"
    elif mutation == "annotated-reassignment":
        replacement = f"{approved}\n_AF_UNIX: int = socket.AF_INET"
    elif mutation == "conditional-reassignment":
        replacement = (
            f"{approved}\n"
            "if os.name == 'posix':\n"
            "    _AF_UNIX = socket.AF_INET"
        )
    elif mutation in {"function-shadow", "named-expression-shadow"}:
        replacement = approved
    else:  # pragma: no cover - protects the parametrized test itself
        raise AssertionError(mutation)

    mutated = source.replace(approved, replacement, 1)
    listener_line = "        listener = socket.socket(_AF_UNIX, socket.SOCK_STREAM)"
    if mutation == "function-shadow":
        assert mutated.count(listener_line) == 1
        mutated = mutated.replace(
            listener_line,
            "        _AF_UNIX = socket.AF_INET\n" + listener_line,
            1,
        )
    elif mutation == "named-expression-shadow":
        assert mutated.count(listener_line) == 1
        mutated = mutated.replace(
            listener_line,
            "        (_AF_UNIX := socket.AF_INET)\n" + listener_line,
            1,
        )
    return mutated


@pytest.mark.parametrize(
    "mutation",
    [
        "reviewer-probe",
        "literal",
        "imported-binding",
        "imported-socket-alias",
        "reassignment",
        "annotated-reassignment",
        "conditional-reassignment",
        "function-shadow",
        "named-expression-shadow",
    ],
)
def test_entrypoint_uds_allowlist_rejects_af_unix_binding_mutations(
    mutation: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts" / "run_sandbox_broker.py").read_text(
        encoding="utf-8"
    )
    mutated = _mutate_af_unix_binding(source, mutation)

    assert "entrypoint._AF_UNIX" in _listener_contract_violations(
        mutated,
        allow_entrypoint_uds=True,
    )


def _mutate_socket_family(source: str, mutation: str) -> str:
    approved = '_AF_UNIX = getattr(socket, "AF_UNIX", 1)'
    assert source.count(approved) == 1
    mutations = {
        "reviewer-probe": "socket.AF_UNIX = socket.AF_INET",
        "annotated-attribute": "socket.AF_UNIX: int = socket.AF_INET",
        "augmented-attribute": "socket.AF_UNIX |= 1",
        "deleted-attribute": "del socket.AF_UNIX",
        "af-inet-target": "socket.AF_INET = 1",
        "transitive-module-alias": (
            "network = socket\n"
            "network_alias = network\n"
            "network_alias.AF_UNIX = socket.AF_INET"
        ),
        "setattr-direct": "setattr(socket, 'AF_UNIX', socket.AF_INET)",
        "setattr-transitive": (
            "network = socket\n"
            "network_alias = network\n"
            "mutate = setattr\n"
            "mutate_alias = mutate\n"
            "mutate_alias(network_alias, 'AF_UNIX', socket.AF_INET)"
        ),
        "delattr-transitive": (
            "network = socket\n"
            "remove = delattr\n"
            "remove_alias = remove\n"
            "remove_alias(network, 'AF_INET')"
        ),
        "monkeypatch-direct": (
            "monkeypatch.setattr(socket, 'AF_UNIX', socket.AF_INET)"
        ),
        "monkeypatch-transitive": (
            "patch = monkeypatch.setattr\n"
            "patch_alias = patch\n"
            "patch_alias(socket, 'AF_INET', 1)"
        ),
        "globals-socket": "globals()['socket'] = replacement_socket",
        "globals-af-unix": "globals()['_AF_UNIX'] = socket.AF_INET",
        "locals-socket": "locals()['socket'] = replacement_socket",
        "socket-dict": "socket.__dict__['AF_UNIX'] = socket.AF_INET",
        "aliased-socket-dict": (
            "network = socket\n"
            "namespace = network.__dict__\n"
            "namespace['AF_INET'] = 1"
        ),
        "deleted-socket-dict": "del socket.__dict__['AF_UNIX']",
    }
    return source.replace(
        approved,
        f"{mutations[mutation]}\n{approved}",
        1,
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "reviewer-probe",
        "annotated-attribute",
        "augmented-attribute",
        "deleted-attribute",
        "af-inet-target",
        "transitive-module-alias",
        "setattr-direct",
        "setattr-transitive",
        "delattr-transitive",
        "monkeypatch-direct",
        "monkeypatch-transitive",
        "globals-socket",
        "globals-af-unix",
        "locals-socket",
        "socket-dict",
        "aliased-socket-dict",
        "deleted-socket-dict",
    ],
)
def test_entrypoint_rejects_protected_socket_family_mutations(
    mutation: str,
) -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts" / "run_sandbox_broker.py").read_text(
        encoding="utf-8"
    )
    mutated = _mutate_socket_family(source, mutation)

    assert "entrypoint.socket_family" in _listener_contract_violations(
        mutated,
        allow_entrypoint_uds=True,
    )


def test_entrypoint_socket_family_guard_allows_benign_reads() -> None:
    root = Path(__file__).resolve().parents[2]
    source = (root / "scripts" / "run_sandbox_broker.py").read_text(
        encoding="utf-8"
    )
    approved = '_AF_UNIX = getattr(socket, "AF_UNIX", 1)'
    benign = """observed_family = socket.AF_UNIX
observed_fallback = getattr(socket, "AF_UNIX", 1)
socket_namespace = socket.__dict__
observed_from_namespace = socket_namespace["AF_UNIX"]
class Unrelated:
    pass
unrelated = Unrelated()
setattr(unrelated, "AF_UNIX", 1)
"""
    source = source.replace(approved, f"{benign}{approved}", 1)

    assert not _listener_contract_violations(
        source,
        allow_entrypoint_uds=True,
    )


def test_socket_family_guard_applies_to_all_production_source_scans() -> None:
    source = """import socket
socket.AF_UNIX = socket.AF_INET
"""

    assert "socket_family" in _listener_contract_violations(source)


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
