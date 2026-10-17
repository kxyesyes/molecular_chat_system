"""Read-only closed science data, not a wire event or execution authority.

Only an already-confirmed original live publication can be observed here.
The caller still owns transport deadlines, terminal delivery and history. This
adapter does not activate routes, publish a first result or renew any grant.
"""
from .docking_coordinator import DockingRequestCoordinator, _label, _owner_label
from .docking_publication import (
    DockingPublicationUnavailable, _Publication, _confirmed_observation, _local,
)


def _unavailable():
    raise DockingPublicationUnavailable()


async def verified_docking_result(coordinator, *, root_id, owner_session_id):
    """Return detached native JSON fields only after reverified live success.

    No candidate, outcome, score, download path or browser-supplied permission
    is accepted. Unavailable observations raise one fixed error without science.
    Cancellation rejects this read while the existing owner retains physical I/O.
    """
    if (type(coordinator) is not DockingRequestCoordinator or not _label(root_id)
            or not _owner_label(owner_session_id)):
        _unavailable()
    root = coordinator._owned(root_id, owner_session_id)
    if root is None:
        _unavailable()
    state = root.publication
    if (type(state) is not _Publication or state._origin[0] is not root
            or state._origin[1] is not root.input_context
            or state._origin[2] is not coordinator or state._origin[4] is not state
            or state.context is not root.input_context
            or state.outcome is None or state.outcome.status != 'SUCCEEDED'
            or state.task is None or not state.task.done()):
        _unavailable()
    try:
        # Original object identity and grant checks precede even observation I/O.
        try:
            _local(state.context, _grant=state.grant)
        except Exception:
            state.access_disabled = True
            raise
        outcome = await _confirmed_observation(state, preserve_terminal_on_cancel=False)
        # The observer has released its model reader. Acquire a new, non-nested
        # reader for the final live-model check and synchronous DTO construction.
        async with coordinator._gate.request():
            try:
                if (coordinator._owned(root_id, owner_session_id) is not root
                        or root.publication is not state or state.outcome is not outcome):
                    _unavailable()
                _local(state.context, capture_model=True, _grant=state.grant)
            except Exception:
                state.access_disabled = True
                raise
            science = outcome.projection
            # Field-by-field projection: no dataclass internals, capability tokens,
            # absolute paths, stdout/stderr, rounding or model-generated language.
            result = {name: getattr(science, name) for name in (
                'tool_name', 'tool_version', 'execution_backend', 'input_hash', 'config_hash',
                'tool_policy_digest', 'source_digest', 'original_receptor_digest',
                'ligand_identity_digest', 'projection_digest', 'score_kind', 'pose_count')}
            result.update(best_energy_kcal_mol=science.energy_kcal_mol, energy_unit='kcal/mol',
                pose=dict(artifact_id='pose', size_bytes=science.pose_size, sha256=science.pose_sha256,
                    download_path=f'/api/agent/docking/tasks/{root.task_id}/artifacts/pose'))
            return result
    except Exception:
        # _confirmed_observation owns sticky invalidation versus transient SQL
        # contention; mapping the public error must not rewrite that decision.
        raise DockingPublicationUnavailable() from None
