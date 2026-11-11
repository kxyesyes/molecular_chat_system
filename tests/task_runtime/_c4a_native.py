"""Opt-in Windows qualification, always inside the bounded CLI Job."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
from types import SimpleNamespace
from uuid import uuid4

import pytest

from scripts import run_c4a_acceptance as contract
from tests.task_runtime import _c4a_sticky_retention_child as child
from tests.task_runtime.test_docking_consent import RECEPTOR, LIGAND


def _c4a_sticky_db_files(root):
    # An independent read-only connection, not the child's report/runtime/store.
    # No payload, owner, nonce, path or error text leaves this fixed observer.
    connection = sqlite3.connect((root / "tasks.sqlite").as_uri() + "?mode=ro", uri=True, timeout=1)
    try:
        row = connection.execute(
            "SELECT execution_occupied,cleanup_state,view_status,primary_reason "
            "FROM docking_consents WHERE preparation_id=? AND task_id=?",
            ("preparation-1", "c-task-1"),
        ).fetchone()
        task = connection.execute("SELECT status FROM tasks WHERE task_id=?", ("c-task-1",)).fetchone()
    finally:
        connection.close()
    assert row is not None and row[0] == 1 and row[1] in {"pending", "unresolved"}
    assert row[2:] == ("CANCELLED", "consent_cancelled")
    assert task is not None and task[0] == "cancel_requested"
    inputs = root / "stage" / "c-task-1" / "inputs"
    assert (inputs / "receptor.pdb").read_bytes() == RECEPTOR
    assert (inputs / "ligand.sdf").read_bytes() == LIGAND
    assert (root / "synthetic-output" / "docking_c-task-1").is_dir()
    assert (root / "synthetic-output" / "docking_neighbor" / "keep").read_bytes() == b"C4a neighbor unchanged"
    return {"occupied": row[0], "cleanup_unsettled": row[1] != "settled",
            "cancelled": row[2] == "CANCELLED", "reason_preserved": row[3] == "consent_cancelled",
            "inputs_unchanged": True, "neighbor_unchanged": True}



def test_late_popen_return(tmp_path, monkeypatch, request):
    """A real suspended root exists before startup can hand its Popen back."""
    contract.verify_suite_job()
    from src.docking.adapters import base
    from tests.test_docking_command_cancellation import _C4aPhysicalResources, _c4a_scope, _c4a_command

    resources = _C4aPhysicalResources(base, monkeypatch)
    request.addfinalizer(resources.cleanup)
    scope = _c4a_scope(tmp_path, resources.cancel)
    created, release = threading.Event(), resources.barrier()

    def withhold(process):
        created.set()
        assert release.wait(15)

    resources.popen_after = withhold
    thread, finished, outcome = resources.invoke(lambda: _c4a_command(tmp_path, scope, resources))
    try:
        assert created.wait(5)
        process, = resources.processes
        assert process.poll() is None and resources.resumes == []
        resources.cancel.set()
        assert finished.wait(5)
        resources.joined(thread)
        assert isinstance(outcome.get("error"), (base.CommandCancelledError, base.CommandOwnershipUncertainError))
        scope.seal()
        settler, done, settlement = resources.invoke(scope.settle)
        assert not done.wait(.1) and scope.snapshot()["pending_count"] == 1
        release.set()
        resources.joined(settler)
        assert settlement == {"value": True}
        assert resources.resumes == [] and process.returncode is not None
        resources.assert_physically_stopped()
    finally:
        release.set()


# Fresh interpreter per invocation. Advance only after pre-rescue oracles AND cleanup pass.
qualified = []


@pytest.mark.parametrize("case", (*contract.FINITE, *contract.STICKY))
def test_native_case(tmp_path, request, case):
    if os.name != "nt":
        pytest.skip("Windows nested Job Objects required")
    from src.docking.adapters import base
    from tests.test_docking_command_cancellation import _C4aPhysicalResources

    contract.verify_suite_job()
    sticky = case in contract.STICKY
    if sticky:
        assert tuple(qualified) == contract.FINITE, "current finite qualification required"
    mode = case if sticky or case == "pipe-descendant" else "clean"
    argv = contract.fixed_command(mode)
    contract.source_digest()
    private = tmp_path / "payload"
    private.mkdir()
    boot = tmp_path / "bootstrap"
    boot.mkdir()
    challenge = uuid4().hex
    if sticky:
        packet = (json.dumps(dict(version="C4A_STICKY_V1", mode=mode, challenge=challenge,
                                  root=str(private))) + "\n").encode()
        child._decode_sticky_guard(packet, mode)
    else:
        packet = contract.finite_packet(private, mode, challenge)
        child._decode_guard(packet, mode)
    if case == "invalid-guard":
        packet = b"{}\n"
    patcher = pytest.MonkeyPatch()
    resources = _C4aPhysicalResources(base, patcher)
    state = SimpleNamespace(pair=None, capture=None, readers=(), empty=False, closed=False,
                            primary=None, cleanup_error=None, close_error=None,
                            local_cleanup=False, observation_error=None, proof=None, independent=None,
                            root_exited_with_descendant=False, child_failure=None)
    original_close = base._WindowsJob.close
    fault_close = case in ("guard-write-cleanup-failure", "close-error-after-root")

    def observed_close(job):
        if job.handle:
            try:
                state.empty = not job.has_active_processes()
            except Exception as error:
                state.observation_error = error
            resources.job_close_fault = fault_close
            try:
                result = original_close(job)
            except Exception as error:
                state.close_error = error
                raise
            state.closed = True
            return result
        return original_close(job)
    patcher.setattr(base._WindowsJob, "close", observed_close)

    class GuardedAdapter(base.CommandAdapter):
        @classmethod
        def _create_windows_suspended(cls, args, cwd, popen_factory=None):
            def guarded_popen(actual, **kwargs):
                assert actual == argv and kwargs["cwd"] == str(boot)
                process = popen_factory(actual, **dict(kwargs, stdin=subprocess.PIPE,
                                                       env=dict(contract.isolated_env(boot),
                                                                MEDCHAT_C4A_SUITE_JOB=os.environ["MEDCHAT_C4A_SUITE_JOB"])))
                resources.streams.append(process.stdin)
                return process

            job, process = super()._create_windows_suspended(args, cwd, popen_factory=guarded_popen)
            try:
                state.pair = job, process
                state.capture = process._medchat_capture_state
                state.readers = tuple(state.capture["threads"])
                if case.startswith("guard-write-"):
                    process.stdin.close()
                if process.stdin.write(packet) != len(packet):
                    raise RuntimeError("c4a_guard_short_write")
                process.stdin.flush()
                process.stdin.close()
                assert process.stdin.closed
            except BaseException as primary:
                state.primary = primary
                try:
                    cls._cleanup_windows_process(job, process, assigned=True)
                    state.local_cleanup = True
                except BaseException as secondary:
                    state.cleanup_error = secondary
                finally:
                    process.stdin.close()
                raise
            return job, process

    def read_proof():
        with (private / "proof.json").open("rb") as stream:
            data = stream.read(child.REPORT_LIMIT + 1)
        parse = child._decode_sticky_report if sticky else child._decode_report
        return parse(data, mode, challenge)

    stop = threading.Event()

    def observe_before_cancel():
        deadline = time.monotonic() + 45
        try:
            while not stop.is_set() and time.monotonic() < deadline:
                if (private / "failure.json").is_file():
                    with (private / "failure.json").open("rb") as stream:
                        state.child_failure = json.loads(stream.read(1024))
                    raise AssertionError("c4a_child_failed")
                if (private / "proof.json").is_file():
                    proof = read_proof()
                    job, process = state.pair
                    if sticky:
                        assert process.poll() is None and job.has_active_processes()
                        state.independent = _c4a_sticky_db_files(private)
                        state.proof = proof
                        return
                    if (process.poll() == 0 and job.has_active_processes() and
                            any(thread.is_alive() for thread in state.readers)):
                        state.root_exited_with_descendant = True
                        state.proof = proof
                        return
                stop.wait(.01)
            raise AssertionError("c4a_report_timeout")
        except BaseException as error:
            state.observation_error = error
        finally:
            resources.cancel.set()

    monitor = None
    caught = result = None
    try:
        if sticky or case == "pipe-descendant":
            monitor = threading.Thread(target=observe_before_cancel, name="c4a-test-report")
            monitor.start()
        try:
            result = GuardedAdapter().run(argv, cwd=str(boot), timeout=60, cancel_event=resources.cancel)
        except Exception as error:
            caught = error
        finally:
            resources.cancel.set()
        for thread in tuple(resources.threads):
            if thread.name == "docking-command-spawn" and thread.ident is not None:
                resources.joined(thread)
        for thread in tuple(resources.threads):
            if thread.ident is not None:
                resources.joined(thread)
        assert len(resources.processes) == len(resources.jobs) == 1
        process = resources.processes[0]
        assert state.capture is not None and len(state.readers) == 2
        assert tuple(state.capture["threads"]) == state.readers
        assert all(t in resources.actual_joins and not t.is_alive() for t in state.readers)
        facts = {
            "job_empty_before_close": state.empty,
            "job_closed": state.closed and len(resources.job_closes) == 1,
            "process_exited": process.returncode is not None,
            "process_handle_closed": process._handle is None,
            "streams_closed": all(s.closed for s in resources.streams),
            "threads_joined": all(not t.is_alive() for t in resources.threads)
                and all(t in resources.actual_joins for t in resources.threads
                        if t.name.startswith("docking-command-")),
            "capture_clean": not state.capture["errors"] and not state.capture["exceeded"].is_set(),
            "no_observer_error": state.observation_error is None,
        }
        if state.child_failure:
            request.node.user_properties.append(("c4a_child_failure", state.child_failure))
        if state.observation_error:
            raise state.observation_error
        if fault_close:
            assert state.close_error is not None and resources.job_closes == []
            assert all(facts[key] is True for key in facts if key != "job_closed")
            with pytest.raises(AssertionError, match="c4a_finite_containment_unconfirmed"):
                contract.require_containment(facts)
        else:
            contract.require_containment(facts)
            resources.assert_physically_stopped()
        if case.startswith("guard-write-"):
            assert isinstance(state.primary, ValueError) and caught.__cause__ is state.primary
            assert not resources.resumes and not (private / "proof.json").exists()
            if fault_close:
                assert not state.local_cleanup and state.cleanup_error.__cause__ is state.close_error
            else:
                assert state.local_cleanup and state.cleanup_error is None
        elif case == "invalid-guard":
            assert caught is None and result.returncode == 1 and len(resources.resumes) == 1
            assert (boot / "invalid-guard-rejected").is_file() and not (private / "proof.json").exists()
        elif sticky or case == "pipe-descendant":
            assert isinstance(caught, base.CommandCancelledError) and len(resources.resumes) == 1
            assert state.primary is state.cleanup_error is None and state.proof == read_proof()
            if sticky:
                assert _c4a_sticky_db_files(private) == state.independent
                assert not (private / "proof.tmp").exists()
            else:
                assert state.root_exited_with_descendant
        else:
            assert caught is None and result.returncode == 0 and len(resources.resumes) == 1
            assert read_proof()["guarded"] is True
    finally:
        resources.cancel.set()
        stop.set()
        try:
            if monitor is not None and monitor.ident is not None:
                monitor.join(10)
                assert not monitor.is_alive()
        finally:
            resources.cleanup()
    if sticky:
        request.node.user_properties.extend([
            ("production_retention_verified", state.proof is not None and state.independent is not None),
            ("test_child_containment_verified", all(facts.values())),
        ])
    else:
        qualified.append(case)
