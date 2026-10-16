"""Server-only, one-use bridge from a live confirmed root to C1 staging.

This is not a wire DTO or an execution capability. It retains the actual source
owner and producer outputs; dictionaries, hashes and caller callbacks cannot
substitute for that owner. No HTTP route exposes this object.
"""
from __future__ import annotations

import asyncio
from contextlib import closing, contextmanager
import hashlib
import json
import threading
import time

from src.docking import chat_preparation
from src.task_runtime.docking_consent import DockingConsentError, canonical_json


_ISSUER = object()


def _deny():
    raise DockingConsentError("consent_proof_mismatch")


def _fingerprint(root):
    # All of these values came from the fixed retained producers/coordinator.
    # Include bytes as well as descriptors: claimed hashes are not proof.
    body = dict(draft=root.draft, slots=root.accepted_slots, revision=root.accepted_revision,
        confirmation=root.confirmation_key, confirmation_revision=root.confirmation_revision,
        preview=root.prepared_preview_json, selection=root.selection,
        source=root.source_result["source"], evidence=root.source_result["evidence"],
        projection=root.projected["projection"], pocket=root.projected["pocket"],
        ligand={key: value for key, value in root.ligand.items() if key != "sdf_bytes"})
    digest = hashlib.sha256(canonical_json(body))
    for value in (root.source_result["pdb_bytes"], root.projected["pdb_bytes"], root.ligand["sdf_bytes"]):
        if type(value) is not bytes:
            _deny()
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.digest()


async def capture_chat_input_context(coordinator, *, root_id, owner_session_id):
    """Internal assembly seam; identical captures reuse the same one-use object."""
    from .docking_coordinator import DockingRequestCoordinator
    if type(coordinator) is not DockingRequestCoordinator:
        _deny()
    root = coordinator._owned(root_id, owner_session_id)
    if root is None or root.closed:
        _deny()
    if root.input_context is not None:
        return root.input_context
    async with coordinator._gate.request():
        def capture():
            coordinator._read_source_reference(root=root)
            if (root.confirmation_key is None or root.missing
                    or root.reason != "inputs_confirmed_staging_required"
                    or root.task is not None and not root.task.done()
                    or any(owner.snapshot()["settled"] is not True for owner in root.preparation_owners)):
                _deny()
            return ChatDockingInputContext(_ISSUER, coordinator, root)
        candidate = await coordinator._sql(capture)
        # No await between the identity check and publication. Concurrent
        # captures may validate twice but cannot publish two usable contexts.
        if root.closed or coordinator._roots.get(root_id) is not root:
            _deny()
        if root.input_context is None:
            root.input_context = candidate
        return root.input_context


class ChatDockingInputContext:
    __slots__ = ("_coordinator", "_root", "_digest", "_draft_json", "_parameters",
                 "_receptor", "_ligand", "_lock", "_claimed", "_operation", "_runtime",
                 "_begun", "_verified_manifest")

    def __init__(self, issuer, coordinator, root):
        if issuer is not _ISSUER:
            _deny()
        receptor, ligand, refs = coordinator._input_descriptors(root)
        if (receptor != root.receptor_descriptor or ligand != root.ligand_descriptor
                or any(root.accepted_slots[key] != dict(state="fixed", value=value)
                       for key, value in refs.items())):
            _deny()
        for content, digest in ((root.source_result["pdb_bytes"], root.source_result["source"]["sha256"]),
                                (root.projected["pdb_bytes"], root.projected["projection"]["derived_input"]["sha256"]),
                                (root.ligand["sdf_bytes"], root.ligand["sha256"])):
            if type(content) is not bytes or hashlib.sha256(content).hexdigest() != digest:
                _deny()
        self._coordinator, self._root = coordinator, root
        self._digest = _fingerprint(root)
        self._draft_json = canonical_json(root.draft)
        self._parameters = {key: root.accepted_slots[key]["value"]
                            for key in ("center", "size", "exhaustiveness", "num_modes")}
        self._parameters = json.loads(canonical_json(self._parameters))
        self._receptor, self._ligand = root.projected["pdb_bytes"], root.ligand["sdf_bytes"]
        self._lock, self._claimed = threading.Lock(), False
        self._operation = self._runtime = self._verified_manifest = None
        self._begun = False

    def __repr__(self):
        return "ChatDockingInputContext(<private>)"

    def claim(self, runtime, operation, *, owner_session_id, revision, receptor_name,
              receptor_bytes, ligand_name, ligand_bytes, parameters):
        """Consume synchronously before any worker/await; failure cannot mint a retry."""
        from src.task_runtime.runtime import TaskRuntime
        root = self._root
        with self._lock:
            if self._claimed:
                _deny()
            self._claimed = True
            if (type(runtime) is not TaskRuntime or runtime.store is not self._coordinator._tasks
                    or runtime._docking_consent_policy != root.current["policy"]
                    or type(operation.preparation_id) is not str
                    or operation.preparation_id != root.preparation_id
                    or type(owner_session_id) is not str or owner_session_id != root.owner
                    or type(revision) is not int or revision != root.accepted_revision
                    or receptor_name != "receptor.pdb" or ligand_name != "ligand.sdf"
                    or receptor_bytes != self._receptor or ligand_bytes != self._ligand
                    or parameters != self._parameters):
                _deny()
            remaining = self._local_current()
            self._runtime, self._operation = runtime, operation
            return remaining / 1000

    def _local_current(self):
        from .docking_coordinator import _Unavailable
        root, coordinator = self._root, self._coordinator
        if self._operation is not None and self._operation.aborted.is_set():
            raise DockingConsentError("consent_not_waiting")
        if (root.closed or root.input_context is not self
                or coordinator._roots.get(root.root_id) is not root):
            _deny()
        try:
            return coordinator._remaining_ms(root)
        except _Unavailable as exc:
            reason = "consent_expired" if str(exc) == "work_deadline_exceeded" else "consent_proof_mismatch"
            raise DockingConsentError(reason) from None

    def _still_current(self, previous):
        # Called after the TaskStore acquires its write lock. Only local facts
        # are read here; Agent DB is already guarded, so there is no lock reversal.
        if not previous():
            return False
        try:
            self._local_current()
            self._coordinator._capture(self._root)
            return _fingerprint(self._root) == self._digest
        except Exception:
            return False

    @contextmanager
    def _source_guard(self):
        coordinator, root = self._coordinator, self._root
        remaining_ms = self._local_current()
        with coordinator._state._lock, closing(coordinator._state._connect()) as guard, guard:
            guard.execute("PRAGMA busy_timeout=" + str(min(5000, remaining_ms)))
            guard.execute("BEGIN IMMEDIATE")
            coordinator._read_source_evidence_guarded(root=root)
            if _fingerprint(root) != self._digest:
                _deny()
            yield
            self._local_current()
            coordinator._capture(root)
            if _fingerprint(root) != self._digest:
                _deny()

    def _preparing_record(self):
        if not self._begun:
            _deny()
        record = json.loads(self._draft_json)
        record.update(state="PREPARING", cleanup_state="pending", version=record["version"] + 1)
        return record

    async def begin(self, **kwargs):
        async with self._coordinator._gate.request():
            return await self._coordinator._sql(self._begin_guarded, **kwargs)

    def _begin_guarded(self, **kwargs):
        with self._source_guard():
            current = kwargs["still_current"]
            kwargs["still_current"] = lambda: self._still_current(current)
            identity = self._runtime.store._begin_docking_preparation(
                expected_chat_draft=json.loads(self._draft_json), **kwargs)
            self._begun = True  # Only the actual commit acknowledgement can set this.
            return identity

    async def check(self):
        async with self._coordinator._gate.request():
            await self._coordinator._sql(self._check_guarded)

    def _check_guarded(self):
        with self._source_guard():
            record = self._runtime.store.get_chat_docking_draft(
                preparation_id=self._root.preparation_id, owner_session_id=self._root.owner)
            if record != self._preparing_record():
                _deny()

    async def verify_staged(self, *, deadline):
        await self.check()
        snapshot = await asyncio.to_thread(self._runtime.stager.read_verified_input_bytes,
            self._root.task_id, "input_manifest.json")
        if snapshot["ligand_bytes"] != self._ligand or snapshot["receptor_bytes"] != self._receptor:
            _deny()
        await self.check()
        # The guarded SQL may have finished before cancellation, while its
        # return was still queued. Recheck locally after the await, before any
        # new physical owner/task can be dispatched.
        self._local_current()
        operation, root = self._operation, self._root
        owner = chat_preparation.PreparationOwner()
        operation.projection_owner = owner
        root.preparation_owners.append(owner)  # Both owners retain it BEFORE dispatch.
        remaining = min(deadline - asyncio.get_running_loop().time(),
                        self._coordinator._remaining_ms(root) / 1000)
        operation.projection_task = asyncio.create_task(chat_preparation.run_preparation(
            "projection_verify", inputs=dict(pdb_bytes=root.source_result["pdb_bytes"],
                source=root.source_result["source"], selection=root.selection,
                derived_bytes=snapshot["receptor_bytes"], projection=root.projected["projection"]),
            deadline=time.monotonic() + remaining, owner=owner))
        result = await operation.projection_task
        if (owner.snapshot()["settled"] is not True or type(result) is not dict
                or result.get("ok") is not True
                or result.get("projection_sha256") != hashlib.sha256(canonical_json(root.projected["projection"])).hexdigest()):
            _deny()
        await self.check()
        current = await asyncio.to_thread(self._runtime.stager.read_verified_input_bytes,
            root.task_id, "input_manifest.json")
        if current != snapshot:
            _deny()
        self._verified_manifest = canonical_json(snapshot["manifest"])
        return snapshot["manifest"]

    async def seal(self, *, manifest, **kwargs):
        if self._verified_manifest is None or canonical_json(manifest) != self._verified_manifest:
            _deny()
        async with self._coordinator._gate.request():
            await self._coordinator._sql(self._seal_guarded, **kwargs)

    def _seal_guarded(self, **kwargs):
        # Agent DB is acquired FIRST; the TaskStore owns its subsequent write
        # lock and compares the exact expected PREPARING record under that lock.
        # Never invoke the two-database read guard inside the TaskStore writer.
        with self._source_guard():
            current = kwargs["still_current"]
            kwargs["still_current"] = lambda: self._still_current(current)
            self._runtime.store._seal_docking_preparation(
                expected_chat_draft=self._preparing_record(), **kwargs)
