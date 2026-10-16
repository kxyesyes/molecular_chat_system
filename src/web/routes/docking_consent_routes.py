"""Opt-in, same-session chat preparation and explicit approval transport.

Only the trusted coordinator supplies frozen producer bytes and parameters.
The browser supplies current opaque references, never files or execution policy.
Application activation remains separate from registering this isolated seam.
"""
import json
import re

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.task_runtime.runtime import TaskRuntime
from src.task_runtime.docking_consent import DockingConsentError, validate_approval
from src.web.agent_session import _origin_allowed
from src.web.docking_coordinator import DockingRequestCoordinator, _label


_MAX_BODY = 4096
_REF = re.compile(r"sha256:[a-f0-9]{64}\Z")
_FIELDS = frozenset({"root", "revision", "receptor_ref", "ligand_ref"})


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


def _constant(_value):
    raise ValueError("invalid number")


async def _json_body(request):
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
    async for chunk in request.stream():
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


async def _prepare_body(request):
    value = await _json_body(request)
    if (set(value) != _FIELDS or not _label(value["root"])
            or type(value["revision"]) is not int or not 0 <= value["revision"] <= 2**63 - 1
            or any(type(value[key]) is not str or _REF.fullmatch(value[key]) is None
                   for key in ("receptor_ref", "ligand_ref"))):
        raise HTTPException(422, "Invalid request")
    return value


async def _approval_body(request):
    value = await _json_body(request)
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

    def authenticate(request):
        owner = request.scope.get("agent_session_id")
        if type(owner) is not str or not owner:
            raise HTTPException(401, "Browser session required")
        if not _origin_allowed(request.scope):
            raise HTTPException(403, "Origin not allowed")
        if (type(coordinator) is not DockingRequestCoordinator or type(task_runtime) is not TaskRuntime
                or task_runtime.store is not coordinator._tasks):
            raise HTTPException(503, "Preparation unavailable")
        return owner

    @app.post("/api/agent/docking/preparations")
    async def prepare(request: Request):
        owner = authenticate(request)
        value = await _prepare_body(request)
        root = coordinator._owned(value["root"], owner)
        if root is None:
            raise HTTPException(404, "Preparation not found")
        if (root.closed or value["revision"] != root.accepted_revision
                or any(root.accepted_slots[key] != dict(state="fixed", value=value[key])
                       for key in ("receptor_ref", "ligand_ref"))):
            raise HTTPException(409, "Preparation changed")
        view = await coordinator.prepare_inputs(root_id=value["root"], owner_session_id=owner,
            expected_revision=value["revision"], receptor_ref=value["receptor_ref"],
            ligand_ref=value["ligand_ref"], runtime=task_runtime)
        if view["phase"] == "READY":
            status = 201 if "approval_nonce" in view["consent_preview"] else 200
        elif view["phase"] in {"BUSY", "PREPARING"}:
            status = 202
        elif view["reason_code"] == "inputs_unconfirmed":
            status = 409
        else:
            status = 503
        return JSONResponse(view, status_code=status, headers={"Cache-Control": "no-store"})

    @app.post('/api/agent/docking/preparations/{preparation_id}/approve')
    async def approve(preparation_id: str, request: Request):
        owner = authenticate(request)
        value = await _approval_body(request)
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
        view = await coordinator.approve_inputs(root_id=root.root_id, owner_session_id=owner, **value)
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
