"""Server-owned anonymous browser sessions for Agent Web entry points."""

import hashlib
import asyncio
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from http.cookies import SimpleCookie
from pathlib import Path
from urllib.parse import urlsplit


COOKIE_NAME = "medchat_agent_session"
SESSION_MAX_AGE = 90 * 86400
UNAVAILABLE_BODY = b'{"detail":"Session service unavailable"}'
HTTPS_REQUIRED_BODY = b'{"detail":"HTTPS required"}'
ORIGIN_DENIED_BODY = b'{"detail":"Origin not allowed"}'
EXPIRED_CLEANUP_LIMIT = 100


class AgentSessionStore:
    """Persist only token digests; never persist browser bearer tokens."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._initialize_lock = threading.Lock()
        self._initialized = False

    def _open(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5)
        connection.execute("PRAGMA busy_timeout=5000")
        return connection

    def _ensure_initialized(self) -> None:
        if self._initialized:
            return
        with self._initialize_lock:
            if self._initialized:
                return
            connection = self._open()
            try:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute(
                    """CREATE TABLE IF NOT EXISTS agent_sessions (
                        token_digest TEXT PRIMARY KEY,
                        session_id TEXT NOT NULL,
                        created_at REAL NOT NULL,
                        expires_at REAL NOT NULL
                    )"""
                )
                connection.execute(
                    """CREATE INDEX IF NOT EXISTS idx_agent_sessions_expires
                    ON agent_sessions(expires_at)"""
                )
                connection.commit()
                self._initialized = True
            finally:
                connection.close()

    @contextmanager
    def _connect(self):
        self._ensure_initialized()
        conn = self._open()
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("utf-8")).hexdigest()

    def issue(self) -> tuple[str, str]:
        token = secrets.token_urlsafe(32)
        session_id = secrets.token_urlsafe(24)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            conn.execute(
                """DELETE FROM agent_sessions WHERE token_digest IN (
                    SELECT token_digest FROM agent_sessions
                    WHERE expires_at <= ? ORDER BY expires_at LIMIT ?
                )""",
                (now, EXPIRED_CLEANUP_LIMIT),
            )
            conn.execute(
                "INSERT INTO agent_sessions VALUES (?, ?, ?, ?)",
                (self._digest(token), session_id, now, now + SESSION_MAX_AGE),
            )
        return token, session_id

    def resolve(self, token: str) -> str | None:
        if not isinstance(token, str) or not token:
            return None
        with self._connect() as conn:
            row = conn.execute(
                "SELECT session_id FROM agent_sessions WHERE token_digest = ? AND expires_at > ?",
                (self._digest(token), time.time()),
            ).fetchone()
        return row[0] if row else None

    def touch(self, token: str) -> bool:
        if not isinstance(token, str) or not token:
            return False
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            cursor = conn.execute(
                "UPDATE agent_sessions SET expires_at = ? WHERE token_digest = ? AND expires_at > ?",
                (now + SESSION_MAX_AGE, self._digest(token), now),
            )
        return cursor.rowcount == 1

    def resolve_and_touch(self, token: str) -> str | None:
        """Atomically validate and extend one live browser session."""
        if not isinstance(token, str) or not token:
            return None
        digest = self._digest(token)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            now = time.time()
            cursor = conn.execute(
                """UPDATE agent_sessions SET expires_at = ?
                WHERE token_digest = ? AND expires_at > ?""",
                (now + SESSION_MAX_AGE, digest, now),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT session_id FROM agent_sessions WHERE token_digest = ?",
                (digest,),
            ).fetchone()
        return row[0] if row else None


def _cookie_token(headers) -> str | None:
    values = [value.decode("latin-1") for name, value in headers if name.lower() == b"cookie"]
    if not values:
        return None
    session_fields = sum(
        1
        for value in values
        for field in value.split(";")
        if field.partition("=")[0].strip() == COOKIE_NAME
    )
    if session_fields != 1:
        return None
    matches: list[str] = []
    for value in values:
        cookie = SimpleCookie()
        try:
            cookie.load(value)
        except Exception:
            return None
        morsel = cookie.get(COOKIE_NAME)
        if morsel is not None:
            matches.append(morsel.value)
    return matches[0] if len(matches) == 1 else None


def _host_authority(scope) -> tuple[str, int] | None:
    headers = scope.get("headers", [])
    hosts = [
        value.decode("latin-1")
        for name, value in headers
        if name.lower() == b"host"
    ]
    if len(hosts) != 1:
        return None
    try:
        parsed = urlsplit("//" + hosts[0])
        if (
            not parsed.hostname
            or hosts[0] != parsed.netloc
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path
            or parsed.query
            or parsed.fragment
        ):
            return None
        scheme = scope.get("scheme")
        default_port = 443 if scheme in {"https", "wss"} else 80
        port = parsed.port if parsed.port is not None else default_port
        return parsed.hostname.lower(), port
    except ValueError:
        return None


def _is_safe_transport(scope) -> bool:
    if scope.get("scheme") in {"https", "wss"}:
        return _host_authority(scope) is not None
    authority = _host_authority(scope)
    return bool(
        scope.get("scheme") in {"http", "ws"}
        and authority
        and authority[0] in {"localhost", "127.0.0.1", "::1"}
    )


def _origin_allowed(scope) -> bool:
    headers = scope.get("headers", [])
    origins = [value.decode("latin-1") for name, value in headers if name.lower() == b"origin"]
    authority = _host_authority(scope)
    if len(origins) != 1 or authority is None:
        return False
    try:
        origin = urlsplit(origins[0])
        expected_scheme = "https" if scope.get("scheme") in {"https", "wss"} else "http"
        return (
            origins[0] == f"{origin.scheme}://{origin.netloc}"
            and not origin.netloc.endswith(":")
            and origin.scheme == expected_scheme
            and origin.hostname is not None
            and origin.hostname.lower() == authority[0]
            and (
                origin.port
                if origin.port is not None
                else (443 if origin.scheme == "https" else 80)
            ) == authority[1]
            and origin.username is None
            and origin.password is None
            and not origin.path
            and not origin.query
            and not origin.fragment
        )
    except ValueError:
        return False


class AgentSessionMiddleware:
    def __init__(self, app, store: AgentSessionStore):
        self.app = app
        self.store = store

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        if not _is_safe_transport(scope):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1008})
            else:
                await send({
                    "type": "http.response.start",
                    "status": 403,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(HTTPS_REQUIRED_BODY)).encode("ascii")),
                    ],
                })
                await send({"type": "http.response.body", "body": HTTPS_REQUIRED_BODY})
            return

        if scope["type"] == "websocket" and not _origin_allowed(scope):
            await send({"type": "websocket.close", "code": 1008})
            return

        if (
            scope["type"] == "http"
            and scope.get("method", "GET") not in {"GET", "HEAD", "OPTIONS", "TRACE"}
            and any(name.lower() == b"origin" for name, _ in scope.get("headers", []))
            and not _origin_allowed(scope)
        ):
            await send({
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(ORIGIN_DENIED_BODY)).encode("ascii")),
                ],
            })
            await send({"type": "http.response.body", "body": ORIGIN_DENIED_BODY})
            return

        token = _cookie_token(scope.get("headers", []))
        if scope["type"] == "websocket" and not token:
            await send({"type": "websocket.close", "code": 1008})
            return

        try:
            if scope["type"] == "websocket":
                session_id = (
                    await asyncio.to_thread(self.store.resolve, token)
                    if token
                    else None
                )
            else:
                session_id = (
                    await asyncio.to_thread(self.store.resolve_and_touch, token)
                    if token
                    else None
                )
            if scope["type"] == "websocket":
                if not session_id:
                    await send({"type": "websocket.close", "code": 1008})
                    return
            elif not session_id:
                token, session_id = await asyncio.to_thread(self.store.issue)
        except (sqlite3.Error, OSError):
            if scope["type"] == "websocket":
                await send({"type": "websocket.close", "code": 1013})
            else:
                await send({
                    "type": "http.response.start",
                    "status": 503,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(UNAVAILABLE_BODY)).encode("ascii"))],
                })
                await send({"type": "http.response.body", "body": UNAVAILABLE_BODY})
            return

        scope["agent_session_id"] = session_id
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return
        cookie = (
            f"{COOKIE_NAME}={token}; Max-Age={SESSION_MAX_AGE}; Path=/; "
            "HttpOnly; SameSite=Lax"
        )
        if scope.get("scheme") == "https":
            cookie += "; Secure"

        async def send_with_cookie(message):
            if message["type"] == "http.response.start":
                message = dict(message)
                message["headers"] = [
                    (name, value)
                    for name, value in message.get("headers", [])
                    if name.lower() != b"cache-control"
                ] + [
                    (b"set-cookie", cookie.encode("latin-1")),
                    (b"cache-control", b"private, no-store"),
                ]
            await send(message)

        await self.app(scope, receive, send_with_cookie)
