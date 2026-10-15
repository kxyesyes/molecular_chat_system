"""INITIAL coordinator contracts; real SQLite/gate/transport, synthetic HTTP only."""
import asyncio
from contextlib import closing
from dataclasses import replace
import importlib
import json
import threading

import httpx
import pytest

from src.agent.decision_transport import docking_preparation_response_guard
from src.agent.openai_compatible_model import OpenAICompatibleModel
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.system.model_lifecycle import ModelRequestGate
from src.task_runtime.database import connection
from src.task_runtime.docking_consent import DockingConsentPolicy, MAX_PENDING_CONSENTS
from src.task_runtime.store import TaskStore


QUERY = ("run docking; receptor_ref=receptor-1; ligand_ref=ligand-1; "
         "center=[1,2,3]; size=[20,20,20]; exhaustiveness=8; num_modes=9.")
VALUES = dict(receptor_ref="receptor-1", ligand_ref="ligand-1", center=[1, 2, 3],
              size=[20, 20, 20], exhaustiveness=8, num_modes=9)


def api():
    name = "src.web.docking_coordinator"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        pytest.fail("INITIAL docking request coordinator is absent", pytrace=False)


def proposal(query=QUERY, unresolved=False):
    spans = [dict(source_id="query", start=0, end=len(query), role="request")]
    fields = {}
    for key, value in VALUES.items():
        if key + "=" not in query:
            continue
        text = json.dumps(value, separators=(",", ":")) if type(value) is not str else value
        start = query.index(key + "=") + len(key) + 1
        spans.append(dict(source_id="query", start=start, end=start + len(text), role="field"))
        fields[key] = dict(value=value, span_indices=[len(spans) - 1])
    return dict(version="1", kind="docking_request", unresolved=unresolved,
                source_spans=spans, fields=fields)


def wire(mode, value):
    raw = json.dumps(dict(proposal=value))
    message = dict(role="assistant", content=raw)
    if mode == "native":
        message.update(content=None, tool_calls=[dict(id="call-1", type="function",
            function=dict(name="docking_preparation", arguments=raw))])
    return dict(choices=[dict(message=message,
        finish_reason="tool_calls" if mode == "native" else "stop")])


class Rig:
    def __init__(self, path):
        self.api = api()
        self.tasks = TaskStore(path / "tasks.sqlite")
        self.state = SQLiteAgentStateStore(path / "agent.sqlite")
        self.gate = ModelRequestGate()
        self.wall, self.mono = 1000000, 100.0
        self.policy = DockingConsentPolicy("e" * 64, "adapter-1", "local",
                                           "policy-1", "runtime-1", 300, 420)
        self.context = dict(policy=self.policy, model_generation="model-1",
                            capability_generation="capability-1")
        self.calls, self.captures = [], 0
        self.mode, self.output = "native", proposal()
        self.args = dict(root_id="root-1", owner_session_id="owner-1", origin_turn_id="turn-1",
            query=QUERY, history=[dict(role="user", content="private-history-marker")],
            descriptors=[dict(source_id="desc-1", kind="receptor", ref="sha256:" + "a" * 64)],
            enable_tools=True, mode=self.mode)
        self.start()

    def start(self, root="root-1", owner="owner-1", status="running"):
        self.state.start_run(dict(trace_id=root, session_id=owner, status=status,
                                  metadata={"keep": "existing"}), exclusive=True)

    def capture(self):
        assert self.gate._readers > 0, "capture must be inside a reader"
        self.captures += 1
        return dict(self.context, model=self.model)

    def client(self, handler=None):
        async def default(request):
            self.calls.append(request)
            return httpx.Response(200, json=wire(self.mode, self.output))
        return httpx.AsyncClient(transport=httpx.MockTransport(handler or default),
            event_hooks={"response": [docking_preparation_response_guard]})

    def coordinator(self, client, **changes):
        if not hasattr(self, "model"):
            self.model = OpenAICompatibleModel("synthetic-only-key", "test-model",
                                               "https://example.invalid/v1", client=client)
        args = dict(task_store=self.tasks, state_store=self.state, gate=self.gate,
            capture_current=self.capture, wall_time_ms=lambda: self.wall,
            monotonic=lambda: self.mono, enabled=True)
        args.update(changes)
        return self.api.DockingRequestCoordinator(**args)

    async def begin(self, coordinator, **changes):
        return await coordinator.begin(**{**self.args, "mode": self.mode, **changes})

    def metadata(self):
        return self.state.get_run("root-1")["metadata"]

    def rows(self):
        with connection(self.tasks.db_path) as conn:
            return [dict(row) for row in conn.execute("SELECT * FROM docking_consents")]

    def read(self, view):
        return self.tasks.get_chat_docking_draft(preparation_id=view["preparation_id"],
                                                 owner_session_id="owner-1")


def safe(view):
    assert view["input_ready"] is False and view["execution_authorized"] is False
    assert set(view) <= {"root_id", "task_id", "preparation_id", "phase", "reason_code",
                         "missing_fields", "input_ready", "execution_authorized"}
    serialized = json.dumps(view)
    for forbidden in (QUERY, "private-history-marker", "synthetic-only-key", "nonce"):
        assert forbidden not in serialized


@pytest.mark.parametrize("mode", ["native", "json"])
def test_whole_request_real_transport_one_draft_no_scientific_authority(tmp_path, mode):
    rig = Rig(tmp_path)
    rig.mode = mode
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            view = await rig.begin(coordinator)
            safe(view)
            assert view["phase"] == "AWAITING_INPUT"
            assert view["reason_code"] == "source_preparation_required"
            record = rig.read(view)
            assert record["draft"]["budget"] == dict(limit_ms=180000, spent_ms=30000, proposal_slots_spent=1)
            assert all(v == {"state": "pending"} for v in record["draft"]["slots"].values())
            assert record["expires_at_ms"] == 1900000 and record["monotonic_expires"] == 1000.0
            claim = rig.metadata()["chat_docking_root"]
            assert claim["work_deadline_wall_ms"] == 1180000
            assert claim["work_deadline_mono"] == 280.0
            assert claim["identity"]["task_id"] == view["task_id"]
            assert rig.metadata()["keep"] == "existing"
            status = await coordinator.status(root_id="root-1", owner_session_id="owner-1")
            status["missing_fields"].append("mutated")
            assert "mutated" not in (await coordinator.status(root_id="root-1", owner_session_id="owner-1"))["missing_fields"]
            duplicate = await rig.begin(coordinator, query="run docking")
            assert duplicate["task_id"] == view["task_id"]
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-1")
    asyncio.run(run())
    assert len(rig.calls) == 1 and len(rig.rows()) == 1
    source = json.loads(json.loads(rig.calls[0].content)["messages"][-1]["content"])
    assert source == {k: rig.args[k] for k in ("query", "history", "descriptors")}
    durable = json.dumps(rig.metadata()) + json.dumps(rig.rows())
    for value in (QUERY, "private-history-marker", "synthetic-only-key"):
        assert value not in durable
    assert rig.gate._readers == 0
    with connection(rig.tasks.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0


@pytest.mark.parametrize("change", [dict(enable_tools=False), dict(enable_tools=1), dict(mode="other"),
    dict(query="What is docking?"), dict(query="do not run docking"),
    dict(query=QUERY + " predict ADMET"), dict(query="run docking; size=[-1,2,3]"),
    dict(root_id=True), dict(root_id="_root"), dict(owner_session_id="-owner"),
    dict(origin_turn_id="_turn"), dict(owner_session_id="../owner"), dict(origin_turn_id="x" * 129),
    dict(history=()), dict(descriptors=[dict(source_id="d", kind="ligand", ref="https://invalid")])])
def test_bad_inputs_never_read_capture_claim_or_reserve(tmp_path, change, monkeypatch):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            monkeypatch.setattr(rig.state, "_connect", lambda: pytest.fail("unexpected state I/O"))
            view = await rig.begin(coordinator, **change)
            safe(view)
            assert view["phase"] in {"UNAVAILABLE", "AWAITING_INPUT"}
    asyncio.run(run())
    assert not rig.calls and not rig.rows() and rig.captures == 0


def test_feature_default_disabled_no_io(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            args = dict(task_store=rig.tasks, state_store=rig.state, gate=rig.gate, capture_current=rig.capture)
            coordinator = rig.api.DockingRequestCoordinator(**args)
            monkeypatch.setattr(rig.state, "_connect", lambda: pytest.fail("unexpected state I/O"))
            safe(await rig.begin(coordinator))
    asyncio.run(run())
    assert not rig.rows() and not rig.calls


@pytest.mark.parametrize("fault", ["span", "value", "history", "unresolved", "kind"])
def test_full_proposal_validation_keeps_model_uncertainty_nonactionable(tmp_path, fault):
    rig = Rig(tmp_path)
    if fault == "span": rig.output["source_spans"][0]["end"] -= 1
    if fault == "value": rig.output["fields"]["exhaustiveness"]["value"] = 12
    if fault == "history": rig.output["source_spans"][1]["source_id"] = "history-0"
    if fault == "unresolved": rig.output["unresolved"] = True
    if fault == "kind": rig.output = dict(version="1", kind="not_c", unresolved=False, source_spans=[], fields={})
    async def run():
        async with rig.client() as client:
            view = await rig.begin(rig.coordinator(client))
            safe(view)
            assert view["phase"] == "AWAITING_INPUT" and view["reason_code"] == "clarification_required"
    asyncio.run(run())
    assert len(rig.calls) == 1


def test_duplicate_concurrency_has_one_issuer_reservation_and_dispatch(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    issuers = []
    original = rig.api.TextSegmentIssuer
    # Observe construction without replacing the capability's concrete type.
    init = original.__init__
    def counted(self, **kwargs):
        issuers.append(self)
        assert "chat_docking_root" in rig.metadata()
        init(self, **kwargs)
    monkeypatch.setattr(original, "__init__", counted)
    async def run():
        entered, release = asyncio.Event(), asyncio.Event()
        async def handler(request):
            rig.calls.append(request)
            entered.set()
            await release.wait()
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async with rig.client(handler) as client:
            coordinator = rig.coordinator(client)
            first = asyncio.create_task(rig.begin(coordinator))
            await asyncio.wait_for(entered.wait(), 5)
            copies = await asyncio.gather(*(rig.begin(coordinator) for _ in range(12)))
            assert {view["phase"] for view in copies} == {"BUSY"}
            assert len({view["task_id"] for view in copies}) == 1
            release.set()
            result = await first
            assert result["task_id"] == copies[0]["task_id"]
    asyncio.run(run())
    assert len(issuers) == len(rig.calls) == len(rig.rows()) == 1


@pytest.mark.parametrize("stage", ["claim_before", "claim_after", "draft_before", "draft_after"])
def test_uncertain_sql_never_recovers_receipt_or_retries(tmp_path, stage, monkeypatch):
    rig = Rig(tmp_path)
    attempts = []
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            target = coordinator if stage.startswith("claim") else rig.tasks
            name = "_claim_root" if stage.startswith("claim") else "reserve_chat_docking_draft"
            original = getattr(target, name)
            def fault(**kwargs):
                attempts.append(1)
                if stage.endswith("after"):
                    original(**kwargs)
                raise RuntimeError("private-history-marker synthetic-only-key")
            monkeypatch.setattr(target, name, fault)
            result = await rig.begin(coordinator)
            safe(result)
            assert result["phase"] == "UNAVAILABLE"
            again = await rig.begin(coordinator)
            safe(again)
            assert len(attempts) == 1
            if stage != "claim_before":
                monkeypatch.setattr(target, name, original)
                restored = await rig.begin(rig.coordinator(client))
                assert restored["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    assert not rig.calls
    assert ("chat_docking_root" in rig.metadata()) == (stage != "claim_before")
    assert len(rig.rows()) == (1 if stage == "draft_after" else 0)


@pytest.mark.parametrize("changed", [False, True])
def test_new_coordinator_never_restores_claimed_root(tmp_path, changed):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            first = await rig.begin(coordinator)
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-1")
            if changed:
                rig.context["policy"] = replace(rig.policy, runtime_generation="runtime-2")
            second = await rig.begin(rig.coordinator(client))
            safe(second)
            assert second["phase"] == "UNAVAILABLE"
            assert rig.rows()[0]["task_id"] == first["task_id"]
    asyncio.run(run())
    assert len(rig.calls) == 1


@pytest.mark.parametrize("fault", ["unknown", "owner", "completed"])
def test_begin_requires_existing_owned_running_run(tmp_path, fault):
    rig = Rig(tmp_path)
    if fault == "completed":
        with closing(rig.state._connect()) as conn, conn:
            conn.execute("UPDATE agent_runs SET status='completed' WHERE trace_id='root-1'")
    async def run():
        async with rig.client() as client:
            changes = {"root_id": "unknown"} if fault == "unknown" else {"owner_session_id": "foreign"} if fault == "owner" else {}
            result = await rig.begin(rig.coordinator(client), **changes)
            assert result["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    assert not rig.calls and not rig.rows() and "chat_docking_root" not in rig.metadata()


def test_foreign_unknown_status_close_fail_before_capture_or_sql(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            captures = rig.captures
            monkeypatch.setattr(rig.state, "_connect", lambda: pytest.fail("foreign read"))
            for method in (coordinator.status, coordinator.close_root):
                for root, owner in [("unknown", "owner-1"), ("root-1", "foreign")]:
                    result = await method(root_id=root, owner_session_id=owner)
                    assert result["phase"] == "UNAVAILABLE" and result["task_id"] is None
            assert rig.captures == captures
    asyncio.run(run())


@pytest.mark.parametrize("switch", ["model", "policy", "model_generation", "capability_generation", "runtime"])
def test_mid_model_generation_switch_cannot_publish(tmp_path, switch):
    rig = Rig(tmp_path)
    async def run():
        async def handler(request):
            rig.calls.append(request)
            if switch == "model": rig.model = object()
            elif switch == "policy": rig.context["policy"] = replace(rig.policy, vina_limit_seconds=299)
            elif switch == "runtime": rig.context["policy"] = replace(rig.policy, runtime_generation="runtime-2")
            else: rig.context[switch] = "changed"
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async with rig.client(handler) as client:
            view = await rig.begin(rig.coordinator(client))
            safe(view)
            assert view["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    assert len(rig.calls) == 1 and rig.gate._readers == 0


def test_cancel_and_close_drain_late_model_before_releasing_reader(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        entered, release, writer_entered = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def handler(request):
            rig.calls.append(request)
            entered.set()
            # A physically late transport which ignores cancellation until cleanup.
            while not release.is_set():
                try: await release.wait()
                except asyncio.CancelledError: pass
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async def writer():
            async with rig.gate.exclusive(): writer_entered.set()
        async with rig.client(handler) as client:
            coordinator = rig.coordinator(client)
            task = asyncio.create_task(rig.begin(coordinator))
            await asyncio.wait_for(entered.wait(), 5)
            task.cancel()
            close = asyncio.create_task(coordinator.close_root(root_id="root-1", owner_session_id="owner-1"))
            write = asyncio.create_task(writer())
            await asyncio.sleep(.03)
            task.cancel()
            assert not task.done() and not close.done() and not writer_entered.is_set()
            assert rig.gate._readers == 1
            release.set()
            with pytest.raises(asyncio.CancelledError): await task
            assert (await close)["phase"] == "CLOSED"
            await write
            assert (await rig.begin(coordinator))["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    assert rig.gate._readers == 0 and len(rig.calls) == 1


@pytest.mark.parametrize("delay", [170000, 180001])
def test_original_work_deadline_includes_sql_queue_not_store_waiting_expiry(tmp_path, delay, monkeypatch):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            original = coordinator._claim_root
            def delayed(**kwargs):
                rig.wall += delay
                rig.mono += delay / 1000
                return original(**kwargs)
            monkeypatch.setattr(coordinator, "_claim_root", delayed)
            view = await rig.begin(coordinator)
            safe(view)
            if delay < 180000:
                assert rig.read(view)["draft"]["budget"]["spent_ms"] == 10000
                assert rig.read(view)["expires_at_ms"] == rig.wall + 900000
                rig.mono += 11
                expired = await coordinator.status(root_id="root-1", owner_session_id="owner-1")
                assert expired["phase"] == "UNAVAILABLE"
                assert rig.rows()[0]["expires_at_ms"] == rig.wall + 900000
            else:
                assert view["phase"] == "UNAVAILABLE" and not rig.rows()
    asyncio.run(run())
    assert len(rig.calls) == (1 if delay < 180000 else 0)


@pytest.mark.parametrize("committed", [False, True])
def test_cancelled_sql_retains_capacity_until_actual_completion(tmp_path, monkeypatch, committed):
    rig = Rig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            original = coordinator._claim_root
            def blocked(**kwargs):
                receipt = original(**kwargs) if committed else None
                entered.set()
                assert release.wait(5)
                return receipt if committed else original(**kwargs)
            monkeypatch.setattr(coordinator, "_claim_root", blocked)
            task = asyncio.create_task(rig.begin(coordinator))
            assert await asyncio.to_thread(entered.wait, 5)
            task.cancel()
            await asyncio.sleep(.02)
            assert not task.done() and rig.gate._readers == 1
            duplicate = await rig.begin(coordinator)
            safe(duplicate)
            assert duplicate["phase"] in {"BUSY", "UNAVAILABLE", "CLOSED"}
            release.set()
            with pytest.raises(asyncio.CancelledError): await task
            assert not rig.calls and not rig.rows()
            assert ("chat_docking_root" in rig.metadata()) is committed
            assert (await rig.begin(coordinator))["phase"] == "UNAVAILABLE"
            if committed:
                assert (await rig.begin(rig.coordinator(client)))["phase"] == "UNAVAILABLE"
    try: asyncio.run(run())
    finally: release.set()


def test_failed_attempt_tombstones_saturate_without_eviction(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    attempts = []
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            def fail(**kwargs):
                attempts.append(1)
                raise RuntimeError("uncommitted")
            monkeypatch.setattr(coordinator, "_claim_root", fail)
            for index in range(MAX_PENDING_CONSENTS + 2):
                root, owner = f"root-{index}", f"owner-{index}"
                if index != 1: rig.start(root, owner)
                view = await rig.begin(coordinator, root_id=root, owner_session_id=owner)
                assert view["phase"] == "UNAVAILABLE"
            assert len(attempts) == MAX_PENDING_CONSENTS
            await rig.begin(coordinator, root_id="root-0", owner_session_id="owner-0")
            assert len(attempts) == MAX_PENDING_CONSENTS
            # Closing a failed attempt must NOT make its identity reusable.
            await coordinator.close_root(root_id="root-0", owner_session_id="owner-0")
            await rig.begin(coordinator, root_id="root-0", owner_session_id="owner-0")
            assert len(attempts) == MAX_PENDING_CONSENTS
    asyncio.run(run())
    assert not rig.rows() and "chat_docking_root" not in rig.metadata()


@pytest.mark.parametrize("fault", ["completed", "owner", "removed_claim"])
def test_live_root_invalidation_during_model_cannot_publish(tmp_path, fault):
    rig = Rig(tmp_path)
    async def run():
        async def handler(request):
            rig.calls.append(request)
            with closing(rig.state._connect()) as conn, conn:
                if fault == "completed":
                    conn.execute("UPDATE agent_runs SET status='completed' WHERE trace_id='root-1'")
                elif fault == "owner":
                    conn.execute("UPDATE agent_runs SET session_id='other' WHERE trace_id='root-1'")
                else:
                    data = rig.metadata()
                    data.pop("chat_docking_root")
                    conn.execute("UPDATE agent_runs SET metadata_json=? WHERE trace_id='root-1'", (json.dumps(data),))
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async with rig.client(handler) as client:
            result = await rig.begin(rig.coordinator(client))
            safe(result)
            assert result["phase"] == "UNAVAILABLE"
    asyncio.run(run())


def test_two_coordinators_concurrently_cannot_reconstruct_second_issuer(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    count = []
    init = rig.api.TextSegmentIssuer.__init__
    def counted(self, **kwargs):
        count.append(1)
        init(self, **kwargs)
    monkeypatch.setattr(rig.api.TextSegmentIssuer, "__init__", counted)
    async def run():
        async with rig.client() as client:
            results = await asyncio.gather(rig.begin(rig.coordinator(client)), rig.begin(rig.coordinator(client)))
            assert sorted(result["phase"] for result in results) == ["AWAITING_INPUT", "UNAVAILABLE"]
    asyncio.run(run())
    assert len(count) == len(rig.calls) == len(rig.rows()) == 1


@pytest.mark.parametrize("commit", [False, True])
def test_real_root_transaction_commit_boundary_never_issues_on_lost_receipt(tmp_path, monkeypatch, commit):
    rig = Rig(tmp_path)
    original = rig.state._connect
    writes = []
    class Connection:
        def __init__(self):
            self.conn = original()
            self.wrote = False
        def __getattr__(self, name): return getattr(self.conn, name)
        def __enter__(self):
            self.conn.__enter__()
            return self
        def execute(self, sql, args=()):
            if sql.startswith("UPDATE agent_runs SET metadata_json"):
                self.wrote = True
                writes.append(1)
            return self.conn.execute(sql, args)
        def __exit__(self, *args):
            if self.wrote:
                if commit: self.conn.__exit__(*args)
                else: self.conn.rollback()
                raise OSError("synthetic-only-key lost transaction receipt")
            return self.conn.__exit__(*args)
    monkeypatch.setattr(rig.state, "_connect", Connection)
    def forbidden(*args, **kwargs): pytest.fail("issuer before commit receipt")
    monkeypatch.setattr(rig.api.TextSegmentIssuer, "__init__", forbidden)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            assert (await rig.begin(coordinator))["phase"] == "UNAVAILABLE"
            assert (await rig.begin(coordinator))["phase"] == "UNAVAILABLE"
            assert len(writes) == 1
    asyncio.run(run())
    assert not rig.calls and not rig.rows()
    assert ("chat_docking_root" in rig.metadata()) is commit


def test_real_sqlite_lock_queue_counts_against_original_work_deadline(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    reached = threading.Event()
    original = rig.state._connect
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            with closing(original()) as blocker:
                blocker.execute("BEGIN IMMEDIATE")
                def connect():
                    value = original()
                    reached.set()
                    return value
                monkeypatch.setattr(rig.state, "_connect", connect)
                task = asyncio.create_task(rig.begin(coordinator))
                assert await asyncio.to_thread(reached.wait, 5)
                await asyncio.sleep(.02)
                assert not task.done()
                rig.wall += 180001
                rig.mono += 180.001
                blocker.rollback()
                assert (await task)["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    assert not rig.calls and not rig.rows() and "chat_docking_root" not in rig.metadata()


def test_captured_inputs_detach_before_first_await_and_no_reader_in_human_wait(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            async with rig.gate.exclusive():
                task = asyncio.create_task(rig.begin(coordinator))
                await asyncio.sleep(.02)
                rig.args["history"][0]["content"] = "changed-history"
                rig.args["descriptors"][0]["ref"] = "changed-ref"
            result = await task
            assert result["phase"] == "AWAITING_INPUT" and rig.gate._readers == 0
            async with rig.gate.exclusive():
                rig.context["policy"] = replace(rig.policy, runtime_generation="runtime-2")
            assert (await coordinator.status(root_id="root-1", owner_session_id="owner-1"))["phase"] == "UNAVAILABLE"
            assert (await rig.begin(coordinator))["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    source = json.loads(json.loads(rig.calls[0].content)["messages"][-1]["content"])
    assert source["history"][0]["content"] == "private-history-marker"
    assert source["descriptors"][0]["ref"] == "sha256:" + "a" * 64
    assert len(rig.calls) == 1


@pytest.mark.parametrize("clock,value", [("wall", True), ("wall", -1), ("wall", 1.0),
    ("mono", True), ("mono", float("inf")), ("mono", float("nan")), ("mono", -1),
    ("mono", 10**1000), ("mono", 1e308)])
def test_invalid_clocks_fail_closed_before_io(tmp_path, monkeypatch, clock, value):
    rig = Rig(tmp_path)
    setattr(rig, clock, value)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            monkeypatch.setattr(rig.state, "_connect", lambda: pytest.fail("invalid clock I/O"))
            assert (await rig.begin(coordinator))["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    assert not rig.calls and not rig.rows()


def test_generation_switch_between_readers_prevents_dispatch(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    propose = rig.api.DockingProposalOwner.propose
    async def switched(self, **kwargs):
        assert rig.gate._readers == 0, "coordinator must release before proposal owner reader"
        async with rig.gate.exclusive():
            rig.context["model_generation"] = "model-2"
        return await propose(self, **kwargs)
    monkeypatch.setattr(rig.api.DockingProposalOwner, "propose", switched)
    async def run():
        async with rig.client() as client:
            assert (await rig.begin(rig.coordinator(client)))["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    assert not rig.calls and len(rig.rows()) == 1


def test_closed_durable_roots_release_heavy_capacity_without_replay(tmp_path):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            for index in range(MAX_PENDING_CONSENTS + 1):
                root, owner = f"root-{index}", f"owner-{index}"
                if index != 1: rig.start(root, owner)
                view = await rig.begin(coordinator, root_id=root, owner_session_id=owner)
                assert view["phase"] == "AWAITING_INPUT"
                await coordinator.close_root(root_id=root, owner_session_id=owner)
                # Actual waiting expiry, never faked or reset to reclaim capacity.
                rig.wall += 900001
                rig.mono += 900.001
            assert (await rig.begin(coordinator, root_id="root-0", owner_session_id="owner-0"))["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    assert len(rig.calls) == MAX_PENDING_CONSENTS + 1


def test_close_drains_status_sql_before_dropping_live_context(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            await rig.begin(coordinator)
            original = coordinator._check_root
            def blocked(**kwargs):
                entered.set()
                assert release.wait(5)
                return original(**kwargs)
            monkeypatch.setattr(coordinator, "_check_root", blocked)
            status = asyncio.create_task(coordinator.status(root_id="root-1", owner_session_id="owner-1"))
            assert await asyncio.to_thread(entered.wait, 5)
            close = asyncio.create_task(coordinator.close_root(root_id="root-1", owner_session_id="owner-1"))
            try:
                await asyncio.sleep(.02)
                assert not close.done(), "close detached an actual status SQL worker"
                assert rig.gate._readers == 1
            finally:
                release.set()
                await asyncio.gather(status, close, return_exceptions=True)
            assert close.result()["phase"] == "CLOSED"
    try: asyncio.run(run())
    finally: release.set()


def test_duplicate_does_not_recapture_original_snapshot(tmp_path, monkeypatch):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            first = await rig.begin(coordinator)
            def forbidden(**kwargs): pytest.fail("duplicate snapshot reconstruction")
            monkeypatch.setattr(rig.api, "capture_source_snapshot", forbidden)
            duplicate = await rig.begin(coordinator)
            assert duplicate["preparation_id"] == first["preparation_id"]
    asyncio.run(run())
    assert len(rig.calls) == 1


_REPLACEMENT_IDENTITY = dict(task_id="different-committed-task", preparation_id="different-preparation",
    admission_revision="different-admission", trace_id="different-root", owner_session_id="different-owner",
    origin_turn_id="different-turn", query_digest="b" * 64, refinement_revision=1)


def replace_persisted_identity(rig, field):
    with connection(rig.tasks.db_path) as conn:
        row = conn.execute("SELECT preparation_id,identity_json FROM docking_consents").fetchone()
        identity = json.loads(row["identity_json"])
        identity[field] = _REPLACEMENT_IDENTITY[field]
        conn.execute("UPDATE docking_consents SET identity_json=? WHERE preparation_id=?",
                     (json.dumps(identity), row["preparation_id"]))


@pytest.mark.parametrize("field", list(_REPLACEMENT_IDENTITY))
def test_reservation_receipt_must_match_complete_root_identity(tmp_path, monkeypatch, field):
    rig = Rig(tmp_path)
    reserve = rig.tasks.reserve_chat_docking_draft
    attempts, proposals = [], []
    def replaced(**kwargs):
        attempts.append(1)
        kwargs["identity"] = {**kwargs["identity"], field: _REPLACEMENT_IDENTITY[field]}
        return reserve(**kwargs)  # Real successful SQLite commit and actual receipt.
    monkeypatch.setattr(rig.tasks, "reserve_chat_docking_draft", replaced)
    propose = rig.api.DockingProposalOwner.propose
    async def observed(self, **kwargs):
        proposals.append(1)
        return await propose(self, **kwargs)
    monkeypatch.setattr(rig.api.DockingProposalOwner, "propose", observed)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            result = await rig.begin(coordinator)
            safe(result)
            assert not rig.calls, "replacement identity dispatched a model call"
            assert not proposals, "invalid receipt entered the proposal owner"
            assert result["phase"] == "UNAVAILABLE"
            assert (await rig.begin(coordinator))["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    assert len(attempts) == len(rig.rows()) == 1
    assert not any(k.startswith("docking_proposal_") for k in rig.metadata())


@pytest.mark.parametrize("field", ["task_id", "preparation_id", "admission_revision"])
@pytest.mark.parametrize("when", ["before_proposal", "budget_receipt", "after_model", "owner_return"])
def test_persisted_identity_rechecked_before_dispatch_and_publication(tmp_path, monkeypatch, field, when):
    rig = Rig(tmp_path)
    if when in {"before_proposal", "budget_receipt"}:
        name = "reserve_chat_docking_draft" if when == "before_proposal" else "reserve_docking_draft_budget"
        original = getattr(rig.tasks, name)
        def changed(**kwargs):
            receipt = original(**kwargs)
            replace_persisted_identity(rig, field)
            return receipt  # The formerly valid receipt must not mask durable drift.
        monkeypatch.setattr(rig.tasks, name, changed)
    if when == "owner_return":
        original_propose = rig.api.DockingProposalOwner.propose
        async def changed_after_owner(self, **kwargs):
            result = await original_propose(self, **kwargs)
            assert result.available
            replace_persisted_identity(rig, field)
            return result
        monkeypatch.setattr(rig.api.DockingProposalOwner, "propose", changed_after_owner)
    async def run():
        async def handler(request):
            rig.calls.append(request)
            if when == "after_model": replace_persisted_identity(rig, field)
            return httpx.Response(200, json=wire(rig.mode, rig.output))
        async with rig.client(handler) as client:
            result = await rig.begin(rig.coordinator(client))
            safe(result)
            assert result["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    assert len(rig.calls) == (1 if when in {"after_model", "owner_return"} else 0)


@pytest.mark.parametrize("fault", ["expired", "deleted", "foreign", "revoked", "task_id",
                                   "preparation_id", "admission_revision"])
def test_refresh_rejects_unavailable_or_rebound_draft(tmp_path, fault):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            first = await rig.begin(coordinator)
            assert first["phase"] == "AWAITING_INPUT"
            if fault == "expired":
                rig.tasks.expire_docking_consents(now_ms=rig.wall, monotonic_now=rig.mono,
                    runtime_generation="runtime-invalidated", policy_generation=rig.policy.policy_generation)
            elif fault in {"deleted", "foreign", "revoked"}:
                with connection(rig.tasks.db_path) as conn:
                    sql = {"deleted": "DELETE FROM docking_consents",
                           "foreign": "UPDATE docking_consents SET owner_session_id='foreign'",
                           "revoked": "UPDATE docking_consents SET state='REVOKED'"}[fault]
                    conn.execute(sql)
            else:
                replace_persisted_identity(rig, fault)
            result = await coordinator.status(root_id="root-1", owner_session_id="owner-1")
            safe(result)
            assert result["phase"] == "UNAVAILABLE"
            assert (await rig.begin(coordinator))["phase"] == "UNAVAILABLE"
    asyncio.run(run())
    assert len(rig.calls) == 1 and rig.gate._readers == 0


@pytest.mark.parametrize("phase", ["initial", "publication", "refresh"])
def test_draft_revalidation_sql_retains_reader_through_close(tmp_path, monkeypatch, phase):
    rig = Rig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            original = coordinator._read_draft
            reads = []
            def blocked(**kwargs):
                receipt = original(**kwargs)
                reads.append(1)
                if len(reads) == {"initial": 1, "publication": 2, "refresh": 3}[phase]:
                    entered.set()
                    assert release.wait(5)
                return receipt
            monkeypatch.setattr(coordinator, "_read_draft", blocked)
            if phase == "refresh":
                assert (await rig.begin(coordinator))["phase"] == "AWAITING_INPUT"
                caller = asyncio.create_task(coordinator.status(root_id="root-1", owner_session_id="owner-1"))
            else:
                caller = asyncio.create_task(rig.begin(coordinator))
            assert await asyncio.to_thread(entered.wait, 5)
            close = asyncio.create_task(coordinator.close_root(root_id="root-1", owner_session_id="owner-1"))
            try:
                await asyncio.sleep(.02)
                caller.cancel()
                await asyncio.sleep(.02)
                assert not caller.done() and not close.done() and rig.gate._readers == 1
            finally:
                release.set()
                await asyncio.gather(caller, close, return_exceptions=True)
            assert caller.cancelled() and close.result()["phase"] == "CLOSED"
    try: asyncio.run(run())
    finally: release.set()
    assert len(rig.calls) == (0 if phase == "initial" else 1)
    assert rig.gate._readers == 0


@pytest.mark.parametrize("phase", ["publication", "refresh"])
@pytest.mark.parametrize("fault", ["completed", "foreign", "removed_claim"])
def test_root_invalidated_after_draft_sql_cannot_publish_waiting(tmp_path, monkeypatch, phase, fault):
    rig = Rig(tmp_path)
    entered, release = threading.Event(), threading.Event()
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            original = coordinator._read_draft
            reads = []
            def blocked(**kwargs):
                record = original(**kwargs)  # Block AFTER actual SQLite read/validation.
                reads.append(1)
                if len(reads) == {"publication": 2, "refresh": 3}[phase]:
                    entered.set()
                    assert release.wait(5)
                return record
            monkeypatch.setattr(coordinator, "_read_draft", blocked)
            if phase == "refresh":
                assert (await rig.begin(coordinator))["phase"] == "AWAITING_INPUT"
                caller = asyncio.create_task(coordinator.status(root_id="root-1", owner_session_id="owner-1"))
            else:
                caller = asyncio.create_task(rig.begin(coordinator))
            try:
                assert await asyncio.to_thread(entered.wait, 5)
                assert not caller.done() and rig.gate._readers == 1
                with closing(rig.state._connect()) as conn, conn:
                    if fault == "completed":
                        conn.execute("UPDATE agent_runs SET status='completed' WHERE trace_id='root-1'")
                    elif fault == "foreign":
                        conn.execute("UPDATE agent_runs SET session_id='foreign' WHERE trace_id='root-1'")
                    else:
                        metadata = rig.metadata()
                        metadata.pop("chat_docking_root")
                        conn.execute("UPDATE agent_runs SET metadata_json=? WHERE trace_id='root-1'",
                                     (json.dumps(metadata),))
            finally:
                release.set()
                await asyncio.gather(caller, return_exceptions=True)
            result = caller.result()
            safe(result)
            assert result["phase"] == "UNAVAILABLE"
            assert result["reason_code"] != "source_preparation_required"
            assert (await rig.begin(coordinator))["phase"] == "UNAVAILABLE"
            assert (await coordinator.status(root_id="root-1", owner_session_id="owner-1"))["phase"] == "UNAVAILABLE"
    try: asyncio.run(run())
    finally: release.set()
    assert len(rig.calls) == len(rig.rows()) == 1
    assert rig.gate._readers == 0


@pytest.mark.parametrize("phase", ["initial", "publication", "refresh"])
def test_draft_revalidation_sql_cannot_extend_work_deadline(tmp_path, monkeypatch, phase):
    rig = Rig(tmp_path)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            original = coordinator._read_draft
            reads = []
            def delayed(**kwargs):
                receipt = original(**kwargs)
                reads.append(1)
                if len(reads) == {"initial": 1, "publication": 2, "refresh": 3}[phase]:
                    rig.wall += 180001
                    rig.mono += 180.001
                return receipt
            monkeypatch.setattr(coordinator, "_read_draft", delayed)
            result = await rig.begin(coordinator)
            if phase == "refresh":
                assert result["phase"] == "AWAITING_INPUT"
                result = await coordinator.status(root_id="root-1", owner_session_id="owner-1")
            assert result["phase"] == "UNAVAILABLE"
            assert rig.metadata()["chat_docking_root"]["work_deadline_wall_ms"] == 1180000
            assert rig.rows()[0]["expires_at_ms"] == 1900000
    asyncio.run(run())
    assert len(rig.calls) == (0 if phase == "initial" else 1)
