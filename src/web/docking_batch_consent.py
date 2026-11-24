"""Bind a single affirmative decision to the ordered, prepared ligand set."""
import asyncio
import hashlib
import hmac
import secrets
from contextlib import contextmanager

from src.system.model_lifecycle import finish_on_cancel
from src.task_runtime.docking_consent import canonical_json, validate_approval
from .docking_input_context import capture_chat_input_context


def batch_binding(root, context):
    rows = [{'ordinal': row['ordinal'], 'smiles': row['smiles']} for row in root.batch_ligands]
    if len(rows) < 2 or rows != root.batch['ligands']:
        raise ValueError('Batch inputs changed')
    preview = dict(task_id=root.task_id, pdb_id=root.batch['pdb_id'], ligands=rows,
        receptor_sha256=hashlib.sha256(context._receptor).hexdigest(),
        reference_residue=root.projected['pocket']['residue'], parameters=context._parameters)
    binding = dict(preview=preview, owner=root.owner, revision=root.accepted_revision,
        input_context=context._digest.hex(),
        sdf_sha256=[hashlib.sha256(row['sdf_bytes']).hexdigest() for row in root.batch_ligands])
    return hashlib.sha256(canonical_json(binding)).hexdigest(), preview


async def _confirmed_inputs(coordinator, root):
    if root.closed or not root.batch or root.reason != 'inputs_confirmed_staging_required':
        raise ValueError('Batch confirmation unavailable')
    context = await capture_chat_input_context(coordinator, root_id=root.root_id, owner_session_id=root.owner)
    await coordinator._check_source(root)
    context._local_current()
    digest, preview = batch_binding(root, context)
    return context, digest, preview


async def batch_preview(coordinator, root, revision):
    if revision != root.accepted_revision:
        raise ValueError('Batch revision changed')
    _, digest, preview = await _confirmed_inputs(coordinator, root)
    if root.batch_submitting or revision != root.accepted_revision:
        raise ValueError('Batch confirmation unavailable')
    if root.batch_proof is None or root.batch_proof[0] != digest:
        root.batch_proof = (digest, secrets.token_hex(32))
    return dict(preview, binding_digest=digest, approval_nonce=root.batch_proof[1])


async def batch_approve(coordinator, root, execution, proof, *, check_request):
    validate_approval(proof['approval_nonce'], proof['binding_digest'], proof['confirm'])
    if (root.batch_proof is None
            or not hmac.compare_digest(root.batch_proof[0], proof['binding_digest'])
            or not hmac.compare_digest(root.batch_proof[1], proof['approval_nonce'])):
        raise ValueError('Batch approval is no longer current')
    if root.batch_submitting:
        await root.batch_submitting.wait()
        return await execution.status(root.task_id, root.owner)
    context, digest, preview = await _confirmed_inputs(coordinator, root)
    if not hmac.compare_digest(digest, proof['binding_digest']):
        raise ValueError('Batch inputs changed')
    # No suspension between proof validation, checking the live request and
    # claiming it. Durable admission checks the evidence again under its lock.
    check_request()
    if root.batch_submitting:
        await root.batch_submitting.wait()
        return await execution.status(root.task_id, root.owner)
    submitted = root.batch_submitting = asyncio.Event()

    @contextmanager
    def admission_guard():
        with context._source_guard():
            # The task writer invokes this while holding its BEGIN IMMEDIATE
            # lock. The approved draft and source cannot change before insert.
            yield lambda: coordinator._read_waiting_draft(root=root, expected_version=root.draft['version'])

    try:
        async with coordinator._gate.request():
            return await finish_on_cancel(execution.submit(task_id=root.task_id, owner_session_id=root.owner,
                pdb_id=preview['pdb_id'], receptor_bytes=context._receptor, parameters=context._parameters,
                ligands=[dict(ordinal=r['ordinal'], smiles=r['smiles'], sdf_bytes=r['sdf_bytes'])
                         for r in root.batch_ligands], admission_guard=admission_guard, binding_digest=digest))
    finally:
        try:
            await finish_on_cancel(coordinator._close_owned_root(root))
        finally:
            submitted.set()
