"""Offline contracts for the unauthenticated, in-memory RCSB source pair."""

import hashlib
import time
import traceback

import pytest
import requests

from src.target_search import downloader


FAILURE = "RCSB source download failed"
FILE_CAP = 16 * 1024 * 1024


class Response:
    def __init__(self, chunks=(b"source\r\n",), *, headers=None, status=200,
                 on_chunk=None, on_close=None):
        self.chunks = chunks
        self.headers = requests.structures.CaseInsensitiveDict(headers or {})
        self.status_code = status
        self.on_chunk = on_chunk
        self.on_close = on_close
        self.closed = 0
        self.iterated = False

    @property
    def content(self):
        pytest.fail("must not read response.content")

    @property
    def text(self):
        pytest.fail("must not read response.text")

    def iter_content(self, chunk_size):
        assert chunk_size == 64 * 1024
        self.iterated = True
        for chunk in self.chunks:
            if self.on_chunk:
                self.on_chunk()
            if isinstance(chunk, Exception):
                raise chunk
            yield chunk

    def close(self):
        self.closed += 1
        if self.on_close:
            self.on_close()


@pytest.fixture
def wire(monkeypatch):
    state = {"now": 100.0, "calls": [], "dns": [], "responses": [],
             "dns_hook": None, "request_hook": None}
    monkeypatch.setattr(time, "monotonic", lambda: state["now"])

    def dns(host, port, *args, **kwargs):
        state["dns"].append((host, port))
        if state["dns_hook"]:
            return state["dns_hook"]()
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    def get(url, **kwargs):
        state["calls"].append((url, kwargs))
        if state["request_hook"]:
            state["request_hook"]()
        assert state["responses"], "unexpected retry/fallback/request"
        response = state["responses"].pop(0)
        if isinstance(response, Exception):
            raise response
        return response

    monkeypatch.setattr(downloader.socket, "getaddrinfo", dns)
    monkeypatch.setattr(downloader.requests, "get", get)
    return state


def failed(pdb_id="1ABC", *, deadline=110.0):
    with pytest.raises(downloader.StructureDownloadError) as caught:
        downloader.fetch_rcsb_source_pair(pdb_id, deadline=deadline)
    error = caught.value
    assert str(error) == FAILURE
    assert error.__cause__ is None
    assert error.__context__ is None
    assert "private diagnostic" not in "".join(traceback.format_exception(error))


def test_exact_pair_preserves_bytes_hashes_and_transport_contract(wire):
    pdb = b"HEADER\r\n\x00\xff\n"
    cif = b"data_1ABC\r\n#\xfe\n"
    first = Response((pdb[:3], b"", pdb[3:]), headers={"Content-Length": str(len(pdb))})
    second = Response((cif,), headers={"content-encoding": "identity"})
    wire["responses"] = [first, second]
    result = downloader.fetch_rcsb_source_pair("1ABC", deadline=110)
    assert type(result) is dict
    assert result == {
        "pdb_id": "1ABC", "pdb_bytes": pdb, "cif_bytes": cif,
        "pdb_sha256": hashlib.sha256(pdb).hexdigest(),
        "cif_sha256": hashlib.sha256(cif).hexdigest(),
    }
    assert type(result["pdb_bytes"]) is bytes
    assert type(result["cif_bytes"]) is bytes
    assert wire["calls"] == [
        (f"https://files.rcsb.org/download/1ABC.{ext}", {
            "timeout": 10.0, "allow_redirects": False, "stream": True,
            "headers": {"Accept-Encoding": "identity"},
            "hooks": {"response": downloader._reject_source_pair_response},
        }) for ext in ("pdb", "cif")
    ]
    assert wire["dns"] == [("files.rcsb.org", 443)] * 4
    assert first.closed == second.closed == 1


class HostileMeta(type):
    def __eq__(cls, other):
        pytest.fail("metaclass equality invoked")

    def __hash__(cls):
        pytest.fail("metaclass hashing invoked")


class Hostile(metaclass=HostileMeta):
    def __str__(self):
        pytest.fail("input coercion invoked")

    def __float__(self):
        pytest.fail("input coercion invoked")


class StringSubclass(str):
    pass


class IntSubclass(int):
    pass


class FloatSubclass(float):
    pass


@pytest.mark.parametrize("pdb_id", [
    None, True, 1234, b"1ABC", "", "1abc", "ABCD", "1AB", "1ABCD",
    "1ABC\n", " 1ABC", "1ABC ", "１ABC", "1ΑBC", "../1ABC",
    "https://files.rcsb.org/download/1ABC.pdb", StringSubclass("1ABC"), Hostile(),
], ids=lambda value: type(value).__name__)
def test_invalid_ids_fail_before_dns_without_coercion(wire, pdb_id):
    failed(pdb_id)
    assert wire["dns"] == wire["calls"] == []


@pytest.mark.parametrize("deadline", [
    None, True, "110", float("nan"), float("inf"), -float("inf"),
    100, 99.0, -1, 10 ** 400, IntSubclass(110), FloatSubclass(110), Hostile(),
], ids=lambda value: type(value).__name__)
def test_invalid_or_expired_deadlines_fail_before_dns(wire, deadline):
    failed(deadline=deadline)
    assert wire["dns"] == wire["calls"] == []


def test_signature_has_no_extra_arguments_or_positional_deadline(wire):
    with pytest.raises(TypeError):
        downloader.fetch_rcsb_source_pair("1ABC", 110)
    with pytest.raises(TypeError):
        downloader.fetch_rcsb_source_pair("1ABC", deadline=110, url="private diagnostic")
    assert wire["calls"] == wire["dns"] == []


def test_timeouts_use_remaining_budget_and_cap_at_thirty(wire):
    first = Response(on_close=lambda: wire.update(now=145.0))
    wire["responses"] = [first, Response()]
    downloader.fetch_rcsb_source_pair("0A09", deadline=150.0)
    assert [kwargs["timeout"] for _, kwargs in wire["calls"]] == [30, 5]


def test_dns_expiry_prevents_http_dispatch(wire):
    def dns():
        wire["now"] = 110.0
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    wire["dns_hook"] = dns
    wire["responses"] = [Response()]
    failed()
    assert wire["calls"] == []


def test_http_timeout_recomputed_after_dns(wire):
    def dns():
        wire["now"] = 109.0
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    wire["dns_hook"] = dns
    wire["responses"] = [Response(), Response()]
    downloader.fetch_rcsb_source_pair("1ABC", deadline=110.0)
    assert [kwargs["timeout"] for _, kwargs in wire["calls"]] == [1, 1]


def test_http_expiry_closes_before_postrequest_dns(wire):
    first = Response()
    wire["responses"] = [first]
    wire["request_hook"] = lambda: wire.update(now=110.0)
    failed()
    assert wire["dns"] == [("files.rcsb.org", 443)]
    assert first.closed == 1 and not first.iterated


@pytest.mark.parametrize("status", [301, 302, 307, 308])
def test_real_requests_session_rejects_redirect_before_buffering_body(wire, monkeypatch, status):
    class Raw:
        consumed = 0
        closed = 0

        def stream(self, amount, decode_content=True):
            for _ in range(1741):
                self.consumed += 10240
                wire["now"] += 0.01
                yield b"x" * 10240

        def close(self):
            self.closed += 1

        def release_conn(self):
            pass

    raw = Raw()

    class Adapter(requests.adapters.BaseAdapter):
        def send(self, request, **kwargs):
            response = requests.Response()
            response.status_code = status
            response.url = request.url
            response.request = request
            response.headers["Location"] = "/never-follow"
            response.raw = raw
            return response

        def close(self):
            pass

    with requests.Session() as session:
        session.trust_env = False
        session.mount("https://", Adapter())
        monkeypatch.setattr(downloader.requests, "get", session.get)
        failed()
    assert raw.consumed == 0, "redirect handling must not buffer an unbounded response"
    assert raw.closed == 1


@pytest.mark.parametrize("stage", ["request", "chunk", "empty_chunk", "close_first", "close_second", "hash", "final_hash"])
def test_deadline_checked_through_request_chunks_close_and_hash(wire, monkeypatch, stage):
    expire = lambda: wire.update(now=110.0)
    first, second = Response(), Response()
    if stage == "request":
        wire["request_hook"] = expire
    elif stage in ("chunk", "empty_chunk"):
        first.chunks = (b"" if stage == "empty_chunk" else b"a",)
        first.on_chunk = expire
    elif stage == "close_first":
        first.on_close = expire
    elif stage == "close_second":
        second.on_close = expire
    else:
        original = hashlib.sha256
        digest_calls = []

        def digest(*args, **kwargs):
            digest_calls.append(1)
            if stage == "hash" or len(digest_calls) == 2:
                expire()
            return original(*args, **kwargs)

        monkeypatch.setattr(hashlib, "sha256", digest)
    wire["responses"] = [first, second]
    failed()
    assert first.closed == 1
    assert second.closed == (len(wire["calls"]) == 2)
    if stage in ("request", "chunk", "empty_chunk", "close_first"):
        assert len(wire["calls"]) == 1


def test_deadline_after_last_chunk_before_close(wire):
    def chunks():
        yield b"source"
        wire["now"] = 110.0

    first = Response(chunks())
    wire["responses"] = [first]
    failed()
    assert first.closed == 1
    assert len(wire["calls"]) == 1


def test_candidate_producer_never_uses_cache_database_or_paths(wire, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("source pair must not use cache/database/filesystem")

    for name in ("get_cache_dir", "get_connection", "absolute_from_project", "relative_to_project"):
        monkeypatch.setattr(downloader, name, forbidden)
    monkeypatch.setattr(downloader.Path, "open", forbidden)
    wire["responses"] = [Response(), Response()]
    assert downloader.fetch_rcsb_source_pair("1ABC", deadline=110)["pdb_id"] == "1ABC"


@pytest.mark.parametrize("headers", [
    {"Content-Length": value} for value in (
        "", "-1", "+1", "1.0", "1, 1", " 1", "1 ", "١", "1\n",
        "9" * 10000, str(FILE_CAP + 1), "0", "2", "4", 3,
    )
] + [{"Content-Encoding": value} for value in ("gzip", "deflate", "br", "identity,gzip", "")])
def test_invalid_headers_or_length_mismatch_close_without_partial_result(wire, headers):
    first = Response((b"abc",), headers=headers)
    wire["responses"] = [first]
    failed()
    assert first.closed == 1
    assert len(wire["calls"]) == 1


@pytest.mark.parametrize("chunks", [(), (b"",), ("abc",), (bytearray(b"a"),),
                                         (memoryview(b"a"),), (None,), (1,)])
def test_empty_or_non_native_bytes_rejected(wire, chunks):
    first = Response(chunks)
    wire["responses"] = [first]
    failed()
    assert first.closed == 1


def test_bytes_subclass_is_rejected(wire):
    class BytesSubclass(bytes):
        pass

    first = Response((BytesSubclass(b"a"),))
    wire["responses"] = [first]
    failed()
    assert first.closed == 1


@pytest.mark.parametrize("status", [201, 204, 206, 301, 302, 307, 308, 404, 500])
@pytest.mark.parametrize("file_index", [0, 1])
def test_only_200_no_retry_or_fallback_in_either_file(wire, status, file_index):
    responses = [Response() for _ in range(file_index)] + [Response(status=status)]
    wire["responses"] = responses.copy()
    failed()
    assert len(wire["calls"]) == file_index + 1
    assert all(response.closed == 1 for response in responses)
    assert not responses[-1].iterated


@pytest.mark.parametrize("file_index", [0, 1])
def test_per_file_overflow_without_content_length(wire, file_index):
    responses = [Response() for _ in range(file_index)] + [
        Response([b"a" * (64 * 1024)] * 256 + [b"x"])
    ]
    wire["responses"] = responses.copy()
    failed()
    assert all(response.closed == 1 for response in responses)


def test_exact_file_and_aggregate_caps_are_accepted(wire):
    chunks = [b"a" * (64 * 1024)] * 256
    responses = [Response(chunks, headers={"Content-Length": str(FILE_CAP)}) for _ in range(2)]
    wire["responses"] = responses.copy()
    result = downloader.fetch_rcsb_source_pair("1ABC", deadline=110)
    assert len(result["pdb_bytes"]) == len(result["cif_bytes"]) == FILE_CAP
    assert all(response.closed == 1 for response in responses)


@pytest.mark.parametrize("where", ["request", "stream", "close"])
def test_second_file_exception_is_fixed_reason_without_partial_result(wire, where):
    raw = requests.RequestException("private diagnostic https://host/path body")

    def crash():
        raise raw

    first, second = Response(), Response()
    if where == "stream":
        second.chunks = (b"prefix", raw)
    elif where == "close":
        second.on_close = crash
    wire["responses"] = [first, raw if where == "request" else second]
    failed()
    assert first.closed == 1
    assert second.closed == (where != "request")
    assert len(wire["calls"]) == 2


@pytest.mark.parametrize("answer", ["private", "changed", "exception"])
def test_postrequest_dns_rejection_always_closes_response(wire, answer):
    first = Response()
    wire["responses"] = [first]

    def dns():
        if len(wire["dns"]) == 1:
            return [(2, 1, 6, "", ("93.184.216.34", 443))]
        if answer == "exception":
            raise OSError("private diagnostic")
        address = "10.0.0.1" if answer == "private" else "93.184.216.35"
        return [(2, 1, 6, "", (address, 443))]

    wire["dns_hook"] = dns
    failed()
    assert first.closed == 1
    assert not first.iterated
    assert len(wire["calls"]) == 1


def test_request_guard_closes_if_postrequest_dns_raises(wire):
    first = Response()
    wire["responses"] = [first]

    def dns():
        if len(wire["dns"]) == 2:
            raise OSError("private diagnostic")
        return [(2, 1, 6, "", ("93.184.216.34", 443))]

    wire["dns_hook"] = dns
    with pytest.raises(downloader.StructureDownloadError):
        downloader._request_structure("https://files.rcsb.org/download/1ABC.pdb", {"files.rcsb.org"})
    assert first.closed == 1
