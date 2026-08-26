"""Narrow FastAPI surface for the standalone sandbox docking broker."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator, Callable

from fastapi import Depends, FastAPI, File, Form, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, ValidationError
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from .artifacts import ArtifactRegistry
from .config import BrokerConfig
from .models import (
    BrokerErrorCode,
    BrokerJobStatus,
    BrokerProvenance,
    DockingManifest,
    DockingParameters,
)
from .opensandbox_client import OpenSandboxClient
from .service import (
    BrokerFailure,
    BrokerJobView,
    SandboxBrokerService,
    StopIncomplete,
)
from .store import BrokerStore


logger = logging.getLogger(__name__)

_REQUEST_JSON_MAX_CHARS = 64 * 1024
_MULTIPART_OVERHEAD_BYTES = 2 * 1024 * 1024


class _MultipartBodyLimitExceeded(StarletteHTTPException):
    def __init__(self) -> None:
        super().__init__(status_code=413)


class _MultipartBodyLimitMiddleware:
    """Enforce the multipart transport limit against actual ASGI body bytes."""

    def __init__(self, app: ASGIApp, *, maximum_bytes: int) -> None:
        if type(maximum_bytes) is not int or maximum_bytes <= 0:
            raise ValueError("invalid multipart body limit")
        self.app = app
        self.maximum_bytes = maximum_bytes

    async def _send_error(self, scope: Scope, receive: Receive, send: Send, status: int, code: str) -> None:
        await _error(status, code)(scope, receive, send)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if (
            scope["type"] != "http"
            or scope.get("method") != "POST"
            or scope.get("path") != "/v1/docking/jobs"
        ):
            await self.app(scope, receive, send)
            return

        raw_lengths = [
            value
            for name, value in scope.get("headers", [])
            if name.lower() == b"content-length"
        ]
        if len(raw_lengths) > 1:
            await self._send_error(
                scope,
                receive,
                send,
                422,
                BrokerErrorCode.INVALID_INPUT.value,
            )
            return
        if raw_lengths:
            raw_length = raw_lengths[0]
            if (
                not raw_length
                or len(raw_length) > 20
                or any(byte < ord("0") or byte > ord("9") for byte in raw_length)
            ):
                await self._send_error(
                    scope,
                    receive,
                    send,
                    422,
                    BrokerErrorCode.INVALID_INPUT.value,
                )
                return
            declared = int(raw_length)
            if declared > self.maximum_bytes:
                await self._send_error(
                    scope,
                    receive,
                    send,
                    413,
                    "payload_too_large",
                )
                return

        total = 0
        response_started = False

        async def limited_receive() -> Message:
            nonlocal total
            message = await receive()
            if message["type"] == "http.request":
                body = message.get("body", b"")
                if type(body) is not bytes:
                    raise _MultipartBodyLimitExceeded
                total += len(body)
                if total > self.maximum_bytes:
                    raise _MultipartBodyLimitExceeded
            return message

        async def tracked_send(message: Message) -> None:
            nonlocal response_started
            if message["type"] == "http.response.start":
                response_started = True
            await send(message)

        try:
            await self.app(scope, limited_receive, tracked_send)
        except _MultipartBodyLimitExceeded:
            if response_started:
                raise
            await self._send_error(
                scope,
                receive,
                send,
                413,
                "payload_too_large",
            )


class PublicJob(BaseModel):
    """Host-path-free job state returned to UDS clients."""

    model_config = ConfigDict(extra="forbid")

    job_id: str
    trace_id: str
    status: BrokerJobStatus
    phase: str
    error_code: str | None
    warnings: tuple[str, ...]
    provenance: BrokerProvenance | None
    cancel_requested: bool
    cleanup_status: str
    created_at: float
    updated_at: float

    @classmethod
    def from_view(cls, view: BrokerJobView) -> "PublicJob":
        return cls(
            job_id=view.job_id,
            trace_id=view.trace_id,
            status=view.status,
            phase=view.phase,
            error_code=view.error_code,
            warnings=view.warnings,
            provenance=view.provenance,
            cancel_requested=view.cancel_requested,
            cleanup_status=view.cleanup_status,
            created_at=view.created_at,
            updated_at=view.updated_at,
        )


def _error(status_code: int, code: str) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code}},
    )


def _raw_idempotency_key(scope: Scope) -> str:
    """Accept exactly one unmerged printable-ASCII idempotency header."""

    try:
        values = [
            value
            for name, value in scope.get("headers", [])
            if type(name) is bytes
            and type(value) is bytes
            and name.lower() == b"idempotency-key"
        ]
        if len(values) != 1:
            raise ValueError
        raw_value = values[0]
        if (
            not 1 <= len(raw_value) <= 256
            or b"," in raw_value
            or any(byte < 0x20 or byte > 0x7E for byte in raw_value)
        ):
            raise ValueError
        value = raw_value.decode("ascii", errors="strict")
        if value != value.strip(" \t"):
            raise ValueError
        return value
    except (UnicodeError, ValueError):
        raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None


def _default_service(config: BrokerConfig) -> SandboxBrokerService:
    store = BrokerStore(config.state_root / "broker.sqlite")
    artifacts = ArtifactRegistry(
        config.state_root,
        store,
        max_artifact_bytes=config.output_max_bytes,
    )
    client = OpenSandboxClient(config)
    return SandboxBrokerService(config, store, client, artifacts)


def create_app(
    config: BrokerConfig,
    *,
    service: object | None = None,
    service_factory: Callable[[BrokerConfig], object] | None = None,
) -> FastAPI:
    """Create the standalone Broker app without loading the OpenSandbox SDK."""

    if type(config) is not BrokerConfig:
        raise ValueError("invalid broker app configuration")
    if service is not None and service_factory is not None:
        raise ValueError("inject either service or service_factory")
    factory = _default_service if service_factory is None else service_factory
    shutdown_timeout = float(config.sandbox_timeout_seconds) + 30.0

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        broker = service
        started = False
        try:
            if broker is None:
                broker = factory(config)
            app.state.sandbox_broker_service = broker
            await broker.recover()
            await broker.start()
            started = True
        except BaseException:
            logger.error("sandbox broker startup failed")
            if broker is not None:
                try:
                    await broker.stop(timeout=shutdown_timeout)
                except BaseException:
                    logger.error("sandbox broker startup cleanup failed")
            raise
        try:
            yield
        finally:
            if started:
                try:
                    await broker.stop(timeout=shutdown_timeout)
                except StopIncomplete:
                    logger.error("sandbox broker shutdown incomplete")
                    raise
                except BaseException:
                    logger.error("sandbox broker shutdown failed")
                    raise

    maximum_body_bytes = (
        config.receptor_max_bytes
        + config.ligand_max_bytes
        + _REQUEST_JSON_MAX_CHARS
        + _MULTIPART_OVERHEAD_BYTES
    )
    app = FastAPI(
        lifespan=lifespan,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        redirect_slashes=False,
    )
    app.add_middleware(
        _MultipartBodyLimitMiddleware,
        maximum_bytes=maximum_body_bytes,
    )

    @app.exception_handler(RequestValidationError)
    async def request_validation_handler(
        request: Request,
        failure: RequestValidationError,
    ) -> JSONResponse:
        del request, failure
        return _error(422, BrokerErrorCode.INVALID_INPUT.value)

    @app.exception_handler(BrokerFailure)
    async def broker_failure_handler(
        request: Request,
        failure: BrokerFailure,
    ) -> JSONResponse:
        del request
        mapping = {
            BrokerErrorCode.INVALID_INPUT: 422,
            BrokerErrorCode.IDEMPOTENCY_CONFLICT: 409,
            BrokerErrorCode.QUEUE_SATURATED: 429,
            BrokerErrorCode.OPENSANDBOX_UNAVAILABLE: 503,
        }
        return _error(mapping.get(failure.code, 503), failure.code.value)

    @app.exception_handler(KeyError)
    async def not_found_handler(request: Request, failure: KeyError) -> JSONResponse:
        del request, failure
        return _error(404, "not_found")

    @app.exception_handler(StarletteHTTPException)
    async def http_failure_handler(
        request: Request,
        failure: StarletteHTTPException,
    ) -> JSONResponse:
        if isinstance(failure, _MultipartBodyLimitExceeded):
            raise failure
        del request
        if failure.status_code == 404:
            return _error(404, "not_found")
        return _error(422, BrokerErrorCode.INVALID_INPUT.value)

    @app.exception_handler(Exception)
    async def internal_failure_handler(
        request: Request,
        failure: Exception,
    ) -> JSONResponse:
        if isinstance(failure, _MultipartBodyLimitExceeded):
            raise failure
        del request, failure
        logger.error("sandbox broker request failed")
        return _error(500, "internal_error")

    def get_service(request: Request) -> object:
        try:
            return request.app.state.sandbox_broker_service
        except AttributeError:
            raise BrokerFailure(BrokerErrorCode.OPENSANDBOX_UNAVAILABLE) from None

    async def require_exact_multipart(request: Request) -> None:
        try:
            form = await request.form()
            names = [name for name, _ in form.multi_items()]
            if sorted(names) != ["ligand", "receptor", "request_json"]:
                raise ValueError
        except _MultipartBodyLimitExceeded:
            raise
        except Exception:
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None

    @app.post(
        "/v1/docking/jobs",
        status_code=202,
        response_model=PublicJob,
        dependencies=[Depends(require_exact_multipart)],
    )
    async def submit_job(
        request: Request,
        request_json: Annotated[
            str,
            Form(min_length=2, max_length=_REQUEST_JSON_MAX_CHARS),
        ],
        receptor: Annotated[UploadFile, File()],
        ligand: Annotated[UploadFile, File()],
        broker: object = Depends(get_service),
    ) -> PublicJob:
        try:
            parameters = DockingParameters.model_validate_json(request_json)
        except ValidationError:
            raise BrokerFailure(BrokerErrorCode.INVALID_INPUT) from None
        view = await broker.submit_uploads(
            parameters,
            receptor,
            ligand,
            _raw_idempotency_key(request.scope),
        )
        return PublicJob.from_view(view)

    @app.get("/v1/docking/jobs/{job_id}", response_model=PublicJob)
    async def get_job(
        job_id: str,
        broker: object = Depends(get_service),
    ) -> PublicJob:
        return PublicJob.from_view(broker.get_job(job_id))

    @app.get(
        "/v1/docking/jobs/{job_id}/manifest",
        response_model=DockingManifest,
    )
    async def get_manifest(
        job_id: str,
        broker: object = Depends(get_service),
    ) -> DockingManifest:
        return broker.get_manifest(job_id)

    @app.get("/v1/docking/jobs/{job_id}/artifacts/{artifact_id}")
    async def get_artifact(
        job_id: str,
        artifact_id: str,
        broker: object = Depends(get_service),
    ) -> StreamingResponse:
        record, stream = broker.open_artifact(job_id, artifact_id)
        headers = {
            "Content-Length": str(record.size_bytes),
            "Content-Disposition": (
                f'attachment; filename="{record.artifact_id}.pdbqt"'
            ),
            "X-Content-Type-Options": "nosniff",
        }
        return StreamingResponse(
            stream,
            media_type=record.media_type,
            headers=headers,
        )

    @app.post(
        "/v1/docking/jobs/{job_id}/cancel",
        status_code=202,
        response_model=PublicJob,
    )
    async def cancel_job(
        job_id: str,
        broker: object = Depends(get_service),
    ) -> PublicJob:
        return PublicJob.from_view(await broker.cancel(job_id))

    @app.get("/healthz")
    async def health() -> dict[str, str]:
        return {"status": "ok", "operation": "molecular_docking"}

    return app
