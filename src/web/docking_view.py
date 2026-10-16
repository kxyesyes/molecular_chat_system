"""Closed, detached C view capture; not a sender or publication authority.

Only trusted server attachments supply sequence/generation. The eventual sender
must bind them to its own physical operation. This adapter never starts first
publication, approves a consent, restores history or writes a success receipt.
"""
import json
import math

from .docking_coordinator import (
    DockingRequestCoordinator, _label, _owner_label, _pending_preparation,
)
from .docking_result_view import verified_docking_result


_MAX_INTEGER = 2**53 - 1
_MAX_BYTES = 64 * 1024
_FIELDS = frozenset({'receptor_ref', 'ligand_ref', 'center', 'size', 'exhaustiveness', 'num_modes'})
_PHASES = {
    'BUSY': ('running', 'ACTIVE'), 'PREPARING': ('running', 'ACTIVE'),
    'AWAITING_INPUT': ('awaiting_input', 'AWAITING_INPUT'),
    'READY': ('awaiting_consent', 'READY'), 'APPROVING': ('running', 'ACTIVE'),
    'EXECUTING': ('running', 'ACTIVE'), 'RESULT_PENDING': ('running', 'ACTIVE'),
    'SUCCEEDED': ('terminal', 'SUCCEEDED'), 'FAILED': ('terminal', 'FAILED'),
    'CANCELLED': ('terminal', 'CANCELLED'), 'CLOSED': ('terminal', 'CANCELLED'),
    'TIMED_OUT': ('terminal', 'TIMED_OUT'), 'UNKNOWN': ('terminal', 'UNKNOWN'),
    'UNAVAILABLE': ('terminal', 'UNKNOWN'),
}
_WAIT_REASONS = frozenset({'source_preparation_required', 'clarification_required',
    'scientific_selection_required', 'parameters_confirmation_required',
    'inputs_confirmed_staging_required'})

# Only these public fields may cross the boundary. Nested containers have their
# own projection; extension keys (including nonce/path/diagnostics) never pass.
_VECTOR = object()
_PARAMETERS = dict(center=_VECTOR, size=_VECTOR, exhaustiveness=None, num_modes=None)
_SOURCE = dict.fromkeys(('pdb_id', 'source_sha256', 'revision_version',
    'input_ready', 'execution_authorized'))
_SOURCE.update(target=dict.fromkeys(('uniprot_id', 'taxonomy_id')),
    choice_counts=dict.fromkeys(('protein_selection', 'reference_residue', 'hetero_choices',
        'conformer_choices', 'residues', 'conformer_constraints', 'connections', 'limitations')))
_PREPARED = dict.fromkeys(('input_kind', 'source_sha256', 'derived_sha256', 'ligand_sha256',
    'selection_sha256', 'refinement_revision', 'pocket_rule', 'pocket_margin',
    'pocket_atom_count', 'input_ready', 'execution_authorized', 'receptor_ref', 'ligand_ref'))
_PREPARED.update(parameters_suggestion=_PARAMETERS, confirmed_parameters=_PARAMETERS,
    reference_residue=dict.fromkeys(('model', 'chain', 'resname', 'resseq', 'icode')),
    ligand_provenance=dict.fromkeys(('input_kind', 'rdkit_version', 'method', 'random_seed',
        'embedding_attempts', 'explicit_hydrogens', 'optimizer', 'max_iterations', 'optimizer_return_code')))
_CONSENT = dict.fromkeys(('schema', 'preparation_id', 'task_id', 'trace_id', 'binding_digest', 'expires_at_ms'))
_CONSENT.update(receptor=dict.fromkeys(('name', 'size', 'sha256')),
    ligand=dict.fromkeys(('name', 'size', 'sha256')),
    parameters=dict(_PARAMETERS, energy_range=None),
    tool_policy=dict.fromkeys(('label', 'digest', 'execution_backend', 'adapter_contract_version',
        'vina_limit_seconds', 'operation_limit_seconds')))


def _deny():
    raise ValueError('docking_view_unavailable')


def _integer(value, *, positive=False):
    return type(value) is int and int(positive) <= value <= _MAX_INTEGER


def _project(value, fields):
    if type(value) is not dict:
        _deny()
    result = {}
    for key, nested in fields.items():
        if key not in value:
            continue
        item = value[key]
        if nested is _VECTOR:
            if (type(item) is not list or len(item) != 3
                    or any(type(number) not in (int, float) or not math.isfinite(number)
                           for number in item)):
                _deny()
            item = list(item)
        elif nested is not None:
            item = _project(item, nested)
        elif type(item) not in (str, int, float, bool):
            _deny()
        result[key] = item
    return result


def _detach(value):
    # Encode incrementally with the same bound as C's physical sender. Neither
    # truncation nor a path-shaped substitute is a valid oversized preview.
    chunks, size = [], 0
    for chunk in json.JSONEncoder(ensure_ascii=False, allow_nan=False,
            separators=(',', ':')).iterencode(value):
        size += len(chunk.encode('utf-8'))
        if size > _MAX_BYTES:
            _deny()
        chunks.append(chunk)
    return json.loads(''.join(chunks))


async def capture_docking_view(coordinator, *, root_id, owner_session_id,
                               origin_turn_id, sequence, send_generation):
    """Refresh only this live root and return one bounded DockingView@1 value.

    No browser identity, candidate, score or preview can be supplied as data.
    Caller cancellation propagates; the coordinator retains physical I/O using
    its existing owners. A returned event is not evidence of socket delivery.
    """
    if (type(coordinator) is not DockingRequestCoordinator or not _label(root_id)
            or not _owner_label(owner_session_id) or not _label(origin_turn_id)
            or not _integer(sequence) or not _integer(send_generation, positive=True)):
        _deny()
    root = coordinator._owned(root_id, owner_session_id)
    if root is None or root.origin != origin_turn_id:
        _deny()
    identity = (root.task_id, root.preparation_id, root.accepted_revision)

    def unchanged(*, released_failure=False):
        current = coordinator._owned(root_id, owner_session_id)
        # The captured original may have finished its own close during status.
        # Only its non-success receipt may leave after that release, never a
        # replacement root, success, preview, or a later reconstructed read.
        released = (released_failure and current is None and root.closed
            and root.root_id not in coordinator._roots and root.close_state in {'confirmed', 'not_acquired'})
        if (current is not root and not released
                or root.origin != origin_turn_id
                or (root.task_id, root.preparation_id, root.accepted_revision) != identity
                or not all(_label(value) for value in identity[:2])
                or not _integer(identity[2])):
            _deny()

    try:
        unchanged()
        view = await coordinator.status(root_id=root_id, owner_session_id=owner_session_id)
        unchanged(released_failure=view['phase'] in {
            'FAILED', 'CANCELLED', 'CLOSED', 'TIMED_OUT', 'UNKNOWN', 'UNAVAILABLE'})
        if (view['root_id'] != root_id or view['task_id'] != identity[0]
                or view['preparation_id'] != identity[1]):
            _deny()
        phase, status = _PHASES[view['phase']]
        science = None
        if status == 'SUCCEEDED':
            science = await verified_docking_result(coordinator,
                root_id=root_id, owner_session_id=owner_session_id)
            unchanged()
            if root.phase != 'SUCCEEDED':
                _deny()
        drained = ((root.task is None or root.task.done()) and not _pending_preparation(root))
        terminal = phase == 'terminal'
        pending = view.get('cleanup_pending', False) is True
        execution = root.input_context._execution if root.input_context is not None else None
        pending = pending or execution is not None and not execution.settled
        publication = root.publication
        persistence = (publication is not None and (
                publication.reconciliation in {'pending', 'unconfirmed'}
                or publication.task is not None and not publication.task.done())
            or root.closed and root.draft_attempted and root.close_state not in {'confirmed', 'not_acquired'}
            or execution is not None and bool(execution.pending))
        preview = {}
        if not terminal and drained:
            for name, fields in (('source', _SOURCE), ('prepared', _PREPARED), ('consent', _CONSENT)):
                key = name + '_preview'
                if key in view:
                    preview[name] = _project(view[key], fields)
        missing = view['missing_fields'] if not terminal else []
        if (type(missing) is not list or len(missing) > len(_FIELDS)
                or any(type(value) is not str or value not in _FIELDS for value in missing)
                or len(set(missing)) != len(missing)):
            _deny()
        reason = (view['reason_code'] if status == 'AWAITING_INPUT'
            and view['reason_code'] in _WAIT_REASONS else 'status_' + status.lower())
        return _detach(dict(type='docking_event', schema='DockingView@1',
            request_id=root_id, root_trace_id=root_id, task_id=identity[0],
            preparation_id=identity[1], origin_turn_id=origin_turn_id,
            revision=identity[2], sequence=sequence, send_generation=send_generation,
            phase=phase, segment_complete=drained, terminal=terminal, view_status=status,
            task_status='unavailable', cleanup_status='pending' if pending or not drained else 'settled',
            reason_code=reason, persistence_pending=bool(persistence),
            missing_fields=missing, preview=preview or None, result=science))
    except Exception:
        raise ValueError('docking_view_unavailable') from None
