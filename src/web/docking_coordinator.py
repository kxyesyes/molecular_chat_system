"""Bounded live INITIAL C coordinator, shared by legacy/decision entry points.

Disabled by default. This is NOT an HTTP/source authorizer, preparation worker,
approval issuer or scheduler. The trusted caller supplies an existing owned
running AgentState run. Never recreate that run or restore a claimed root.

Two independent deadlines: upstream work gets 180 seconds from before the first
await, while TaskStore keeps its unchanged 900-second consent waiting expiry.
Neither refresh nor human waiting renews either deadline. Future source work
must use the minimum of these clocks too; this slice never invokes source/C1.

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
from dataclasses import asdict, dataclass
import json
import math
import re
import time
from uuid import uuid4

from src.agent.persistence.redaction import contains_sensitive_text
from src.system.model_lifecycle import finish_on_cancel
from src.task_runtime.docking_consent import (
    DRAFT_SLOT_NAMES, MAX_PENDING_CONSENTS, DockingConsentPolicy,
    validate_clocks, validate_draft_identity, validate_chat_draft,
)
from src.web.docking_proposal import (
    DockingProposalOwner, TextSegmentIssuer, capture_source_snapshot, capture_text_segment,
)
from src.web.docking_request import assess_docking_request_text, validate_docking_text_proposal


# Intersection of proposal-issuer labels and durable draft identity labels.
_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_WORK_MS = 180000
_CLAIM_KEY = "chat_docking_root"


def _wall_time_ms():
    return time.time_ns() // 1000000


def _label(value):
    return type(value) is str and _LABEL.fullmatch(value) is not None and not contains_sensitive_text(value)


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


def _view(root=None, *, reason="root_unavailable", phase="UNAVAILABLE"):
    return dict(root_id=root.root_id if root else None,
        task_id=root.task_id if root else None,
        preparation_id=root.preparation_id if root else None,
        phase=root.phase if root else phase, reason_code=root.reason if root else reason,
        missing_fields=list(root.missing) if root else [],
        input_ready=False, execution_authorized=False)


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
    def __init__(self, store, validate):
        self._store, self._validate = store, validate
        self.db_path = store.db_path

    def get_chat_docking_draft(self, **kwargs):
        return self._validate(self._store.get_chat_docking_draft(**kwargs))

    def reserve_docking_draft_budget(self, **kwargs):
        return self._validate(self._store.reserve_docking_draft_budget(**kwargs))


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
        deadlines = [root.wall_deadline - wall, (root.mono_deadline - mono) * 1000]
        if root.draft is not None:
            deadlines += [root.draft["expires_at_ms"] - wall,
                          (root.draft["monotonic_expires"] - mono) * 1000]
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

    def _validate_draft_record(self, root, record, *, expected_version=None):
        """A receipt/read validates an existing root; it never creates authority.

        INITIAL has no accepted refinements or prepared slots. Compare the whole
        native identity, including task/preparation/admission, with the committed
        root receipt instead of borrowing identity from a later store response.
        """
        self._remaining_ms(root)
        if type(root.claim) is not _ClaimReceipt or type(record) is not dict:
            raise _Unavailable("draft_unavailable")
        expected = json.loads(root.claim.binding_json)["identity"]
        identity = validate_draft_identity(record["identity"])
        draft = validate_chat_draft(record["draft"])
        if (identity != expected or expected != self._identity(root)
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
        return self._validate_draft_record(root, record, expected_version=expected_version)

    def _read_waiting_draft(self, *, root, expected_version):
        # The draft read may block after an earlier root check. Revalidate the
        # committed claim only once that read settles, in the same retained SQL
        # worker, before either publication or refresh can expose a waiting view.
        record = self._read_draft(root=root, expected_version=expected_version)
        self._check_root(root=root)
        return record

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
        if not root.closed or (root.task is not None and not root.task.done()):
            return
        root.snapshot = root.current = root.issuer = root.proposal = root.draft = root.claim = root.task = None
        if root.durable and self._roots.get(root.root_id) is root:
            del self._roots[root.root_id]
        # No confirmed durable claim: retain only a bounded non-retryable tombstone.

    async def begin(self, *, root_id, owner_session_id, origin_turn_id, query,
                    history, descriptors, enable_tools, mode):
        # Entire input capture/preflight, clocks and the local attempt burn precede
        # the first await; duplicate coroutines cannot race to create an issuer.
        try:
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
                return _view(root) if root.owner == owner_session_id else _view()
            if len(self._roots) >= MAX_PENDING_CONSENTS:
                return _view(reason="coordinator_capacity_full")
            snapshot = capture_source_snapshot(query=query, history=history, descriptors=descriptors)
        except Exception:
            return _view(reason="invalid_input")
        root = _Root(root_id, owner_session_id, origin_turn_id, str(uuid4()), str(uuid4()),
                     snapshot, mode, wall + _WORK_MS, mono + _WORK_MS / 1000,
                     tuple(assessment["missing_fields"]))
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
            return _view(root)
        except asyncio.CancelledError:
            self._interrupt(root, "cancelled")
            await _drain(task)
            raise
        except Exception:
            self._interrupt(root, "operation_unavailable")
            if await _drain(task):
                raise asyncio.CancelledError
            return _view(root)
        finally:
            self._release_closed(root)

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
            bound_store = _BoundDraftStore(self._tasks, lambda record: self._validate_draft_record(root, record))
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
        if root.closed or (root.task is not None and not root.task.done()):
            return _view(root)
        root.task = asyncio.create_task(self._refresh(root))
        self._gate.retain_task(root.task)
        return await self._wait_root(root)

    async def _refresh(self, root):
        # Status SQL has the same physical ownership as initial work. Concurrent
        # refreshes coalesce; close/cancellation cannot drop its heavy context.
        try:
            async with self._gate.request():
                await self._sql(self._check_root, root=root)
                current_draft = await self._sql(self._read_waiting_draft, root=root,
                                               expected_version=root.draft["version"])
                self._capture(root)
                root.draft = current_draft
        except asyncio.CancelledError:
            self._stop(root, "cancelled")
            raise
        except Exception:
            self._stop(root, "context_unavailable")

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
            return _view(root)
        finally:
            self._release_closed(root)


__all__ = ["DockingRequestCoordinator"]
