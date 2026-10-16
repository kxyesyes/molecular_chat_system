"""C transport facts and bounded control DTOs; no consent or grant authority.

The existing WebDecisionRuntime owns these records; this module is not a
sender/scheduler. Trusted text is UTF-8, at most 64 KiB, rejected rather than
truncated. Identity is a detached exact native dict with bounded IDs and native
nonnegative revision/sequence integers. Send generation is server-owned.

`delivered` means local physical completion *observed* before the deadline,
never peer receipt. A sealed receipt cannot be upgraded by late cleanup.
"""
from dataclasses import dataclass
import re
import time
from typing import Literal

from src.system.redaction import contains_sensitive_text


FEEDBACK_SECONDS = 5.0
SHUTDOWN_SECONDS = 10.0
MAX_TEXT_BYTES = 64 * 1024
MAX_PENDING_SENDS = 64
_now = time.monotonic


MAX_CONTROL_BYTES = 24 * 1024
MAX_CONTROL_MESSAGE_CHARS = 8192
MAX_CONTROL_REVISION = 2**53 - 1
# Same label contract as the coordinator, without importing its execution graph.
_CONTROL_LABEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,127}\Z")
_CONTROL_FIELDS = {
    "docking_refine": frozenset({"type", "request_id", "revision", "message"}),
    "docking_watch": frozenset({"type", "task_id"}),
}


class DockingControlError(ValueError):
    """Fixed public failure, never a raw value, key or parser diagnostic."""

    def __init__(self):
        self.code = "invalid_docking_control"
        super().__init__(self.code)


def validate_docking_control(payload) -> dict:
    """Validate bounded native scalars before making a detached shallow copy.

    These are untrusted control requests, not ownership or execution proofs.
    Native message size is in Unicode characters; wire byte size is checked by
    decode_docking_control, including JSON escaping and framing whitespace.
    """
    if type(payload) is not dict or len(payload) not in (2, 4):
        raise DockingControlError()
    # Never hash/compare a custom key or copy an unbounded container/key set.
    if any(type(key) is not str or len(key) > 10 for key in payload):
        raise DockingControlError()
    frame_type = payload.get("type")
    if (type(frame_type) is not str or len(frame_type) > 14
            or frame_type not in _CONTROL_FIELDS):
        raise DockingControlError()
    if set(payload) != _CONTROL_FIELDS[frame_type]:
        raise DockingControlError()
    identifier = payload["request_id" if frame_type == "docking_refine" else "task_id"]
    if (type(identifier) is not str or not 1 <= len(identifier) <= 128
            or _CONTROL_LABEL.fullmatch(identifier) is None
            or contains_sensitive_text(identifier)):
        raise DockingControlError()
    if frame_type == "docking_refine":
        revision, message = payload["revision"], payload["message"]
        if type(revision) is not int or not 0 <= revision <= MAX_CONTROL_REVISION:
            raise DockingControlError()
        if (type(message) is not str or not 1 <= len(message) <= MAX_CONTROL_MESSAGE_CHARS
                or not message.strip()):
            raise DockingControlError()
        # Unicode Cc controls (C0/C1), except the three allowed text separators.
        if any((ord(char) < 32 or 127 <= ord(char) <= 159) and char not in "\t\r\n"
               for char in message):
            raise DockingControlError()
        try:
            message.encode("utf-8")
        except UnicodeError:
            raise DockingControlError() from None
    return dict(payload)


def decode_docking_control(raw) -> dict:
    """Strict UTF-8 JSON, rejecting ambiguity before schema validation."""
    # Bound characters before the shared parser allocates a UTF-8 encoding.
    if type(raw) is not str or not raw or len(raw) > MAX_CONTROL_BYTES:
        raise DockingControlError()
    # Lazy contract import keeps existing transport-only imports lightweight.
    from src.agent.contracts.decision import DecisionProtocolError, decode_protocol_json

    try:
        payload = decode_protocol_json(raw, max_bytes=MAX_CONTROL_BYTES)
    except DecisionProtocolError:
        raise DockingControlError() from None
    return validate_docking_control(payload)


@dataclass(frozen=True)
class DockingIdentity:
    root_id: str
    task_id: str
    turn_id: str
    revision: int
    sequence: int

    @classmethod
    def parse(cls, value):
        if type(value) is not dict or set(value) != {
                'root_id', 'task_id', 'turn_id', 'revision', 'sequence'}:
            raise ValueError('invalid transport identity')
        for key in ('root_id', 'task_id', 'turn_id'):
            if type(value[key]) is not str or not 0 < len(value[key].encode('utf-8')) <= 128:
                raise ValueError('invalid transport identity')
        for key in ('revision', 'sequence'):
            if type(value[key]) is not int or not 0 <= value[key] <= 2**63 - 1:
                raise ValueError('invalid transport identity')
        return cls(**value)


@dataclass(frozen=True)
class DockingDelivery:
    delivery: Literal['delivered', 'undeliverable']
    identity: DockingIdentity | None = None
    send_generation: int | None = None


@dataclass(frozen=True)
class _Binding:
    identity: DockingIdentity
    send_generation: int
    deadline: float


@dataclass(eq=False)
class _PhysicalOperation:
    sender: object
    kind: Literal['send', 'close']
    binding: _Binding | None = None
    task: object = None
    started: bool = False
    settled: bool = False
    succeeded: bool = False
    failed: bool = False
    cancel_requested: bool = False
    live: bool = True
    receipt: DockingDelivery | None = None

    def cancel_once(self):
        if self.task is not None and not self.task.done() and not self.cancel_requested:
            self.cancel_requested = True
            self.task.cancel()

    def seal(self, delivery):
        if self.receipt is None:
            self.live = False
            self.receipt = DockingDelivery(delivery, self.binding.identity,
                                          self.binding.send_generation)
        return self.receipt
