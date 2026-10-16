"""D4 private capture, live publication latch and same-generation pose grant.

A candidate is a private observation of an original settled C2 operation. Even
a genuine candidate is NOT success/download authority: future publication must
revalidate it and win the coordinator's live terminal latch and durable CAS.
SQL candidates alone never authorize success. Public activation stays disabled.
"""
from __future__ import annotations

from contextlib import closing
import asyncio
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import sqlite3
from uuid import uuid4

from src.task_runtime.completion import DockingCompletionStore
from src.task_runtime.docking_consent import DockingConsentPolicy, canonical_json, validate_clocks
from src.task_runtime.docking_execution import (
    DockingExecution, _manifest_input_hash, _VinaPoseStreamValidator,
)
from src.task_runtime.docking_private_output import PrivateDockingOutput
from src.task_runtime.models import TaskStatus
from src.task_runtime.runtime import TaskRuntime, _ConsentExecution
from src.task_runtime.staging import DockingInputStager, _config_hash
from src.target_search.remote_clients import _resolve_organism
from .docking_coordinator import DockingRequestCoordinator, _label, _owner_label
from .docking_input_context import ChatDockingInputContext, _fingerprint
from .docking_request import OwnedDockingStructureEvidence, assess_docking_request_text


class DockingPublicationUnavailable(ValueError):
    """Fixed private failure; never expose paths, manifests or tool errors."""

    def __init__(self):
        super().__init__('docking_publication_unavailable')


def _deny():
    raise DockingPublicationUnavailable()


def _sqlite_contention(exc):
    """Only BUSY/LOCKED are request unavailability, never invalid evidence.

    Python 3.10 lacks sqlite_errorcode; accept only SQLite's exact canonical
    messages there. On newer Python prefer the primary byte of extended codes.
    Do not infer contention from substrings, causes, or non-SQLite exceptions.
    """
    if not isinstance(exc, sqlite3.OperationalError):
        return False
    code = getattr(exc, 'sqlite_errorcode', None)
    if code is not None:
        return type(code) is int and code >= 0 and (code & 0xff) in (5, 6)
    return len(exc.args) == 1 and exc.args[0] in (
        'database is locked', 'database table is locked', 'database schema is locked')


def _digest(value):
    # Identical to DockingCompletionStore.read_verified_artifact's digest rule.
    return hashlib.sha256(canonical_json(value)).hexdigest()


@dataclass(frozen=True, slots=True)
class DockingScienceProjection:
    tool_name: str
    execution_backend: str
    tool_version: str
    energy_kcal_mol: float
    pose_count: int
    score_kind: str
    pose_artifact_id: str
    pose_size: int
    pose_sha256: str
    input_hash: str
    config_hash: str
    tool_policy_digest: str
    source_digest: str
    original_receptor_digest: str
    ligand_identity_digest: str
    projection_digest: str


@dataclass(frozen=True, slots=True, repr=False)
class DockingPublicationCandidate:
    projection: DockingScienceProjection
    completion_digest: str
    context_fingerprint: str
    consent_version: int
    binding_digest: str
    runtime_generation: str
    model_generation: str
    capability_generation: str
    operation_deadline_ms: int
    operation_monotonic_expires: float
    # Retain exact owners and immutable comparison bytes for a FUTURE CAS.
    # These are deliberately not a wire DTO, authorization token or registry.
    _identities: tuple = field(repr=False)
    _consent_json: bytes = field(repr=False)

    def __repr__(self):
        return 'DockingPublicationCandidate(<private; not authority>)'


def _settled_owner(context):
    """No publication latch may take over cleanup from a physical C1/C2 owner."""
    root = context._root
    runtime, operation = context._runtime, context._execution
    if (type(runtime) is not TaskRuntime or type(operation) is not _ConsentExecution
            or operation.input_context is not context or not operation.acquired
            or operation.settled is not True or operation.status != 'SUCCEEDED'
            or operation.pending or operation.claim_uncertain or operation.unused
            or operation.reason is not None or operation.physical_unresolved is not None
            or context._operation is None or context._operation.physical_pending
            or context._publication_output is None
            or context._publication_output[-2] is not runtime
            or context._publication_output[-1] is not operation):
        _deny()
    # Settlement removes the runtime map entry; the original context retains
    # the actual operation and its completed settlement owner instead.
    if (operation.settlement is None or not operation.settlement.done()
            or operation.settlement.cancelled() or operation.settlement.exception() is not None
            or runtime._consent_executions.get(root.task_id, operation) is not operation
            or not runtime._consent_commands_released(operation)
            or any(owner.snapshot()['settled'] is not True for owner in root.preparation_owners)):
        _deny()
    for name in ('admission', 'submission', 'worker', 'backend_owner', 'final_guard', 'cancellation'):
        task = getattr(operation, name)
        if task is not None and (not task.done() or task.cancelled() or task.exception() is not None):
            _deny()


def _local(context, *, capture_model=False, _grant=None):
    """Publication clock: original claim budget, NEVER raw/unused-consent TTL."""
    _settled_owner(context)
    coordinator, root = context._coordinator, context._root
    runtime, operation = context._runtime, context._execution
    if (coordinator._enabled is not True or root.closed or root.cancel_requested
            or coordinator._roots.get(root.root_id) is not root or root.input_context is not context
            or operation.signal.is_set() or operation.backend_signal is not None and operation.backend_signal.is_set()
            or runtime._closed or runtime._closing or runtime.store is not coordinator._tasks
            or context._operation.aborted.is_set()
            or context._verified_manifest is None or context._ready_record is None):
        _deny()
    origin = context._publication_origin
    if ((root.root_id, root.owner, root.task_id, root.preparation_id, root.origin) != origin[:5]
            or root.claim is not origin[5] or root.snapshot is not origin[6]
            or root.structure_context_json != origin[7]
            or canonical_json(root.source_reference) != origin[8]
            or coordinator._state is not origin[9] or coordinator._tasks is not origin[10]
            or coordinator is not origin[11] or root.current['model'] is not origin[12]
            or root.current['policy'] is not origin[13]
            or (root.current['model_generation'], root.current['capability_generation']) != origin[14:16]
            or root is not origin[16] or context is not origin[17]
            or _fingerprint(root) != context._digest):
        _deny()
    receptor, ligand, refs = coordinator._input_descriptors(root)
    if (receptor != root.receptor_descriptor or ligand != root.ligand_descriptor
            or any(root.accepted_slots[key] != dict(state='fixed', value=value) for key, value in refs.items())
            or root.projected['pdb_bytes'] != context._receptor
            or root.ligand['sdf_bytes'] != context._ligand):
        _deny()
    output, execution, stager, completions, input_stager, original_runtime, original_operation = context._publication_output
    if (type(output) is not PrivateDockingOutput or type(execution) is not DockingExecution
            or type(stager) is not DockingInputStager or type(input_stager) is not DockingInputStager
            or type(completions) is not DockingCompletionStore
            or runtime is not original_runtime or operation is not original_operation
            or runtime._docking_private_output is not output
            or runtime._docking_consent_execution is not execution or runtime.stager is not input_stager
            or input_stager._root != output.staging_root
            or execution._stager is not stager or execution._completions is not completions
            or type(runtime._docking_consent_policy) is not DockingConsentPolicy
            or runtime._docking_consent_policy is not output.policy
            or runtime._docking_consent_policy != root.current['policy']):
        _deny()
    limits = context.claimed_execution_limits(operation)
    if any(operation.row[key] != value for key, value in limits.items()):
        _deny()
    if _grant is not None:
        left = _grant_remaining(context, _grant)
    else:
        remaining = []
        for wall, mono in ((coordinator._wall(), coordinator._mono()),
                           (runtime._consent_wall_time_ms(), runtime._consent_monotonic())):
            validate_clocks(wall, mono)
            remaining.extend((limits['operation_deadline_ms'] - wall,
                              (limits['operation_monotonic_expires'] - mono) * 1000))
        left = min(remaining)
    if not math.isfinite(left) or left < 1:
        _deny()
    if capture_model:
        # _capture() calls _remaining_ms(), which includes the raw deadline.
        # Use its exact identity contract here with the publication clock above.
        live, current = coordinator._capture_current(), root.current
        if (type(live) is not dict or set(live) != set(current)
                or live['model'] is not current['model']
                or any(live[key] != current[key] for key in (
                    'policy', 'model_generation', 'capability_generation'))):
            _deny()
    return int(left)


def _source(context, *, _grant=None):
    """Existing Agent-first source pattern, without the raw execution clock."""
    coordinator, root = context._coordinator, context._root
    _local(context, capture_model=True, _grant=_grant)
    digest = hashlib.sha256(root.structure_context_json.encode('utf-8')).hexdigest()
    if json.loads(root.claim.binding_json).get('structure_context_digest') != digest:
        _deny()
    assessment = assess_docking_request_text(root.snapshot.query, enabled=True, enable_tools=True)
    pdb_id = assessment['inputs'].get('pdb_id')
    resolved = OwnedDockingStructureEvidence(coordinator._state,
        clock=lambda: coordinator._wall() / 1000).resolve(
            json.loads(root.structure_context_json), session_id=root.owner, pdb_id=pdb_id)
    if resolved is None:
        _deny()
    _, taxonomy = _resolve_organism(resolved['target']['organism'])
    reference = dict(context=resolved['context'], pdb_id=pdb_id,
        target=dict(uniprot_id=resolved['target']['uniprot_id'], taxonomy_id=taxonomy))
    if reference != root.source_reference:
        _deny()
    coordinator._check_root(root=root)


def _receipt(context):
    runtime, operation, root = context._runtime, context._execution, context._root
    row = runtime.store._owned_docking_consent(
        preparation_id=root.preparation_id, owner_session_id=root.owner)
    if any(row[key] != value for key, value in context._execution_facts.items()):
        _deny()
    limits = context.claimed_execution_limits(operation)
    if any(row[key] != value for key, value in limits.items()):
        _deny()
    reserved = operation.raw_expires is not None
    if (row['state'] != ('DISPATCH_RESERVED' if reserved else 'CLAIMED')
            or row['dispatch_state'] != ('reserved' if reserved else 'not_reserved')
            or row['version'] != context._ready_record['version'] + (4 if reserved else 3)
            or row['cleanup_state'] != 'settled' or row['execution_occupied'] != 0
            or row['execution_token'] != operation.token or row['view_status'] != 'SUCCEEDED'
            or row['primary_reason'] is not None or row['cancel_requested'] != 0):
        _deny()
    binding = json.loads(row['binding_json'])
    if (binding != context._ready_record['binding']
            or json.loads(row['policy_json']) != asdict(runtime._docking_consent_policy)
            or row['binding_digest'] != hashlib.sha256(
                b'medchat-docking-consent-v1\0' + canonical_json(binding)).hexdigest()
            or any(binding[key] != value for key, value in json.loads(row['identity_json']).items())):
        _deny()
    task = runtime.store.get_owned(root.task_id, root.owner)
    if (task.task_id != root.task_id or task.status is not TaskStatus.SUCCEEDED
            or task.task_type != 'docking' or task.backend != 'local'
            or task.input_manifest_path != str(context._operation.manifest_path)
            or runtime.store.get_submission_digest(root.task_id) != binding['request_digest']):
        _deny()
    # Task result/provenance/DB score is explicitly NOT scientific evidence.
    return row, binding


def _capture_guarded(context, *, _grant=None):
    coordinator, root, runtime = context._coordinator, context._root, context._runtime
    remaining = _local(context, capture_model=True, _grant=_grant)
    # Both databases are reserved Agent-first, as in the existing source guard.
    # No SQL mutation and no locks acquired in reverse order.
    with coordinator._state._lock, closing(coordinator._state._connect()) as guard, guard:
        guard.execute('ATTACH DATABASE ? AS docking_publication_read', (str(coordinator._tasks.db_path),))
        guard.execute('PRAGMA busy_timeout=' + str(min(5000, remaining)))
        guard.execute('BEGIN IMMEDIATE')
        _source(context, _grant=_grant)
        row, binding = _receipt(context)
        runtime._require_consent_output()
        output = context._publication_output[0]
        snapshot = output.stager.read_verified_input_bytes(root.task_id, row['manifest_locator'])
        manifest = snapshot['manifest']
        if (canonical_json(manifest) != context._verified_manifest
                or snapshot['receptor_bytes'] != context._receptor
                or snapshot['ligand_bytes'] != context._ligand
                or _manifest_input_hash(manifest) != binding['input_hash']
                or _config_hash(manifest['config']) != binding['config_hash']
                or runtime._docking_request_digest(manifest) != binding['request_digest']
                or any(manifest['config'][key] != value for key, value in context._parameters.items())):
            _deny()
        hashes = dict(input_hash=_manifest_input_hash(manifest), config_hash=_config_hash(manifest['config']))
        completion = output.completions.load_verified(root.task_id, **hashes)
        if (type(completion.best_energy) not in (int, float) or not math.isfinite(completion.best_energy)
                or type(completion.pose_count) is not int or completion.pose_count < 1
                or completion.demo_mode is not False or completion.fallback_used is not False
                or completion.real_execution is not True):
            _deny()
        completion_digest = _digest(completion.to_dict())
        pose = output.completions.read_verified_artifact(root.task_id, **hashes,
            completion_digest=completion_digest, artifact_id='pose')
        if (type(pose['content']) is not bytes or len(pose['content']) != completion.pose.size
                or hashlib.sha256(pose['content']).hexdigest() != completion.pose.sha256):
            _deny()
        # Reuse the execution boundary's parsed-pose contract; a schema-valid
        # changed score/count must not become a scientific observation.
        validator = _VinaPoseStreamValidator(expected_pose_count=completion.pose_count,
            expected_best_energy=completion.best_energy)
        validator.feed(pose['content'])
        validator.finish()
        # Re-read to fence mutation during the physical observation. This is a
        # bounded observation only, NOT a lock on future files or publication.
        if (output.stager.read_verified_input_bytes(root.task_id, row['manifest_locator']) != snapshot
                or _digest(output.completions.load_verified(root.task_id, **hashes).to_dict()) != completion_digest
                or output.completions.has_invalidation_marker(root.task_id)):
            _deny()
        _source(context, _grant=_grant)
        if _receipt(context)[0] != row:
            _deny()
        runtime._require_consent_output()
        projection = DockingScienceProjection(
            tool_name='molecular_docking', execution_backend='local', tool_version=completion.tool_version,
            energy_kcal_mol=completion.best_energy, pose_count=completion.pose_count,
            score_kind='computed_score_not_experimental', pose_artifact_id='pose',
            pose_size=completion.pose.size, pose_sha256=completion.pose.sha256, **hashes,
            tool_policy_digest=binding['tool_policy_digest'], source_digest=_digest(root.source_reference),
            original_receptor_digest=_digest(root.receptor_descriptor),
            ligand_identity_digest=_digest(root.ligand_descriptor),
            projection_digest=_digest(root.projected['projection']))
        candidate = DockingPublicationCandidate(projection, completion_digest, context._digest.hex(),
            row['version'], row['binding_digest'], row['runtime_generation'],
            root.current['model_generation'], root.current['capability_generation'],
            row['operation_deadline_ms'], row['operation_monotonic_expires'],
            (coordinator, root, context, runtime, context._execution, *context._publication_output),
            canonical_json(row))
        if _grant is not None:
            if candidate != _grant._origin[2]:
                _deny()
            return pose['content']
        return candidate


async def capture_docking_publication_candidate(coordinator, *, root_id, owner_session_id):
    """Capture original live C2 evidence or raise DockingPublicationUnavailable.

    Server-only. No caller path, score, completion, dictionary or candidate can
    supply authority. Does not publish, persist, rerun science or mint a grant.
    """
    if (type(coordinator) is not DockingRequestCoordinator
            or not _label(root_id) or not _owner_label(owner_session_id)):
        _deny()
    root = coordinator._owned(root_id, owner_session_id)
    if root is None or type(root.input_context) is not ChatDockingInputContext:
        _deny()
    context = root.input_context
    try:
        if context._coordinator is not coordinator or context._root is not root:
            _deny()
        _local(context)
        async with coordinator._gate.request():
            # Existing shield/drain keeps the real thread AND reader until it
            # returns even on repeated caller cancellation. No detached future.
            candidate = await coordinator._sql(_capture_guarded, context=context)
            _local(context, capture_model=True)
            return candidate
    except DockingPublicationUnavailable:
        raise
    except Exception:
        raise DockingPublicationUnavailable() from None


@dataclass(frozen=True, slots=True, repr=False)
class DockingPublicationOutcome:
    """Private live observation; NOT a download capability or transport DTO."""
    status: str
    reason_code: str
    projection: DockingScienceProjection | None = None

    def __repr__(self):
        return 'DockingPublicationOutcome(<private; not download authority>)'


@dataclass(eq=False, repr=False)
class _Publication:
    context: ChatDockingInputContext
    feedback_deadline: float
    task: object = None
    started: bool = field(default=False, init=False)
    outcome: DockingPublicationOutcome | None = None
    candidate: DockingPublicationCandidate | None = None
    receipt: dict | None = None
    acknowledged: bool = False
    reconciliation: str = 'not_needed'
    observation: object = None
    access_disabled: bool = False
    grant: object = None
    _latch: tuple | None = field(default=None, init=False, repr=False)
    snapshots: set = field(default_factory=set, repr=False)
    _origin: tuple = field(init=False, repr=False)

    def __post_init__(self):
        origin = self.context._publication_origin
        self._origin = (origin[16], self.context, origin[11], origin[10], self)

    def _finish_unstarted(self, done):
        # A task cancelled before its first step never enters the worker's
        # finally. Only that exact no-I/O case can settle outside the worker;
        # completion/cancellation of a started wrapper is NOT cleanup evidence.
        root, context, coordinator, _, original = self._origin
        if (original is not self or self.context is not context
                or context._root is not root or context._coordinator is not coordinator
                or coordinator._roots.get(root.root_id) is not root
                or root.publication is not self or root.input_context is not context
                or done is not self.task or root.task is not done
                or self.started is not False or not done.done() or not done.cancelled()
                or self.candidate is not None or self.receipt is not None
                or self.acknowledged or self.observation is not None
                or self.reconciliation != 'not_needed' or root.close_state != 'not_started'
                or self.outcome is not None and self.outcome.status == 'SUCCEEDED'):
            return
        try:
            _settled_owner(context)
        except Exception:
            return  # Preserve unresolved physical C1/C2 ownership.
        self.suppress('cancelled')
        root.close_state = 'confirmed'

    def suppress(self, reason):
        # Event-loop-only, first terminal wins. In particular an exception after
        # success can never overwrite that decision; access is separately checked.
        if self.outcome is None:
            status = ('CANCELLED' if reason in {'cancelled', 'closed'} else
                      'TIMED_OUT' if reason == 'work_deadline_exceeded' else 'UNKNOWN')
            self.outcome = DockingPublicationOutcome(status, 'publication_' + status.lower())
        if self.outcome.status != 'SUCCEEDED':
            root = self._origin[0]
            root.phase, root.reason = self.outcome.status, self.outcome.reason_code
            root.closed = True
            if root.issuer is not None:
                root.issuer.close()
        return self.outcome


@dataclass(frozen=True, slots=True, repr=False)
class _PoseGrant:
    """Private immutable latch witness, never serialized or recovered from SQL."""
    owner: str
    task_id: str
    completion_digest: str
    runtime_generation: str
    clocks: tuple
    _origin: tuple
    artifact_id: str = field(default='pose', init=False)
    _self: object = field(init=False, repr=False)

    def __post_init__(self):
        object.__setattr__(self, '_self', self)


def _grant_remaining(context, grant):
    state = context._root.publication
    if (type(grant) is not _PoseGrant or grant._self is not grant
            or type(state) is not _Publication or state.grant is not grant
            or state.access_disabled or not state.acknowledged
            or state.context is not context or state._origin[4] is not state
            or any(a is not b for a, b in zip(state._origin[:3],
                   (context._root, context, context._coordinator)))
            or any(a is not b for a, b in zip(grant._origin,
                   (state, state.outcome, state.candidate, context)))
            or type(state._latch) is not tuple or len(state._latch) != 4
            or any(a is not b for a, b in zip(state._latch,
                   (state, state.outcome, state.candidate, grant)))
            or state.outcome is None or state.outcome.status != 'SUCCEEDED'
            or grant.owner != context._root.owner or grant.task_id != context._root.task_id
            or grant.completion_digest != state.candidate.completion_digest
            or grant.runtime_generation != context._runtime._docking_consent_policy.runtime_generation):
        _deny()
    coordinator, runtime = context._coordinator, context._runtime
    remaining = []
    for (wall, mono), (start_wall, start_mono) in zip(
            ((coordinator._wall(), coordinator._mono()),
             (runtime._consent_wall_time_ms(), runtime._consent_monotonic())), grant.clocks):
        validate_clocks(wall, mono)
        remaining.extend((start_wall + 86_400_000 - wall, (start_mono + 86_400 - mono) * 1000))
    left = min(remaining)
    if not math.isfinite(left) or left < 1:
        _deny()
    return left


def _publication_record(state, candidate):
    root = state._origin[0]
    row = json.loads(candidate._consent_json)
    projection = candidate.projection
    return dict(receipt_id=str(uuid4()), preparation_id=root.preparation_id,
        task_id=root.task_id, root_id=root.root_id, owner_session_id=root.owner,
        runtime_generation=candidate.runtime_generation, model_generation=candidate.model_generation,
        capability_generation=candidate.capability_generation, execution_token=row['execution_token'],
        consent_version=candidate.consent_version,
        consent_digest=hashlib.sha256(candidate._consent_json).hexdigest(),
        binding_digest=candidate.binding_digest, completion_digest=candidate.completion_digest,
        science_digest=_digest(asdict(projection)), context_fingerprint=candidate.context_fingerprint,
        **{name: getattr(projection, name) for name in ('projection_digest', 'source_digest',
            'original_receptor_digest', 'ligand_identity_digest', 'input_hash', 'config_hash',
            'tool_policy_digest')},
        operation_deadline_ms=candidate.operation_deadline_ms,
        operation_monotonic_expires=candidate.operation_monotonic_expires)


def _publication_live(state):
    context = state.context
    root, original_context, coordinator, _, original_state = state._origin
    if (original_context is not context or original_state is not state
            or root.publication is not state or root.input_context is not context
            or root.task is not state.task or asyncio.current_task() is not state.task
            or coordinator._roots.get(root.root_id) is not root or state.outcome is not None):
        _deny()
    left = _local(context, capture_model=True)
    # One existing control-feedback slice, shared by duplicates; never renew the
    # original operation clocks or use the raw 300s deadline for publication.
    if asyncio.get_running_loop().time() >= state.feedback_deadline:
        _deny()
    return left


def _publication_failure(state):
    """Classify only the original operation clocks; never infer a new budget."""
    root, context, coordinator, _, _ = state._origin
    try:
        limits = context.claimed_execution_limits(context._execution)
        runtime = context._publication_output[-2]
        for wall, mono in ((coordinator._wall(), coordinator._mono()),
                           (runtime._consent_wall_time_ms(), runtime._consent_monotonic())):
            validate_clocks(wall, mono)
            if min(limits['operation_deadline_ms'] - wall,
                   (limits['operation_monotonic_expires'] - mono) * 1000) < 1:
                return 'work_deadline_exceeded'
    except Exception:
        pass
    return 'publication_unavailable'


async def _run_publication(state):
    state.started = True  # Before the first await or any reader/SQL dispatch.
    root, context, coordinator, task_store, _ = state._origin
    try:
        candidate = await capture_docking_publication_candidate(coordinator,
            root_id=root.root_id, owner_session_id=root.owner)
        state.candidate = candidate
        state.receipt = _publication_record(state, candidate)
        async with coordinator._gate.request():
            _publication_live(state)
            # _capture_guarded has EXITED its Agent-first attached transaction.
            # Never acquire this independent writer connection under that lock.
            ack = await coordinator._sql(task_store._record_docking_publication,
                expected_consent=candidate._consent_json, receipt=state.receipt)
            if type(ack) is not dict or canonical_json(ack) != canonical_json(state.receipt):
                _deny()
            state.acknowledged = True
            _publication_live(state)
            fresh = await coordinator._sql(_capture_guarded, context=context)
            if fresh != candidate:
                _deny()
            _publication_live(state)
            # No await from the final original-identity/clock check through the
            # one-terminal decision. Persisted science is never this latch.
            runtime = context._runtime
            clocks = ((coordinator._wall(), coordinator._mono()),
                      (runtime._consent_wall_time_ms(), runtime._consent_monotonic()))
            for wall, mono in clocks:
                validate_clocks(wall, mono)
                if min(fresh.operation_deadline_ms - wall,
                       (fresh.operation_monotonic_expires - mono) * 1000) < 1:
                    _deny()
            if asyncio.get_running_loop().time() >= state.feedback_deadline:
                _deny()
            outcome = DockingPublicationOutcome('SUCCEEDED', 'publication_confirmed', fresh.projection)
            grant = _PoseGrant(root.owner, root.task_id, fresh.completion_digest,
                fresh.runtime_generation, clocks, (state, outcome, candidate, context))
            state.outcome, state.grant = outcome, grant
            state._latch = (state, outcome, candidate, grant)
            root.phase, root.reason = state.outcome.status, state.outcome.reason_code
    except asyncio.CancelledError:
        state.suppress('cancelled')
    except Exception:
        state.suppress(_publication_failure(state))
    finally:
        # _sql shields/drains the actual to_thread before reaching this block,
        # including repeated cancellation and a commit followed by an exception.
        if state.outcome is None:
            state.suppress('publication_unavailable')
        if state.outcome.status != 'SUCCEEDED':
            if state.receipt is not None:
                state.reconciliation = 'pending'
                try:
                    ack = await coordinator._sql(task_store._suppress_docking_publication,
                        receipt=state.receipt, status=state.outcome.status)
                    state.reconciliation = 'confirmed' if ack is True else 'unconfirmed'
                except (Exception, asyncio.CancelledError):
                    state.reconciliation = 'unconfirmed'
            root.close_state = ('confirmed' if state.reconciliation in {'not_needed', 'confirmed'}
                                else 'unconfirmed')


async def _observe_published(state):
    """One retained reader per original root; invalidation never unseals success."""
    root, context, coordinator, _, _ = state._origin
    try:
        async with coordinator._gate.request():
            await coordinator._sql(_capture_guarded, context=context, _grant=state.grant)
            _local(context, capture_model=True, _grant=state.grant)
        return state.outcome
    except (Exception, asyncio.CancelledError) as exc:
        if not _sqlite_contention(exc):
            state.access_disabled = True
        raise


async def _confirmed_observation(state):
    root, _, coordinator, _, _ = state._origin
    if state.access_disabled:
        _deny()
    try:
        _grant_remaining(state.context, state.grant)
    except Exception:
        state.access_disabled = True
        raise
    if not state.task.done():
        # The initial owner has just won the synchronous latch and is exiting
        # its reader. Do not replace its task during physical lease release.
        return state.outcome
    if state.observation is None or state.observation.done():
        state.observation = root.task = asyncio.create_task(_observe_published(state))
        coordinator._gate.retain_task(state.observation)
    try:
        return await asyncio.shield(state.observation)
    except asyncio.CancelledError:
        # No second terminal and no cancelled wrapper detached from its reader.
        # This is the existing outcome, not a fresh grant/download authorization.
        if state.access_disabled:
            _deny()
        return state.outcome


async def publish_docking_result(coordinator, *, root_id, owner_session_id):
    """Internal only: fresh original C2 evidence -> candidate CAS -> live latch.

    No supplied candidate, path, score, callbacks, retries or DB recovery. The
    original root retains the actual task before it can enter any SQL/file work.
    A feedback timeout/cancel seals non-success while that task still owns I/O.
    """
    if (type(coordinator) is not DockingRequestCoordinator
            or not _label(root_id) or not _owner_label(owner_session_id)):
        _deny()
    root = coordinator._owned(root_id, owner_session_id)
    if root is None or type(root.input_context) is not ChatDockingInputContext:
        _deny()
    context = root.input_context
    # These original-object checks also apply to terminal observations; copying
    # reciprocal pointers cannot transfer a successful or suppressed latch.
    if (context._coordinator is not coordinator or context._root is not root
            or context._publication_origin[11] is not coordinator
            or context._publication_origin[16] is not root
            or context._publication_origin[17] is not context):
        _deny()
    state = root.publication
    if state is not None:
        if (type(state) is not _Publication or state.context is not context
                or state._origin[0] is not root or state._origin[1] is not context
                or state._origin[2] is not coordinator or state._origin[4] is not state):
            _deny()
        if state.outcome is not None:
            if state.outcome.status == 'SUCCEEDED':
                # Invalid generation/source/bytes removes access, not the first
                # outcome. Never consult the publication table to reconstruct it.
                return await _confirmed_observation(state)
            return state.outcome
    else:
        if root.closed or root.task is not None and not root.task.done():
            _deny()  # Never replace another actual root worker.
        # This check and state installation have no await between them. Failed
        # admission must leave normal C1/C2 stop/drain/cleanup fully in charge;
        # publication failure is not evidence of physical settlement.
        _settled_owner(context)
        state = _Publication(context, asyncio.get_running_loop().time() + 5.0)
        root.publication = state
        try:
            _local(context)
        except Exception:
            state.suppress(_publication_failure(state))
            root.close_state = 'confirmed'  # No publication I/O was dispatched.
            return state.outcome
        root.phase, root.reason = 'RESULT_PENDING', 'publication_pending'
        state.task = root.task = asyncio.create_task(_run_publication(state))
        coordinator._gate.retain_task(state.task)
        state.task.add_done_callback(state._finish_unstarted)
    try:
        left = _local(context)
        timeout = min(left / 1000, max(0, state.feedback_deadline - asyncio.get_running_loop().time()))
        done, _ = await asyncio.wait({state.task}, timeout=timeout)
        if not done:
            try:
                _local(context)
            except Exception:
                reason = 'work_deadline_exceeded'
            else:
                reason = 'publication_unavailable'
            coordinator._interrupt(root, reason)
        elif not state.task.cancelled():
            state.task.result()
        if state.outcome is None:
            coordinator._stop(root, 'publication_unavailable')
        return state.outcome
    except asyncio.CancelledError:
        if state.outcome is not None and state.outcome.status == 'SUCCEEDED':
            return state.outcome  # The live latch won before this cancellation.
        coordinator._interrupt(root, 'cancelled')
        raise
    except Exception:
        # A duplicate that observes invalidation while the real writer is held
        # must seal that original operation too, not leave it eligible for a
        # later restored snapshot. No readback/retry is allowed.
        state.suppress(_publication_failure(state))
        coordinator._interrupt(root, 'publication_unavailable')
        return state.outcome
