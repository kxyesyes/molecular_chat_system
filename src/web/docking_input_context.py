"""Server-only, one-use bridge from a live confirmed root to C1 and C2.

This is not a wire DTO or execution consent. It retains the actual source
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
from src.task_runtime.docking_consent import CONSENT_TTL_MS, DockingConsentError, canonical_json


_ISSUER = object()
_EXECUTION_LIMITS = ('operation_deadline_ms', 'operation_monotonic_expires')


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
        caller = asyncio.current_task()
        def capture():
            coordinator._read_source_reference(root=root)
            if (root.confirmation_key is None or root.missing
                    or root.reason != "inputs_confirmed_staging_required"
                    or root.task is not None and not root.task.done()
                       and not (root.staging_started and root.task is caller)
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
                 "_begun", "_verified_manifest", "_ready_record", "_execution", "_execution_facts",
                 "_execution_limits", "_publication_origin", "_publication_output")

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
        self._ready_record = None
        self._execution = None
        self._execution_facts = None
        self._execution_limits = None
        # Original live identities for D4's read-only capture, not publication
        # authority. Never recover these from a later TaskStore read.
        self._publication_origin = (
            root.root_id, root.owner, root.task_id, root.preparation_id, root.origin,
            root.claim, root.snapshot, root.structure_context_json,
            canonical_json(root.source_reference), coordinator._state, coordinator._tasks,
            coordinator, root.current['model'], root.current['policy'],
            root.current['model_generation'], root.current['capability_generation'], root, self)
        self._publication_output = None

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
            # Derive only from the acknowledged seal, never recover authority
            # from a later READY read. Keep the original upstream draft immutable.
            record = self._preparing_record()
            record.update(state="READY", version=record["version"] + 1,
                binding=json.loads(canonical_json(kwargs["binding"])),
                binding_digest=kwargs["digest"], approval_nonce_hash=kwargs["nonce_hash"],
                manifest_locator="input_manifest.json",
                expires_at_ms=kwargs["binding"]["expires_at_ms"],
                monotonic_expires=kwargs["monotonic_now"] + CONSENT_TTL_MS / 1000)
            self._ready_record = record

    def _ready_current(self):
        if (self._ready_record is None or self._runtime.store is not self._coordinator._tasks
                or self._runtime._closed or self._runtime._closing
                or self._runtime._docking_consent_policy != self._root.current["policy"]):
            _deny()
        self._local_current()

    def _check_ready_guarded(self):
        coordinator, root = self._coordinator, self._root
        self._ready_current()
        # Same Agent-first read guard; READY uses its separately acknowledged
        # consent TTL, never the exhausted upstream preparation budget.
        with coordinator._state._lock, closing(coordinator._state._connect()) as guard, guard:
            guard.execute("ATTACH DATABASE ? AS docking_ready_read", (str(coordinator._tasks.db_path),))
            guard.execute("PRAGMA busy_timeout=" + str(min(5000, coordinator._remaining_ms(root))))
            guard.execute("BEGIN IMMEDIATE")
            coordinator._read_source_evidence_guarded(root=root)
            record = self._runtime.store.get_chat_docking_draft(
                preparation_id=root.preparation_id, owner_session_id=root.owner)
            if record != self._ready_record or _fingerprint(root) != self._digest:
                _deny()
            self._ready_current()
            coordinator._capture(root)

    def bind_execution(self, runtime, operation):
        """Bind only the original C1 runtime/receipt, never a reconstructed context."""
        from src.task_runtime.runtime import TaskRuntime
        if (type(runtime) is not TaskRuntime or runtime is not self._runtime
                or self._execution is not None and self._execution is not operation):
            _deny()
        self._ready_current()
        if runtime.store._chat_draft_record(operation.row) != self._ready_record:
            _deny()
        mutable = {'state', 'version', 'cleanup_state', 'execution_token', 'execution_occupied',
                   'dispatch_state', 'view_status', 'primary_reason', 'cancel_requested',
                   'operation_deadline_ms', 'operation_monotonic_expires'}
        self._execution_facts = {key: value for key, value in operation.row.items() if key not in mutable}
        output = runtime._docking_private_output
        runtime._require_consent_output()
        self._publication_output = (output, output.execution, output.stager,
                                    output.completions, runtime.stager, runtime, operation)
        self._execution = operation

    def validate_execution_row(self, operation, row, *, phase=None):
        """Local-only under the TaskStore writer; Agent lock is already held."""
        self._ready_current()
        if (operation is not self._execution or operation.signal.is_set()
                or self._runtime._consent_executions.get(row['task_id']) is not operation):
            _deny()
        self._coordinator._capture(self._root)
        if any(row[key] != value for key, value in self._execution_facts.items()):
            _deny()
        limits = (None, None) if self._execution_limits is None else self._execution_limits
        if tuple(row[key] for key in _EXECUTION_LIMITS) != limits:
            _deny()
        record = self._runtime.store._chat_draft_record(row)
        expected = self._ready_record
        if (any(record[key] != expected[key] for key in expected
                if key not in {'state', 'version', 'cleanup_state'})
                or _fingerprint(self._root) != self._digest):
            _deny()
        if row['state'] == 'READY':
            if record != expected:
                _deny()
        elif (row['state'] not in {'CLAIMED', 'DISPATCH_RESERVED'}
                or row['version'] != expected['version'] + (1 if row['state'] == 'CLAIMED' else 2)
                or row['cleanup_state'] != 'pending'
                or row['execution_token'] != operation.token
                or row['view_status'] != 'ACTIVE' or row['cancel_requested']):
            _deny()
        if phase is not None:
            reserved = phase == 'dispatch' or phase == 'result' and operation.raw_expires is not None
            state = 'DISPATCH_RESERVED' if reserved else 'READY' if phase == 'claim' else 'CLAIMED'
            if (row['state'] != state
                    or row['dispatch_state'] != ('reserved' if reserved else 'not_reserved')
                    or row['execution_occupied'] != (0 if phase == 'claim' else 1)):
                _deny()

    async def claim_execution(self, operation):
        async with self._coordinator._gate.request():
            return await self._coordinator._sql(self._claim_execution_guarded, execution=operation)

    def _claim_execution_guarded(self, execution):
        operation = execution
        with self.execution_guard(operation, phase='claim'):
            self._runtime._validate_consent_manifest(operation)
            # Record the exact clock samples passed to the real SQL UPDATE,
            # not fresh clocks or a mutable post-claim database read.
            samples = [[], []]
            def clock(index, source):
                value = source()
                samples[index].append(value)
                return value
            acquired = self._runtime.store._claim_docking_consent(
                operation.row, operation.token,
                lambda row: self._runtime._validate_consent_row(operation, row, phase='claim'),
                lambda: clock(0, self._runtime._consent_wall_time_ms),
                lambda: clock(1, self._runtime._consent_monotonic))
            # Preserve the real commit acknowledgement before any post-guard check.
            operation.acquired = acquired
            if acquired:
                if any(len(values) != 1 for values in samples):
                    _deny()
                limit = self._ready_record['binding']['operation_limit_seconds']
                self._execution_limits = (samples[0][0] + limit * 1000, samples[1][0] + limit)
            return acquired

    def claimed_execution_limits(self, operation):
        if (operation is not self._execution or not operation.acquired
                or self._execution_limits is None):
            _deny()
        return dict(zip(_EXECUTION_LIMITS, self._execution_limits))

    def execution_step(self, operation, inputs, phase):
        # Called from the retained execution worker, never the event-loop thread.
        # One gate owner retains its physical SQL work; cancellation cannot detach
        # it. The reader ends before returning to the actual raw executor.
        future = asyncio.run_coroutine_threadsafe(
            self._execution_step(operation, inputs, phase), operation.loop)
        return future.result()

    async def _execution_step(self, operation, inputs, phase):
        async with self._coordinator._gate.request():
            return await self._coordinator._sql(self._execution_step_guarded,
                execution=operation, inputs=inputs, phase=phase)

    def _execution_step_guarded(self, execution, inputs, phase):
        operation = execution
        with self.execution_guard(operation, phase=phase):
            return self._runtime._consent_execution_guard_owned(operation, inputs, phase)

    @contextmanager
    def execution_guard(self, operation, *, phase=None):
        from .docking_coordinator import _Unavailable
        if operation is not self._execution:
            _deny()
        try:
            with self._source_guard():
                row = self._runtime.store._owned_docking_consent(
                    owner_session_id=self._root.owner, preparation_id=self._root.preparation_id)
                self.validate_execution_row(operation, row, phase=phase)
                yield
        except _Unavailable:
            _deny()
