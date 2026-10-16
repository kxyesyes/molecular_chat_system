"""Opt-in, same-session chat preparation and explicit approval transport.

Only the trusted coordinator supplies frozen producer bytes and parameters.
The browser supplies current opaque references, never files or execution policy.
Application activation remains separate from registering this isolated seam.
"""
import asyncio
import json
import re
import time

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.task_runtime.runtime import TaskRuntime
from src.task_runtime.docking_consent import DockingConsentError, validate_approval, verify_approval
from src.web.agent_session import _origin_allowed
from src.web.docking_coordinator import DockingRequestCoordinator, _label
from src.web.docking_http import DockingHTTPMiddleware, check_feedback, envelope_for, request_deadline


_MAX_BODY = 4096
_REF = re.compile(r"sha256:[a-f0-9]{64}\Z")
_FIELDS = frozenset({"root", "revision", "receptor_ref", "ligand_ref"})
_CONTROL_SECONDS = 4.9  # Reserve response construction time within the 5s slice.
_PREPARE_SECONDS = 30.0
_MAX_CONTROL_TASKS = 32
_PHASES = frozenset({'BUSY', 'PREPARING', 'AWAITING_INPUT', 'READY', 'APPROVING',
    'EXECUTING', 'RESULT_PENDING', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'UNKNOWN',
    'CLOSED', 'UNAVAILABLE'})


def _receipt(task_id, preparation_id, phase, *, pending=False):
    """Closed projection: never forward previews, nonce, science or authority."""
    phase = phase if phase in _PHASES else 'UNKNOWN'
    return dict(task_id=task_id, preparation_id=preparation_id, phase=phase,
        reason_code='publication_unavailable' if phase == 'UNKNOWN' else 'status_' + phase.lower(),
        cleanup_pending=bool(pending), input_ready=False, execution_authorized=False)


def _empty_headers(request):
    if request.query_params:
        raise HTTPException(422, 'Invalid request')
    if request.headers.getlist('content-encoding'):
        raise HTTPException(415, 'Encoded body not accepted')
    lengths = request.headers.getlist('content-length')
    transfers = request.headers.getlist('transfer-encoding')
    if (transfers and (transfers != ['chunked'] or lengths)
            or len(lengths) > 1 or lengths and lengths != ['0']):
        raise HTTPException(422, 'Empty body required')


async def _empty_body(request, deadline):
    # This retained task ONLY reads. Its late completion has no path to dispatch.
    # Read raw chunks: Request.stream hides empty more_body chunks from callers.
    while time.monotonic() < deadline:
        message = await request.receive()
        if message['type'] == 'http.disconnect':
            raise HTTPException(408, 'Request disconnected')
        if message['type'] != 'http.request' or message.get('body', b''):
            raise HTTPException(422, 'Empty body required')
        if not message.get('more_body', False):
            return
    raise HTTPException(408, 'Request deadline exceeded')


class _ControlTransport:
    """Bound feedback, not physical cleanup; no scheduler or replacement owner."""
    def __init__(self, coordinator):
        self.coordinator = coordinator
        self.tasks = coordinator._http_control_tasks

    def retain(self, key, operation):
        task = self.tasks.get(key)
        if task is not None and not task.done():
            return task
        if len(self.tasks) >= _MAX_CONTROL_TASKS:
            raise HTTPException(503, 'Control capacity unavailable')
        pending = operation()
        try:
            task = asyncio.create_task(pending)
        except (Exception, asyncio.CancelledError) as exc:
            pending.close()
            if isinstance(exc, asyncio.CancelledError):
                raise
            raise HTTPException(503, 'Control capacity unavailable') from None
        self.tasks[key] = task
        self.coordinator._gate.retain_task(task)

        def completed(done):
            if self.tasks.get(key) is done:
                del self.tasks[key]
        task.add_done_callback(completed)
        return task

    async def wait(self, task, deadline, *, body=False):
        try:
            remaining = deadline - time.monotonic()
            if remaining > 0:
                done, _ = await asyncio.wait({task}, timeout=remaining)
                if done and time.monotonic() < deadline:
                    return task.result()
            raise HTTPException(408 if body else 503, 'Control deadline exceeded')
        finally:
            # Do not drain cancellation-resistant receive or cancel shared SQL /
            # close tasks. The existing gate retains their physical lifetime.
            if body and not task.done():
                task.cancel()

    def live(self, owner, key, value):
        if not _label(value):
            raise HTTPException(404, 'Docking receipt not found')
        return next((root for root in self.coordinator._roots.values()
                     if root.owner == owner and getattr(root, key) == value), None)

    async def durable(self, owner, key, value, deadline):
        async def read():
            try:
                row = await self.coordinator._sql(self.coordinator._tasks._owned_docking_consent,
                    owner_session_id=owner, **{key: value})
            except DockingConsentError as exc:
                if exc.reason_code == 'consent_not_found':
                    raise HTTPException(404, 'Docking receipt not found') from None
                raise HTTPException(503, 'Receipt unavailable') from None
            except Exception:
                raise HTTPException(503, 'Receipt unavailable') from None
            if (row.get('owner_session_id') != owner or row.get(key) != value
                    or not _label(row.get('task_id')) or not _label(row.get('preparation_id'))):
                raise HTTPException(503, 'Receipt unavailable')
            # No context reconstruction, backend refresh or runtime.cancel here.
            # A consumed success without its live source is NOT publishable.
            phase = 'UNKNOWN'
            if row.get('state') in {'REVOKED', 'EXPIRED'} and not row.get('execution_token'):
                phase = 'CANCELLED' if row['state'] == 'REVOKED' else 'TIMED_OUT'
            elif row.get('view_status') in {'FAILED', 'CANCELLED', 'TIMED_OUT'}:
                phase = row['view_status']
            return _receipt(row['task_id'], row['preparation_id'], phase,
                pending=row.get('cleanup_state') != 'settled' or row.get('execution_occupied') != 0)
        task = self.retain(('read', owner, key, value), read)
        return await self.wait(task, deadline)

    async def status(self, root, deadline):
        async def read():
            view = await self.coordinator.status(root_id=root.root_id, owner_session_id=root.owner)
            return _receipt(root.task_id, root.preparation_id, view['phase'],
                pending=view.get('cleanup_pending', False))
        task = self.retain(('status', id(root)), read)
        return await self.wait(task, deadline)

    async def control(self, request, owner, key, value, deadline, *, revoke):
        _empty_headers(request)
        root = self.live(owner, key, value)
        if root is None:
            await self.durable(owner, key, value, deadline)
            raise HTTPException(409, 'Live owner unavailable')
        body = self.retain(object(), lambda: _empty_body(request, deadline))
        await self.wait(body, deadline, body=True)
        # Admission belongs to THIS request, never the shared cleanup task.
        # No await separates these checks, retaining cleanup, and the exact
        # coordinator stop signal. Approval cannot enter between them.
        check_feedback(request)
        if time.monotonic() >= deadline:
            raise HTTPException(408, 'Request deadline exceeded')
        if self.coordinator._owned(root.root_id, owner) is not root:
            raise HTTPException(409, 'Live owner unavailable')
        context = root.input_context
        if revoke and (root.phase == 'APPROVING'
                or context is not None and context._execution is not None):
            raise HTTPException(409, 'Preparation already consumed')

        async def close():
            # Only physical cleanup is coalesced: no caller deadline or revoke
            # policy can reject a later, independently admitted cancel.
            await self.coordinator._close_owned_root(root)

        task = self.retain(('close', id(root)), close)
        # Retain BEFORE stopping: capacity/scheduling rejection leaves the root
        # unchanged; accepted cleanup survives HTTP timeout/disconnect.
        self.coordinator._interrupt(root, 'closed', 'CLOSED')
        try:
            await self.wait(task, deadline)
        except HTTPException as exc:
            if exc.status_code != 503 or not root.closed:
                raise
        pending = (not task.done() or root.close_state not in {'confirmed', 'not_acquired'}
                   or self.coordinator._roots.get(root.root_id) is root)
        return JSONResponse(_receipt(root.task_id, root.preparation_id, 'CLOSED', pending=pending),
            status_code=202 if pending else 200, headers={'Cache-Control': 'private, no-store'})


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("invalid number")


async def _bounded_chunks(request, deadline):
    # Request.stream hides empty more_body chunks; inspect every physical read.
    while time.monotonic() < deadline:
        message = await request.receive()
        if time.monotonic() >= deadline:
            break
        if message['type'] == 'http.disconnect':
            raise HTTPException(408, 'Request disconnected')
        if message['type'] != 'http.request':
            raise HTTPException(422, 'Invalid request')
        yield message.get('body', b'')
        if not message.get('more_body', False):
            return
    raise HTTPException(408, 'Request deadline exceeded')


async def _json_body(request, *, deadline=None):
    if request.query_params:
        raise HTTPException(422, "Invalid request")
    if request.headers.getlist("content-encoding"):
        raise HTTPException(415, "Encoded body not accepted")
    content_types = request.headers.getlist("content-type")
    if content_types != ["application/json"]:
        raise HTTPException(415, "JSON required")
    lengths = request.headers.getlist("content-length")
    transfers = request.headers.getlist('transfer-encoding')
    if transfers and (transfers != ['chunked'] or lengths):
        raise HTTPException(422, "Invalid request")
    if len(lengths) > 1 or lengths and not re.fullmatch(r"[0-9]{1,10}", lengths[0]):
        raise HTTPException(422, "Invalid request")
    if lengths and int(lengths[0]) > _MAX_BODY:
        raise HTTPException(413, "Request too large")
    data = bytearray()
    chunks = request.stream() if deadline is None else _bounded_chunks(request, deadline)
    async for chunk in chunks:
        if len(data) + len(chunk) > _MAX_BODY:
            raise HTTPException(413, "Request too large")
        data.extend(chunk)
    if lengths and len(data) != int(lengths[0]):
        raise HTTPException(422, "Invalid request")
    try:
        value = json.loads(data.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
        if type(value) is not dict:
            raise ValueError
        return value
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise HTTPException(422, "Invalid request") from None


async def _prepare_body(request, deadline):
    # Parsing is independent of dispatch, including cancellation-resistant reads.
    value = await _json_body(request, deadline=deadline)
    if (set(value) != _FIELDS or not _label(value["root"])
            or type(value["revision"]) is not int or not 0 <= value["revision"] <= 2**63 - 1
            or any(type(value[key]) is not str or _REF.fullmatch(value[key]) is None
                   for key in ("receptor_ref", "ligand_ref"))):
        raise HTTPException(422, "Invalid request")
    return value


async def _approval_body(request, deadline):
    # Parse only: a retained receive completing late must never dispatch work.
    value = await _json_body(request, deadline=deadline)
    if set(value) != {'approval_nonce', 'binding_digest', 'confirm'}:
        raise HTTPException(422, "Invalid request")
    try:
        validate_approval(value['approval_nonce'], value['binding_digest'], value['confirm'])
    except DockingConsentError:
        raise HTTPException(422, "Invalid request") from None
    return value


def setup_docking_consent_routes(app, *, coordinator, task_runtime, enabled=False):
    if enabled is not True:
        return

    # Last-added middleware is outermost: include the existing session service
    # even in isolated apps which installed it before registering these routes.
    app.add_middleware(DockingHTTPMiddleware, transport=_ControlTransport(coordinator),
        budget=lambda scope: (_PREPARE_SECONDS if scope.get('method') == 'POST'
            and scope.get('path') == '/api/agent/docking/preparations' else _CONTROL_SECONDS + 0.1))

    def authenticate(request, *, mutation=True):
        owner = request.scope.get("agent_session_id")
        if type(owner) is not str or not owner:
            raise HTTPException(401, "Browser session required")
        if mutation and not _origin_allowed(request.scope):
            raise HTTPException(403, "Origin not allowed")
        if (type(coordinator) is not DockingRequestCoordinator or type(task_runtime) is not TaskRuntime
                or task_runtime.store is not coordinator._tasks):
            raise HTTPException(503, "Preparation unavailable")
        return owner

    @app.get('/api/agent/docking/tasks/{task_id}/artifacts/{artifact_id}')
    async def artifact(task_id: str, artifact_id: str, request: Request):
        from src.web.docking_artifacts import docking_artifact_response
        deadline = request_deadline(request, _CONTROL_SECONDS)
        owner = authenticate(request)  # Downloads require the same origin too.
        if request.scope.get('query_string'):
            raise HTTPException(422, 'Invalid request')
        _empty_headers(request)
        if request.headers.getlist('range'):
            raise HTTPException(416, 'Range not supported')
        # Validate live identity/grant before any body or file read; constructing
        # the response does not acquire physical ownership.
        response = docking_artifact_response(coordinator, task_runtime=task_runtime,
            task_id=task_id, artifact_id=artifact_id, owner_session_id=owner, scope=request.scope)
        transport = _ControlTransport(coordinator)
        body = transport.retain(object(), lambda: _empty_body(request, deadline))
        await transport.wait(body, deadline, body=True)
        check_feedback(request)
        return response

    @app.get('/api/agent/docking/tasks/{task_id}')
    async def status(task_id: str, request: Request):
        deadline = request_deadline(request, _CONTROL_SECONDS)
        owner = authenticate(request, mutation=False)
        if request.query_params:
            raise HTTPException(422, 'Invalid request')
        transport = _ControlTransport(coordinator)
        root = transport.live(owner, 'task_id', task_id)
        view = (await transport.status(root, deadline) if root is not None
                else await transport.durable(owner, 'task_id', task_id, deadline))
        return JSONResponse(view, headers={'Cache-Control': 'private, no-store'})

    @app.post('/api/agent/docking/preparations/{preparation_id}/revoke')
    async def revoke(preparation_id: str, request: Request):
        deadline = request_deadline(request, _CONTROL_SECONDS)
        owner = authenticate(request)
        return await _ControlTransport(coordinator).control(request, owner,
            'preparation_id', preparation_id, deadline, revoke=True)

    @app.post('/api/agent/docking/tasks/{task_id}/cancel')
    async def cancel(task_id: str, request: Request):
        deadline = request_deadline(request, _CONTROL_SECONDS)
        owner = authenticate(request)
        return await _ControlTransport(coordinator).control(request, owner,
            'task_id', task_id, deadline, revoke=False)

    @app.post("/api/agent/docking/preparations")
    async def prepare(request: Request):
        deadline = request_deadline(request, _PREPARE_SECONDS)
        owner = authenticate(request)
        transport = _ControlTransport(coordinator)
        body = transport.retain(object(), lambda: _prepare_body(request, deadline))
        value = await transport.wait(body, deadline, body=True)
        root = coordinator._owned(value["root"], owner)
        if root is None:
            raise HTTPException(404, "Preparation not found")
        if (root.closed or value["revision"] != root.accepted_revision
                or any(root.accepted_slots[key] != dict(state="fixed", value=value[key])
                       for key in ("receptor_ref", "ligand_ref"))):
            raise HTTPException(409, "Preparation changed")
        check_feedback(request)
        if time.monotonic() >= deadline:
            raise HTTPException(408, 'Request deadline exceeded')

        result = asyncio.get_running_loop().create_future()
        feedback = asyncio.Event()
        response_ready = False
        envelope = envelope_for(request.scope)
        dispatched = initializes = cleanup_owned = False
        entry_task, entry_context = root.task, root.input_context

        def execution_owned():
            context = root.input_context
            return (root.phase == 'APPROVING'
                    or context is not None and context._execution is not None)

        def can_initialize():
            # Mirror the coordinator's synchronous admission, not HTTP entry:
            # queued requests may have become observers before they dispatch.
            context = root.input_context
            return (coordinator._owned(root.root_id, owner) is root
                and not root.closed and not root.staging_started
                and (root.task is None or root.task.done())
                and root.reason == 'inputs_confirmed_staging_required'
                and not execution_owned()
                and (context is None or context._operation is None)
                and root.accepted_revision == value['revision']
                and all(root.accepted_slots[key] == dict(state='fixed', value=value[key])
                        for key in ('receptor_ref', 'ligand_ref')))

        def finish_feedback(delivered):
            nonlocal cleanup_owned
            if feedback.is_set():
                return
            # A repeat owns only its status wait, never another request's C1/C2.
            cleanup_owned = (not delivered and not execution_owned()
                and (initializes or (not dispatched and root.task is entry_task
                    and root.input_context is entry_context and can_initialize())))
            if cleanup_owned:
                coordinator._interrupt(root, 'closed', 'CLOSED')
                context = root.input_context
                if context is not None and context._operation is not None:
                    context._operation.aborted.set()
            if result.done() and not result.cancelled():
                result.exception()
            else:
                result.cancel()
            feedback.set()

        async def preparation():
            nonlocal dispatched, initializes
            try:
                # Never cancel this wrapper on HTTP exit: even if it has not
                # started yet, its reserved slot must still run exact cleanup.
                if (feedback.is_set() or envelope is not None and envelope.closed
                        or time.monotonic() >= deadline):
                    raise HTTPException(408, 'Request deadline exceeded')
                if coordinator._owned(root.root_id, owner) is not root:
                    raise HTTPException(409, 'Preparation changed')
                initializes = can_initialize()
                dispatched = True
                # No await separates this classification from coordinator entry.
                view = await coordinator.prepare_inputs(root_id=root.root_id, owner_session_id=owner,
                    expected_revision=value['revision'], receptor_ref=value['receptor_ref'],
                    ligand_ref=value['ligand_ref'], runtime=task_runtime)
                if not result.done():
                    result.set_result(view)
            except (Exception, asyncio.CancelledError) as exc:
                if not result.done():
                    result.set_exception(exc)
            finally:
                # Keep capacity through the HTTP verdict, including a result
                # queued just as its deadline expires. Only cleanup is shared
                # by the coordinator; nonce-bearing results are per request.
                await feedback.wait()
                if cleanup_owned and not execution_owned():
                    await coordinator._close_owned_root(root)

        transport.retain(object(), preparation)
        if envelope is not None:
            envelope.feedback = finish_feedback
        try:
            view = await transport.wait(result, deadline)
            if view['phase'] == 'READY':
                if (root.closed or execution_owned()
                        or coordinator._owned(root.root_id, owner) is not root):
                    raise HTTPException(409, 'Preparation changed')
                status = 201 if 'approval_nonce' in view['consent_preview'] else 200
            elif view['phase'] in {'BUSY', 'PREPARING'}:
                status = 202
            elif view['reason_code'] == 'inputs_unconfirmed':
                status = 409
            else:
                status = 503
            response = JSONResponse(view, status_code=status, headers={'Cache-Control': 'no-store'})
            if time.monotonic() >= deadline:
                raise HTTPException(503, 'Preparation deadline exceeded')
            response_ready = True
            return response
        finally:
            # Direct route callers retain their historical response boundary.
            # ASGI callers keep this same cleanup owner through physical send.
            if envelope is None or not response_ready:
                finish_feedback(response_ready)

    @app.post('/api/agent/docking/preparations/{preparation_id}/approve')
    async def approve(preparation_id: str, request: Request):
        deadline = request_deadline(request, _CONTROL_SECONDS)
        owner = authenticate(request)
        transport = _ControlTransport(coordinator)
        body = transport.retain(object(), lambda: _approval_body(request, deadline))
        value = await transport.wait(body, deadline, body=True)
        # Only bounded, live coordinator roots are eligible. A database receipt
        # or supplied preparation ID cannot reconstruct a missing source owner.
        root = next((root for root in coordinator._roots.values()
                     if root.owner == owner and root.preparation_id == preparation_id), None)
        if root is None:
            raise HTTPException(404, 'Preparation not found')
        context = root.input_context
        if context is None or context._ready_record is None:
            raise HTTPException(409, 'Preparation changed')
        execution = context._execution
        if root.closed and not (execution is not None and (execution.acquired or execution.claim_uncertain)):
            raise HTTPException(409, 'Preparation changed')
        if context._runtime is not task_runtime:
            raise HTTPException(503, 'Preparation unavailable')
        # Each request must prove the live consent BEFORE it can join a shared
        # pending approval. Backend claim/source/one-execution gates still run
        # exclusively through approve_inputs and the existing runtime.
        try:
            verify_approval(context._ready_record, value['approval_nonce'], value['binding_digest'])
        except DockingConsentError:
            raise HTTPException(409, 'Preparation changed') from None
        check_feedback(request)
        if time.monotonic() >= deadline:
            raise HTTPException(408, 'Request deadline exceeded')

        async def approval():
            # A queued transport job must not resolve the locator to a new
            # root/context. No await separates this check from coordinator entry.
            if (coordinator._owned(root.root_id, owner) is not root
                    or root.input_context is not context):
                raise HTTPException(409, 'Preparation changed')
            return await coordinator.approve_inputs(root_id=root.root_id, owner_session_id=owner, **value)

        # This synchronous retain is admission. HTTP is not an attached WS:
        # once accepted, its owned approval survives caller cancellation or
        # timeout without interrupting/draining the physical execution.
        task = transport.retain(('approve', id(root)), approval)
        view = await transport.wait(task, deadline)
        if view['reason_code'] == 'approval_expired':
            status = 410
        elif view['reason_code'] in {'approval_unavailable', 'approval_changed'}:
            status = 409
        elif view['phase'] in {'APPROVING', 'EXECUTING'}:
            status = 202
        elif view['phase'] in {'RESULT_PENDING', 'FAILED', 'CANCELLED', 'TIMED_OUT', 'UNKNOWN'}:
            status = 200
        else:
            status = 503
        return JSONResponse(view, status_code=status, headers={'Cache-Control': 'no-store'})
