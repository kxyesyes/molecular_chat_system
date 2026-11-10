"""Offline contract tests for the local Ollama metadata probe.

Only socket, monotonic clock and Timer are replaced. HTTPResponse, JSON decoding,
collector, summary functions and CLI remain the implementation under test.
All provider bytes are synthetic. No local server, real socket, sleep or asset.
"""

import ast
import builtins
from collections.abc import MutableMapping
from contextlib import contextmanager
import hashlib
import http.client
import importlib.util
import io
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import weakref

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/g3_ollama_metadata.py"
ENDPOINT = "http://127.0.0.1:11434"
MODEL = "gmm-llama:latest"
ARGS = ["--endpoint", ENDPOINT, "--model", MODEL]
LIMIT = 1024 * 1024
MARKER = "SYNTHETIC_PRIVATE_MARKER_58ad"
ERRORS = {
    "invalid_arguments", "http_error", "transport_error", "request_timeout",
    "overall_timeout", "response_too_large", "invalid_http",
    "unsupported_encoding", "invalid_json", "invalid_metadata", "internal_error",
}
QUANTS = (
    "F32", "F16", "BF16", "Q4_0", "Q4_1", "Q4_K_S", "Q4_K_M",
    "Q5_0", "Q5_1", "Q5_K_S", "Q5_K_M", "Q6_K", "Q8_0",
)
MODULE_RIGS = weakref.WeakKeyDictionary()


def show_payload():
    return {
        "template": "{{ .System }}\n{{ .Prompt }} " + MARKER,
        "system": "合成系统 " + MARKER,
        "parameters": "num_ctx 2048\nnum_predict 1000\n" + MARKER,
        "model_info": {"llama.context_length": 2048},
        "details": {"format": "gguf", "family": "llama", "quantization_level": "Q4_K_M"},
    }


def wire(payload=None, *, body=None, status=200, headers=(), length=True, chunked=False):
    if body is None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    lines = [f"HTTP/1.1 {status} Synthetic", "Content-Type: application/json"]
    lines.extend(headers)
    if chunked:
        lines.append("Transfer-Encoding: chunked")
        body = f"{len(body):x}\r\n".encode() + body + b"\r\n0\r\n\r\n"
    elif length:
        lines.append(f"Content-Length: {len(body)}")
    return ("\r\n".join(lines) + "\r\n\r\n").encode() + body


def exact_wire_size(size, *, chunked=False, length=True):
    # Valid JSON followed by whitespace; fixture size concerns the entire wire.
    body = b'{"version":"0.12.0"}'
    for _ in range(12):
        candidate = wire(body=body, chunked=chunked, length=length)
        delta = size - len(candidate)
        if delta == 0:
            return candidate
        body = body + b" " * delta if delta > 0 else body[:delta]
    raise AssertionError("wire-size fixture did not converge")


class Clock:
    """Runs only callbacks scheduled by production; contains no timeout policy."""

    def __init__(self):
        self.now = 0.0
        self.timers = []
        self.join_delay = 0.0

    def monotonic(self):
        return self.now

    def advance(self, seconds):
        end = self.now + seconds
        while True:
            due = [t for t in self.timers if t.is_alive() and t.due <= end]
            if not due:
                break
            timer = min(due, key=lambda t: t.due)
            self.now = max(self.now, timer.due)
            timer.fired = True
            timer.function(*timer.args, **timer.kwargs)
        self.now = max(self.now, end)

    def timer(self, interval, function, args=None, kwargs=None):
        timer = ManualTimer(self, interval, function, args, kwargs)
        self.timers.append(timer)
        return timer


class ManualTimer:
    def __init__(self, clock, interval, function, args, kwargs):
        self.clock, self.interval, self.function = clock, interval, function
        self.args, self.kwargs = args or (), kwargs or {}
        self.started = self.cancelled = self.fired = self.joined = False
        self.daemon = False
        self.due = float("inf")

    def start(self):
        self.started = True
        self.due = self.clock.now + self.interval

    def cancel(self):
        self.cancelled = True

    def join(self, timeout=None):
        self.joined = True
        delay, self.clock.join_delay = self.clock.join_delay, 0.0
        self.clock.advance(delay)

    def is_alive(self):
        return self.started and not self.cancelled and not self.fired


class WireReader(io.RawIOBase):
    """Finite socket file. Closing the socket interrupts pending fake reads."""

    def __init__(self, owner):
        super().__init__()
        self.owner = owner
        self.position = 0
        self.header_end = owner.data.find(b"\r\n\r\n") + 4
        self.body_reads = 0
        self.read_records = []
        self.read_records_omitted = 0

    def readable(self):
        return True

    def _check(self):
        if self.closed or self.owner.closed or self.owner.shutdown_called:
            raise OSError(MARKER)

    def _timer_state(self):
        timers = self.owner.rig.clock.timers
        return {name: sum(bool(getattr(timer, name)) for timer in timers)
                for name in ("started", "fired", "cancelled", "joined")}

    def _take(self, size, *, line=False, operation="read"):
        # Observation only: no zero-size fast path, delay or deadline change.
        record = None
        if len(self.read_records) < 16:
            record = {
                "operation": operation,
                "size": size if type(size) is int or size is None else "invalid_size",
                "position_before": self.position,
                "clock_before": self.owner.rig.clock.now,
                "timers_before": self._timer_state(),
            }
            self.read_records.append(record)
        else:
            self.read_records_omitted += 1
        try:
            return self._take_unrecorded(size, line=line)
        finally:
            if record is not None:
                record.update({
                    "position_after": self.position,
                    "clock_after": self.owner.rig.clock.now,
                    "timers_after": self._timer_state(),
                })

    def _take_unrecorded(self, size, *, line=False):
        self._check()
        if size == 0:
            return b""
        owner = self.owner
        in_body = self.position >= self.header_end
        if in_body:
            self.body_reads += 1
            if owner.fail == "body":
                raise OSError(MARKER)
            if owner.body_delay:
                owner.rig.clock.advance(owner.body_delay)
                self._check()
        elif line:
            if owner.fail == "headers":
                raise OSError(MARKER)
            owner.rig.clock.advance(owner.header_delay)
            self._check()
        remaining = owner.data[self.position:]
        count = len(remaining) if size is None or size < 0 else min(size, len(remaining))
        if line:
            newline = remaining.find(b"\n", 0, count)
            if newline >= 0:
                count = newline + 1
        if in_body and owner.byte_delay:
            for _ in range(count):
                owner.rig.clock.advance(owner.byte_delay)
                self._check()
        result = remaining[:count]
        self.position += count
        return result

    def read(self, size=-1):
        return self._take(size)

    def read1(self, size=-1):
        return self._take(size, operation="read1")

    def readline(self, size=-1):
        return self._take(size, line=True, operation="readline")

    def readinto(self, target):
        data = self.read(len(target))
        target[:len(data)] = data
        return len(data)


class WireSocket:
    def __init__(self, rig, data, **settings):
        self.rig, self.data = rig, data
        self.closed = self.shutdown_called = False
        self.address = None
        self.connect_calls = 0
        self.sent = bytearray()
        self.timeouts = []
        self.readers = []
        self.fail = settings.get("fail")
        self.connect_delay = settings.get("connect_delay", 0.0)
        self.header_delay = settings.get("header_delay", 0.0)
        self.body_delay = settings.get("body_delay", 0.0)
        self.byte_delay = settings.get("byte_delay", 0.0)

    def connect(self, address):
        self.connect_calls += 1
        self.rig.connection_attempts.append((self, address))
        if (self.connect_calls != 1 or address != ("127.0.0.1", 11434)
                or len(self.rig.connection_attempts) > len(self.rig.responses)):
            self.rig.deny("unexpected_connect")
        self.address = address
        self.rig.clock.advance(self.connect_delay)
        if self.fail == "connect" or self.closed or self.shutdown_called:
            raise OSError(MARKER)

    def settimeout(self, value):
        self.timeouts.append(value)

    def gettimeout(self):
        return self.timeouts[-1] if self.timeouts else None

    def setsockopt(self, *args):
        pass

    def sendall(self, data):
        if self.fail == "send" or self.closed or self.shutdown_called:
            raise OSError(MARKER)
        self.sent.extend(data)

    def makefile(self, mode="r", *args, **kwargs):
        assert mode == "rb", "collector must read bytes through the real HTTP parser"
        reader = WireReader(self)
        self.readers.append(reader)
        return reader

    def shutdown(self, how):
        self.shutdown_called = True

    def close(self):
        self.closed = True


class Rig:
    def __init__(self, responses=None, settings=None):
        self.responses = list(responses if responses is not None else [
            wire({"version": "0.12.0"}), wire(show_payload()),
        ])
        self.settings = settings or {}
        self.clock = Clock()
        self.sockets = []
        self.forbidden = []
        self.creation_attempts = 0
        self.connection_attempts = []

    def socket(self, family=socket.AF_INET, type=socket.SOCK_STREAM, proto=0, fileno=None):
        self.creation_attempts += 1
        if not (family == socket.AF_INET and type == socket.SOCK_STREAM and fileno is None):
            self.deny("unexpected_socket_kind")
        index = len(self.sockets)
        if index >= len(self.responses):
            self.deny("unexpected_extra_socket")
        obj = WireSocket(self, self.responses[index], **self.settings.get(index, {}))
        self.sockets.append(obj)
        return obj

    def deny(self, *args, **kwargs):
        self.forbidden.append("forbidden_io")
        raise AssertionError("unexpected I/O boundary")

    def assert_clean(self):
        assert not self.forbidden, "latched forbidden I/O"
        assert self.creation_attempts == len(self.sockets)
        assert len(self.connection_attempts) == sum(s.connect_calls for s in self.sockets)
        assert all(s.connect_calls <= 1 for s in self.sockets)
        assert all(s.closed for s in self.sockets)
        assert all(r.closed for s in self.sockets for r in s.readers)
        assert all(t.cancelled and t.joined and not t.is_alive() for t in self.clock.timers)

    @contextmanager
    def io_guard(self, *, source_load=False):
        """Same guard around import execution and CLI; violations are sticky.

        Only the harness's first binary read of SCRIPT is allowed when loading.
        No pyc/cache/stdlib/asset reads are exempt: required stdlib modules are
        already imported by this test module/pytest before entering this scope.
        """
        native_open = builtins.open
        source_reads = 0
        allowed_path = os.path.normcase(os.fspath(SCRIPT))

        def guarded_open(file, mode="r", *args, **kwargs):
            nonlocal source_reads
            path = os.fspath(file) if isinstance(file, (str, bytes, os.PathLike)) else None
            if (source_load and source_reads == 0 and isinstance(path, str)
                    and os.path.normcase(path) == allowed_path and mode == "rb"):
                source_reads += 1
                return native_open(file, mode, *args, **kwargs)
            return self.deny("file_read_or_write")

        native_import = builtins.__import__

        def guarded_import(name, globals=None, locals=None, fromlist=(), level=0):
            if level == 0 and name.split(".")[0] not in sys.stdlib_module_names:
                return self.deny("non_stdlib_import")
            return native_import(name, globals, locals, fromlist, level)

        with pytest.MonkeyPatch.context() as guard:
            guard.setattr(os, "environ", NoEnvironment(self))
            if hasattr(os, "environb"):
                guard.setattr(os, "environb", NoEnvironment(self))
            guard.setattr(os, "getenv", self.deny)
            if hasattr(os, "getenvb"):
                guard.setattr(os, "getenvb", self.deny)
            guard.setattr(builtins, "open", guarded_open)
            guard.setattr(io, "open", guarded_open)
            guard.setattr(sys.modules["_io"], "open", guarded_open)
            # Import machinery uses _io.open_code, not necessarily builtins.open.
            guard.setattr(sys.modules["_io"], "open_code", self.deny)
            guard.setattr(io, "open_code", self.deny)
            guard.setattr(builtins, "__import__", guarded_import)
            for name in ("open", "listdir", "scandir", "stat", "lstat", "system", "popen"):
                guard.setattr(os, name, self.deny)
            guard.setattr(subprocess, "Popen", self.deny)
            yield


class NoEnvironment(MutableMapping):
    """Not a dict subclass: dict(env) cannot copy a hidden backing dict."""

    def __init__(self, rig):
        self.rig = rig

    def reject(self, *args, **kwargs):
        return self.rig.deny("environment_access")

    __getitem__ = __setitem__ = __delitem__ = __iter__ = __len__ = reject
    __contains__ = __repr__ = get = copy = items = keys = values = reject
    clear = pop = popitem = setdefault = update = reject


@pytest.fixture
def load_api(monkeypatch):
    # Loading is requested by each test BODY, so absence is phase=call, not setup.
    def load(rig=None):
        assert SCRIPT.is_file(), "G3 API RED: missing scripts/g3_ollama_metadata.py"
        rig = rig or Rig()
        monkeypatch.setattr(socket, "socket", rig.socket)
        monkeypatch.setattr(socket, "getaddrinfo", rig.deny)
        monkeypatch.setattr(socket, "create_connection", rig.deny)
        monkeypatch.setattr(time, "monotonic", rig.clock.monotonic)
        monkeypatch.setattr(threading, "Timer", rig.clock.timer)
        name = "_g3_metadata_under_test"
        spec = importlib.util.spec_from_file_location(name, SCRIPT)
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        # Read source once under the guard and execute those bytes directly.
        # Avoid SourceFileLoader's incidental pyc reads/writes and broad import
        # file exemptions; all script top-level code runs with guards installed.
        with rig.io_guard(source_load=True):
            with open(SCRIPT, "rb") as source:
                code = compile(source.read(), str(SCRIPT), "exec")
            exec(code, module.__dict__)
        for symbol in ("summarize_version", "summarize_show", "collect_metadata", "main"):
            assert callable(getattr(module, symbol, None)), f"G3 API RED: missing {symbol}"
        assert not rig.creation_attempts and not rig.connection_attempts, "import attempted network I/O"
        rig.assert_clean()  # Fails even when top-level code swallowed a guard error.
        MODULE_RIGS[module] = rig
        return module, rig
    return load


def run_cli(module, capsys, args=None):
    with MODULE_RIGS[module].io_guard():
        try:
            code = module.main(list(ARGS if args is None else args))
        except SystemExit as exc:
            code = exc.code
    captured = capsys.readouterr()
    assert captured.err == ""
    assert MARKER not in captured.out
    assert ENDPOINT not in captured.out and MODEL not in captured.out
    assert "Traceback" not in captured.out
    document = json.loads(captured.out)  # Rejects multiple JSON documents/log lines.
    assert set(document) == {
        "schema_version", "scope", "assembly_match", "enforcement", "attempts", "version", "show",
    }
    assert type(document["schema_version"]) is int and document["schema_version"] == 1
    assert document["scope"] == "candidate_metadata_only"
    assert document["assembly_match"] == document["enforcement"] == "unverified"
    assert type(document["attempts"]) is int and 0 <= document["attempts"] <= 2
    for key in ("version", "show"):
        result = document[key]
        assert set(result) == {"state", "error", "summary"}
        if result["state"] == "ok":
            assert result["error"] is None and type(result["summary"]) is dict
        elif result["state"] == "error":
            assert result["error"] in ERRORS and result["summary"] is None
        else:
            assert result == {"state": "not_attempted", "error": None, "summary": None}
    assert type(code) is int and code in (0, 1, 2)
    return code, document


def assert_failure(module, rig, capsys, error, *, phase="version", attempts=1, args=None):
    code, result = run_cli(module, capsys, args)
    assert code == (2 if error == "invalid_arguments" else 1)
    assert result["attempts"] == attempts
    assert result[phase] == {"state": "error", "error": error, "summary": None}
    if phase == "version":
        assert result["show"]["state"] == "not_attempted"
    rig.assert_clean()
    return result


def fingerprint(value):
    data = value.encode("utf-8")
    return {"present": True, "utf8_bytes": len(data), "sha256": hashlib.sha256(data).hexdigest()}


def test_exact_two_requests_real_http_parser_and_cleanup(load_api, monkeypatch, capsys):
    parser = http.client.HTTPResponse
    original_begin = parser.begin
    parsed = []

    def observe_begin(response):
        parsed.append(response)
        return original_begin(response)

    monkeypatch.setattr(parser, "begin", observe_begin)
    module, rig = load_api()
    code, result = run_cli(module, capsys)
    assert code == 0 and result["attempts"] == 2
    assert rig.creation_attempts == len(rig.connection_attempts) == 2
    assert http.client.HTTPResponse is parser
    assert len(parsed) == 2 and all(isinstance(response, parser) for response in parsed)
    assert all(response.isclosed() for response in parsed)
    assert result["version"]["summary"] == {"server_version": "0.12.0"}
    assert result["show"]["summary"] == module.summarize_show(show_payload())
    for index, sock in enumerate(rig.sockets):
        assert sock.address == ("127.0.0.1", 11434)
        head, body = bytes(sock.sent).split(b"\r\n\r\n", 1)
        lines = head.decode("ascii").split("\r\n")
        assert lines[0] == ("GET /api/version HTTP/1.1" if index == 0 else "POST /api/show HTTP/1.1")
        headers = dict(line.lower().split(": ", 1) for line in lines[1:])
        expected = {"host": "127.0.0.1:11434", "accept": "application/json", "connection": "close"}
        if index:
            assert body == b'{"model":"gmm-llama:latest","verbose":false}'
            expected.update({"content-type": "application/json", "content-length": str(len(body))})
        else:
            assert body == b""
        assert headers == expected and len(headers) == len(lines) - 1
        assert sock.timeouts and all(0 < t <= 5 for t in sock.timeouts)
    assert len(rig.clock.timers) == 2
    rig.assert_clean()


@pytest.mark.parametrize("args", [
    [], ["--endpoint", ENDPOINT], ["--model", MODEL],
    ARGS + ["--endpoint", ENDPOINT], ARGS + ["--model", MODEL],
    ARGS + ["--scope", MARKER], ARGS + [MARKER], ["--help"],
    ["--endpoint", "http://user:" + MARKER + "@127.0.0.1:11434", "--model", MODEL],
    *[["--endpoint", endpoint, "--model", MODEL] for endpoint in (
        "http://localhost:11434", "http://127.0.0.1:11435", "https://127.0.0.1:11434",
        ENDPOINT + "/", ENDPOINT + "/api", ENDPOINT + "?x=" + MARKER,
        ENDPOINT + "#" + MARKER, "http://[::1]:11434", "http://192.0.2.1:11434",
    )],
    ["--endpoint", ENDPOINT, "--model", MARKER],
    ["--endpoint", ENDPOINT, "--model", MODEL + "\n"],
])
def test_argument_rejection_zero_dispatch(load_api, capsys, args):
    module, rig = load_api()
    assert_failure(module, rig, capsys, "invalid_arguments", attempts=0, args=args)
    assert not rig.sockets and not rig.clock.timers


@pytest.mark.parametrize("status", [301, 302, 303, 307, 308, 401, 403, 404, 429, 500, 503])
def test_http_error_no_body_read_redirect_auth_or_second_request(load_api, capsys, status):
    rig = Rig([wire(body=MARKER.encode(), status=status, headers=(
        "Location: http://192.0.2.1/" + MARKER,
        'WWW-Authenticate: Basic realm="' + MARKER + '"',
    ))])
    module, rig = load_api(rig)
    assert_failure(module, rig, capsys, "http_error")
    assert len(rig.sockets) == 1
    assert all(r.body_reads == 0 for r in rig.sockets[0].readers)


@pytest.mark.parametrize("stage", ["connect", "send", "headers", "body"])
def test_transport_failures_stop_and_close_without_diagnostics(load_api, capsys, stage):
    module, rig = load_api(Rig(settings={0: {"fail": stage}}))
    assert_failure(module, rig, capsys, "transport_error")
    assert len(rig.sockets) == 1


def test_show_failure_preserves_only_valid_version(load_api, capsys):
    module, rig = load_api(Rig([wire({"version": "0.12.0"}), wire(body=MARKER.encode(), status=500)]))
    result = assert_failure(module, rig, capsys, "http_error", phase="show", attempts=2)
    assert result["version"]["summary"] == {"server_version": "0.12.0"}


@pytest.mark.parametrize("settings", [
    {"connect_delay": 5.1}, {"header_delay": 1.3},
    {"body_delay": 5.1}, {"byte_delay": 0.02},
])
def test_absolute_request_deadline_closes_real_owned_socket(load_api, capsys, settings):
    # Fake never raises TimeoutError or chooses a code. Production's Timer callback
    # must shut down the socket; the resulting OSError must be classified correctly.
    response = wire(body=b'{"version":"0.12.0"}' + b" " * 400)
    module, rig = load_api(Rig([response], settings={0: settings}))
    assert_failure(module, rig, capsys, "request_timeout")
    assert len(rig.sockets) == 1
    assert rig.sockets[0].shutdown_called
    assert any(t.fired for t in rig.clock.timers)
    assert all(t.interval <= 5 for t in rig.clock.timers)


def test_overall_elapsed_after_cleanup_prevents_next_dispatch(load_api, capsys):
    # Model scheduler delay in join, not fake collector/deadline policy.
    rig = Rig()
    rig.clock.join_delay = 15.1
    module, rig = load_api(rig)
    code, result = run_cli(module, capsys)
    assert code == 1 and result["attempts"] == 1
    assert any(result[k]["error"] == "overall_timeout" for k in ("version", "show"))
    assert len(rig.sockets) == 1
    rig.assert_clean()


def deadline_diagnostic(code, result, rig):
    """Fixed safe projection, never the full report/provider body/headers."""
    reads = []
    for socket_index, sock in enumerate(rig.sockets[:2]):
        for reader in sock.readers[:1]:
            reads.append({
                "socket_index": socket_index,
                "records": reader.read_records[:16],
                "omitted": reader.read_records_omitted,
            })
    return {
        "exit_code": code,
        "attempts": result["attempts"],
        "version_state": result["version"]["state"],
        "version_error": result["version"]["error"],
        "show_state": result["show"]["state"],
        "show_error": result["show"]["error"],
        "reads": reads,
    }


@pytest.mark.parametrize("delay,success", [(4.999, True), (5.0, False)])
def test_request_deadline_boundary_does_not_accept_late_success(load_api, capsys, delay, success):
    module, rig = load_api(Rig(settings={0: {"body_delay": delay}}))
    if success:
        code, result = run_cli(module, capsys)
        diagnostic = deadline_diagnostic(code, result, rig)
        if code != 0 or result["attempts"] != 2:
            # One bounded failure-only JSON line survives abbreviated dict reprs
            # in pytest's assertion rendering. CLI capture was already validated.
            print("G3_DEADLINE_DIAGNOSTIC=" + json.dumps(diagnostic, sort_keys=True))
        assert code == 0 and result["attempts"] == 2, diagnostic
        rig.assert_clean()
    else:
        assert_failure(module, rig, capsys, "request_timeout")
        assert rig.sockets[0].shutdown_called


@pytest.mark.parametrize("method", ["read", "read1"])
@pytest.mark.parametrize("drained", [False, True])
def test_fixture_zero_length_read_must_not_advance_clock_or_consume_body(method, drained):
    # Desired fixture contract BEFORE correction; intentionally not xfail.
    # No production or real socket is involved in this isolated control.
    rig = Rig([wire({"version": "0.12.0"})], settings={0: {"body_delay": 4.999}})
    sock = rig.socket()
    sock.connect(("127.0.0.1", 11434))
    reader = sock.makefile("rb")
    reader.position = len(sock.data) if drained else reader.header_end
    before = (reader.position, rig.clock.now, reader.body_reads)
    try:
        data = getattr(reader, method)(0)
        assert len(data) == 0
        assert (reader.position, rig.clock.now, reader.body_reads) == before, reader.read_records
        assert len(reader.read_records) == 1
        assert reader.read_records[0]["size"] == 0
        assert reader.read_records[0]["timers_before"] == reader.read_records[0]["timers_after"]
    finally:
        reader.close()
        sock.close()
        rig.assert_clean()


@pytest.mark.parametrize("method", ["read", "read1"])
def test_fixture_positive_length_read_keeps_body_delay(method):
    rig = Rig([wire({"version": "0.12.0"})], settings={0: {"body_delay": 4.999}})
    sock = rig.socket()
    sock.connect(("127.0.0.1", 11434))
    reader = sock.makefile("rb")
    reader.position = reader.header_end
    before = reader.position
    try:
        data = getattr(reader, method)(1)
        assert len(data) == 1 and reader.position == before + 1
        assert rig.clock.now == 4.999 and reader.body_reads == 1
        assert len(reader.read_records) == 1
        assert reader.read_records[0]["size"] == 1
        assert reader.read_records[0]["clock_before"] == 0.0
        assert reader.read_records[0]["clock_after"] == 4.999
    finally:
        reader.close()
        sock.close()
        rig.assert_clean()


@pytest.mark.parametrize("chunked,length", [(False, True), (False, False), (True, False)])
@pytest.mark.parametrize("excess", [0, 1])
def test_whole_response_size_boundary(load_api, capsys, chunked, length, excess):
    data = exact_wire_size(LIMIT + excess, chunked=chunked, length=length)
    assert len(data) == LIMIT + excess
    module, rig = load_api(Rig([data, wire(show_payload())]))
    if excess:
        assert_failure(module, rig, capsys, "response_too_large")
        assert len(rig.sockets) == 1
    else:
        code, result = run_cli(module, capsys)
        assert code == 0 and result["attempts"] == 2
        rig.assert_clean()
    assert all(r.position <= LIMIT + 1 for s in rig.sockets for r in s.readers)


@pytest.mark.parametrize("data,error", [
    (b"NOT_HTTP " + MARKER.encode() + b"\r\n\r\n", "invalid_http"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 90\r\n\r\n{}", "invalid_http"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\nXX\r\n", "invalid_http"),
    (b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n5\r\n{}", "invalid_http"),
    (wire({"version": "0.12.0"}, headers=("Content-Encoding: gzip",)), "unsupported_encoding"),
    (b"HTTP/1.1 200 OK\r\nContent-Length: 1048577\r\n\r\n", "response_too_large"),
])
def test_bad_framing_and_encoding_are_fixed_errors(load_api, capsys, data, error):
    module, rig = load_api(Rig([data]))
    assert_failure(module, rig, capsys, error)


@pytest.mark.parametrize("body,error", [
    (b'{"version":"0.12.0","version":"0.13.0"}', "invalid_json"),
    (b'{"version":"0.12.0","extra":{"x":1,"x":2}}', "invalid_json"),
    (b'{"version":"0.12.0","extra":NaN}', "invalid_json"),
    (b'{"version":"0.12.0","extra":Infinity}', "invalid_json"),
    (b'{"version":"0.12.0"}\xff', "invalid_json"),
    (b'{"version":"0.12.0"} {}', "invalid_json"),
    (b'{"version":', "invalid_json"),
    (b"null", "invalid_metadata"), (b"[]", "invalid_metadata"),
    (b'{"version":"0.12.0","error":null}', "invalid_metadata"),
    (b'{"version":true}', "invalid_metadata"),
    (b'{"version":"0.12.0\\n"}', "invalid_metadata"),
    (b"[" * 2000 + b"]" * 2000, "invalid_json"),
])
def test_untrusted_json_and_metadata_never_echo_body(load_api, capsys, body, error):
    module, rig = load_api(Rig([wire(body=body)]))
    assert_failure(module, rig, capsys, error)


@pytest.mark.parametrize("version", ["0.12.0", "1.2.3-rc.1", "1.2.3-beta.2", "1.2.3-alpha.3", "9999.9999.9999"])
def test_version_allowlist_and_detached_result(load_api, version):
    module, rig = load_api()
    payload = {"version": version, "private": MARKER}
    summary = module.summarize_version(payload)
    payload["version"] = MARKER
    assert summary == {"server_version": version}
    rig.assert_clean()


@pytest.mark.parametrize("version", [None, False, 12, [], {}, "", MARKER, "v0.12.0", "1.2", "10000.1.1", "1.2.3-dev.1", "1.2.3\n"])
def test_invalid_version_has_no_partial_summary(load_api, capsys, version):
    module, rig = load_api(Rig([wire({"version": version})]))
    assert_failure(module, rig, capsys, "invalid_metadata")


@pytest.mark.parametrize("text", ["", "abc", "分子\n模板\r\n", "é", "e\u0301", MARKER])
def test_show_hashes_original_utf8_and_only_whitelisted_keys(load_api, text):
    module, rig = load_api()
    payload = show_payload()
    for field in ("template", "system", "parameters"):
        payload[field] = text
    payload.update({"modelfile": MARKER, "messages": [MARKER], "license": MARKER, "path": "D:/" + MARKER})
    payload["model_info"]["tokenizer.ggml.tokens"] = [MARKER]
    summary = module.summarize_show(payload)
    expected = {field: fingerprint(text) for field in ("template", "system", "parameters")}
    expected.update({"format": "gguf", "family": "llama", "quantization": "Q4_K_M",
                     "declared_context": 2048, "tokenizer_identity": "unverified"})
    assert summary == expected
    payload["model_info"].clear()
    payload["template"] = "changed"
    assert summary == expected and MARKER not in json.dumps(summary)
    rig.assert_clean()


def test_missing_optional_fields_are_not_zero_or_empty(load_api):
    module, rig = load_api()
    summary = module.summarize_show({"template": "", "model_info": {}})
    absent = {"present": False, "utf8_bytes": None, "sha256": None}
    assert summary == {"template": fingerprint(""), "system": absent, "parameters": absent,
                       "format": "missing", "family": "missing", "quantization": "missing",
                       "declared_context": None, "tokenizer_identity": "unverified"}
    rig.assert_clean()


@pytest.mark.parametrize("quantization", QUANTS)
def test_known_quantization_whitelist(load_api, quantization):
    module, rig = load_api()
    payload = show_payload()
    payload["details"]["quantization_level"] = quantization
    assert module.summarize_show(payload)["quantization"] == quantization
    rig.assert_clean()


def test_unknown_strings_collapse_without_expanding_metadata(load_api):
    module, rig = load_api()
    payload = show_payload()
    payload["details"] = dict.fromkeys(("format", "family", "quantization_level"), MARKER)
    summary = module.summarize_show(payload)
    assert [summary[k] for k in ("format", "family", "quantization")] == ["unknown"] * 3
    assert MARKER not in json.dumps(summary)
    rig.assert_clean()


@pytest.mark.parametrize("context", [1, 2048, 2**63 - 1])
def test_native_context_bound_is_reported_as_declared_only(load_api, context):
    module, rig = load_api()
    payload = show_payload()
    payload["model_info"]["llama.context_length"] = context
    summary = module.summarize_show(payload)
    assert summary["declared_context"] == context and type(summary["declared_context"]) is int
    assert "effective_context" not in summary and "token_grant" not in summary
    rig.assert_clean()


@pytest.mark.parametrize("path,value", [
    ((), None), ((), []), (("template",), None), (("template",), 1),
    (("system",), None), (("system",), False), (("parameters",), []),
    (("model_info",), None), (("model_info",), []), (("details",), None),
    (("details", "format"), 1), (("details", "family"), []),
    (("details", "quantization_level"), None),
    *[(("model_info", "llama.context_length"), v) for v in (True, "2048", 0, -1, 2**63, 1.5, None)],
])
def test_invalid_show_fields_discard_entire_summary(load_api, capsys, path, value):
    payload = show_payload()
    if not path:
        payload = value
    elif len(path) == 1:
        payload[path[0]] = value
    else:
        payload[path[0]][path[1]] = value
    module, rig = load_api(Rig([wire({"version": "0.12.0"}), wire(payload)]))
    assert_failure(module, rig, capsys, "invalid_metadata", phase="show", attempts=2)


@pytest.mark.parametrize("missing", ["template", "model_info"])
def test_required_show_fields_cannot_be_missing(load_api, capsys, missing):
    payload = show_payload()
    del payload[missing]
    module, rig = load_api(Rig([wire({"version": "0.12.0"}), wire(payload)]))
    assert_failure(module, rig, capsys, "invalid_metadata", phase="show", attempts=2)


@pytest.mark.parametrize("phase", ["version", "show"])
@pytest.mark.parametrize("error", [None, "", MARKER, {"path": "D:/" + MARKER}])
def test_top_level_provider_error_is_rejected_even_null(load_api, capsys, phase, error):
    payload = {"version": "0.12.0"} if phase == "version" else show_payload()
    payload["error"] = error
    responses = [wire(payload)] if phase == "version" else [wire({"version": "0.12.0"}), wire(payload)]
    module, rig = load_api(Rig(responses))
    assert_failure(module, rig, capsys, "invalid_metadata", phase=phase, attempts=len(responses))


def test_missing_version_is_not_an_empty_or_default_version(load_api, capsys):
    module, rig = load_api(Rig([wire({"private": MARKER})]))
    assert_failure(module, rig, capsys, "invalid_metadata")


def test_script_imports_standard_library_only(load_api):
    module, rig = load_api()
    tree = ast.parse(SCRIPT.read_text(encoding="utf-8"))
    roots = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.level == 0
            roots.add((node.module or "").split(".")[0])
    assert roots <= sys.stdlib_module_names | {"__future__"}
    assert "http" in roots  # Require the planned standard HTTP parser, not copied HTTP code.
    rig.assert_clean()


def test_import_and_runtime_do_not_read_environment_files_or_spawn(load_api, capsys):
    module, rig = load_api()
    code, result = run_cli(module, capsys)
    assert code == 0 and result["attempts"] == 2
    rig.assert_clean()


@pytest.mark.parametrize("phase", ["import", "cli_captured_alias"])
@pytest.mark.parametrize("operation", ["open", "Popen"])
def test_guard_control_swallowed_import_or_captured_cli_io_stays_latched(phase, operation):
    # Positive control of the guard, not a replacement metadata implementation.
    # No real script/file/process is created or imported by this control.
    rig = Rig([])
    namespace = {}
    with rig.io_guard():
        exec("from builtins import open\nfrom subprocess import Popen", namespace)
        if phase == "import":
            exec("try:\n " + operation + "('synthetic-forbidden')\nexcept Exception:\n pass", namespace)
    if phase == "cli_captured_alias":
        with rig.io_guard():
            try:
                namespace[operation]("synthetic-forbidden")
            except Exception:
                pass
    assert len(rig.forbidden) == 1
    with pytest.raises(AssertionError, match="latched forbidden I/O"):
        rig.assert_clean()


@pytest.mark.parametrize("operation", [
    lambda env: env["HTTP_PROXY"], lambda env: env.get("HTTP_PROXY"),
    lambda env: "HTTP_PROXY" in env, lambda env: iter(env), lambda env: len(env),
    lambda env: env.copy(), lambda env: env.items(), lambda env: env.keys(),
    lambda env: env.values(), lambda env: dict(env),
])
def test_environment_guard_control_all_read_paths_latch_when_swallowed(operation):
    rig = Rig([])
    with rig.io_guard():
        try:
            operation(os.environ)
        except Exception:
            pass
    assert len(rig.forbidden) == 1
    with pytest.raises(AssertionError, match="latched forbidden I/O"):
        rig.assert_clean()


@pytest.mark.parametrize("violation", ["extra_socket", "repeat_connect", "wrong_address"])
def test_socket_guard_control_swallowed_attempts_are_counted_and_latched(violation):
    rig = Rig([b""])
    sock = rig.socket()
    sock.connect(("127.0.0.1", 11434))
    try:
        if violation == "extra_socket":
            rig.socket()
        else:
            sock.connect(("127.0.0.1", 11434) if violation == "repeat_connect" else ("192.0.2.1", 11434))
    except Exception:
        pass
    sock.close()
    assert rig.creation_attempts == (2 if violation == "extra_socket" else 1)
    assert len(rig.connection_attempts) == (1 if violation == "extra_socket" else 2)
    assert sock.connect_calls == len(rig.connection_attempts)
    assert sock.address == ("127.0.0.1", 11434)  # First accepted address not overwritten.
    assert len(rig.forbidden) == 1
    with pytest.raises(AssertionError, match="latched forbidden I/O"):
        rig.assert_clean()


def test_unexpected_exception_is_fixed_at_cli_boundary(load_api, monkeypatch, capsys):
    module, rig = load_api()
    # Transport failure that is not a known HTTP/socket exception. No replacement
    # of main/collector/summary: production must sanitize this at its outer boundary.
    def fail_send(self, data):
        raise RuntimeError(MARKER + " D:/private/secret")
    monkeypatch.setattr(WireSocket, "sendall", fail_send)
    assert_failure(module, rig, capsys, "internal_error")
