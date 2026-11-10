"""Bounded candidate-only Ollama metadata; no inference or project imports."""

import hashlib
import http.client
import io
import json
import math
import re
import socket
import sys
import threading
import time


_ENDPOINT = "http://127.0.0.1:11434"
_MODEL = "gmm-llama:latest"
_ADDRESS = ("127.0.0.1", 11434)
_WIRE_LIMIT = 1024 * 1024
_SHOW_BODY = b'{"model":"gmm-llama:latest","verbose":false}'
_VERSION = re.compile(r"[0-9]{1,4}\.[0-9]{1,4}\.[0-9]{1,4}(?:-(?:rc|beta|alpha)\.[0-9]{1,4})?")
_QUANTIZATIONS = frozenset((
    "F32", "F16", "BF16", "Q4_0", "Q4_1", "Q4_K_S", "Q4_K_M",
    "Q5_0", "Q5_1", "Q5_K_S", "Q5_K_M", "Q6_K", "Q8_0",
))
_ERRORS = frozenset((
    "invalid_arguments", "http_error", "transport_error", "request_timeout",
    "overall_timeout", "response_too_large", "invalid_http",
    "unsupported_encoding", "invalid_json", "invalid_metadata", "internal_error",
))


class _Failure(Exception):
    def __init__(self, code):
        self.code = code if type(code) is str and code in _ERRORS else "internal_error"
        super().__init__(self.code)


def _object(payload):
    if type(payload) is not dict or "error" in payload:
        raise _Failure("invalid_metadata")


def summarize_version(payload):
    """Return only a conservative native version string, not build authority."""
    _object(payload)
    value = payload.get("version")
    if type(value) is not str or _VERSION.fullmatch(value) is None:
        raise _Failure("invalid_metadata")
    return {"server_version": value}


def _fingerprint(payload, key):
    if key not in payload:
        return {"present": False, "utf8_bytes": None, "sha256": None}
    value = payload[key]
    if type(value) is not str:
        raise _Failure("invalid_metadata")
    try:
        encoded = value.encode("utf-8", errors="strict")
    except UnicodeError:
        pass
    else:
        return {"present": True, "utf8_bytes": len(encoded),
                "sha256": hashlib.sha256(encoded).hexdigest()}
    raise _Failure("invalid_metadata")


def _choice(payload, key, allowed):
    if key not in payload:
        return "missing"
    value = payload[key]
    if type(value) is not str:
        raise _Failure("invalid_metadata")
    return value if value in allowed else "unknown"


def summarize_show(payload):
    """Hash original text; never return templates, systems, paths or raw metadata."""
    _object(payload)
    if type(payload.get("template")) is not str or type(payload.get("model_info")) is not dict:
        raise _Failure("invalid_metadata")
    details = payload.get("details", {})
    if type(details) is not dict:
        raise _Failure("invalid_metadata")
    info = payload["model_info"]
    context = None
    if "llama.context_length" in info:
        context = info["llama.context_length"]
        if type(context) is not int or not 0 < context <= 2**63 - 1:
            raise _Failure("invalid_metadata")
    return {
        "template": _fingerprint(payload, "template"),
        "system": _fingerprint(payload, "system"),
        "parameters": _fingerprint(payload, "parameters"),
        "format": _choice(details, "format", ("gguf",)),
        "family": _choice(details, "family", ("llama",)),
        "quantization": _choice(details, "quantization_level", _QUANTIZATIONS),
        "declared_context": context,
        "tokenizer_identity": "unverified",
    }


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError("duplicate_key")
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError("nonfinite_json")


def _finite_float(value):
    result = float(value)
    if not math.isfinite(result):
        raise ValueError("nonfinite_json")
    return result


def _decode(data):
    try:
        return json.loads(data.decode("utf-8", errors="strict"),
                          object_pairs_hook=_pairs, parse_constant=_nonfinite,
                          parse_float=_finite_float)
    except (ValueError, UnicodeError, RecursionError):
        pass
    raise _Failure("invalid_json")


def _overall_check(deadline):
    if time.monotonic() >= deadline:
        raise _Failure("overall_timeout")


class _RequestDeadline:
    """Own one socket's absolute deadline, including stalled header/body reads."""

    def __init__(self, sock, overall):
        self.sock = sock
        self.overall = overall
        self.end = min(time.monotonic() + 5.0, overall)
        self.expired = False
        self.close_failed = False

    def expire(self):
        self.expired = True
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            # An unconnected/already closed socket still needs close below.
            pass
        except Exception:
            self.close_failed = True
        try:
            self.sock.close()
        except Exception:
            self.close_failed = True

    def check(self):
        now = time.monotonic()
        if now >= self.overall:
            raise _Failure("overall_timeout")
        if self.expired or now >= self.end:
            raise _Failure("request_timeout")

    def before_io(self):
        self.check()
        remaining = self.end - time.monotonic()
        if remaining <= 0:
            self.check()
        self.sock.settimeout(remaining)


class _CappedReader(io.RawIOBase):
    """Cap actual parser input, including headers and chunk framing; no prefetch."""

    def __init__(self, raw, deadline):
        super().__init__()
        self.raw = raw
        self.deadline = deadline
        self.used = 0

    def readable(self):
        return True

    def _part(self, size, *, line=False):
        self.deadline.before_io()
        available = _WIRE_LIMIT + 1 - self.used
        amount = available if size is None or size < 0 else min(size, available)
        data = self.raw.readline(amount) if line else self.raw.read(amount)
        self.deadline.check()
        self.used += len(data)
        if self.used > _WIRE_LIMIT:
            raise _Failure("response_too_large")
        return data

    def read(self, size=-1):
        # HTTPResponse._safe_read expects file.read(n) to fill n or encounter EOF.
        # SocketIO is unbuffered and may otherwise return a short, non-EOF read.
        pieces = []
        remaining = size
        while remaining != 0:
            piece = self._part(remaining)
            if not piece:
                break
            pieces.append(piece)
            if remaining > 0:
                remaining -= len(piece)
        return b"".join(pieces)

    def read1(self, size=-1):
        return self._part(size)

    def readline(self, size=-1):
        return self._part(size, line=True)

    def readinto(self, target):
        data = self.read(len(target))
        target[:len(data)] = data
        return len(data)

    def close(self):
        try:
            if not self.closed:
                self.raw.close()
        finally:
            super().close()


class _ResponseSocket:
    """Only the makefile boundary required by the real stdlib HTTPResponse."""

    def __init__(self, sock, deadline):
        self.sock, self.deadline = sock, deadline
        self.reader = None

    def makefile(self, mode):
        self.reader = _CappedReader(self.sock.makefile(mode, buffering=0), self.deadline)
        return self.reader


def _validate_headers(response, reader):
    encodings = response.headers.get_all("Content-Encoding", [])
    if any(value.strip().lower() != "identity" for value in encodings):
        raise _Failure("unsupported_encoding")
    lengths = response.headers.get_all("Content-Length", [])
    transfers = response.headers.get_all("Transfer-Encoding", [])
    if len(lengths) > 1 or len(transfers) > 1 or (lengths and transfers):
        raise _Failure("invalid_http")
    if transfers and transfers[0].strip().lower() != "chunked":
        raise _Failure("invalid_http")
    if lengths:
        text = lengths[0].strip()
        if re.fullmatch(r"[0-9]+", text) is None:
            raise _Failure("invalid_http")
        # Avoid converting an adversarial integer before applying a fixed byte cap.
        digits = text.lstrip("0") or "0"
        if len(digits) > 7 or int(digits) + reader.used > _WIRE_LIMIT:
            raise _Failure("response_too_large")


def _request(method, path, body, overall, report):
    _overall_check(overall)
    sock = deadline = None
    timer = connection = response = adapter = None
    started = False
    failure = None
    data = b""
    cleanup_failed = False
    try:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        deadline = _RequestDeadline(sock, overall)
        timer = threading.Timer(max(0.0, deadline.end - time.monotonic()), deadline.expire)
        timer.start()
        started = True
        deadline.before_io()
        report["attempts"] += 1
        sock.connect(_ADDRESS)
        deadline.check()
        connection = http.client.HTTPConnection(*_ADDRESS)
        connection.auto_open = 0  # No reconnect/DNS fallback if the owned socket closes.
        connection.sock = sock
        connection.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        connection.putheader("Host", "127.0.0.1:11434")
        connection.putheader("Accept", "application/json")
        connection.putheader("Connection", "close")
        if body is not None:
            connection.putheader("Content-Type", "application/json")
            connection.putheader("Content-Length", str(len(body)))
        deadline.before_io()
        connection.endheaders()
        if body is not None:
            deadline.before_io()
            connection.send(body)
        adapter = _ResponseSocket(sock, deadline)
        response = http.client.HTTPResponse(adapter, method=method)
        response.begin()
        if response.status != 200:
            raise _Failure("http_error")
        _validate_headers(response, adapter.reader)
        pieces = []
        while True:
            piece = response.read1(65536)
            if not piece:
                if response.length not in (None, 0):
                    raise _Failure("invalid_http")
                break
            pieces.append(piece)
        deadline.check()
        data = b"".join(pieces)
    except _Failure as exc:
        failure = exc.code
    except http.client.HTTPException:
        failure = "invalid_http"
    except TimeoutError:
        failure = "request_timeout"
    except OSError:
        failure = "transport_error"
    finally:
        # Attempt every release even when another fails; never leave a live Timer.
        # No cleanup exception, request object or provider diagnostic is emitted.
        if timer is not None:
            try:
                timer.cancel()
            except Exception:
                cleanup_failed = True
        for resource in (response, adapter.reader if adapter is not None else None, connection, sock):
            if resource is not None:
                try:
                    resource.close()
                except Exception:
                    cleanup_failed = True
        if started:
            try:
                timer.join()
            except Exception:
                cleanup_failed = True
        # Recheck after join: expiration wins even over a close-induced OSError
        # or unexpected exception, and delayed cleanup cannot admit the next call.
        if deadline is not None:
            deadline.check()
        if cleanup_failed or (deadline is not None and deadline.close_failed):
            raise _Failure("internal_error")
    if failure is not None:
        raise _Failure(failure)
    return data


def _empty_report():
    return {
        "schema_version": 1, "scope": "candidate_metadata_only",
        "assembly_match": "unverified", "enforcement": "unverified", "attempts": 0,
        "version": {"state": "not_attempted", "error": None, "summary": None},
        "show": {"state": "not_attempted", "error": None, "summary": None},
    }


def _fail(report, code):
    phase = "version" if report["version"]["state"] != "ok" else "show"
    report[phase] = {"state": "error", "error": code, "summary": None}


def _validate_candidate(endpoint, model):
    if type(endpoint) is not str or type(model) is not str or endpoint != _ENDPOINT or model != _MODEL:
        raise _Failure("invalid_arguments")


def _collect_into(endpoint, model, report, overall):
    _validate_candidate(endpoint, model)
    for phase, method, path, body, summarize in (
        ("version", "GET", "/api/version", None, summarize_version),
        ("show", "POST", "/api/show", _SHOW_BODY, summarize_show),
    ):
        _overall_check(overall)
        data = _request(method, path, body, overall, report)
        _overall_check(overall)
        summary = summarize(_decode(data))
        _overall_check(overall)
        report[phase] = {"state": "ok", "error": None, "summary": summary}


def collect_metadata(endpoint, model):
    """Standalone API; fixed known failures, no automatic execution on import."""
    report = _empty_report()
    try:
        _collect_into(endpoint, model, report, time.monotonic() + 15.0)
    except _Failure as exc:
        _fail(report, exc.code)
    return report


def _arguments(argv):
    if type(argv) not in (list, tuple) or len(argv) != 4 or any(type(v) is not str for v in argv):
        raise _Failure("invalid_arguments")
    values = {}
    for offset in (0, 2):
        flag, value = argv[offset:offset + 2]
        if flag not in ("--endpoint", "--model") or flag in values:
            raise _Failure("invalid_arguments")
        values[flag] = value
    endpoint, model = values["--endpoint"], values["--model"]
    _validate_candidate(endpoint, model)
    return endpoint, model


def main(argv=None):
    report = _empty_report()
    overall = time.monotonic() + 15.0
    try:
        endpoint, model = _arguments(sys.argv[1:] if argv is None else argv)
        # Share the same collector core as the public API. Keeping its report
        # local lets the outermost exception boundary preserve actual attempts.
        _collect_into(endpoint, model, report, overall)
    except _Failure as exc:
        _fail(report, exc.code)
    except Exception:
        _fail(report, "internal_error")
    if time.monotonic() >= overall:
        _fail(report, "overall_timeout")
    output = json.dumps(report, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    if time.monotonic() >= overall:
        _fail(report, "overall_timeout")
        output = json.dumps(report, ensure_ascii=True, allow_nan=False, separators=(",", ":"))
    sys.stdout.write(output + "\n")
    if any(report[phase]["error"] == "invalid_arguments" for phase in ("version", "show")):
        return 2
    return 0 if report["show"]["state"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
