"""Offline C4a protocol contracts. Native cases require the opt-in runner."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tests.task_runtime import _c4a_sticky_retention_child as child
from scripts import run_c4a_acceptance as runner


@pytest.mark.parametrize("change", [
    "none", "empty", "oversize", "duplicate", "extra-line", "extra-key", "missing-key",
    "wrong-challenge", "wrong-mode", "bool-version", "int-guarded", "false-guarded", "wrong-descendant",
])
def test_c4a_sticky_child_protocol_controls(change):
    challenge = "a" * 32
    value = {"version": 1, "mode": "clean", "challenge": challenge,
             "guarded": True, "descendant_ready": False}
    if change == "extra-key":
        value["extra"] = False
    elif change == "missing-key":
        del value["guarded"]
    elif change == "wrong-challenge":
        value["challenge"] = "b" * 32
    elif change == "wrong-mode":
        value["mode"] = "close"  # A permanent mode cannot pass the finite parser.
    elif change == "bool-version":
        value["version"] = True
    elif change == "int-guarded":
        value["guarded"] = 1
    elif change == "false-guarded":
        value["guarded"] = False
    elif change == "wrong-descendant":
        value["descendant_ready"] = True
    data = (json.dumps(value) + "\n").encode()
    if change == "empty":
        data = b""
    elif change == "oversize":
        data = b" " * child.REPORT_LIMIT + data
    elif change == "duplicate":
        data = data.replace(b'"version": 1', b'"version": 1, "version": 1')
    elif change == "extra-line":
        data += data
    if change == "none":
        assert child._decode_report(data, "clean", challenge) == value
    else:
        with pytest.raises(ValueError):
            child._decode_report(data, "clean", challenge)


@pytest.mark.parametrize("fault", ["none", "threads_joined", "job_closed", "job_empty_before_close",
                                   "process_handle_closed", "capture_clean", "extra", "missing", "integer"])
def test_c4a_sticky_child_containment_receipt_controls(fault):
    facts = dict.fromkeys(("job_empty_before_close", "job_closed", "process_exited",
                           "process_handle_closed", "streams_closed", "threads_joined",
                           "capture_clean", "no_observer_error"), True)
    if fault == "none":
        runner.require_containment(facts)
        return
    if fault == "extra":
        facts["extra"] = True
    elif fault == "missing":
        del facts["threads_joined"]
    elif fault == "integer":
        facts["job_closed"] = 1
    else:
        facts[fault] = False
    with pytest.raises(AssertionError, match="^c4a_finite_containment_unconfirmed$"):
        runner.require_containment(facts)


@pytest.mark.parametrize("fault", ["none", "missing", "oversize", "duplicate", "permanent-mode", "nonempty-root"])
def test_c4a_sticky_child_guard_denies_unowned_entry(tmp_path, monkeypatch, fault):
    root = tmp_path / "private"
    root.mkdir()
    packet = runner.finite_packet(root, "clean", "c" * 32)
    if fault == "missing":
        packet = b""
    elif fault == "oversize":
        packet = b" " * child.GUARD_LIMIT + packet
    elif fault == "duplicate":
        packet = packet.replace(b'"mode": "clean"', b'"mode": "clean", "mode": "clean"')
    elif fault == "permanent-mode":
        packet = runner.finite_packet(root, "close", "c" * 32)
    elif fault == "nonempty-root":
        (root / "occupied").write_bytes(b"finite sentinel")
    entered = []
    monkeypatch.setattr(child, "_receive_guard", lambda request: packet)
    monkeypatch.setattr(child, "_run_finite", lambda *args: entered.append(True))
    request = SimpleNamespace(config=SimpleNamespace(args=[runner.child_selector("clean")]))
    if fault == "none":
        child.test_c4a_finite_child(request, "clean")
        assert entered == [True]
    else:
        with pytest.raises(ValueError):
            child.test_c4a_finite_child(request, "clean")
        assert entered == []
    # Pure guard controls are not evidence of native pipe/Job membership.
@pytest.mark.parametrize("mode", ["close", "delete"])
@pytest.mark.parametrize("fault", ["none", "false-worker", "bool-count", "wrong-kind", "physical",
                                   "cleanup", "attempts", "extra", "missing", "duplicate", "extra-line"])
def test_c4a_sticky_retention_report_controls(mode, fault):
    # Literal positive/negative protocol fixtures only, never runtime evidence.
    value = dict.fromkeys(child._STICKY_TRUE_FIELDS, True)
    value.update(dict.fromkeys(child._STICKY_ZERO_FIELDS, 0))
    value.update(version=1, mode=mode, challenge="a" * 32, raw_calls=1, occupied=1,
                 cleanup_settled=False, shutdown_reason="consent_cleanup_unresolved",
                 shutdown_repeat_reason="consent_cleanup_unresolved",
                 unresolved_kind="command_ownership_unresolved" if mode == "close" else "own_job_cleanup_unresolved",
                 physical_settled=mode == "delete", cleanup_failed=mode == "delete",
                 fault_attempts=1 if mode == "close" else 2)
    if fault == "false-worker":
        value["worker_pending"] = False
    elif fault == "bool-count":
        value["raw_calls"] = True
    elif fault == "wrong-kind":
        value["unresolved_kind"] = "other"
    elif fault == "physical":
        value["physical_settled"] = not value["physical_settled"]
    elif fault == "cleanup":
        value["cleanup_failed"] = not value["cleanup_failed"]
    elif fault == "attempts":
        value["fault_attempts"] = 0
    elif fault == "extra":
        value["extra"] = True
    elif fault == "missing":
        del value["inputs_open"]
    data = (json.dumps(value) + "\n").encode()
    if fault == "duplicate":
        data = data.replace(b'"version": 1', b'"version": 1, "version": 1')
    elif fault == "extra-line":
        data += data
    if fault == "none":
        assert child._decode_sticky_report(data, mode, "a" * 32) == value
    else:
        with pytest.raises(ValueError):
            child._decode_sticky_report(data, mode, "a" * 32)


def test_reviewed_source_contract_detects_change(monkeypatch):
    digest = runner.source_digest()
    assert len(digest) == 64
    original = Path.read_bytes

    def changed(path):
        data = original(path)
        return data + b"# change" if path.name == "base.py" else data
    monkeypatch.setattr(Path, "read_bytes", changed)
    with pytest.raises(ValueError, match="source_contract"):
        runner.source_digest()


@pytest.mark.parametrize("case", ["unknown", "clean::other", "../external", None])
def test_no_arbitrary_child_selector(case):
    with pytest.raises(ValueError):
        runner.child_selector(case)

@pytest.mark.parametrize("fault", ["none", "missing", "finite-version", "wrong-mode", "nonempty-root",
                                   "qualification", "not-selected"])
def test_sticky_guard_before_payload(tmp_path, monkeypatch, fault):
    root = tmp_path / "guard"
    root.mkdir()
    packet = {"version": "C4A_STICKY_V1", "mode": "close", "challenge": "a" * 32, "root": str(root)}
    if fault == "finite-version":
        packet["version"] = "C4A_STICKY_FINITE_V1"
    elif fault == "wrong-mode":
        packet["mode"] = "clean"
    elif fault == "nonempty-root":
        (root / "occupied").touch()
    entered = []

    async def payload(*args):
        entered.append(True)

    monkeypatch.setattr(child, "_run_sticky", payload)
    data = (json.dumps(packet) + "\n").encode() if fault != "missing" else b""
    monkeypatch.setattr(child, "_receive_guard", lambda request: data)
    if fault == "qualification":
        monkeypatch.setattr(Path, "read_bytes", lambda path: b"{}")
    request = SimpleNamespace(config=SimpleNamespace(args=[] if fault == "not-selected" else
                                                      [runner.child_selector("close")]))
    if fault == "none":
        child.test_c4a_sticky_child(request, monkeypatch, "close")
        assert entered == [True]
    else:
        with pytest.raises(ValueError):
            child.test_c4a_sticky_child(request, monkeypatch, "close")
        assert not entered

@pytest.mark.parametrize("change", ["none", "extra-field", "nonboolean", "wrong-order", "invalid-location"])
def test_parent_receipt_has_closed_schema(change):
    records = [{"case": case, "status": "passed"} for case in runner.FINITE]
    records.append({"case": "close", "status": "passed", "production_retention_verified": True,
                    "test_child_containment_verified": True})
    if change == "extra-field":
        records[0]["unreviewed"] = "private"
    elif change == "nonboolean":
        records[-1]["production_retention_verified"] = 1
    elif change == "wrong-order":
        records[:2] = reversed(records[:2])
    elif change == "invalid-location":
        records[-1] = {"case": "close", "status": "failed",
                       "failure": {"module": "private/path", "error": "Failure", "line": 1}}
    data = "".join(json.dumps(record) + "\n" for record in records).encode()
    if change == "none":
        assert runner.decode_journal(data, (*runner.FINITE, *runner.STICKY)) == records
    else:
        with pytest.raises(ValueError):
            runner.decode_journal(data, (*runner.FINITE, *runner.STICKY))

def test_manifest_requires_complete_reviewed_set(tmp_path, monkeypatch):
    contract = json.loads(runner.MANIFEST.read_bytes())
    del contract["files"]["src/docking/adapters/base.py"]
    manifest = tmp_path / "incomplete.json"
    manifest.write_text(json.dumps(contract))
    monkeypatch.setattr(runner, "MANIFEST", manifest)
    with pytest.raises(ValueError):
        runner.source_digest()


def test_ambient_job_cannot_authorize_suite(monkeypatch):
    monkeypatch.delenv("MEDCHAT_C4A_SUITE_JOB", raising=False)
    with pytest.raises(ValueError, match="suite_owner_required"):
        runner.verify_suite_job()

@pytest.mark.parametrize("raises", [False, True])
def test_false_settlement_retains_owner_until_independent_release(raises, capsys):
    import threading

    observed, release = threading.Event(), threading.Event()
    outcome = {}
    scope = SimpleNamespace(seal=lambda: None)

    def settle():
        if raises:
            raise OSError("private failure detail")
        return False

    scope.settle = settle

    def independently_gone():
        observed.set()
        return release.is_set()

    owner = threading.Thread(target=runner.settle_suite, args=(scope, outcome, independently_gone))
    owner.start()
    try:
        assert observed.wait(2) and owner.is_alive()
    finally:
        release.set()
        owner.join(2)
    assert not owner.is_alive() and outcome["settled"] is False
    assert isinstance(outcome["error"], (RuntimeError, OSError))
    assert json.loads(capsys.readouterr().out) == {"status": "ownership_uncertain", "slot_released": False}


def test_interrupt_during_wait_preserves_owner_and_original_exception(tmp_path):
    import threading
    import time

    release, cancelled = threading.Event(), threading.Event()
    first_interrupt = KeyboardInterrupt()
    observed = []

    class InterruptedWait(threading.Event):
        def wait(self, timeout=None):
            # Two successive interrupts, including during cancellation/cleanup.
            observed.append(cancelled.is_set())
            if len(observed) <= 2:
                raise first_interrupt if len(observed) == 1 else KeyboardInterrupt()
            release.set()
            return super().wait(timeout)

    completed = InterruptedWait()
    def owner():
        assert release.wait(2) and cancelled.is_set()
        assert tmp_path.exists()
        completed.set()

    worker = threading.Thread(target=owner)
    try:
        with pytest.raises(KeyboardInterrupt) as exc:
            runner.wait_for_owner(worker, completed, cancelled, time.monotonic() + 1)
        assert exc.value is first_interrupt
        assert observed[:3] == [False, True, True]
        assert completed.is_set() and not worker.is_alive()
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(2)


def test_journal_rejects_duplicate_success():
    data = b'{"case":"clean","status":"failed","status":"passed"}\n'
    with pytest.raises(ValueError, match="duplicate"):
        runner.decode_journal(data, ("clean",))


def test_sigint_during_start_defers_until_owner_completes(monkeypatch):
    import signal
    import threading
    import time

    completed, cancel = threading.Event(), threading.Event()
    previous = signal.getsignal(signal.SIGINT)
    def cleanup():
        assert cancel.wait(2)
        completed.set()

    worker = threading.Thread(target=cleanup)
    start = worker.start

    def interrupted_start():
        # Deliver the registered callback at its hardest startup boundary.
        signal.getsignal(signal.SIGINT)(signal.SIGINT, None)
        start()

    monkeypatch.setattr(worker, "start", interrupted_start)
    try:
        with pytest.raises(KeyboardInterrupt):
            runner.wait_for_owner(worker, completed, cancel, time.monotonic() + 2)
        assert completed.is_set() and not worker.is_alive()
        assert signal.getsignal(signal.SIGINT) is previous
    finally:
        cancel.set()
        if worker.ident is not None:
            worker.join(2)
