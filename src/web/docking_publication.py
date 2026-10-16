"""D4 verified-result capture ONLY: no publication, grants, routes or recovery.

A candidate is a private observation of an original settled C2 operation. Even
a genuine candidate is NOT success/download authority: future publication must
revalidate it and win the coordinator's live terminal latch and durable CAS.
"""
from __future__ import annotations

from contextlib import closing
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math

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


def _local(context, *, capture_model=False):
    """Publication clock: original claim budget, NEVER raw/unused-consent TTL."""
    coordinator, root = context._coordinator, context._root
    runtime, operation = context._runtime, context._execution
    if (coordinator._enabled is not True or root.closed or root.cancel_requested
            or coordinator._roots.get(root.root_id) is not root or root.input_context is not context
            or type(runtime) is not TaskRuntime or type(operation) is not _ConsentExecution
            or operation.input_context is not context or not operation.acquired
            or operation.settled is not True or operation.status != 'SUCCEEDED'
            or operation.pending or operation.claim_uncertain or operation.unused
            or operation.reason is not None or operation.physical_unresolved is not None
            or operation.signal.is_set() or operation.backend_signal is not None and operation.backend_signal.is_set()
            or runtime._closed or runtime._closing or runtime.store is not coordinator._tasks
            or context._operation is None or context._operation.aborted.is_set()
            or context._operation.physical_pending
            or context._verified_manifest is None or context._ready_record is None
            or context._publication_output is None):
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


def _source(context):
    """Existing Agent-first source pattern, without the raw execution clock."""
    coordinator, root = context._coordinator, context._root
    _local(context, capture_model=True)
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


def _capture_guarded(context):
    coordinator, root, runtime = context._coordinator, context._root, context._runtime
    remaining = _local(context, capture_model=True)
    # Both databases are reserved Agent-first, as in the existing source guard.
    # No SQL mutation and no locks acquired in reverse order.
    with coordinator._state._lock, closing(coordinator._state._connect()) as guard, guard:
        guard.execute('ATTACH DATABASE ? AS docking_publication_read', (str(coordinator._tasks.db_path),))
        guard.execute('PRAGMA busy_timeout=' + str(min(5000, remaining)))
        guard.execute('BEGIN IMMEDIATE')
        _source(context)
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
        _source(context)
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
        return DockingPublicationCandidate(projection, completion_digest, context._digest.hex(),
            row['version'], row['binding_digest'], row['runtime_generation'],
            root.current['model_generation'], root.current['capability_generation'],
            row['operation_deadline_ms'], row['operation_monotonic_expires'],
            (coordinator, root, context, runtime, context._execution, *context._publication_output),
            canonical_json(row))


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
