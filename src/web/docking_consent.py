"""C transport facts only; no consent, publication, grants or wire DTOs yet.

The existing WebDecisionRuntime owns these records; this module is not a
sender/scheduler. Trusted text is UTF-8, at most 64 KiB, rejected rather than
truncated. Identity is a detached exact native dict with bounded IDs and native
nonnegative revision/sequence integers. Send generation is server-owned.

`delivered` means local physical completion *observed* before the deadline,
never peer receipt. A sealed receipt cannot be upgraded by late cleanup.
"""
from dataclasses import dataclass
import time
from typing import Literal


FEEDBACK_SECONDS = 5.0
SHUTDOWN_SECONDS = 10.0
MAX_TEXT_BYTES = 64 * 1024
MAX_PENDING_SENDS = 64
_now = time.monotonic


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
