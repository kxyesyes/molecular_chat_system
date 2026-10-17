"""Opt-in owned pose snapshots; no publication, execution, recovery or retention job."""
import asyncio
import time

from fastapi import HTTPException
from fastapi.responses import Response

from src.system.model_lifecycle import finish_on_cancel
from src.task_runtime.runtime import TaskRuntime
from src.task_runtime.staging import ManifestError
from .docking_coordinator import DockingRequestCoordinator, _label, _owner_label
from .docking_http import envelope_for
from .docking_publication import (
    _Publication, _capture_guarded, _grant_remaining, _local, _sqlite_contention,
)


def _unavailable():
    raise HTTPException(404, 'Docking artifact unavailable')


def _owned(coordinator, runtime, task_id, artifact_id, owner):
    # No SQL/file lookup, including for foreign/malformed/missing identifiers.
    if (type(coordinator) is not DockingRequestCoordinator or type(runtime) is not TaskRuntime
            or not _label(task_id) or not _owner_label(owner)
            or type(artifact_id) is not str or artifact_id != 'pose'):
        _unavailable()
    root = next((r for r in coordinator._roots.values()
                 if r.owner == owner and r.task_id == task_id), None)
    if root is None or root.input_context is None:
        _unavailable()
    state = root.publication
    if (type(state) is not _Publication or state.context is not root.input_context
            or state._origin[0] is not root or state._origin[2] is not coordinator
            or state._origin[4] is not state):
        _unavailable()
    if state.outcome is None or state.outcome.status != 'SUCCEEDED':
        _unavailable()  # Observation cannot poison an in-progress publication.
    try:
        if state.context._runtime is not runtime:
            _unavailable()
        _grant_remaining(state.context, state.grant)
        _local(state.context, _grant=state.grant)
    except Exception:
        # Only this exact original state can lose access; success is immutable.
        state.access_disabled = True
        _unavailable()
    return state


class _OwnedSnapshot:
    """Retained before lease acquisition until physical send AND release finish."""
    def __init__(self, state, envelope):
        self.state = state
        self.envelope = envelope
        self.context = state.context
        self.coordinator = state._origin[2]
        self.lease = None
        self.content = None
        self.closed = False
        self.unresolved = False

    def acquire(self):
        self.envelope.check(self.envelope.work_deadline)
        _local(self.context, _grant=self.state.grant)
        stager = self.context._publication_output[0].stager
        self.lease = stager._task_lease(self.state.grant.task_id, timeout_seconds=0)
        self.lease.__enter__()

    async def read(self):
        async def verify():
            try:
                async with self.coordinator._gate.request():
                    self.envelope.check(self.envelope.work_deadline)
                    _local(self.context, capture_model=True, _grant=self.state.grant)
                    await self.coordinator._sql(self.acquire)
                    self.envelope.check(self.envelope.work_deadline)
                    self.content = await self.coordinator._sql(self.capture)
                    _local(self.context, capture_model=True, _grant=self.state.grant)
            except ManifestError as exc:
                if exc.reason_code != 'manifest_busy':
                    self.state.access_disabled = True
                raise
            except Exception as exc:
                # Also retain invalidation observed by the final loop-side
                # check when the HTTP waiter has already been cancelled.
                if not _sqlite_contention(exc):
                    self.state.access_disabled = True
                raise
        # Cancellation cannot skip model-reader exit or detach the real thread.
        await finish_on_cancel(verify())

    def capture(self):
        try:
            return _capture_guarded(self.context, _grant=self.state.grant)
        except Exception as exc:
            # A cancelled HTTP waiter may suppress the worker's exception while
            # draining it. The actual observer must still seal invalid access.
            if not _sqlite_contention(exc):
                self.state.access_disabled = True
            raise

    def release(self):
        try:
            if self.lease is not None:
                self.lease.__exit__(None, None, None)
                if self.lease.acquired:
                    raise RuntimeError('artifact_lease_unresolved')
            self.content = None
            self.closed = True
        except BaseException:
            self.unresolved = True
            self.state.access_disabled = True
            raise


class _ArtifactResponse(Response):
    def __init__(self, state, envelope):
        super().__init__(content=b'')
        self.state = state
        self.envelope = envelope

    def check(self):
        self.envelope.check(self.envelope.work_deadline)
        try:
            _local(self.state.context, _grant=self.state.grant)
        except Exception:
            self.state.access_disabled = True
            _unavailable()

    async def __call__(self, scope, receive, send):
        # Route construction/abandonment creates no lease or phantom reader.
        self.check()
        state = self.state
        # The existing OS task-shard lease is exclusive. Bound per-root readers
        # before dispatch, while global ownership remains the HTTP capacity map.
        if state.snapshots:
            raise HTTPException(503, 'Artifact capacity unavailable')
        snapshot = _OwnedSnapshot(state, self.envelope)
        state.snapshots.add(snapshot)
        try:
            try:
                await snapshot.read()
                self.check()
            except asyncio.CancelledError:
                raise
            except ManifestError as exc:
                if exc.reason_code == 'manifest_busy':
                    raise HTTPException(503, 'Artifact capacity unavailable') from None
                state.access_disabled = True
                _unavailable()
            except Exception as exc:
                if _sqlite_contention(exc):
                    raise HTTPException(503, 'Artifact capacity unavailable') from None
                state.access_disabled = True
                _unavailable()
            # No model reader across slow ASGI send. The existing HTTP envelope
            # retains this actual application task on deadline/disconnect.
            response = Response(snapshot.content, media_type='application/octet-stream', headers={
                'Content-Disposition': 'attachment; filename="pose.pdbqt"',
                'Cache-Control': 'private, no-store', 'X-Content-Type-Options': 'nosniff'})
            await response(scope, receive, send)
        finally:
            try:
                await snapshot.coordinator._sql(snapshot.release)
            finally:
                if snapshot.closed:
                    state.snapshots.discard(snapshot)


def docking_artifact_response(coordinator, *, task_runtime, task_id, artifact_id,
                             owner_session_id, scope):
    envelope = envelope_for(scope)
    if envelope is None or envelope.closed or time.monotonic() >= envelope.work_deadline:
        _unavailable()
    state = _owned(coordinator, task_runtime, task_id, artifact_id, owner_session_id)
    return _ArtifactResponse(state, envelope)
