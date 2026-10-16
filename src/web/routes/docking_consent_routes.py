"""Opt-in, same-session chat preparation transport; no execution entry yet.

Only the trusted coordinator supplies frozen producer bytes and parameters.
The browser supplies current opaque references, never files or execution policy.
Application activation remains separate from registering this isolated seam.
"""
import json
import re

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from src.task_runtime.runtime import TaskRuntime
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


async def _prepare_body(request):
    if request.query_params:
        raise HTTPException(422, "Invalid request")
    if request.headers.getlist("content-encoding"):
        raise HTTPException(415, "Encoded body not accepted")
    content_types = request.headers.getlist("content-type")
    if content_types != ["application/json"]:
        raise HTTPException(415, "JSON required")
    lengths = request.headers.getlist("content-length")
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
        if (type(value) is not dict or set(value) != _FIELDS
                or not _label(value["root"])
                or type(value["revision"]) is not int or not 0 <= value["revision"] <= 2**63 - 1
                or any(type(value[key]) is not str or _REF.fullmatch(value[key]) is None
                       for key in ("receptor_ref", "ligand_ref"))):
            raise ValueError
        return value
    except (ValueError, TypeError, RecursionError, UnicodeError):
        raise HTTPException(422, "Invalid request") from None


def setup_docking_consent_routes(app, *, coordinator, task_runtime, enabled=False):
    if enabled is not True:
        return

    @app.post("/api/agent/docking/preparations")
    async def prepare(request: Request):
        owner = request.scope.get("agent_session_id")
        if type(owner) is not str or not owner:
            raise HTTPException(401, "Browser session required")
        if not _origin_allowed(request.scope):
            raise HTTPException(403, "Origin not allowed")
        if (type(coordinator) is not DockingRequestCoordinator or type(task_runtime) is not TaskRuntime
                or task_runtime.store is not coordinator._tasks):
            raise HTTPException(503, "Preparation unavailable")
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
