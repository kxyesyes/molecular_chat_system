import asyncio
import functools
import hashlib
import os
import re
import sqlite3
import stat
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from http.cookies import SimpleCookie
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

import src.web.agent_session as agent_session_module
from src.web.agent_session import AgentSessionMiddleware, AgentSessionStore, _cookie_token


COOKIE_NAME = "medchat_agent_session"


def make_app(store):
    app = FastAPI()
    app.add_middleware(AgentSessionMiddleware, store=store)

    @app.get("/who")
    async def who(request: Request):
        return {"id": request.scope["agent_session_id"]}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text(websocket.scope["agent_session_id"])
        await websocket.close()

    return app


def make_client(store, *, base_url="http://localhost"):
    return TestClient(make_app(store), base_url=base_url)


def cookie_from(response):
    cookie = SimpleCookie()
    cookie.load(response.headers["set-cookie"])
    return cookie[COOKIE_NAME]


def test_two_browsers_get_distinct_opaque_cookies_and_persist_across_store_restart(tmp_path):
    db_path = tmp_path / "sessions.sqlite"
    store = AgentSessionStore(db_path)
    app = make_app(store)
    first = TestClient(app, base_url="http://localhost")
    second = TestClient(app, base_url="http://localhost")
    one, two = first.get("/who"), second.get("/who")
    first_cookie, second_cookie = cookie_from(one), cookie_from(two)

    assert one.json()["id"] != two.json()["id"]
    assert first_cookie.value != second_cookie.value
    assert re.fullmatch(r"[A-Za-z0-9_-]{40,}", first_cookie.value)
    assert first_cookie["httponly"] is True
    assert first_cookie["samesite"].lower() == "lax"
    assert first_cookie["path"] == "/"
    assert first_cookie["max-age"] == str(90 * 86400)
    assert not first_cookie["secure"]

    renewed = first.get("/who")
    assert renewed.json()["id"] == one.json()["id"]
    assert cookie_from(renewed).value == first_cookie.value
    restarted = make_client(AgentSessionStore(db_path))
    restarted.cookies.set(COOKIE_NAME, first_cookie.value)
    assert restarted.get("/who").json()["id"] == one.json()["id"]

    with sqlite3.connect(db_path) as conn:
        rows = conn.execute("SELECT token_digest, session_id, created_at, expires_at FROM agent_sessions").fetchall()
    assert len(rows) == 2
    assert any(row[0] == hashlib.sha256(first_cookie.value.encode("ascii")).hexdigest() for row in rows)
    assert all(first_cookie.value not in str(row) and second_cookie.value not in str(row) for row in rows)
    assert all(row[3] > row[2] for row in rows)


def test_first_session_creates_database_parent(tmp_path):
    db_path = tmp_path / "new-config" / "sessions.sqlite"

    response = make_client(AgentSessionStore(db_path)).get("/who")

    assert response.status_code == 200
    assert db_path.is_file()


def test_store_requests_private_parent_creation_mode(tmp_path, monkeypatch):
    parent = tmp_path / "new-config"
    requested_modes = []
    real_mkdir = Path.mkdir

    def record_mkdir(path, mode=0o777, parents=False, exist_ok=False):
        if path == parent:
            requested_modes.append(mode)
        return real_mkdir(path, mode=mode, parents=parents, exist_ok=exist_ok)

    monkeypatch.setattr(Path, "mkdir", record_mkdir)
    AgentSessionStore(parent / "sessions.sqlite")

    assert requested_modes == [0o700]


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory permission semantics")
def test_store_creates_private_parent_directory(tmp_path):
    parent = tmp_path / "new-config"
    AgentSessionStore(parent / "sessions.sqlite")

    assert stat.S_IMODE(parent.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name != "posix", reason="POSIX directory permission semantics")
def test_store_preserves_existing_parent_permissions(tmp_path):
    parent = tmp_path / "existing-config"
    parent.mkdir()
    parent.chmod(0o750)
    AgentSessionStore(parent / "sessions.sqlite")

    assert stat.S_IMODE(parent.stat().st_mode) == 0o750


def test_expired_and_unknown_cookies_get_new_identity(tmp_path):
    store = AgentSessionStore(tmp_path / "sessions.sqlite")
    client = make_client(store)
    original = client.get("/who")
    token = cookie_from(original).value
    with sqlite3.connect(store.db_path) as conn:
        conn.execute("UPDATE agent_sessions SET expires_at = 0 WHERE token_digest = ?", (hashlib.sha256(token.encode("ascii")).hexdigest(),))
    assert store.resolve(token) is None
    expired = client.get("/who")
    assert expired.json()["id"] != original.json()["id"]
    assert cookie_from(expired).value != token

    unknown_client = make_client(store)
    unknown_client.cookies.set(COOKIE_NAME, "synthetic-unknown-token")
    unknown = unknown_client.get("/who")
    assert unknown.json()["id"] != expired.json()["id"]
    assert cookie_from(unknown).value != "synthetic-unknown-token"


def test_http_renews_idle_ttl_but_websocket_does_not_write(tmp_path):
    store = AgentSessionStore(tmp_path / "sessions.sqlite")
    client = make_client(store)
    token = cookie_from(client.get("/who")).value
    digest = hashlib.sha256(token.encode("ascii")).hexdigest()
    near_expiry = time.time() + 60
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            "UPDATE agent_sessions SET expires_at = ? WHERE token_digest = ?",
            (near_expiry, digest),
        )
    with client.websocket_connect(
        "/ws",
        headers={
            "host": "localhost",
            "origin": "http://localhost",
            "cookie": f"{COOKIE_NAME}={token}",
        },
    ) as ws:
        ws.receive_text()
    with sqlite3.connect(store.db_path) as conn:
        after_ws = conn.execute(
            "SELECT expires_at FROM agent_sessions WHERE token_digest = ?", (digest,)
        ).fetchone()[0]
    assert after_ws == near_expiry
    assert cookie_from(client.get("/who")).value == token
    with sqlite3.connect(store.db_path) as conn:
        after_http = conn.execute(
            "SELECT expires_at FROM agent_sessions WHERE token_digest = ?", (digest,)
        ).fetchone()[0]
    assert after_http > time.time() + 89 * 86400


def test_websocket_requires_existing_cookie_and_matching_origin(tmp_path):
    client = make_client(AgentSessionStore(tmp_path / "sessions.sqlite"))
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws", headers={"host": "localhost", "origin": "http://localhost"}
        ):
            pass
    identity = client.get("/who").json()["id"]
    token = client.cookies.get(COOKIE_NAME)
    for headers in ({}, {"origin": "https://evil.example"}):
        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect("/ws", headers=headers):
                pass
    with client.websocket_connect(
        "/ws",
        headers={
            "host": "localhost",
            "origin": "http://localhost",
            "cookie": f"{COOKIE_NAME}={token}",
        },
    ) as ws:
        assert ws.receive_text() == identity


def test_websocket_rejects_empty_host_and_origin_host(tmp_path):
    client = make_client(AgentSessionStore(tmp_path / "sessions.sqlite"))
    client.get("/who")
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/ws", headers={"host": "", "origin": "http://"}):
            pass


@pytest.mark.parametrize("host", ["evil@localhost", "localhost/path", "localhost?x=1"])
def test_websocket_rejects_malformed_host_authority(tmp_path, host):
    client = make_client(AgentSessionStore(tmp_path / "sessions.sqlite"))
    client.get("/who")

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws", headers={"host": host, "origin": "http://localhost"}
        ):
            pass


def test_websocket_does_not_treat_explicit_port_zero_as_default_port(tmp_path):
    client = make_client(AgentSessionStore(tmp_path / "sessions.sqlite"))
    token = cookie_from(client.get("/who")).value

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws",
            headers={
                "host": "localhost:0",
                "origin": "http://localhost",
                "cookie": f"{COOKIE_NAME}={token}",
            },
        ):
            pass


def test_database_unavailable_fails_closed_for_http_and_websocket(tmp_path):
    good = AgentSessionStore(tmp_path / "good.sqlite")
    token, _ = good.issue()
    client = make_client(AgentSessionStore(tmp_path))
    client.cookies.set(COOKIE_NAME, token)
    response = client.get("/who")
    assert response.status_code == 503
    assert response.json() == {"detail": "Session service unavailable"}
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/ws",
            headers={
                "host": "localhost",
                "origin": "http://localhost",
                "cookie": f"{COOKIE_NAME}={token}",
            },
        ):
            pass


def test_secure_cookie_depends_on_actual_asgi_scheme(tmp_path):
    client = make_client(AgentSessionStore(tmp_path / "sessions.sqlite"))
    assert not cookie_from(client.get("/who", headers={"x-forwarded-proto": "https"}))["secure"]
    secure_client = make_client(
        AgentSessionStore(tmp_path / "secure.sqlite"),
        base_url="https://public.example",
    )
    assert cookie_from(secure_client.get("/who"))["secure"] is True


def test_non_loopback_plain_http_fails_closed_without_bearer_cookie(tmp_path):
    response = make_client(
        AgentSessionStore(tmp_path / "sessions.sqlite"),
        base_url="http://public.example",
    ).get("/who")

    assert response.status_code == 403
    assert "set-cookie" not in response.headers


def test_blocking_store_does_not_block_concurrent_async_marker():
    entered = threading.Event()
    release = threading.Event()

    class BlockingStore:
        def issue(self):
            entered.set()
            released_by_marker = release.wait(timeout=1)
            return "token", "session" if released_by_marker else "marker-was-blocked"

    async def app(_scope, _receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    sent = []

    async def capture(message):
        sent.append(message)

    middleware = AgentSessionMiddleware(app, BlockingStore())
    scope = {
        "type": "http",
        "scheme": "http",
        "headers": [(b"host", b"localhost")],
    }

    async def scenario():
        request = asyncio.create_task(middleware(scope, None, capture))
        assert await asyncio.to_thread(entered.wait, 2)
        marker_ran = asyncio.Event()

        async def marker():
            marker_ran.set()
            release.set()

        marker_task = asyncio.create_task(marker())
        await asyncio.wait_for(marker_ran.wait(), timeout=0.5)
        await asyncio.gather(request, marker_task)

    asyncio.run(scenario())

    assert scope["agent_session_id"] == "session"


def test_store_initializes_schema_wal_and_index_once_per_instance_thread_safely(
    tmp_path, monkeypatch
):
    statements = []
    statements_lock = threading.Lock()
    real_connect = sqlite3.connect

    class TracingConnection(sqlite3.Connection):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)

            def trace(statement):
                with statements_lock:
                    statements.append(statement)

            self.set_trace_callback(trace)

    monkeypatch.setattr(
        agent_session_module.sqlite3,
        "connect",
        functools.partial(real_connect, factory=TracingConnection),
    )
    store = AgentSessionStore(tmp_path / "sessions.sqlite")

    with ThreadPoolExecutor(max_workers=6) as pool:
        issued = list(pool.map(lambda _index: store.issue(), range(6)))
    store.resolve(issued[0][0])
    store.touch(issued[0][0])

    normalized = [statement.upper() for statement in statements]
    assert sum("PRAGMA JOURNAL_MODE=WAL" in statement for statement in normalized) == 1
    assert sum("CREATE TABLE IF NOT EXISTS AGENT_SESSIONS" in statement for statement in normalized) == 1
    assert sum("CREATE INDEX IF NOT EXISTS" in statement for statement in normalized) == 1


def test_http_does_not_authorize_session_when_guarded_renewal_updates_zero_rows(tmp_path):
    store = AgentSessionStore(tmp_path / "sessions.sqlite")
    client = make_client(store)
    original = client.get("/who")
    original_id = original.json()["id"]
    original_token = cookie_from(original).value
    with sqlite3.connect(store.db_path) as conn:
        conn.execute(
            """CREATE TRIGGER erase_session_before_renewal
            BEFORE UPDATE OF expires_at ON agent_sessions
            BEGIN
                DELETE FROM agent_sessions WHERE token_digest = OLD.token_digest;
            END"""
        )

    renewed = client.get("/who")

    assert renewed.json()["id"] != original_id
    assert cookie_from(renewed).value != original_token


def test_issue_performs_only_bounded_expired_cleanup_and_reads_do_not_sweep(tmp_path):
    store = AgentSessionStore(tmp_path / "sessions.sqlite")
    live_token, _ = store.issue()
    expired_count = 150
    with sqlite3.connect(store.db_path) as conn:
        conn.executemany(
            "INSERT INTO agent_sessions VALUES (?, ?, 0, 0)",
            [(f"expired-{index}", f"session-{index}") for index in range(expired_count)],
        )

    for _ in range(3):
        assert store.resolve(live_token) is not None
    with sqlite3.connect(store.db_path) as conn:
        after_reads = conn.execute(
            "SELECT COUNT(*) FROM agent_sessions WHERE expires_at <= 0"
        ).fetchone()[0]

    store.issue()
    with sqlite3.connect(store.db_path) as conn:
        after_issue = conn.execute(
            "SELECT COUNT(*) FROM agent_sessions WHERE expires_at <= 0"
        ).fetchone()[0]

    assert after_reads == expired_count
    assert expired_count - after_issue == agent_session_module.EXPIRED_CLEANUP_LIMIT
    assert store.resolve(live_token) is not None


def test_cookie_parser_combines_multiple_http2_cookie_fields():
    assert _cookie_token(
        [
            (b"cookie", b"theme=dark"),
            (b"cookie", f"{COOKIE_NAME}=split-token".encode("ascii")),
        ]
    ) == "split-token"


@pytest.mark.parametrize(
    "headers",
    [
        [
            (b"cookie", f"{COOKIE_NAME}=first".encode("ascii")),
            (b"cookie", f"{COOKIE_NAME}=second".encode("ascii")),
        ],
        [
            (
                b"cookie",
                f"{COOKIE_NAME}=first; {COOKIE_NAME}=second".encode("ascii"),
            )
        ],
    ],
)
def test_cookie_parser_rejects_duplicate_session_cookie_names(headers):
    assert _cookie_token(headers) is None


@pytest.fixture
def contended_session_store(tmp_path, monkeypatch):
    clock = SimpleNamespace(now=1000.0)
    monkeypatch.setattr(
        agent_session_module, "time", SimpleNamespace(time=lambda: clock.now)
    )
    store = AgentSessionStore(tmp_path / "sessions.sqlite")
    token, session_id = store.issue()
    real_connect = sqlite3.connect
    lock_contended = threading.Event()

    class ContendingConnection(sqlite3.Connection):
        def execute(self, statement, *args, **kwargs):
            if statement != "BEGIN IMMEDIATE":
                return super().execute(statement, *args, **kwargs)
            # Prove this worker encounters a real SQLite write lock before
            # advancing the clock; then retry using the normal busy timeout.
            super().execute("PRAGMA busy_timeout=0")
            try:
                return super().execute(statement, *args, **kwargs)
            except sqlite3.OperationalError as error:
                if str(error) != "database is locked":
                    raise
            finally:
                super().execute("PRAGMA busy_timeout=5000")
            lock_contended.set()
            return super().execute(statement, *args, **kwargs)

    monkeypatch.setattr(
        agent_session_module.sqlite3,
        "connect",
        functools.partial(real_connect, factory=ContendingConnection),
    )

    def run_after_lock_wait(operation):
        with closing(real_connect(store.db_path)) as blocker:
            blocker.execute("BEGIN IMMEDIATE")
            with ThreadPoolExecutor(max_workers=1) as pool:
                pending = pool.submit(operation)
                try:
                    assert lock_contended.wait(timeout=2), "Worker never hit the SQLite lock"
                    assert not pending.done()
                    clock.now = 1100.0
                finally:
                    # Release even on assertion failure, before joining the worker.
                    blocker.rollback()
                return pending.result(timeout=2)

    return store, token, session_id, clock, run_after_lock_wait


@pytest.mark.parametrize("method", ["touch", "resolve_and_touch"])
@pytest.mark.parametrize("expires_at", [1050.0, 1200.0], ids=["expired", "live"])
def test_renewal_checks_expiry_and_sets_ttl_after_sqlite_lock_wait(
    contended_session_store, method, expires_at
):
    store, token, session_id, clock, run_after_lock_wait = contended_session_store
    with closing(sqlite3.connect(store.db_path)) as conn:
        conn.execute("UPDATE agent_sessions SET expires_at = ?", (expires_at,))
        conn.commit()

    result = run_after_lock_wait(lambda: getattr(store, method)(token))

    if expires_at <= clock.now:
        assert result is (False if method == "touch" else None)
        assert store.resolve(token) is None
        expected_expiry = expires_at
    else:
        assert result == (True if method == "touch" else session_id)
        assert store.resolve(token) == session_id
        expected_expiry = clock.now + agent_session_module.SESSION_MAX_AGE
    with closing(sqlite3.connect(store.db_path)) as conn:
        row = conn.execute("SELECT session_id, expires_at FROM agent_sessions").fetchone()
    assert row == (session_id, expected_expiry)


def test_issue_dates_session_and_cleans_expired_rows_after_sqlite_lock_wait(
    contended_session_store,
):
    store, old_token, _, clock, run_after_lock_wait = contended_session_store
    with closing(sqlite3.connect(store.db_path)) as conn:
        conn.execute("UPDATE agent_sessions SET expires_at = 1050")
        conn.commit()

    token, session_id = run_after_lock_wait(store.issue)

    with closing(sqlite3.connect(store.db_path)) as conn:
        rows = conn.execute(
            "SELECT session_id, created_at, expires_at FROM agent_sessions"
        ).fetchall()
    assert rows == [
        (session_id, clock.now, clock.now + agent_session_module.SESSION_MAX_AGE)
    ]
    assert store.resolve(old_token) is None
    assert store.resolve(token) == session_id


def run_http_session_request(
    store, *, method="POST", scheme="https", headers=(), response_headers=()
):
    sent = []
    handled = []
    scope = {
        "type": "http",
        "scheme": scheme,
        "method": method,
        "headers": [(b"host", b"localhost"), *headers],
    }

    async def app(request_scope, _receive, send):
        handled.append(request_scope["agent_session_id"])
        await send({
            "type": "http.response.start", "status": 204, "headers": list(response_headers)
        })
        await send({"type": "http.response.body", "body": b""})

    async def capture(message):
        sent.append(message)

    asyncio.run(AgentSessionMiddleware(app, store)(scope, None, capture))
    return sent, handled


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "origins",
    [
        [b"https://sibling.localhost"],
        [b"https://localhost:444"],
        [b"http://localhost"],
        [b"null"],
        [b""],
        [b"https://"],
        [b"https://localhost/path"],
        [b"https://@localhost"],
        [b"https://localhost?"],
        [b" https://localhost"],
        [b"https://local\nhost"],
        [b"https://localhost:"],
        [b"https://localhost", b"https://localhost"],
        [b"https://localhost", b"https://evil.example"],
    ],
)
@pytest.mark.parametrize("has_cookie", [False, True], ids=["issuance", "renewal"])
def test_unsafe_http_rejects_bad_origin_before_session_or_handler(method, origins, has_cookie):
    calls = []

    class RecordingStore:
        def issue(self):
            calls.append("issue")
            return "synthetic-token", "synthetic-session"

        def resolve_and_touch(self, token):
            calls.append("resolve_and_touch")
            return "synthetic-session"

    headers = [(b"origin", origin) for origin in origins]
    if has_cookie:
        headers.append((b"cookie", f"{COOKIE_NAME}=synthetic-token".encode("ascii")))
    sent, handled = run_http_session_request(RecordingStore(), method=method, headers=headers)

    assert sent[0]["status"] == 403
    assert sent[1]["body"] == b'{"detail":"Origin not allowed"}'
    assert not calls
    assert not handled
    assert not any(name.lower() == b"set-cookie" for name, _ in sent[0]["headers"])


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize(
    "scheme, origin",
    [("http", b"http://localhost"), ("https", b"https://localhost"),
     ("https", b"https://localhost:443"), ("https", None)],
)
def test_unsafe_http_accepts_same_origin_and_no_origin_native_clients(tmp_path, method, scheme, origin):
    store = AgentSessionStore(tmp_path / "sessions.sqlite")
    headers = [] if origin is None else [(b"origin", origin)]
    sent, handled = run_http_session_request(store, method=method, scheme=scheme, headers=headers)

    assert sent[0]["status"] == 204
    assert len(handled) == 1
    cookie = SimpleCookie()
    cookie.load(dict(sent[0]["headers"])[b"set-cookie"].decode("ascii"))
    assert store.resolve(cookie[COOKIE_NAME].value) == handled[0]


@pytest.mark.parametrize("method", ["GET", "HEAD", "OPTIONS", "TRACE"])
def test_safe_http_methods_do_not_require_matching_origin(tmp_path, method):
    sent, handled = run_http_session_request(
        AgentSessionStore(tmp_path / "sessions.sqlite"),
        method=method,
        headers=[(b"origin", b"https://foreign.example")],
    )
    assert sent[0]["status"] == 204
    assert len(handled) == 1


@pytest.mark.parametrize("scheme", ["https", "wss"])
def test_origin_validation_recognizes_secure_http_and_websocket_schemes(scheme):
    assert agent_session_module._origin_allowed({
        "scheme": scheme,
        "headers": [(b"host", b"localhost"), (b"origin", b"https://localhost")],
    })


@pytest.mark.parametrize(
    "upstream_cache_headers",
    [
        [],
        [(b"cache-control", b"public, max-age=3600, s-maxage=86400")],
        [(b"Cache-Control", b"public"), (b"cache-control", b"immutable, max-age=3600")],
    ],
    ids=["missing", "public", "duplicate-mixed-case"],
)
@pytest.mark.parametrize("renewal", [False, True], ids=["issuance", "renewal"])
def test_session_issuance_and_renewal_override_cache_control(tmp_path, upstream_cache_headers, renewal):
    store = AgentSessionStore(tmp_path / "sessions.sqlite")
    preserved_headers = [(b"x-handler", b"preserved"), (b"set-cookie", b"theme=dark")]
    request_headers = []
    original_session_id = None
    original_token = None
    if renewal:
        original_token, original_session_id = store.issue()
        request_headers = [(b"cookie", f"{COOKIE_NAME}={original_token}".encode("ascii"))]

    sent, handled = run_http_session_request(
        store,
        method="GET",
        headers=request_headers,
        response_headers=[*upstream_cache_headers, *preserved_headers],
    )
    headers = sent[0]["headers"]
    cache_values = [value for name, value in headers if name.lower() == b"cache-control"]
    assert cache_values == [b"private, no-store"]
    assert all(header in headers for header in preserved_headers)
    cookies = SimpleCookie()
    for name, value in headers:
        if name.lower() == b"set-cookie":
            cookies.load(value.decode("ascii"))
    token = cookies[COOKIE_NAME].value
    assert store.resolve(token) == handled[0]
    if renewal:
        assert handled[0] == original_session_id
        assert token == original_token
