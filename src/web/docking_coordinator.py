"""Bounded live C preparation coordinator, shared by legacy/decision entry points.

Disabled by default. This is NOT an HTTP/approval issuer or scheduler. The
trusted caller supplies a captured live S03 context and an existing owned
running AgentState run. Never recreate that run or restore a claimed root.

Two independent deadlines: upstream work gets 180 seconds from before the first
await, while TaskStore keeps its unchanged 900-second consent waiting expiry.
Neither refresh nor human waiting renews either deadline. Fixed source and
inspection children use that original deadline too. Confirmed producer bytes can
enter C1 through the private one-use context; this module never approves Vina.

One event-loop-owned instance must serve both chat paths. Live roots PLUS failed
unconfirmed attempts occupy at most MAX_PENDING_CONSENTS entries. Failed local
attempts are permanent tombstones for this instance: saturation rejects new
roots until instance exit (an intentional availability limit). A returned root
claim receipt permits dropping closed heavy context AFTER actual workers drain.
An existing durable claim rejects replay, but never recovers an uncertain receipt.
Pre-commit failures do not invent persisted consumed claims.

capture_current is a trusted thread-safe synchronous callback with the exact
model/policy/model_generation/capability_generation shape used by the proposal
owner. Every model capture occurs inside a model reader, including callbacks in
SQL threads. No reader is held while waiting for human input; propose acquires
its own reader, outside the coordinator's short capture/reservation reader.
"""
from __future__ import annotations

import asyncio
from contextlib import closing
from dataclasses import asdict, dataclass, field
import hashlib
import json
import math
import re
import time
from uuid import uuid4

from src.agent.persistence.redaction import contains_sensitive_text
from src.system.model_lifecycle import finish_on_cancel
from src.task_runtime.docking_consent import (
    DRAFT_SLOT_NAMES, MAX_PENDING_CONSENTS, DockingConsentPolicy,
    validate_clocks, validate_draft_identity, validate_chat_draft, validate_identity,
    validate_draft_patch, materialize_draft_identity,
)
from src.web.docking_proposal import (
    DockingProposalOwner, TextSegmentIssuer, capture_source_snapshot, capture_text_segment,
)
from src.web.docking_request import assess_docking_request_text, validate_docking_text_proposal
from src.web.docking_request import OwnedDockingStructureEvidence, _STRUCTURE_POINTER
from src.target_search.remote_clients import _resolve_organism
from src.docking import source_preparation, chat_preparation


# Intersection of proposal-issuer labels and durable draft identity labels.
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_WORK_MS = 180000
_CLAIM_KEY = "chat_docking_root"
_PAGE_BYTES = 48 * 1024
_SECTIONS = frozenset({"protein_selection", "reference_residue", "hetero_choices",
    "conformer_choices", "residues", "conformer_constraints", "connections", "limitations"})


def _wall_time_ms():
    return time.time_ns() // 1000000


def _label(value):
    return type(value) is str and _LABEL.fullmatch(value) is not None and not contains_sensitive_text(value)


def _small_json(value, limit=_PAGE_BYTES):
    """Bound output while encoding, before joining/copying a whole inspection."""
    parts, size = [], 0
    for part in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                allow_nan=False).iterencode(value):
        size += len(part.encode("utf-8"))
        if size > limit:
            raise ValueError("source_preview_too_large")
        parts.append(part)
    return "".join(parts)


def _capture_structure_context(value):
    # This is a bounded server capture, not authentication. The actual current
    # owner/checkpoint/evidence ledger must still resolve on every use.
    if value is None:
        return None
    if type(value) is not dict or len(value) != len(_STRUCTURE_POINTER):
        raise ValueError("invalid_structure_context")
    detached = {}
    for key, item in value.items():
        if type(key) is not str or key not in _STRUCTURE_POINTER:
            raise ValueError("invalid_structure_context")
        if key == "schema_version":
            if type(item) is not int or item != 1:
                raise ValueError("invalid_structure_context")
        elif type(item) is not str or not 0 < len(item) <= 128:
            raise ValueError("invalid_structure_context")
        detached[key] = item
    if set(detached) != _STRUCTURE_POINTER:
        raise ValueError("invalid_structure_context")
    return _small_json(detached, 4096)


def _pending_preparation(root):
    try:
        return any(owner.snapshot()["settled"] is not True for owner in root.preparation_owners)
    except Exception:
        return True


def _content_ref(value, limit=64 * 1024 * 1024):
    """Hash bounded private native facts without constructing a full JSON copy."""
    digest, size = hashlib.sha256(), 0
    for chunk in json.JSONEncoder(ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                                  allow_nan=False).iterencode(value):
        raw = chunk.encode("utf-8")
        size += len(raw)
        if size > limit:
            raise ValueError("descriptor_too_large")
        digest.update(raw)
    return "sha256:" + digest.hexdigest()


def _capture_selection(value):
    from src.docking.receptor_projection_pdb import AllocationBudget
    from src.docking.receptor_projection_source import check_projection_resources
    if type(value) is not dict:
        raise ValueError("invalid_selection")
    _, captured = check_projection_resources(value, budget=AllocationBudget(64 * 1024 * 1024),
                                             kind="selection", _capture=True)
    if set(captured) != {"protein_selection", "reference_residue", "hetero_choices",
                         "conformer_choices", "reviewed_limitations"}:
        raise ValueError("invalid_selection")
    # These fields are sets of explicit choices. Their input ordering must not
    # turn an identical choice into a request to replace fixed inputs. P3 still
    # validates every identity/value and normalizes to original source order.
    for name in ("hetero_choices", "conformer_choices", "reviewed_limitations"):
        if type(captured[name]) is not list:
            raise ValueError("invalid_selection")
        captured[name].sort(key=lambda row: _small_json(row, 8 * 1024 * 1024))
    protein = captured["protein_selection"]
    if type(protein) is not dict or type(protein.get("chains")) is not list:
        raise ValueError("invalid_selection")
    protein["chains"].sort(key=lambda row: _small_json(row, 8 * 1024 * 1024))
    return captured, _content_ref(captured, 8 * 1024 * 1024)


def _capture_parameters(value):
    names = {"center", "size", "exhaustiveness", "num_modes"}
    if type(value) is not dict or len(value) != len(names):
        raise ValueError("parameters_invalid")
    captured = {}
    for key, item in value.items():
        if type(key) is not str or key not in names:
            raise ValueError("parameters_invalid")
        if key in {"center", "size"}:
            if type(item) is not list or len(item) != 3:
                raise ValueError("parameters_invalid")
            item = list(item)
        captured[key] = item
    captured = validate_draft_patch(captured)
    # Numerical equivalence is a duplicate, not a changed input (e.g. 10/10.0).
    for key in ("center", "size"):
        captured[key] = [int(item) if type(item) is float and item.is_integer() else item
                         for item in captured[key]]
    return captured


class _Unavailable(Exception):
    pass


class _AlreadyClaimed(Exception):
    pass


@dataclass(frozen=True)
class _ClaimReceipt:
    root_id: str
    preparation_id: str
    binding_json: str


@dataclass(repr=False)
class _Root:
    root_id: str
    owner: str
    origin: str
    preparation_id: str
    task_id: str
    snapshot: object
    mode: str
    wall_deadline: int
    mono_deadline: float
    missing: tuple
    current: dict | None = None
    issuer: object = None
    proposal: object = None
    draft: dict | None = None
    claim: _ClaimReceipt | None = None
    task: object = None
    durable: bool = False
    closed: bool = False
    cancel_requested: bool = False
    phase: str = "BUSY"
    reason: str = "root_busy"
    structure_context_json: str | None = None
    physical_deadline: float | None = None
    source_reference: dict | None = None
    source_result: dict | None = None
    inspection: dict | None = None
    source_preview_json: str | None = None
    preparation_owners: list = field(default_factory=list)
    accepted_revision: int = 0
    accepted_slots: dict = field(default_factory=lambda: {key: {"state": "pending"} for key in DRAFT_SLOT_NAMES})
    selection_key: str | None = None
    selection: dict | None = None
    projected: dict | None = None
    ligand: dict | None = None
    receptor_descriptor: dict | None = None
    ligand_descriptor: dict | None = None
    prepared_preview_json: str | None = None
    confirmation_key: str | None = None
    confirmation_revision: int | None = None
    draft_attempted: bool = False
    close_task: object = None
    close_state: str = "not_started"
    input_context: object = None
    staging_started: bool = False
    ready_preview_json: str | None = None


def _view(root=None, *, reason="root_unavailable", phase="UNAVAILABLE"):
    view = dict(root_id=root.root_id if root else None,
        task_id=root.task_id if root else None,
        preparation_id=root.preparation_id if root else None,
        phase=root.phase if root else phase, reason_code=root.reason if root else reason,
        missing_fields=list(root.missing) if root else [],
        input_ready=False, execution_authorized=False)
    if root is not None:
        if (_pending_preparation(root)
                or (root.closed and root.task is not None and not root.task.done())
                or (root.closed and root.draft_attempted and root.close_state != "confirmed")):
            view["cleanup_pending"] = True
        if (not root.closed and root.source_preview_json is not None
                and (root.task is None or root.task.done())):
            view["source_preview"] = json.loads(root.source_preview_json)
            if root.prepared_preview_json is not None:
                view["prepared_preview"] = json.loads(root.prepared_preview_json)
        if not root.closed and root.phase == "READY" and root.ready_preview_json is not None:
            if root.task is not None and not root.task.done():
                view.update(phase="BUSY", reason_code="context_validation_pending")
            else:
                view["input_ready"] = True
                view["consent_preview"] = json.loads(root.ready_preview_json)
    return view


async def _drain(task):
    """Repeated caller cancellation cannot detach the retained physical work."""
    interrupted = False
    while not task.done():
        try:
            await asyncio.wait({task})
        except asyncio.CancelledError:
            interrupted = True
    if not task.cancelled():
        task.exception()
    return interrupted


class _BoundDraftStore:
    """Pin the existing proposal owner's SQL receipts/reads to this live root.

    These synchronous calls run inside that owner's retained, shielded SQL work;
    no new transport, transaction, retry or worker ownership is introduced.
    """
    def __init__(self, store, validate, reserve_validate):
        self._store, self._validate = store, validate
        self._reserve_validate = reserve_validate
        self.db_path = store.db_path

    def get_chat_docking_draft(self, **kwargs):
        return self._validate(self._store.get_chat_docking_draft(**kwargs))

    def reserve_docking_draft_budget(self, **kwargs):
        return self._reserve_validate(self._store.reserve_docking_draft_budget(**kwargs),
                                      call_slice_ms=kwargs["call_slice_ms"])


class DockingRequestCoordinator:
    def __init__(self, *, task_store, state_store, gate, capture_current,
                 wall_time_ms=None, monotonic=None, enabled=False):
        self._tasks, self._state, self._gate = task_store, state_store, gate
        self._capture_current = capture_current
        self._wall = _wall_time_ms if wall_time_ms is None else wall_time_ms
        self._mono = time.monotonic if monotonic is None else monotonic
        self._enabled = enabled
        self._roots = {}

    def _remaining_ms(self, root):
        wall, mono = self._wall(), self._mono()
        validate_clocks(wall, mono)
        if root.closed or self._enabled is not True:
            raise _Unavailable("root_closed")
        ready_clock = root.ready_preview_json is not None
        if ready_clock:
            record = root.input_context._ready_record
            if record is None:
                raise _Unavailable("consent_unconfirmed")
            deadlines = [record["expires_at_ms"] - wall,
                         (record["monotonic_expires"] - mono) * 1000]
        else:
            deadlines = [root.wall_deadline - wall, (root.mono_deadline - mono) * 1000]
        if root.draft is not None and not ready_clock:
            deadlines += [root.draft["expires_at_ms"] - wall,
                          (root.draft["monotonic_expires"] - mono) * 1000]
        if root.physical_deadline is not None and not ready_clock:
            deadlines.append((root.physical_deadline - time.monotonic()) * 1000)
        left = min(deadlines)
        if not math.isfinite(left) or left < 1:
            raise _Unavailable("work_deadline_exceeded")
        return int(left)

    def _capture(self, root):
        self._remaining_ms(root)
        live = self._capture_current()
        if (type(live) is not dict or set(live) != {
                "model", "policy", "model_generation", "capability_generation"}
                or type(live["policy"]) is not DockingConsentPolicy
                or any(not _label(live[k]) for k in ("model_generation", "capability_generation"))
                or any(not _label(v) for v in (live["policy"].runtime_generation,
                    live["policy"].policy_generation, live["policy"].adapter_contract_version))):
            raise _Unavailable("context_unavailable")
        current = root.current
        if current is not None and (live["model"] is not current["model"]
                or any(live[k] != current[k] for k in ("policy", "model_generation", "capability_generation"))):
            raise _Unavailable("generation_mismatch")
        return dict(live)

    def _context(self, root):
        return {k: v for k, v in self._capture(root).items() if k != "model"}

    async def _sql(self, operation, **kwargs):
        return await finish_on_cancel(asyncio.to_thread(operation, **kwargs))

    def _claim_root(self, *, root):
        """Owner-bound BEGIN IMMEDIATE; ONLY a successful commit yields authority."""
        with self._state._lock, closing(self._state._connect()) as conn, conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT metadata_json FROM agent_runs WHERE trace_id=? AND session_id=? AND status='running'",
                (root.root_id, root.owner)).fetchone()
            if row is None:
                raise _Unavailable("root_unavailable")
            metadata = json.loads(row["metadata_json"])
            if type(metadata) is not dict:
                raise _Unavailable("root_unavailable")
            if _CLAIM_KEY in metadata:
                raise _AlreadyClaimed
            self._capture(root)  # Includes queue time and the exact accepted model.
            metadata[_CLAIM_KEY] = dict(schema="ChatDockingRoot@1", identity=self._identity(root),
                source_snapshot_digest=root.snapshot.source_snapshot_digest,
                model_generation=root.current["model_generation"],
                capability_generation=root.current["capability_generation"],
                policy=asdict(root.current["policy"]),
                work_deadline_wall_ms=root.wall_deadline, work_deadline_mono=root.mono_deadline)
            if root.structure_context_json is not None:
                metadata[_CLAIM_KEY]["structure_context_digest"] = hashlib.sha256(
                    root.structure_context_json.encode("utf-8")).hexdigest()
            changed = conn.execute(
                "UPDATE agent_runs SET metadata_json=?,updated_at=? WHERE trace_id=? AND session_id=? AND status='running'",
                (json.dumps(metadata, ensure_ascii=True, allow_nan=False), time.time(), root.root_id, root.owner),
            ).rowcount
            if changed != 1:
                raise _Unavailable("root_unavailable")
        return _ClaimReceipt(root.root_id, root.preparation_id,
                             json.dumps(metadata[_CLAIM_KEY], sort_keys=True, allow_nan=False))

    def _check_root(self, *, root):
        """Revalidate an existing receipt, never reconstruct one from readback."""
        if type(root.claim) is not _ClaimReceipt:
            raise _Unavailable("claim_unconfirmed")
        with closing(self._state._connect()) as conn:
            row = conn.execute(
                "SELECT metadata_json FROM agent_runs WHERE trace_id=? AND session_id=? AND status='running'",
                (root.root_id, root.owner)).fetchone()
            try:
                matches = row is not None and json.dumps(
                    json.loads(row["metadata_json"])[_CLAIM_KEY], sort_keys=True,
                    allow_nan=False) == root.claim.binding_json
            except (ValueError, TypeError, KeyError):
                matches = False
            if not matches:
                # A replaced/invalidated run cannot make this local attempt reusable.
                root.durable = False
                raise _Unavailable("root_unavailable")

    @staticmethod
    def _identity(root):
        return dict(preparation_id=root.preparation_id, task_id=root.task_id,
            owner_session_id=root.owner, trace_id=root.root_id, origin_turn_id=root.origin,
            query_digest=root.snapshot.query_digest, refinement_revision=0,
            admission_revision="docking-initial-v1")

    def _validate_draft_record(self, root, record, *, expected_version=None,
                               accepted_revision=None, accepted_slots=None):
        """A receipt/read validates an existing root; it never creates authority.

        Compare the immutable claim plus only locally confirmed refinement
        receipts, never adopting a revision or fixed slot from later readback.
        """
        self._remaining_ms(root)
        if type(root.claim) is not _ClaimReceipt or type(record) is not dict:
            raise _Unavailable("draft_unavailable")
        original = json.loads(root.claim.binding_json)["identity"]
        revision = root.accepted_revision if accepted_revision is None else accepted_revision
        slots = root.accepted_slots if accepted_slots is None else accepted_slots
        draft = validate_chat_draft(record["draft"])
        expected = materialize_draft_identity(dict(original, refinement_revision=revision),
                                             dict(draft, slots=slots))
        identity = (validate_identity(record["identity"]) if "source_refs" in expected
                    else validate_draft_identity(record["identity"]))
        if (identity != expected or original != self._identity(root) or draft["slots"] != slots
                or record["state"] != "AWAITING_INPUT" or record["cleanup_state"] != "settled"
                or type(record["version"]) is not int or record["version"] < 1
                or (expected_version is not None and record["version"] != expected_version)
                or draft["source_snapshot_digest"] != root.snapshot.source_snapshot_digest
                or any(draft[k] != root.current[k] for k in ("model_generation", "capability_generation"))):
            raise _Unavailable("draft_binding_mismatch")
        wall, mono = self._wall(), self._mono()
        validate_clocks(wall, mono)
        validate_clocks(record["expires_at_ms"], record["monotonic_expires"])
        if (wall >= record["expires_at_ms"] or mono >= record["monotonic_expires"]
                or (root.draft is not None and any(record[k] != root.draft[k]
                    for k in ("expires_at_ms", "monotonic_expires")))):
            raise _Unavailable("draft_unavailable")
        return {**record, "identity": identity, "draft": draft}

    def _read_draft(self, *, root, expected_version):
        if root.draft is None:
            raise _Unavailable("draft_unconfirmed")  # Never recover a missing receipt by reading.
        record = self._tasks.get_chat_docking_draft(
            preparation_id=root.preparation_id, owner_session_id=root.owner)
        record = self._validate_draft_record(root, record, expected_version=expected_version)
        if (expected_version == root.draft["version"]
                and record["draft"]["budget"] != root.draft["draft"]["budget"]):
            raise _Unavailable("draft_binding_mismatch")
        return record

    def _read_waiting_draft(self, *, root, expected_version):
        # The draft read may block after an earlier root check. Revalidate the
        # committed claim only once that read settles, in the same retained SQL
        # worker, before either publication or refresh can expose a waiting view.
        record = self._read_draft(root=root, expected_version=expected_version)
        self._check_root(root=root)
        return record

    def _accept_proposal_budget(self, root, record, *, call_slice_ms):
        # The actual CAS acknowledgement arrives before model dispatch. Preserve
        # it for exact close even if the later model result is cancelled/lost;
        # ordinary readback cannot enter this receipt-only path.
        previous = root.draft
        confirmed = self._validate_draft_record(root, record, expected_version=previous["version"] + 1)
        expected = dict(previous["draft"]["budget"])
        expected["spent_ms"] += call_slice_ms
        expected["proposal_slots_spent"] += 1
        if confirmed["draft"]["budget"] != expected:
            raise _Unavailable("draft_binding_mismatch")
        root.draft = confirmed
        return confirmed

    def _stop(self, root, reason, phase="UNAVAILABLE"):
        if not root.closed:
            root.closed, root.reason, root.phase = True, reason, phase
            if root.issuer is not None:
                root.issuer.close()

    def _interrupt(self, root, reason, phase="UNAVAILABLE"):
        self._stop(root, reason, phase)
        if root.task is not None and not root.task.done() and not root.cancel_requested:
            root.cancel_requested = True
            root.task.cancel()

    def _release_closed(self, root):
        if (not root.closed or (root.task is not None and not root.task.done())
                or _pending_preparation(root)
                or root.close_state not in {"confirmed", "not_acquired"}
                or (root.close_task is not None and not root.close_task.done())):
            return
        root.snapshot = root.current = root.issuer = root.proposal = root.draft = root.claim = root.task = None
        root.structure_context_json = root.source_reference = root.source_result = None
        root.inspection = root.source_preview_json = None
        root.selection = root.projected = root.ligand = None
        root.receptor_descriptor = root.ligand_descriptor = root.prepared_preview_json = None
        root.ready_preview_json = root.input_context = None
        root.preparation_owners.clear()
        root.close_task = None
        if root.durable and self._roots.get(root.root_id) is root:
            del self._roots[root.root_id]
        # No confirmed durable claim: retain only a bounded non-retryable tombstone.

    async def _finish_closed(self, root):
        """Join one exact waiting-draft close, never infer a lost SQL receipt.

        A still-owned physical child prevents releasing the durable owner slot.
        Later status/close can observe its settlement and start this finalizer;
        neither a deadline nor a finished wrapper proves physical cleanup.
        """
        if (not root.closed or (root.task is not None and not root.task.done())
                or _pending_preparation(root)):
            return
        if root.close_state == "not_started":
            if not root.draft_attempted:
                root.close_state = "not_acquired"
            elif root.draft is None:
                root.close_state = "unconfirmed"
            else:
                root.close_state = "pending"
                closer = (self._close_staged_inputs if root.input_context is not None
                          and root.input_context._operation is not None else self._close_waiting_draft)
                root.close_task = asyncio.create_task(closer(root))
                self._gate.retain_task(root.close_task)
        try:
            if root.close_task is not None and await _drain(root.close_task):
                raise asyncio.CancelledError
        finally:
            self._release_closed(root)

    async def _close_waiting_draft(self, root):
        try:
            receipt = await self._sql(self._tasks.close_chat_docking_draft,
                preparation_id=root.preparation_id, owner_session_id=root.owner,
                expected_revision=root.accepted_revision, expected_version=root.draft["version"],
                task_id=root.task_id, trace_id=root.root_id, origin_turn_id=root.origin)
            root.close_state = "confirmed" if receipt is True else "unconfirmed"
        except (Exception, asyncio.CancelledError):
            # No retry/readback: the accepted version may be older than an
            # uncertain refinement commit, or this close's acknowledgement lost.
            root.close_state = "unconfirmed"

    async def _close_staged_inputs(self, root):
        context = root.input_context
        operation, runtime = context._operation, context._runtime
        try:
            # READY is still owned by the exact C1 writer even after its worker
            # left the runtime's pending set. Reattach before initiating cleanup.
            runtime._consent_preparations.add(operation)
            await runtime._abort_consent_preparation(operation)
            await _drain(operation.settlement)
            if operation.revocation_status == "confirmed" and operation.settled:
                root.close_state = "confirmed"
            elif not context._begun and operation.settled:
                await self._close_waiting_draft(root)
            else:
                root.close_state = "unconfirmed"
        except (Exception, asyncio.CancelledError):
            root.close_state = "unconfirmed"

    async def begin(self, *, root_id, owner_session_id, origin_turn_id, query,
                    history, descriptors, enable_tools, mode, structure_context=None):
        # Entire input capture/preflight, clocks and the local attempt burn precede
        # the first await; duplicate coroutines cannot race to create an issuer.
        try:
            physical_begin = time.monotonic()
            wall, mono = self._wall(), self._mono()
            validate_clocks(wall, mono)
            if not math.isfinite(mono + 180) or mono + 180 <= mono:
                raise ValueError
            if (any(not _label(v) for v in (root_id, owner_session_id, origin_turn_id))
                    or type(mode) is not str or mode not in {"native", "json"}):
                return _view(reason="invalid_input")
            assessment = assess_docking_request_text(query, enabled=self._enabled, enable_tools=enable_tools)
            if not assessment["assessed"]:
                return _view(reason="clarification_required", phase="AWAITING_INPUT")
            root = self._roots.get(root_id)
            if root is not None:
                # A duplicate cannot replace or reconstruct the original inputs.
                if root.owner == owner_session_id and root.source_preview_json is not None:
                    return await self.status(root_id=root_id, owner_session_id=owner_session_id)
                return _view(root) if root.owner == owner_session_id else _view()
            if len(self._roots) >= MAX_PENDING_CONSENTS:
                return _view(reason="coordinator_capacity_full")
            snapshot = capture_source_snapshot(query=query, history=history, descriptors=descriptors)
            structure_context_json = _capture_structure_context(structure_context)
        except Exception:
            return _view(reason="invalid_input")
        root = _Root(root_id, owner_session_id, origin_turn_id, str(uuid4()), str(uuid4()),
                     snapshot, mode, wall + _WORK_MS, mono + _WORK_MS / 1000,
                     tuple(assessment["missing_fields"]), structure_context_json=structure_context_json,
                     physical_deadline=physical_begin + _WORK_MS / 1000)
        self._roots[root_id] = root
        root.task = asyncio.create_task(self._run(root))
        self._gate.retain_task(root.task)
        return await self._wait_root(root)

    async def _wait_root(self, root):
        task = root.task
        try:
            done, _ = await asyncio.wait({task}, timeout=self._remaining_ms(root) / 1000)
            if not done:
                self._interrupt(root, "work_deadline_exceeded")
                if await _drain(task):
                    raise asyncio.CancelledError
            elif not task.cancelled():
                task.result()
        except asyncio.CancelledError:
            self._interrupt(root, "cancelled")
            await _drain(task)
            raise
        except Exception:
            self._interrupt(root, "operation_unavailable")
            if await _drain(task):
                raise asyncio.CancelledError
        finally:
            await self._finish_closed(root)
        return _view(root)

    async def _run(self, root):
        try:
            async with self._gate.request():
                root.current = self._capture(root)
                receipt = await self._sql(self._claim_root, root=root)
                if (type(receipt) is not _ClaimReceipt or receipt.root_id != root.root_id
                        or receipt.preparation_id != root.preparation_id):
                    raise _Unavailable("claim_unconfirmed")
                root.durable = True
                root.claim = receipt
                self._capture(root)
                await self._sql(self._check_root, root=root)
                self._capture(root)
                root.issuer = TextSegmentIssuer(root_id=root.root_id, owner_session_id=root.owner,
                    preparation_id=root.preparation_id, origin_turn_id=root.origin,
                    original_snapshot=root.snapshot, runtime_generation=root.current["policy"].runtime_generation)
                segment = capture_text_segment(issuer=root.issuer, original_snapshot=root.snapshot,
                    text_segment_index=0, server_turn_id=root.origin,
                    current_text=root.snapshot.query, preceding_refinements=[])
                root.draft_attempted = True  # Before dispatch, including uncertain receipts.
                draft_receipt = await self._sql(self._tasks.reserve_chat_docking_draft,
                    identity=self._identity(root), draft=dict(schema="DockingDraft@1",
                        model_generation=root.current["model_generation"],
                        capability_generation=root.current["capability_generation"],
                        source_snapshot_digest=root.snapshot.source_snapshot_digest,
                        slots={key: {"state": "pending"} for key in DRAFT_SLOT_NAMES},
                        budget=dict(limit_ms=_WORK_MS, spent_ms=0, proposal_slots_spent=0)),
                    policy=root.current["policy"], wall_time_ms=self._wall, monotonic=self._mono,
                    current_context=lambda: self._context(root))
                root.draft = self._validate_draft_record(root, draft_receipt, expected_version=1)
                await self._sql(self._read_draft, root=root, expected_version=root.draft["version"])
                self._capture(root)
            # Do NOT nest readers: a waiting model writer must be able to enter.
            bound_store = _BoundDraftStore(self._tasks,
                lambda record: self._validate_draft_record(root, record),
                lambda record, **kw: self._accept_proposal_budget(root, record, **kw))
            owner = DockingProposalOwner(task_store=bound_store, state_store=self._state,
                gate=self._gate, capture_current=lambda: self._capture(root),
                wall_time_ms=self._wall, monotonic=self._mono)
            budget = root.draft["draft"]["budget"]
            call_slice_ms = min(30000, self._remaining_ms(root), budget["limit_ms"] - budget["spent_ms"])
            if call_slice_ms <= 0:
                raise _Unavailable("budget_exhausted")
            result = await owner.propose(root_id=root.root_id, preparation_id=root.preparation_id,
                owner_session_id=root.owner, expected_revision=0, expected_version=root.draft["version"],
                snapshot=root.snapshot, policy=root.current["policy"],
                model_generation=root.current["model_generation"],
                capability_generation=root.current["capability_generation"], mode=root.mode,
                segment=segment, call_slice_ms=call_slice_ms)
            async with self._gate.request():
                await self._sql(self._check_root, root=root)
                self._capture(root)
                if not result.available:
                    raise _Unavailable("proposal_unavailable")
                current_draft = await self._sql(self._read_waiting_draft, root=root,
                                               expected_version=result.reserved_version)
                self._capture(root)  # The final draft SQL is inside the original work deadline too.
                root.draft = current_draft
                checked = validate_docking_text_proposal(root.snapshot.query, result.proposal,
                                                        enabled=self._enabled, enable_tools=True)
                if checked["consistent"]:
                    root.proposal = result.proposal  # Private, not factual input/source authority.
                root.phase = "AWAITING_INPUT"
                root.reason = ("source_preparation_required" if checked["consistent"]
                    and not checked["model_unresolved"] else "clarification_required")
            # Missing scientific inputs are expected here. A consistent explicit
            # source request may inspect facts without silently resolving them.
            if (checked["consistent"] and root.structure_context_json is not None
                    and checked["assessment"]["inputs"].get("pdb_id")):
                await self._prepare_source(root)
        except _AlreadyClaimed:
            # Presence proves replay must reject, NOT a usable claim receipt.
            root.durable = True
            self._stop(root, "root_already_claimed")
        except asyncio.CancelledError:
            self._stop(root, "cancelled")
            raise
        except _Unavailable as exc:
            self._stop(root, str(exc))  # Only locally defined fixed reasons.
        except Exception:
            self._stop(root, "operation_unavailable")

    def _owned(self, root_id, owner_session_id):
        if not _label(root_id) or not _label(owner_session_id):
            return None
        root = self._roots.get(root_id)
        return root if root is not None and root.owner == owner_session_id else None

    async def status(self, *, root_id, owner_session_id):
        root = self._owned(root_id, owner_session_id)
        if root is None:
            return _view()
        if root.closed:
            await self._finish_closed(root)
            return _view(root)
        if root.task is not None and not root.task.done():
            return _view(root)
        root.task = asyncio.create_task(self._refresh(root))
        self._gate.retain_task(root.task)
        return await self._wait_root(root)

    async def _refresh(self, root):
        # Status SQL has the same physical ownership as initial work. Concurrent
        # refreshes coalesce; close/cancellation cannot drop its heavy context.
        try:
            async with self._gate.request():
                if root.phase == "READY":
                    await self._sql(root.input_context._check_ready_guarded)
                    root.input_context._ready_current()
                    self._capture(root)
                    return
                await self._sql(self._check_root, root=root)
                current_draft = await self._sql(self._read_waiting_draft, root=root,
                                               expected_version=root.draft["version"])
                self._capture(root)
                root.draft = current_draft
                if root.source_reference is not None:
                    await self._sql(self._read_source_reference, root=root)
                    self._capture(root)
        except asyncio.CancelledError:
            self._stop(root, "cancelled")
            raise
        except Exception:
            self._stop(root, "context_unavailable")

    def _read_source_reference(self, *, root):
        # A sequential pair of reads cannot close both invalidation races: a
        # draft can change during source resolution, or vice versa. This short,
        # read-only transaction excludes writers in BOTH existing databases.
        # Agent DB is main, TaskStore is attached: one consistent lock order,
        # no writes, no cross-database publication/commit protocol, no worker or
        # human wait under these locks. An uncertain/contended read fails closed.
        self._capture(root)
        with self._state._lock, closing(self._state._connect()) as guard, guard:
            guard.execute("ATTACH DATABASE ? AS docking_draft_read", (str(self._tasks.db_path),))
            guard.execute("PRAGMA busy_timeout=" + str(min(5000, self._remaining_ms(root))))
            guard.execute("BEGIN IMMEDIATE")
            return self._read_source_reference_guarded(root=root)

    def _read_source_reference_guarded(self, *, root):
        # Existing resolver/store methods still read their own connections;
        # the reservation above prevents either database from changing meanwhile.
        self._read_waiting_draft(root=root, expected_version=root.draft["version"])
        return self._read_source_evidence_guarded(root=root)

    def _read_source_evidence_guarded(self, *, root):
        # C1's concrete input context calls this under an Agent-DB-first guard;
        # its exact PREPARING receipt is checked separately under TaskStore's lock.
        self._capture(root)
        if root.structure_context_json is None or root.claim is None:
            raise _Unavailable("source_unavailable")
        digest = hashlib.sha256(root.structure_context_json.encode("utf-8")).hexdigest()
        if json.loads(root.claim.binding_json).get("structure_context_digest") != digest:
            raise _Unavailable("source_unavailable")
        assessment = assess_docking_request_text(root.snapshot.query, enabled=self._enabled, enable_tools=True)
        pdb_id = assessment["inputs"].get("pdb_id")
        evidence = OwnedDockingStructureEvidence(self._state, clock=lambda: self._wall() / 1000)
        resolved = evidence.resolve(json.loads(root.structure_context_json), session_id=root.owner, pdb_id=pdb_id)
        if resolved is None:
            raise _Unavailable("source_unavailable")
        _, taxonomy = _resolve_organism(resolved["target"]["organism"])
        reference = dict(context=resolved["context"], pdb_id=pdb_id,
            target=dict(uniprot_id=resolved["target"]["uniprot_id"], taxonomy_id=taxonomy))
        if root.source_reference is not None and root.source_reference != reference:
            raise _Unavailable("source_unavailable")
        self._check_root(root=root)
        self._capture(root)
        return reference

    def _child_deadline(self, root):
        return min(root.mono_deadline, root.physical_deadline,
                   time.monotonic() + self._remaining_ms(root) / 1000)

    async def _prepare_source(self, root):
        async with self._gate.request():
            reference = await self._sql(self._read_source_reference, root=root)
            self._capture(root)
        root.source_reference = reference
        owner = chat_preparation.PreparationOwner()
        root.preparation_owners.append(owner)  # Before physical dispatch.
        result = await source_preparation.run_source_preparation(
            pdb_id=reference["pdb_id"], target=reference["target"],
            deadline=self._child_deadline(root), owner=owner)
        if (owner.snapshot()["settled"] is not True or type(result) is not dict
                or result.get("ok") is not True):
            raise _Unavailable("source_preparation_unavailable")
        async with self._gate.request():
            await self._sql(self._read_source_reference, root=root)
            self._capture(root)
        # Only the fixed canonical worker supplies these bytes/metadata. Neither
        # a model descriptor nor a caller hash can enter this path.
        source = result["source"]
        if (source["pdb_id"] != reference["pdb_id"] or source["target"] != reference["target"]
                or hashlib.sha256(result["pdb_bytes"]).hexdigest() != source["sha256"]):
            raise _Unavailable("source_preparation_unavailable")
        root.source_result = result
        inspection_owner = chat_preparation.PreparationOwner()
        root.preparation_owners.append(inspection_owner)
        inspection = await chat_preparation.run_preparation("projection_inspect",
            inputs=dict(pdb_bytes=result["pdb_bytes"], source=source),
            deadline=self._child_deadline(root), owner=inspection_owner)
        if (inspection_owner.snapshot()["settled"] is not True or type(inspection) is not dict
                or inspection.get("ok") is not True or inspection["source_sha256"] != source["sha256"]):
            raise _Unavailable("source_inspection_unavailable")
        async with self._gate.request():
            await self._sql(self._read_source_reference, root=root)
            self._capture(root)
            root.inspection = inspection
            counts = {section: len(inspection["limitations"] if section == "limitations"
                                   else inspection["choices"][section]) for section in _SECTIONS}
            root.source_preview_json = _small_json(dict(pdb_id=source["pdb_id"], target=source["target"],
                source_sha256=source["sha256"], revision_version=result["evidence"]["revision_version"],
                choice_counts=counts, input_ready=False, execution_authorized=False))
            root.phase, root.reason = "AWAITING_INPUT", "scientific_selection_required"

    async def source_page(self, *, root_id, owner_session_id, source_sha256, section, offset=0, limit=32):
        denied = dict(ok=False, reason_code="source_unavailable", input_ready=False, execution_authorized=False)
        if (type(source_sha256) is not str or re.fullmatch(r"[0-9a-f]{64}", source_sha256) is None
                or type(section) is not str or section not in _SECTIONS
                or type(offset) is not int or not 0 <= offset <= 99999
                or type(limit) is not int or not 1 <= limit <= 32):
            return denied
        view = await self.status(root_id=root_id, owner_session_id=owner_session_id)
        root = self._owned(root_id, owner_session_id)
        if (root is None or root.closed or root.inspection is None or "source_preview" not in view
                or view["source_preview"]["source_sha256"] != source_sha256):
            return denied
        records = root.inspection["limitations"] if section == "limitations" else root.inspection["choices"][section]
        if offset > len(records):
            return denied
        items = []
        for record in records[offset:min(len(records), offset + limit)]:
            try:
                # Encode at most one bounded page before detaching its containers.
                _small_json([*items, record], _PAGE_BYTES - 1024)
            except (ValueError, TypeError, UnicodeError):
                if not items:
                    return denied
                break
            items.append(record)
        next_offset = offset + len(items)
        return json.loads(_small_json(dict(ok=True, source_sha256=source_sha256, section=section,
            offset=offset, next_offset=next_offset if next_offset < len(records) else None,
            items=items, input_ready=False, execution_authorized=False)))

    async def select_source(self, *, root_id, owner_session_id, source_sha256, selection):
        """Explicit choices for this current preview; no C1 or execution grant.

        Capture native input before the first await. Only the retained canonical
        source feeds the fixed projection/ligand workers. Exact duplicates reuse
        their bytes; any later change burns this root instead of regenerating.
        """
        root = self._owned(root_id, owner_session_id)
        if (root is None or root.closed or root.source_result is None or root.inspection is None
                or type(source_sha256) is not str
                or source_sha256 != root.source_result["source"]["sha256"]):
            return _view()
        try:
            choice, key = _capture_selection(selection)
        except Exception:
            return _view(reason="selection_invalid", phase="AWAITING_INPUT")
        if root.selection_key is not None:
            if key != root.selection_key:
                self._interrupt(root, "new_root_required")
                try:
                    if root.task is not None and await _drain(root.task):
                        raise asyncio.CancelledError
                finally:
                    await self._finish_closed(root)
                return _view(root)
            return await self.status(root_id=root_id, owner_session_id=owner_session_id)
        if root.task is not None and not root.task.done():
            return _view(root)
        root.selection, root.selection_key = choice, key
        root.phase, root.reason = "BUSY", "input_preparation_running"
        root.task = asyncio.create_task(self._run_selection(root))
        self._gate.retain_task(root.task)
        return await self._wait_root(root)

    async def _check_source(self, root):
        async with self._gate.request():
            await self._sql(self._read_source_reference, root=root)
            self._capture(root)

    async def _selected_child(self, root, name, inputs):
        owner = chat_preparation.PreparationOwner()
        root.preparation_owners.append(owner)
        result = await chat_preparation.run_preparation(name, inputs=inputs,
            deadline=self._child_deadline(root), owner=owner)
        if (owner.snapshot()["settled"] is not True or type(result) is not dict
                or result.get("ok") is not True):
            raise _Unavailable("selected_input_unavailable")
        await self._check_source(root)
        return result

    @staticmethod
    def _input_descriptors(root):
        receptor = dict(schema="chat-docking-receptor-v1", kind="explicit-residue-conformer-v1",
            source_evidence=root.source_result["evidence"], projection=root.projected["projection"],
            pocket=root.projected["pocket"])
        ligand = dict(schema="chat-docking-ligand-v1", kind="modeled_input_not_experimental_pose",
            query_digest=root.snapshot.query_digest, sha256=root.ligand["sha256"],
            canonical_smiles=root.ligand["canonical_smiles"], atom_count=root.ligand["atom_count"],
            formal_charge=root.ligand["formal_charge"], provenance=root.ligand["provenance"])
        return receptor, ligand, dict(receptor_ref=_content_ref(receptor), ligand_ref=_content_ref(ligand))

    async def _refine_slots(self, root, patch):
        patch = validate_draft_patch(patch)
        # The store's existing CAS owns its write transaction. Never nest the
        # two-database read guard inside that TaskStore-first transaction.
        async with self._gate.request():
            await self._sql(self._read_source_reference, root=root)
            self._capture(root)
            slots = {key: dict(value) for key, value in root.accepted_slots.items()}
            for key, value in patch.items():
                if slots[key]["state"] != "pending":
                    raise _Unavailable("new_root_required")
                slots[key] = dict(state="fixed", value=value)
            revision, version = root.accepted_revision + 1, root.draft["version"] + 1
            result = await self._sql(self._tasks.refine_docking_consent_draft,
                preparation_id=root.preparation_id, owner_session_id=root.owner,
                expected_revision=root.accepted_revision, expected_version=root.draft["version"],
                policy=root.current["policy"], model_generation=root.current["model_generation"],
                capability_generation=root.current["capability_generation"],
                wall_time_ms=self._wall, monotonic=self._mono,
                current_context=lambda: self._context(root), patch=patch)
            confirmed = self._validate_draft_record(root, result, expected_version=version,
                                                   accepted_revision=revision, accepted_slots=slots)
            if confirmed["draft"]["budget"] != root.draft["draft"]["budget"]:
                raise _Unavailable("draft_binding_mismatch")
            root.accepted_revision, root.accepted_slots, root.draft = revision, slots, confirmed
            await self._sql(self._read_source_reference, root=root)
            self._capture(root)
            root.missing = tuple(key for key in sorted(DRAFT_SLOT_NAMES) if slots[key]["state"] == "pending")

    async def _run_selection(self, root):
        try:
            await self._check_source(root)
            assessed = assess_docking_request_text(root.snapshot.query, enabled=self._enabled, enable_tools=True)
            if not assessed["inputs"].get("smiles"):
                raise _Unavailable("ligand_input_missing")
            root.projected = await self._selected_child(root, "projection", dict(
                pdb_bytes=root.source_result["pdb_bytes"], source=root.source_result["source"],
                selection=root.selection))
            root.ligand = await self._selected_child(root, "ligand", dict(smiles=assessed["inputs"]["smiles"]))
            receptor, ligand, refs = await self._sql(self._input_descriptors, root=root)
            await self._check_source(root)
            explicit = {key: value for key, value in assessed["fields"].items()
                        if key in {"center", "size", "exhaustiveness", "num_modes"}}
            await self._refine_slots(root, dict(refs, **explicit))
            root.receptor_descriptor, root.ligand_descriptor = receptor, ligand
            projected = root.projected["projection"]
            pocket = root.projected["pocket"]
            suggested = dict(center=pocket["center"], size=pocket["size"], exhaustiveness=8, num_modes=9)
            suggested.update(explicit)  # Original explicit values are never overwritten.
            preview_json = _small_json(dict(input_kind="explicit-residue-conformer-v1",
                source_sha256=root.source_result["source"]["sha256"],
                derived_sha256=projected["derived_input"]["sha256"], ligand_sha256=root.ligand["sha256"],
                selection_sha256=projected["selection"]["sha256"], refinement_revision=root.accepted_revision,
                parameters_suggestion=suggested, ligand_provenance=root.ligand["provenance"],
                reference_residue=pocket["residue"], pocket_rule=pocket["rule_version"],
                pocket_margin=pocket["margin"], pocket_atom_count=pocket["atom_count"],
                input_ready=False, execution_authorized=False, **refs))
            await self._check_source(root)
            root.prepared_preview_json = preview_json
            root.phase, root.reason = "AWAITING_INPUT", "parameters_confirmation_required"
        except asyncio.CancelledError:
            self._stop(root, "cancelled")
            raise
        except _Unavailable as exc:
            self._stop(root, str(exc))
        except Exception:
            self._stop(root, "selection_unavailable")

    async def confirm_parameters(self, *, root_id, owner_session_id, expected_revision,
                                 receptor_ref, ligand_ref, parameters):
        """Confirm the displayed inputs, without staging, nonce or execution.

        Native values are detached before suspension. The existing draft CAS
        fills pending slots only; exact duplicates reuse the accepted receipt.
        Original fixed values cannot be replaced by a new confirmation.
        """
        root = self._owned(root_id, owner_session_id)
        if (root is None or root.closed or root.prepared_preview_json is None
                or type(expected_revision) is not int
                or expected_revision not in {root.accepted_revision, root.confirmation_revision}
                or type(receptor_ref) is not str or type(ligand_ref) is not str
                or receptor_ref != root.accepted_slots["receptor_ref"].get("value")
                or ligand_ref != root.accepted_slots["ligand_ref"].get("value")):
            return _view(reason="parameters_unavailable")
        try:
            captured = _capture_parameters(parameters)
            key = _content_ref(dict(receptor_ref=receptor_ref, ligand_ref=ligand_ref,
                                    parameters=captured), 2048)
        except Exception:
            return _view(reason="parameters_invalid", phase="AWAITING_INPUT")
        if (expected_revision != root.accepted_revision
                and (expected_revision != root.confirmation_revision or key != root.confirmation_key)):
            # The old revision is an idempotent retry exception, not authority
            # to change or revoke a more recently accepted input set.
            return _view(reason="parameters_unavailable")
        changed_fixed = any(slot["state"] == "fixed" and slot["value"] != captured[name]
            for name, slot in root.accepted_slots.items() if name in captured)
        if changed_fixed or (root.confirmation_key is not None and key != root.confirmation_key):
            self._interrupt(root, "new_root_required")
            try:
                if root.task is not None and await _drain(root.task):
                    raise asyncio.CancelledError
            finally:
                await self._finish_closed(root)
            return _view(root)
        if root.confirmation_key is not None:
            return await self.status(root_id=root_id, owner_session_id=owner_session_id)
        for name in ("center", "size"):
            slot = root.accepted_slots[name]
            expected = slot["value"] if slot["state"] == "fixed" else root.projected["pocket"][name]
            if captured[name] != expected:
                return _view(reason="parameters_invalid", phase="AWAITING_INPUT")
        if root.task is not None and not root.task.done():
            return _view(root)
        root.confirmation_key, root.confirmation_revision = key, root.accepted_revision
        root.phase, root.reason = "BUSY", "parameter_confirmation_running"
        root.task = asyncio.create_task(self._run_confirmation(root, captured))
        self._gate.retain_task(root.task)
        return await self._wait_root(root)

    async def _run_confirmation(self, root, parameters):
        try:
            await self._check_source(root)
            patch = {key: value for key, value in parameters.items()
                     if root.accepted_slots[key]["state"] == "pending"}
            if patch:
                await self._refine_slots(root, patch)
            preview = json.loads(root.prepared_preview_json)
            preview.update(confirmed_parameters=parameters, refinement_revision=root.accepted_revision)
            preview_json = _small_json(preview)
            await self._check_source(root)
            root.prepared_preview_json = preview_json
            root.phase, root.reason = "AWAITING_INPUT", "inputs_confirmed_staging_required"
        except asyncio.CancelledError:
            self._stop(root, "cancelled")
            raise
        except _Unavailable as exc:
            self._stop(root, str(exc))
        except Exception:
            self._stop(root, "confirmation_unavailable")

    async def prepare_inputs(self, *, root_id, owner_session_id, expected_revision,
                             receptor_ref, ligand_ref, runtime):
        """Stage this exact confirmed root; only the initiating call gets a nonce.

        ``runtime`` is trusted application assembly, never a transport field.
        The public inputs are current references, not bytes, paths or authority.
        """
        from src.task_runtime.runtime import TaskRuntime
        if type(runtime) is not TaskRuntime or runtime.store is not self._tasks:
            return _view()
        root = self._owned(root_id, owner_session_id)
        if (root is None or root.closed or type(expected_revision) is not int
                or expected_revision != root.accepted_revision
                or any(type(value) is not str or root.accepted_slots[key] !=
                       dict(state="fixed", value=value) for key, value in
                       (("receptor_ref", receptor_ref), ("ligand_ref", ligand_ref)))):
            return _view()
        if root.staging_started:
            return await self.status(root_id=root_id, owner_session_id=owner_session_id)
        if root.task is not None and not root.task.done():
            return _view(root)
        if root.reason != "inputs_confirmed_staging_required":
            return _view(reason="inputs_unconfirmed")
        root.staging_started = True
        root.phase = "PREPARING"
        root.task = task = asyncio.create_task(self._run_staging(root, runtime))
        self._gate.retain_task(task)
        result = await self._wait_root(root)
        if (not root.closed and root.task is task and not task.cancelled()
                and result["phase"] == "READY" and result["input_ready"] is True):
            result["consent_preview"] = task.result()
        # Do not retain the one-time nonce in a completed coordinator task.
        if root.task is task and task.done():
            root.task = None
        return result

    async def _run_staging(self, root, runtime):
        from .docking_input_context import capture_chat_input_context
        try:
            context = await capture_chat_input_context(self,
                root_id=root.root_id, owner_session_id=root.owner)
            preview = (await runtime.prepare_docking_consent(
                preparation_id=root.preparation_id, owner_session_id=root.owner,
                revision=root.accepted_revision, receptor_name="receptor.pdb",
                receptor_bytes=context._receptor, ligand_name="ligand.sdf",
                ligand_bytes=context._ligand, parameters=context._parameters,
                chat_input_context=context)).to_dict()
            self._remaining_ms(root)
            redacted = {key: value for key, value in preview.items() if key != "approval_nonce"}
            root.ready_preview_json = _small_json(redacted)
            async with self._gate.request():
                await self._sql(context._check_ready_guarded)
                context._ready_current()
                self._capture(root)
            root.phase, root.reason = "READY", "approval_required"
            return preview
        except asyncio.CancelledError:
            self._stop(root, "cancelled")
            raise
        except Exception:
            self._stop(root, "staging_unavailable")

    async def close_root(self, *, root_id, owner_session_id):
        root = self._owned(root_id, owner_session_id)
        if root is None:
            return _view()
        self._interrupt(root, "closed", "CLOSED")
        root.phase, root.reason = "CLOSED", "closed"
        task = root.task
        try:
            if task is not None and await _drain(task):
                raise asyncio.CancelledError
        finally:
            await self._finish_closed(root)
        return _view(root)


__all__ = ["DockingRequestCoordinator"]
