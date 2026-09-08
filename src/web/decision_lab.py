"""Explicit loopback-only acceptance app; never imported by the production app."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
import ipaddress
import json
from pathlib import Path
import secrets
import time
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

from src.agent.contracts import AgentContext
from src.agent.contracts.decision import decode_protocol_json, DecisionProtocolError
from src.agent.harness.decision_loop import ModelDecisionLoop
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.persistence.redaction import redact_sensitive
from src.agent.tooling.factory import build_tool_registry
from src.agent.tools.property_calculator import PropertyCalculator
from src.web.chat_handler import ChatHandler


PREFIX = '/decision-lab/'
COOKIE = 'medchat_decision_lab_session'
ASSETS = Path(__file__).parent / 'static' / 'decision_lab'


class Start(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['start']
    case_id: Literal['chat', 'properties', 'clarify', 'invalid']


class Resume(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    action: Literal['resume']
    continuation_id: str = Field(min_length=1, max_length=128)
    query: str = Field(min_length=1, max_length=8192)


COMMAND = TypeAdapter(Annotated[Start | Resume, Field(discriminator='action')])


class TerminalBuffer:
    """Expose completion only after the owner-bound continuation is registered."""
    def __init__(self, websocket):
        self.websocket = websocket
        self.terminal = None

    async def send_text(self, text):
        if json.loads(text).get('type') == 'complete':
            if self.terminal is not None:
                raise RuntimeError('Duplicate lab terminal')
            self.terminal = text
        else:
            await self.websocket.send_text(text)

    async def flush(self):
        if self.terminal is None:
            raise RuntimeError('Missing lab terminal')
        await self.websocket.send_text(self.terminal)


@dataclass
class Session:
    owner: str
    session_id: str
    expires: float
    active: bool = False
    task: asyncio.Task | None = None
    pending: dict = field(default_factory=dict)
    revoked: asyncio.Event = field(default_factory=asyncio.Event)


def case_options(case_id, session):
    # These fixtures specify acceptance obligations, NOT a predetermined tool sequence.
    prompts = {
        'chat': '你好，请简短介绍你能提供哪些帮助。不要调用科研工具。',
        'properties': '请计算这两个分子的性质。\nSMILES: CCO\nSMILES: CCN',
        'clarify': '请计算分子性质。我还没有提供 SMILES，请先询问我，不要自行选择分子。',
        'invalid': '请分析这个 SMILES 的成药性：CC(C)((。无效时不要返回模拟性质。',
    }
    subjects = {'properties': ['CCO', 'CCN'], 'clarify': ['CCN']}.get(case_id)
    scientific = case_id != 'chat'
    requirements = None if not subjects else {'version': '1', 'molecular_results': [{
        'tool_name': 'property_calculator', 'exact_molecule_count': len(subjects),
        'expected_smiles': subjects,
        'required_metrics': ['molecular_weight', 'logp', 'tpsa', 'hbd', 'hba', 'qed'],
    }]}
    return dict(context=AgentContext(prompts[case_id], 'lab-' + uuid4().hex,
                    user_id=session.owner, session_id=session.session_id),
                request_kind='scientific' if scientific else 'chat',
                allowed_tools={'property_calculator'} if scientific else set(),
                required_tools={'property_calculator'} if scientific else set(),
                requirements=requirements)


def create_decision_lab(model, db_path, *, port=6012, mode='native', session_ttl=1800,
                        max_sessions=16, clock=time.monotonic):
    """An explicitly constructed local test app, not authentication for deployment."""
    if (type(port) is not int or not 1024 <= port <= 65535 or mode not in ('native', 'json')
            or type(session_ttl) is not int or not 1 <= session_ttl <= 1800
            or type(max_sessions) is not int or not 1 <= max_sessions <= 16):
        raise ValueError('Invalid lab limits')
    host = f'127.0.0.1:{port}'
    origin = 'http://' + host
    sessions: dict[str, Session] = {}
    registry = build_tool_registry([PropertyCalculator()])
    store = SQLiteAgentStateStore(db_path)
    decision_loop = ModelDecisionLoop(model, registry, store, mode=mode,
        max_model_requests=4, max_tool_attempts=2, timeout_seconds=90)
    handler = ChatHandler(None, None, None, {})
    started_runs = 0

    @asynccontextmanager
    async def lifespan(app):
        try:
            yield
        finally:
            tasks = [s.task for s in sessions.values() if s.task and not s.task.done()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            sessions.clear()
            registry.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    def local(connection, *, require_origin=False):
        try:
            peer_is_local = bool(connection.client and ipaddress.ip_address(connection.client.host).is_loopback)
        except ValueError:
            peer_is_local = False
        return (peer_is_local and connection.headers.get('host') == host
                and (not require_origin or connection.headers.get('origin') == origin))

    @app.middleware('http')
    async def guard(request: Request, call_next):
        if not local(request, require_origin=request.method != 'GET'):
            response = JSONResponse({'success': False}, status_code=403)
        else:
            response = await call_next(request)
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'no-referrer',
            'Content-Security-Policy': "default-src 'none'; script-src 'self'; style-src 'self'; "
                f"connect-src 'self' ws://{host}; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })
        return response

    @app.get('/')
    async def root():
        return RedirectResponse(PREFIX)

    @app.get(PREFIX)
    async def page():
        return FileResponse(ASSETS / 'index.html')

    @app.get(PREFIX + '{asset}')
    async def asset(asset: str):
        if asset not in ('app.js', 'style.css'):
            return JSONResponse({'success': False}, status_code=404)
        return FileResponse(ASSETS / asset)

    @app.post(PREFIX + 'session')
    async def establish(request: Request):
        for token, session in list(sessions.items()):
            if session.expires <= clock():
                session.revoked.set()
                if session.task:
                    session.task.cancel()
                sessions.pop(token)
        old = request.cookies.get(COOKIE)
        if old in sessions:
            previous = sessions.pop(old)
            previous.revoked.set()
            if previous.task:
                previous.task.cancel()
        if len(sessions) >= max_sessions:
            return JSONResponse({'success': False}, status_code=429)
        token = secrets.token_urlsafe(32)
        sessions[token] = Session(uuid4().hex, uuid4().hex, clock() + session_ttl)
        response = JSONResponse({'success': True})
        # HTTP loopback test only: a production HTTPS session requires Secure/authentication.
        response.set_cookie(COOKIE, token, max_age=session_ttl, httponly=True,
                            samesite='strict', path=PREFIX)
        return response

    async def error(ws, code):
        await ws.send_json({'type': 'error', 'code': code,
                           'message': '请求被拒绝或会话已失效；未执行新的科研计算。'})

    async def receive_text(ws, session):
        incoming = asyncio.create_task(ws.receive())
        revoked = asyncio.create_task(session.revoked.wait())
        try:
            done, _ = await asyncio.wait({incoming, revoked}, return_when=asyncio.FIRST_COMPLETED)
            if revoked in done:
                raise WebSocketDisconnect(1008)
            message = incoming.result()
        finally:
            for task in (incoming, revoked):
                task.cancel()
            await asyncio.gather(incoming, revoked, return_exceptions=True)
        if message['type'] == 'websocket.disconnect':
            raise WebSocketDisconnect(message.get('code', 1000))
        if not isinstance(message.get('text'), str):
            await ws.close(code=1003)
            raise WebSocketDisconnect(1003)
        return message['text']

    @app.websocket(PREFIX + 'ws')
    async def socket(ws: WebSocket):
        nonlocal started_runs
        token = ws.cookies.get(COOKIE)
        session = sessions.get(token)
        if (not local(ws, require_origin=True) or not session or session.expires <= clock()
                or session.active):
            await ws.close(code=1008)
            return
        session.active = True
        receiver = None
        try:
            await ws.accept()
            while session.expires > clock() and sessions.get(token) is session:
                raw = await asyncio.wait_for(receive_text(ws, session), timeout=session.expires - clock())
                if session.expires <= clock() or sessions.get(token) is not session:
                    break
                try:
                    command = COMMAND.validate_python(decode_protocol_json(raw, max_bytes=16384))
                except (ValidationError, DecisionProtocolError):
                    await error(ws, 'invalid_request')
                    continue
                resume = {}
                if isinstance(command, Start):
                    if started_runs >= 128 or len(session.pending) >= 8:
                        await error(ws, 'lab_budget_exhausted')
                        continue
                    options = case_options(command.case_id, session)
                    started_runs += 1
                else:
                    if (not command.query.strip() or len(command.query.encode('utf-8')) > 16384
                            or redact_sensitive(command.query) != command.query):
                        await error(ws, 'invalid_clarification')
                        continue
                    options = session.pending.pop(command.continuation_id, None)
                    if options is None:
                        await error(ws, 'continuation_not_owned')
                        continue
                    resume = dict(continuation_id=command.continuation_id, clarified_query=command.query)
                transport = TerminalBuffer(ws)
                session.task = asyncio.create_task(handler.process_decision_message(transport,
                    decision_loop=decision_loop, **options, **resume))
                # Receive concurrently: a silent disconnected browser cannot leave a model call running.
                receiver = asyncio.create_task(receive_text(ws, session))
                while not session.task.done():
                    done, _ = await asyncio.wait({session.task, receiver},
                        timeout=max(0, session.expires - clock()), return_when=asyncio.FIRST_COMPLETED)
                    if not done:
                        raise asyncio.TimeoutError()
                    if receiver in done:
                        receiver.result()  # Propagate disconnect; no queued/replayed commands.
                        await error(ws, 'request_in_progress')
                        receiver = asyncio.create_task(receive_text(ws, session))
                receiver.cancel()
                with suppress(asyncio.CancelledError):
                    await receiver
                receiver = None
                result = await session.task
                if result.metadata.get('waiting_for_input'):
                    session.pending[result.metadata['continuation_id']] = options
                session.task = None
                await transport.flush()
        except (WebSocketDisconnect, asyncio.TimeoutError):
            pass
        finally:
            # A send-side disconnect may already have failed the bridge task.
            # Drain both tasks without letting that failure retain session ownership.
            pending = [task for task in (receiver, session.task) if task is not None]
            try:
                for task in pending:
                    task.cancel()
                await asyncio.gather(*pending, return_exceptions=True)
            finally:
                session.task = None
                session.active = False
                with suppress(RuntimeError, WebSocketDisconnect, OSError):
                    await ws.close()

    return app
