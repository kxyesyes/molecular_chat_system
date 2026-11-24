"""Session-owned controls and per-ligand artifacts for homepage batch docking."""
from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse, Response

from src.task_runtime.docking_consent import DockingConsentError
from src.web.docking_batch_consent import batch_approve, batch_preview
from src.web.docking_http import check_feedback, request_deadline
from .docking_consent_routes import (
    _artifact_origin_allowed, _approval_body, _ControlTransport, _CONTROL_SECONDS, _json_body,
)


def setup_batch_routes(app, coordinator, execution, authenticate):
    prefix = '/api/agent/docking/tasks/{task_id}'
    transport = _ControlTransport(coordinator)
    private = {'Cache-Control': 'private, no-store'}

    def owned_root(owner, task_id):
        root = transport.live(owner, 'task_id', task_id)
        if root is None or not root.batch:
            raise HTTPException(404, 'Batch not found')
        return root

    async def stored(operation):
        try:
            return await operation
        except KeyError:
            raise HTTPException(404, 'Batch or pose not found') from None

    @app.post(prefix + '/batch-preview')
    async def preview(task_id: str, request: Request):
        owner = authenticate(request)
        value = await _json_body(request, deadline=request_deadline(request, _CONTROL_SECONDS))
        if set(value) != {'revision'} or type(value['revision']) is not int:
            raise HTTPException(422, 'Batch revision required')
        try:
            result = await batch_preview(coordinator, owned_root(owner, task_id), value['revision'])
        except (ValueError, DockingConsentError):
            raise HTTPException(409, 'Batch inputs unavailable') from None
        return JSONResponse(result, headers=private)

    @app.post(prefix + '/batch-approve')
    async def approve(task_id: str, request: Request):
        owner = authenticate(request)
        proof = await _approval_body(request, request_deadline(request, _CONTROL_SECONDS))
        root = transport.live(owner, 'task_id', task_id)
        if root is None:
            # A retry after commit returns the existing owner's receipt only.
            return JSONResponse(await stored(execution.status(task_id, owner)), headers=private)
        if not root.batch:
            raise HTTPException(404, 'Batch not found')
        try:
            result = await batch_approve(coordinator, root, execution, proof,
                                         check_request=lambda: check_feedback(request))
        except (ValueError, DockingConsentError):
            raise HTTPException(409, 'Batch confirmation unavailable') from None
        return JSONResponse(result, headers=private)

    @app.get(prefix + '/batch-status')
    async def status(task_id: str, request: Request):
        owner = authenticate(request, mutation=False)
        return JSONResponse(await stored(execution.status(task_id, owner)), headers=private)

    @app.post(prefix + '/batch-cancel')
    async def cancel(task_id: str, request: Request):
        owner = authenticate(request)
        if await _json_body(request, deadline=request_deadline(request, _CONTROL_SECONDS)):
            raise HTTPException(422, 'Expected an empty object')
        check_feedback(request)
        return JSONResponse(await stored(execution.cancel(task_id, owner)), headers=private)

    @app.get(prefix + '/batch-poses/{ordinal}')
    async def pose(task_id: str, ordinal: int, request: Request):
        owner = authenticate(request, mutation=False)
        if not _artifact_origin_allowed(request):
            raise HTTPException(403, 'Origin not allowed')
        content = await stored(execution.pose(task_id, owner, ordinal))
        return Response(content, media_type='application/octet-stream', headers={
            **private, 'X-Content-Type-Options': 'nosniff',
            'Content-Disposition': f'attachment; filename="ligand-{ordinal}.pdbqt"'})
