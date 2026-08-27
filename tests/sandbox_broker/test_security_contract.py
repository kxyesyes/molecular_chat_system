"""Cross-boundary security and scientific failure contracts for OpenSandbox."""

from __future__ import annotations

import asyncio
import ast
import json
import logging
import math
import threading
import time
from dataclasses import dataclass, field
from functools import lru_cache
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


@dataclass(frozen=True)
class _PartialString:
    fragments: tuple[str, ...] = ()
    unresolved: bool = True

    @property
    def exact(self) -> str | None:
        return None if self.unresolved else "".join(self.fragments)

    @property
    def known(self) -> str:
        return "".join(self.fragments)


@dataclass
class _LexicalScope:
    parent: _LexicalScope | None
    kind: str
    function: str | None
    symbols: dict[str, str | None] = field(default_factory=dict)
    strings: dict[str, _PartialString | None] = field(default_factory=dict)
    environment: list[_PartialString] = field(default_factory=list)
    starts_listener: bool = False


@dataclass(frozen=True)
class _SourceAnalysis:
    tree: ast.Module
    calls: tuple[tuple[ast.Call, str, str | None], ...]
    violations: frozenset[str]
    environment_names: frozenset[str]


class _StatementOrderedAnalyzer:
    def __init__(self, tree: ast.Module) -> None:
        self.tree = tree
        self.calls: list[tuple[ast.Call, str, str | None]] = []
        self.violations: set[str] = set()
        self.environment_names: set[str] = set()
        self.scopes: list[_LexicalScope] = []

    def analyze(self) -> _SourceAnalysis:
        module = _LexicalScope(None, "module", None)
        self.scopes.append(module)
        self._visit_block(self.tree.body, module)
        for scope in self.scopes:
            if scope.starts_listener and any(
                value.unresolved for value in scope.environment
            ):
                self.violations.add("metrics.environment")
        return _SourceAnalysis(
            tree=self.tree,
            calls=tuple(self.calls),
            violations=frozenset(self.violations),
            environment_names=frozenset(self.environment_names),
        )

    @staticmethod
    def _lookup_symbol(scope: _LexicalScope, name: str) -> str:
        current: _LexicalScope | None = scope
        while current is not None:
            if name in current.symbols:
                return current.symbols[name] or ""
            current = current.parent
        return name

    @staticmethod
    def _lookup_string(
        scope: _LexicalScope,
        name: str,
    ) -> _PartialString:
        current: _LexicalScope | None = scope
        while current is not None:
            if name in current.strings:
                return current.strings[name] or _PartialString()
            current = current.parent
        return _PartialString()

    def _partial_string(
        self,
        node: ast.expr,
        scope: _LexicalScope,
    ) -> _PartialString:
        if isinstance(node, ast.Constant) and type(node.value) is str:
            return _PartialString((node.value,), False)
        if isinstance(node, ast.Name):
            return self._lookup_string(scope, node.id)
        if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
            left = self._partial_string(node.left, scope)
            right = self._partial_string(node.right, scope)
            return _PartialString(
                left.fragments + right.fragments,
                left.unresolved or right.unresolved,
            )
        if isinstance(node, ast.JoinedStr):
            fragments: list[str] = []
            unresolved = False
            for value in node.values:
                if isinstance(value, ast.Constant) and type(value.value) is str:
                    fragments.append(value.value)
                elif isinstance(value, ast.FormattedValue):
                    observed = self._partial_string(value.value, scope)
                    fragments.extend(observed.fragments)
                    unresolved = unresolved or observed.unresolved
                else:
                    unresolved = True
            return _PartialString(tuple(fragments), unresolved)
        return _PartialString()

    def _resolve(self, node: ast.expr, scope: _LexicalScope) -> str:
        if isinstance(node, ast.Name):
            return _normalize_symbol(self._lookup_symbol(scope, node.id))
        if isinstance(node, ast.Attribute):
            owner = self._resolve(node.value, scope)
            return _normalize_symbol(f"{owner}.{node.attr}" if owner else node.attr)
        if isinstance(node, ast.Subscript):
            owner = self._resolve(node.value, scope)
            key = self._partial_string(node.slice, scope).exact
            if owner in {"globals", "locals"} and key:
                return self._lookup_symbol(scope, key)
            if owner == "socket.__dict__" and key:
                return f"socket.{key}"
            return ""
        if isinstance(node, ast.Call):
            function = self._resolve(node.func, scope)
            if function in {"getattr", "builtins.getattr"} and len(node.args) >= 2:
                owner = self._resolve(node.args[0], scope)
                attribute = self._partial_string(node.args[1], scope).exact
                if owner and attribute:
                    return _normalize_symbol(f"{owner}.{attribute}")
            if function in {"vars", "builtins.vars"} and len(node.args) == 1:
                owner = self._resolve(node.args[0], scope)
                return f"{owner}.__dict__" if owner else ""
            if function in {"vars", "builtins.vars"} and not node.args:
                return "locals"
            return function
        return ""

    def _bind_name(
        self,
        scope: _LexicalScope,
        name: str,
        value: ast.expr | None = None,
        *,
        symbol: str | None = None,
    ) -> None:
        explicit_symbol = symbol is not None
        resolved = symbol if explicit_symbol else (
            self._resolve(value, scope) if value is not None else ""
        )
        scope.symbols[name] = (
            resolved
            if resolved and (explicit_symbol or resolved != name)
            else None
        )
        scope.strings[name] = (
            self._partial_string(value, scope) if value is not None else None
        )

    def _bind_target(
        self,
        target: ast.expr,
        scope: _LexicalScope,
        value: ast.expr | None = None,
    ) -> None:
        if isinstance(target, ast.Name):
            self._bind_name(scope, target.id, value)
        elif isinstance(target, (ast.Tuple, ast.List)):
            for element in target.elts:
                self._bind_target(element, scope)
        elif isinstance(target, ast.Starred):
            self._bind_target(target.value, scope)

    def _is_socket_reference(
        self,
        node: ast.expr,
        scope: _LexicalScope,
    ) -> bool:
        return self._resolve(node, scope) == "socket"

    def _target_mutates_socket(
        self,
        target: ast.expr,
        scope: _LexicalScope,
    ) -> bool:
        if isinstance(target, (ast.Tuple, ast.List)):
            return any(
                self._target_mutates_socket(element, scope)
                for element in target.elts
            )
        if isinstance(target, ast.Starred):
            return self._target_mutates_socket(target.value, scope)
        if isinstance(target, ast.Attribute):
            return bool(
                target.attr in _PROTECTED_SOCKET_FAMILY_ATTRIBUTES
                and self._is_socket_reference(target.value, scope)
            )
        if not isinstance(target, ast.Subscript):
            return False
        owner = self._resolve(target.value, scope)
        key = self._partial_string(target.slice, scope).exact
        return bool(
            (owner in {"globals", "locals"} and key in {"_AF_UNIX", "socket"})
            or (
                owner in {"socket", "socket.__dict__"}
                and key in _PROTECTED_SOCKET_FAMILY_ATTRIBUTES
            )
        )

    def _call_mutates_socket(
        self,
        call: ast.Call,
        symbol: str,
        scope: _LexicalScope,
    ) -> bool:
        leaf = symbol.rsplit(".", 1)[-1]
        if leaf in {"setattr", "delattr"} and len(call.args) >= 2:
            return bool(
                self._is_socket_reference(call.args[0], scope)
                and self._partial_string(call.args[1], scope).exact
                in _PROTECTED_SOCKET_FAMILY_ATTRIBUTES
            )
        if leaf in {"__setattr__", "__delattr__"}:
            owner = (
                self._resolve(call.func.value, scope)
                if isinstance(call.func, ast.Attribute)
                else ""
            )
            if owner == "socket" and call.args:
                return self._partial_string(call.args[0], scope).exact in (
                    _PROTECTED_SOCKET_FAMILY_ATTRIBUTES
                )
            if len(call.args) >= 2:
                return bool(
                    self._is_socket_reference(call.args[0], scope)
                    and self._partial_string(call.args[1], scope).exact
                    in _PROTECTED_SOCKET_FAMILY_ATTRIBUTES
                )

        owner = (
            self._resolve(call.func.value, scope)
            if isinstance(call.func, ast.Attribute)
            else ""
        )
        protected = (
            {"_AF_UNIX", "socket"}
            if owner in {"globals", "locals"}
            else _PROTECTED_SOCKET_FAMILY_ATTRIBUTES
            if owner == "socket.__dict__"
            else set()
        )
        if not protected:
            return False
        if leaf in {"__setitem__", "pop", "setdefault"} and call.args:
            return self._partial_string(call.args[0], scope).exact in protected
        if leaf == "update" and call.args and isinstance(call.args[0], ast.Dict):
            return any(
                key is not None
                and self._partial_string(key, scope).exact in protected
                for key in call.args[0].keys
            )
        return False

    def _observe_environment(
        self,
        value: _PartialString,
        scope: _LexicalScope,
    ) -> None:
        scope.environment.append(value)
        if value.exact is not None:
            self.environment_names.add(value.exact)
        upper = value.known.upper()
        metrics_fragment = "METRIC" in upper or "PROMETHEUS" in upper
        host_port_fragment = "HOST" in upper or "PORT" in upper
        if metrics_fragment and (host_port_fragment or value.unresolved):
            self.violations.add("metrics.environment")

    def _visit_call(self, call: ast.Call, scope: _LexicalScope) -> None:
        self._visit_expr(call.func, scope)
        for argument in call.args:
            self._visit_expr(argument, scope)
        for keyword in call.keywords:
            self._visit_expr(keyword.value, scope)

        symbol = self._resolve(call.func, scope)
        self.calls.append((call, symbol, scope.function))
        if symbol.startswith("_socket."):
            self.violations.add("_socket")
            scope.starts_listener = True
        if symbol in _LISTENER_CALLS or symbol in {
            "prometheus_client.start_http_server",
            "prometheus_client.start_wsgi_server",
            "socket.socket",
        }:
            scope.starts_listener = True
        if self._call_mutates_socket(call, symbol, scope):
            self.violations.add("socket_family")
        if symbol in {"os.environ.get", "os.getenv"} and call.args:
            self._observe_environment(
                self._partial_string(call.args[0], scope),
                scope,
            )

    def _visit_comprehension(
        self,
        node: ast.ListComp | ast.SetComp | ast.GeneratorExp | ast.DictComp,
        scope: _LexicalScope,
    ) -> None:
        child = _LexicalScope(scope, "comprehension", scope.function)
        self.scopes.append(child)
        for index, generator in enumerate(node.generators):
            self._visit_expr(generator.iter, scope if index == 0 else child)
            self._bind_target(generator.target, child)
            for condition in generator.ifs:
                self._visit_expr(condition, child)
        if isinstance(node, ast.DictComp):
            self._visit_expr(node.key, child)
            self._visit_expr(node.value, child)
        else:
            self._visit_expr(node.elt, child)

    def _visit_expr(self, node: ast.expr | None, scope: _LexicalScope) -> None:
        if node is None:
            return
        if isinstance(node, ast.Call):
            self._visit_call(node, scope)
            return
        if isinstance(node, ast.Lambda):
            for default in (*node.args.defaults, *node.args.kw_defaults):
                self._visit_expr(default, scope)
            child = _LexicalScope(scope, "lambda", scope.function)
            self.scopes.append(child)
            self._bind_arguments(node.args, child)
            self._visit_expr(node.body, child)
            return
        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp, ast.DictComp)):
            self._visit_comprehension(node, scope)
            return
        if isinstance(node, ast.NamedExpr):
            self._visit_expr(node.value, scope)
            if self._target_mutates_socket(node.target, scope):
                self.violations.add("socket_family")
            self._bind_target(node.target, scope, node.value)
            return
        if isinstance(node, ast.Subscript):
            self._visit_expr(node.value, scope)
            self._visit_expr(node.slice, scope)
            if (
                isinstance(node.ctx, ast.Load)
                and self._resolve(node.value, scope) == "os.environ"
            ):
                self._observe_environment(
                    self._partial_string(node.slice, scope),
                    scope,
                )
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._visit_expr(child, scope)

    def _bind_arguments(self, arguments: ast.arguments, scope: _LexicalScope) -> None:
        for argument in (
            *arguments.posonlyargs,
            *arguments.args,
            *arguments.kwonlyargs,
        ):
            self._bind_name(scope, argument.arg)
        if arguments.vararg is not None:
            self._bind_name(scope, arguments.vararg.arg)
        if arguments.kwarg is not None:
            self._bind_name(scope, arguments.kwarg.arg)

    def _branch(self, statements: list[ast.stmt], scope: _LexicalScope) -> None:
        child = _LexicalScope(scope, "block", scope.function)
        self.scopes.append(child)
        self._visit_block(statements, child)

    def _visit_statement(self, node: ast.stmt, scope: _LexicalScope) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            for expression in (
                *node.decorator_list,
                *node.args.defaults,
                *node.args.kw_defaults,
            ):
                self._visit_expr(expression, scope)
            self._bind_name(scope, node.name)
            parent = scope.parent if scope.kind == "class" else scope
            child = _LexicalScope(parent, "function", node.name)
            self.scopes.append(child)
            self._bind_arguments(node.args, child)
            self._visit_block(node.body, child)
            return
        if isinstance(node, ast.ClassDef):
            for expression in (*node.decorator_list, *node.bases):
                self._visit_expr(expression, scope)
            class_symbol = self._resolve(node.bases[0], scope) if len(node.bases) == 1 else ""
            self._bind_name(scope, node.name, symbol=class_symbol or None)
            child = _LexicalScope(scope, "class", scope.function)
            self.scopes.append(child)
            self._visit_block(node.body, child)
            return
        if isinstance(node, ast.Import):
            for imported in node.names:
                bound = imported.asname or imported.name.split(".", 1)[0]
                self._bind_name(scope, bound, symbol=imported.name)
                if imported.name == "_socket":
                    self.violations.add("_socket")
            return
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            for imported in node.names:
                bound = imported.asname or imported.name
                symbol = _normalize_symbol(f"{module}.{imported.name}")
                self._bind_name(scope, bound, symbol=symbol)
                if module == "_socket":
                    self.violations.add("_socket")
            return
        if isinstance(node, ast.Assign):
            self._visit_expr(node.value, scope)
            if any(self._target_mutates_socket(target, scope) for target in node.targets):
                self.violations.add("socket_family")
            for target in node.targets:
                self._bind_target(target, scope, node.value)
            return
        if isinstance(node, ast.AnnAssign):
            self._visit_expr(node.value, scope)
            if self._target_mutates_socket(node.target, scope):
                self.violations.add("socket_family")
            self._bind_target(node.target, scope, node.value)
            return
        if isinstance(node, ast.AugAssign):
            self._visit_expr(node.target, scope)
            self._visit_expr(node.value, scope)
            if self._target_mutates_socket(node.target, scope):
                self.violations.add("socket_family")
            self._bind_target(node.target, scope)
            return
        if isinstance(node, ast.Delete):
            if any(self._target_mutates_socket(target, scope) for target in node.targets):
                self.violations.add("socket_family")
            for target in node.targets:
                self._bind_target(target, scope)
            return
        if isinstance(node, ast.Expr):
            self._visit_expr(node.value, scope)
            return
        if isinstance(node, (ast.Return, ast.Raise, ast.Assert)):
            for child in ast.iter_child_nodes(node):
                if isinstance(child, ast.expr):
                    self._visit_expr(child, scope)
            return
        if isinstance(node, ast.If):
            self._visit_expr(node.test, scope)
            self._branch(node.body, scope)
            self._branch(node.orelse, scope)
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            self._visit_expr(node.iter, scope)
            body = _LexicalScope(scope, "block", scope.function)
            self.scopes.append(body)
            self._bind_target(node.target, body)
            self._visit_block(node.body, body)
            self._branch(node.orelse, scope)
            return
        if isinstance(node, ast.While):
            self._visit_expr(node.test, scope)
            self._branch(node.body, scope)
            self._branch(node.orelse, scope)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            child = _LexicalScope(scope, "block", scope.function)
            self.scopes.append(child)
            for item in node.items:
                self._visit_expr(item.context_expr, scope)
                if item.optional_vars is not None:
                    self._bind_target(item.optional_vars, child)
            self._visit_block(node.body, child)
            return
        if isinstance(node, ast.Try):
            self._branch(node.body, scope)
            for handler in node.handlers:
                child = _LexicalScope(scope, "block", scope.function)
                self.scopes.append(child)
                if handler.name:
                    self._bind_name(child, handler.name)
                self._visit_block(handler.body, child)
            self._branch(node.orelse, scope)
            self._branch(node.finalbody, scope)
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self._visit_expr(child, scope)

    def _visit_block(
        self,
        statements: list[ast.stmt],
        scope: _LexicalScope,
    ) -> None:
        for statement in statements:
            self._visit_statement(statement, scope)


def _analyze_tree(tree: ast.Module) -> _SourceAnalysis:
    return _StatementOrderedAnalyzer(tree).analyze()


@lru_cache(maxsize=256)
def _analyze_source(source: str) -> _SourceAnalysis:
    return _analyze_tree(ast.parse(source))


@lru_cache(maxsize=1)
def _broker_production_sources() -> tuple[tuple[Path, str, bool], ...]:
    root = Path(__file__).resolve().parents[2]
    entrypoint = root / "scripts" / "run_sandbox_broker.py"
    paths = (*sorted((root / "src" / "sandbox_broker").rglob("*.py")), entrypoint)
    return tuple(
        (path, path.read_text(encoding="utf-8"), path == entrypoint)
        for path in paths
        if path.is_file()
    )


def _entrypoint_source() -> str:
    return _broker_production_sources()[-1][1]


def _resolved_python_calls(source: str) -> tuple[ast.Module, list[str]]:
    analysis = _analyze_source(source)
    return analysis.tree, [symbol for _, symbol, _ in analysis.calls]


def _environment_names(tree: ast.Module) -> set[str]:
    return set(_analyze_tree(tree).environment_names)


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
    analysis = _analyze_source(source)
    violations = set(analysis.violations)
    if allow_entrypoint_uds and not _has_verified_af_unix_binding(analysis.tree):
        violations.add("entrypoint._AF_UNIX")
    if allow_entrypoint_uds and "socket_family" in violations:
        violations.remove("socket_family")
        violations.add("entrypoint.socket_family")
    for call, symbol, function in analysis.calls:
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
    counts: dict[str, int] = {}
    for call, symbol, function in _analyze_source(source).calls:
        if _allowed_entrypoint_call(call, symbol, function):
            key = f"{function}:{symbol}"
            counts[key] = counts.get(key, 0) + 1
    return counts


def test_broker_metrics_cannot_start_a_second_listener_or_use_tcp_configuration() -> None:
    observed_environment: set[str] = set()

    for path, source, allow_entrypoint_uds in _broker_production_sources():
        analysis = _analyze_source(source)
        violations = _listener_contract_violations(
            source,
            allow_entrypoint_uds=allow_entrypoint_uds,
        )
        assert not violations, (path, violations)
        observed_environment.update(analysis.environment_names)

    assert not {
        name
        for name in observed_environment
        if ("METRIC" in name.upper() or "PROMETHEUS" in name.upper())
        and ("HOST" in name.upper() or "PORT" in name.upper())
    }

    assert _entrypoint_allowlist_counts(_entrypoint_source()) == {
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


def test_listener_analysis_reviewer_false_negative_keeps_module_scope_order() -> None:
    source = """import socket

def harmless():
    socket = object()
    return socket

listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
listener.bind(("127.0.0.1", 9000))
"""

    violations = _listener_contract_violations(source)

    assert "socket.socket" in violations
    assert "socket.socket.bind" in violations


@pytest.mark.parametrize(
    "source",
    [
        "def harmless(socket):\n    socket.socket()\n",
        "harmless = lambda socket: socket.socket()\n",
        "results = [socket.socket() for socket in values]\n",
        (
            "def harmless():\n"
            "    class LocalSocket:\n"
            "        def socket(self):\n"
            "            return None\n"
            "    socket = LocalSocket()\n"
            "    socket.socket()\n"
        ),
    ],
)
def test_listener_analysis_reviewer_false_positive_respects_lexical_shadowing(
    source: str,
) -> None:
    assert not _listener_contract_violations(source)


def test_listener_aliases_apply_only_after_the_binding_statement() -> None:
    source = """serve()
from prometheus_client import start_http_server as serve
serve(9000)
"""

    _, calls = _resolved_python_calls(source)

    assert calls == ["serve", "prometheus_client.start_http_server"]


@pytest.mark.parametrize(
    ("source", "violation"),
    [
        (
            "import socket\n"
            "open_socket = getattr(socket, 'socket')\n"
            "open_socket(socket.AF_INET, socket.SOCK_STREAM)\n",
            "socket.socket",
        ),
        (
            "import socket\n"
            "listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "bind = getattr(listener, 'bind')\n"
            "bind(('127.0.0.1', 9000))\n",
            "socket.socket.bind",
        ),
        (
            "import socket\n"
            "listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)\n"
            "getattr(listener, 'listen')(8)\n",
            "socket.socket.listen",
        ),
        (
            "import _socket\n"
            "_socket.socket(_socket.AF_INET, _socket.SOCK_STREAM)\n",
            "_socket",
        ),
        (
            "from _socket import socket as open_socket\n"
            "open_socket(AF_INET, SOCK_STREAM)\n",
            "_socket",
        ),
        (
            "import socket\n"
            "namespace = vars(socket)\n"
            "namespace['AF_UNIX'] = socket.AF_INET\n",
            "socket_family",
        ),
        (
            "import socket\n"
            "vars()['_AF_UNIX'] = socket.AF_INET\n",
            "socket_family",
        ),
        (
            "import socket\n"
            "globals().update({'_AF_UNIX': socket.AF_INET})\n",
            "socket_family",
        ),
        (
            "import socket\n"
            "locals().__setitem__('socket', replacement)\n",
            "socket_family",
        ),
        (
            "import builtins\nimport socket\n"
            "mutate = getattr(builtins, 'setattr')\n"
            "mutate(socket, 'AF_UNIX', socket.AF_INET)\n",
            "socket_family",
        ),
    ],
)
def test_listener_contract_rejects_reflection_and_low_level_socket_bypasses(
    source: str,
    violation: str,
) -> None:
    assert violation in _listener_contract_violations(source)


@pytest.mark.parametrize(
    "source",
    [
        (
            "import os\n"
            "kind = unresolved\n"
            "os.getenv('PROMETHEUS_METRICS_' + kind)\n"
        ),
        (
            "import os\n"
            "kind = unresolved\n"
            "os.environ.get(f'METRICS_{kind}')\n"
        ),
        (
            "from os import getenv as read_environment\n"
            "prefix = 'PROMETHEUS_'\n"
            "middle = 'METRICS_'\n"
            "kind = unresolved\n"
            "key = prefix + middle + kind\n"
            "read_environment(key)\n"
        ),
    ],
)
def test_metrics_environment_contract_rejects_partially_computed_keys(
    source: str,
) -> None:
    assert "metrics.environment" in _listener_contract_violations(source)


def test_metrics_environment_contract_fails_closed_only_in_listener_scope() -> None:
    listener_source = """import os
from prometheus_client import start_http_server

def launch_metrics(kind):
    os.getenv("BIND_" + kind)
    start_http_server(9000)
"""
    separate_scopes = """import os
from prometheus_client import start_http_server

def read_application_setting(kind):
    os.getenv("APPLICATION_" + kind)

def launch_metrics():
    start_http_server(9000)
"""

    assert "metrics.environment" in _listener_contract_violations(listener_source)
    assert "metrics.environment" not in _listener_contract_violations(
        separate_scopes
    )


@pytest.mark.parametrize(
    "source",
    [
        "import os\nos.getenv('APPLICATION_' + kind)\n",
        "import os\nos.environ.get(f'SERVICE_{kind}')\n",
        "import os\nos.environ['NORMAL_STATIC_KEY']\n",
    ],
)
def test_metrics_environment_contract_allows_unrelated_environment_reads(
    source: str,
) -> None:
    assert not _listener_contract_violations(source)


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
    mutated = _mutate_af_unix_binding(_entrypoint_source(), mutation)

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
    mutated = _mutate_socket_family(_entrypoint_source(), mutation)

    assert "entrypoint.socket_family" in _listener_contract_violations(
        mutated,
        allow_entrypoint_uds=True,
    )


def test_entrypoint_socket_family_guard_allows_benign_reads() -> None:
    source = _entrypoint_source()
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
