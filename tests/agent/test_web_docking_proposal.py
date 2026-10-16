"""Proposal ownership only: real stores/gate/transport, synthetic HTTP I/O."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import FrozenInstanceError, replace
import importlib
import json
import multiprocessing
import pickle
import threading
from types import SimpleNamespace

import httpx
import pytest

from src.agent.decision_transport import docking_preparation_response_guard
from src.agent.openai_compatible_model import OpenAICompatibleModel
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.system.model_lifecycle import ModelRequestGate
from src.task_runtime.database import connection
from src.task_runtime.docking_consent import DockingConsentPolicy
from src.task_runtime.store import TaskStore


def api():
    name = "src.web.docking_proposal"
    try:
        return importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        pytest.fail("D2 proposal-call owner is absent", pytrace=False)


@pytest.mark.parametrize('index', [0, 1, 2])
def test_prompt_supplies_exact_current_codepoint_range_without_rewriting_sources(index):
    mod = api()
    query = '  对接 🧪 e\u0301\n原文  '
    snapshot = mod.capture_source_snapshot(query=query,
        history=[dict(role='assistant', content='history says end=999999')], descriptors=[])
    before = snapshot.source_snapshot_digest
    issuer = mod.TextSegmentIssuer(root_id='root-1', owner_session_id='owner-1',
        preparation_id='prep-1', origin_turn_id='turn-1', original_snapshot=snapshot,
        runtime_generation='runtime-1')
    current = query if index == 0 else '  size=[20,20,20] 🧪\n'
    segment = mod.capture_text_segment(issuer=issuer, original_snapshot=snapshot,
        text_segment_index=index, server_turn_id='turn-1' if index == 0 else f'turn-{index+1}',
        current_text=current,
        preceding_refinements=[dict(server_turn_id='turn-2', text='earlier text')]
            if index == 2 else [])
    messages = segment.messages()
    source = 'query' if index == 0 else f'refinement-{index}'
    expected = f'Current text range: source_id={source}; start=0; end={len(current)} (Unicode code points, end-exclusive).'
    assert expected in messages[0]['content']
    assert json.loads(messages[1]['content']) == dict(query=query,
        history=[dict(role='assistant', content='history says end=999999')], descriptors=[])
    assert snapshot.source_snapshot_digest == before
    if index:
        assert json.loads(messages[-1]['content'])['refinements'][-1]['text'] == current
    else:
        assert messages == snapshot.messages()


def wire(mode="native", kind="docking_request"):
    raw = json.dumps({"proposal": dict(version="1", kind=kind, unresolved=True,
                                       source_spans=[], fields={})})
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
        self.snapshot = self.api.capture_source_snapshot(
            query="original whole request marker", history=[dict(role="user", content="history marker")],
            descriptors=[dict(source_id="source-1", kind="receptor", ref="sha256:" + "a" * 64)])
        self.issuer = self.make_issuer()
        self.context = dict(policy=self.policy, model_generation="model-1",
                            capability_generation="capability-1")
        self.identity = dict(preparation_id="prep-1", task_id="future-1", owner_session_id="owner-1",
            trace_id="root-1", origin_turn_id="turn-1", query_digest=self.snapshot.query_digest,
            refinement_revision=0, admission_revision="admission-1")
        self.tasks.reserve_chat_docking_draft(identity=self.identity, draft=dict(
            schema="DockingDraft@1", model_generation="model-1", capability_generation="capability-1",
            source_snapshot_digest=self.snapshot.source_snapshot_digest,
            slots={key: {"state": "pending"} for key in
                ("receptor_ref", "ligand_ref", "center", "size", "exhaustiveness", "num_modes")},
            budget=dict(limit_ms=180000, spent_ms=0, proposal_slots_spent=0)),
            policy=self.policy, wall_time_ms=lambda: self.wall, monotonic=lambda: self.mono,
            current_context=lambda: self.context)
        self.state.start_run(dict(trace_id="root-1", session_id="owner-1", status="running",
                                  metadata={"keep": "existing"}), exclusive=True)
        self.calls = []

    def make_issuer(self, **changes):
        factory = getattr(self.api, "TextSegmentIssuer", None)
        assert callable(factory), "root-local accepted-segment issuer is missing"
        args = dict(root_id="root-1", preparation_id="prep-1", owner_session_id="owner-1",
                    origin_turn_id="turn-1", original_snapshot=self.snapshot,
                    runtime_generation=self.policy.runtime_generation)
        args.update(changes)
        return factory(**args)

    def read(self):
        return self.tasks.get_chat_docking_draft(preparation_id="prep-1", owner_session_id="owner-1")

    def journals(self):
        return [v for k, v in self.state.get_run("root-1")["metadata"].items()
                if k.startswith("docking_proposal_")]

    def client(self, handle=None):
        def default(request):
            self.calls.append(request)
            return httpx.Response(200, json=wire(self.mode))
        return httpx.AsyncClient(transport=httpx.MockTransport(handle or default),
            event_hooks={"response": [docking_preparation_response_guard]})

    def owner(self, client, mode="native", tasks=None):
        self.mode = mode
        model = OpenAICompatibleModel("synthetic-only-key", "test-model", "https://example.invalid/v1",
                                       client=client)
        self.model = model
        def capture():
            assert self.gate._readers == 1
            return dict(self.context, model=model)
        return self.api.DockingProposalOwner(task_store=tasks or self.tasks, state_store=self.state,
            gate=self.gate, capture_current=capture, wall_time_ms=lambda: self.wall,
            monotonic=lambda: self.mono)

    async def call(self, owner, **changes):
        args = dict(root_id="root-1", preparation_id="prep-1", owner_session_id="owner-1",
            expected_revision=0, expected_version=self.read()["version"], snapshot=self.snapshot,
            policy=self.policy, model_generation="model-1", capability_generation="capability-1",
            mode=self.mode, call_slice_ms=10000)
        args.update(changes)
        if "segment" not in args:
            args["segment"] = self.segment()
        return await owner.propose(**args)

    def segment(self, index=0, **changes):
        capture = getattr(self.api, "capture_text_segment", None)
        assert callable(capture), "accepted text segment capture is missing"
        args = dict(issuer=self.issuer, original_snapshot=self.snapshot, text_segment_index=index,
            server_turn_id="turn-1" if index == 0 else f"refinement-turn-{index}",
            current_text=self.snapshot.query if index == 0 else f"accepted refinement text {index}",
            preceding_refinements=[dict(server_turn_id=f"refinement-turn-{i}",
                                        text=f"accepted refinement text {i}") for i in range(1, index)])
        args.update(changes)
        return capture(**args)


@pytest.fixture
def rig(tmp_path):
    return lambda: Rig(tmp_path)


def test_snapshot_is_bounded_exact_native_immutable_and_detached():
    mod = api()
    history = [dict(role="assistant", content="history")]
    descriptors = [dict(source_id="source-1", kind="ligand", ref="sha256:" + "b" * 64)]
    snap = mod.capture_source_snapshot(query=" query ", history=history, descriptors=descriptors)
    original = snap.source_snapshot_digest
    history[0]["content"] = "changed"
    descriptors[0]["ref"] = "sha256:" + "c" * 64
    assert snap.source_snapshot_digest == original
    with pytest.raises(FrozenInstanceError):
        snap.query = "other"
    assert mod.capture_source_snapshot(query="query", history=[], descriptors=[]).query_digest != snap.query_digest
    assert mod.capture_source_snapshot(query=" query ", history=history, descriptors=descriptors).source_snapshot_digest != original
    assert "history" in json.dumps(snap.messages())


@pytest.mark.parametrize("fault", ["query_type", "history_type", "history_extra", "large", "url", "path", "binary"])
def test_snapshot_rejects_unsupported_or_unsafe_input(fault):
    args = dict(query="query", history=[], descriptors=[])
    if fault == "query_type": args["query"] = True
    elif fault == "history_type": args["history"] = ()
    elif fault == "history_extra": args["history"] = [dict(role="user", content="x", extra="x")]
    elif fault == "large": args["query"] = "x" * 65537
    else: args["descriptors"] = [dict(source_id="s", kind="ligand",
        ref={"url": "https://private.invalid", "path": "C:\\secret", "binary": b"bytes"}[fault])]
    with pytest.raises(ValueError): api().capture_source_snapshot(**args)


@pytest.mark.parametrize("mode", ["native", "json"])
def test_real_transport_precharged_private_journal_and_no_execution(rig, mode):
    rig = rig()
    async def run():
        async with rig.client() as client:
            result = await rig.call(rig.owner(client, mode))
            assert result.available and result.proposal.kind == "docking_request"
            assert result.reserved_version == 2
    asyncio.run(run())
    assert len(rig.calls) == 1
    payload = json.loads(rig.calls[0].content)
    assert payload["messages"][1:] == rig.snapshot.messages()
    source = json.loads(payload["messages"][-1]["content"])
    assert source["query"] == rig.snapshot.query and source["history"][0]["content"] == "history marker"
    assert source["descriptors"][0]["ref"] == "sha256:" + "a" * 64
    assert rig.read()["draft"]["budget"] == dict(limit_ms=180000, spent_ms=10000, proposal_slots_spent=1)
    assert rig.read()["state"] == "AWAITING_INPUT"
    facts = rig.journals()
    assert len(facts) == 1 and facts[0]["journal"]["stages"] == [
        "created", "validated", "dispatch_started", "response_received", "parsed"]
    assert "original whole request marker" not in json.dumps(facts)
    assert "history marker" not in json.dumps(facts)
    assert "synthetic-only-key" not in json.dumps(facts)
    assert rig.state.get_run("root-1")["metadata"]["keep"] == "existing"
    assert rig.gate._readers == 0
    with connection(rig.tasks.db_path) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0
        assert conn.execute("SELECT COUNT(*) AS n FROM task_events").fetchone()["n"] == 0


@pytest.mark.parametrize("fault", ["owner", "root", "query", "source", "model", "capability",
    "revision", "version", "budget", "expiry", "run_owner"])
def test_mismatch_never_dispatches(rig, fault):
    rig = rig()
    args = {}
    if fault == "owner": args["owner_session_id"] = "foreign"
    elif fault == "root": args["root_id"] = "foreign"
    elif fault == "query": args["snapshot"] = rig.api.capture_source_snapshot(query="different", history=[], descriptors=[])
    elif fault == "source": args["snapshot"] = rig.api.capture_source_snapshot(query=rig.snapshot.query, history=[], descriptors=[])
    elif fault in ("model", "capability"): rig.context[fault + "_generation"] = "other"
    elif fault == "revision": args["expected_revision"] = 1
    elif fault == "version": args["expected_version"] = 0
    elif fault == "budget": args["call_slice_ms"] = 180001
    elif fault == "expiry": rig.mono += 901
    elif fault == "run_owner":
        rig.state.start_run(dict(trace_id="root-1", session_id="foreign", metadata={}))
    async def run():
        async with rig.client() as client:
            result = await rig.call(rig.owner(client), **args)
            assert not result.available and result.proposal is None
    asyncio.run(run())
    assert not rig.calls


def test_same_text_segment_fresh_version_never_reopens_model_opportunity(rig):
    rig = rig()
    async def run():
        async with rig.client() as client:
            assert (await rig.call(rig.owner(client), call_slice_ms=45000)).available
            for _ in range(6):
                owner = rig.owner(client, tasks=TaskStore(rig.tasks.db_path))
                assert not (await rig.call(owner, call_slice_ms=45000)).available
    asyncio.run(run())
    assert len(rig.calls) == 1 and len(rig.journals()) == 1
    assert rig.read()["draft"]["budget"] == dict(limit_ms=180000, spent_ms=45000, proposal_slots_spent=1)


def test_six_real_policy_rejections_cannot_allocate_six_journals(rig):
    rig = rig()
    rig.policy = replace(rig.policy, vina_limit_seconds=299)
    rig.context["policy"] = rig.policy
    async def run():
        async with rig.client() as client:
            for _ in range(6):
                assert not (await rig.call(rig.owner(client))).available
    asyncio.run(run())
    assert len(rig.journals()) == 1 and not rig.calls
    assert rig.read()["version"] == 1
    assert rig.read()["draft"]["budget"]["proposal_slots_spent"] == 0


def test_six_lost_journal_receipts_cannot_allocate_six_journals(rig, monkeypatch):
    rig = rig()
    original = rig.state._connect
    class Connection:
        def __init__(self):
            self.conn, self.wrote = original(), False
        def __getattr__(self, name): return getattr(self.conn, name)
        def __enter__(self):
            self.conn.__enter__()
            return self
        def execute(self, sql, args=()):
            if sql.startswith("UPDATE agent_runs SET metadata_json"):
                self.wrote = True
            return self.conn.execute(sql, args)
        def __exit__(self, *args):
            result = self.conn.__exit__(*args)
            if self.wrote: raise OSError("private lost receipt")
            return result
    monkeypatch.setattr(rig.state, "_connect", Connection)
    async def run():
        async with rig.client() as client:
            for _ in range(6):
                assert not (await rig.call(rig.owner(client))).available
    asyncio.run(run())
    assert len(rig.journals()) == 1 and not rig.calls
    assert rig.journals()[0]["journal"]["stages"] == ["created"]
    assert rig.read()["version"] == 1


@pytest.mark.parametrize("kind", ["not_c", "mixed", "uncertain", "docking_observation"])
def test_nonexecutable_kinds_remain_proposals(rig, kind):
    rig = rig()
    async def run():
        async with rig.client(lambda request: httpx.Response(200, json=wire(kind=kind))) as client:
            result = await rig.call(rig.owner(client))
            assert result.available and result.proposal.kind == kind
    asyncio.run(run())
    assert rig.read()["state"] == "AWAITING_INPUT"


@pytest.mark.parametrize("fault", ["malformed", "http_timeout"])
def test_invalid_or_uncertain_response_is_charged_without_retry(rig, fault):
    rig = rig()
    def handle(request):
        rig.calls.append(request)
        if fault == "http_timeout": raise httpx.ReadTimeout("private error marker")
        return httpx.Response(200, content=b"private raw response marker")
    async def run():
        async with rig.client(handle) as client:
            result = await rig.call(rig.owner(client))
            assert not result.available and result.proposal is None
    asyncio.run(run())
    assert len(rig.calls) == 1 and rig.read()["draft"]["budget"]["proposal_slots_spent"] == 1
    assert rig.journals()[0]["journal"]["stage"] in ("failed", "timeout")
    assert "private" not in json.dumps(rig.journals())


@pytest.mark.parametrize("where", ["http", "close"])
def test_cancel_resistant_workers_retain_reader_and_root(rig, where):
    rig = rig()
    async def run():
        entered, cancelled, release, writer_entered = (asyncio.Event() for _ in range(4))
        async def resistant():
            entered.set()
            try: await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
        class Stream(httpx.AsyncByteStream):
            async def __aiter__(self): yield json.dumps(wire()).encode()
            async def aclose(self): await resistant()
        async def handle(request):
            rig.calls.append(request)
            if where == "http": await resistant()
            return httpx.Response(200, stream=Stream()) if where == "close" else httpx.Response(200, json=wire())
        async def writer():
            async with rig.gate.exclusive(): writer_entered.set()
        async with rig.client(handle) as client:
            owner = rig.owner(client)
            task = asyncio.create_task(rig.call(owner))
            await asyncio.wait_for(entered.wait(), 3)
            task.cancel()
            writing = asyncio.create_task(writer())
            try:
                await asyncio.sleep(.03)
                task.cancel()
                assert rig.gate._readers == 1 and not task.done() and not writer_entered.is_set()
                duplicate = await rig.call(rig.owner(client, tasks=TaskStore(rig.tasks.db_path)))
                assert not duplicate.available
            finally:
                release.set()
                outcomes = await asyncio.gather(task, writing, return_exceptions=True)
            assert isinstance(outcomes[0], asyncio.CancelledError)
            assert writer_entered.is_set() and rig.gate._readers == 0
    asyncio.run(run())
    assert len(rig.calls) == 1
    assert rig.journals()[0]["outcome"] == "cancelled"
    assert "dispatch_started" in rig.journals()[0]["journal"]["stages"]


def test_reader_queue_consumes_original_slice_without_capturing_model(rig):
    rig = rig()
    async def run():
        async with rig.client() as client:
            owner = rig.owner(client)
            async with rig.gate.exclusive():
                result = await rig.call(owner, call_slice_ms=15)
                assert not result.available
            assert rig.gate._readers == 0
    asyncio.run(run())
    assert not rig.calls


@pytest.mark.parametrize("clock", ["mono", "wall"])
def test_late_parsed_success_is_unavailable_after_cleanup(rig, clock):
    rig = rig()
    class Stream(httpx.AsyncByteStream):
        async def __aiter__(self): yield json.dumps(wire()).encode()
        async def aclose(self):
            if clock == "mono": rig.mono += 11
            else: rig.wall += 11000
    async def run():
        async with rig.client(lambda request: httpx.Response(200, stream=Stream())) as client:
            result = await rig.call(rig.owner(client))
            assert not result.available and result.proposal is None
    asyncio.run(run())
    assert rig.read()["draft"]["budget"]["spent_ms"] == 10000
    assert rig.journals()[0]["journal"]["stage"] == "parsed"


def test_budget_commit_then_raise_never_dispatches_or_refunds(rig, monkeypatch):
    rig = rig()
    original = rig.tasks.reserve_docking_draft_budget
    def uncertain(**kwargs):
        original(**kwargs)
        raise OSError("private receipt marker")
    monkeypatch.setattr(rig.tasks, "reserve_docking_draft_budget", uncertain)
    async def run():
        async with rig.client() as client:
            result = await rig.call(rig.owner(client))
            assert not result.available
    asyncio.run(run())
    assert not rig.calls and rig.read()["draft"]["budget"]["proposal_slots_spent"] == 1
    assert rig.journals()[0]["outcome"] == "reservation_uncertain"
    assert "private receipt marker" not in json.dumps(rig.journals())


def test_same_expected_version_cannot_dispatch_twice_across_connections(rig):
    rig = rig()
    async def run():
        async with rig.client() as client:
            assert (await rig.call(rig.owner(client), expected_version=1)).available
            other = rig.owner(client, tasks=TaskStore(rig.tasks.db_path))
            assert not (await rig.call(other, expected_version=1)).available
    asyncio.run(run())
    assert len(rig.calls) == 1


def test_snapshot_has_no_mutable_instance_dictionary():
    snapshot = api().capture_source_snapshot(query="query", history=[], descriptors=[])
    assert not hasattr(snapshot, "__dict__")


def test_returned_journal_is_sanitized_like_durable_facts(rig):
    rig = rig()
    async def run():
        async with rig.client() as client:
            owner = rig.owner(client)
            rig.model.model_name = "C:\\private-model-directory\\model"
            result = await rig.call(owner)
            assert result.available
            assert "private-model-directory" not in json.dumps(result.journal)
    asyncio.run(run())
    assert "private-model-directory" not in json.dumps(rig.journals())


@pytest.mark.parametrize("write_number", [1, 2])
def test_journal_commit_then_raise_blocks_transport_or_success(rig, monkeypatch, write_number):
    rig = rig()
    original = rig.state._connect
    writes = 0
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
            return self.conn.execute(sql, args)
        def __exit__(self, *args):
            nonlocal writes
            result = self.conn.__exit__(*args)
            if self.wrote:
                writes += 1
                if writes == write_number:
                    raise OSError("private journal receipt marker")
            return result
    monkeypatch.setattr(rig.state, "_connect", Connection)
    async def run():
        async with rig.client() as client:
            result = await rig.call(rig.owner(client))
            assert not result.available and result.proposal is None
    asyncio.run(run())
    assert len(rig.calls) == (0 if write_number == 1 else 1)
    facts = rig.journals()
    assert len(facts) == 1
    assert facts[0]["outcome"] == ("claim_uncertain" if write_number == 1 else "journal_uncertain")
    if write_number == 1:
        assert writes == 1 and facts[0]["journal"]["stages"] == ["created"]
    assert "private journal receipt marker" not in json.dumps(facts)


@pytest.mark.parametrize("phase", ["reservation", "final_journal"])
def test_cancelled_sql_is_joined_before_reader_release(rig, monkeypatch, phase):
    rig = rig()
    entered, release = threading.Event(), threading.Event()
    if phase == "reservation":
        original = rig.tasks.reserve_docking_draft_budget
        def delayed(**kwargs):
            value = original(**kwargs)
            entered.set()
            assert release.wait(5)
            return value
        monkeypatch.setattr(rig.tasks, "reserve_docking_draft_budget", delayed)
    else:
        original = rig.api.DockingProposalOwner._write_journal
        def delayed(self, **kwargs):
            value = original(self, **kwargs)
            if kwargs["outcome"] == "proposed":
                entered.set()
                assert release.wait(5)
            return value
        monkeypatch.setattr(rig.api.DockingProposalOwner, "_write_journal", delayed)
    async def run():
        async with rig.client() as client:
            task = asyncio.create_task(rig.call(rig.owner(client)))
            assert await asyncio.to_thread(entered.wait, 3)
            task.cancel()
            try:
                await asyncio.sleep(.03)
                task.cancel()
                assert not task.done() and rig.gate._readers == 1
                assert not (await rig.call(rig.owner(client))).available
            finally:
                release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert isinstance(result, asyncio.CancelledError)
            assert rig.gate._readers == 0
    asyncio.run(run())
    assert len(rig.calls) == (0 if phase == "reservation" else 1)
    assert rig.journals()[0]["outcome"] == "cancelled"
    assert rig.read()["draft"]["budget"]["proposal_slots_spent"] == 1


def test_timeout_drains_cancellation_resistant_transport_and_rejects_late_success(rig):
    rig = rig()
    async def run():
        entered, cancelled, release = (asyncio.Event() for _ in range(3))
        async def handle(request):
            rig.calls.append(request)
            entered.set()
            try: await release.wait()
            except asyncio.CancelledError:
                cancelled.set()
                await release.wait()
            return httpx.Response(200, json=wire())
        async with rig.client(handle) as client:
            # Include real SQLite setup in the slice, but leave enough time to
            # reach the deliberately blocked HTTP seam on a busy Windows host.
            task = asyncio.create_task(rig.call(rig.owner(client), call_slice_ms=2000))
            await asyncio.wait_for(entered.wait(), 3)
            try:
                await asyncio.wait_for(cancelled.wait(), 3)
                assert not task.done() and rig.gate._readers == 1
            finally:
                release.set()
            result = await task
            assert not result.available and result.proposal is None
    asyncio.run(run())
    assert rig.journals()[0]["outcome"] == "timeout"
    assert rig.read()["draft"]["budget"]["spent_ms"] == 2000


@pytest.mark.parametrize("change", ["generation", "run_owner", "version", "expiry"])
def test_change_during_http_invalidates_parsed_proposal(rig, change):
    rig = rig()
    def handle(request):
        rig.calls.append(request)
        if change == "generation": rig.context["model_generation"] = "model-2"
        elif change == "run_owner":
            rig.state.start_run(dict(trace_id="root-1", session_id="foreign", metadata={}))
        elif change == "expiry": rig.wall += 900001
        else:
            with connection(rig.tasks.db_path) as conn:
                conn.execute("UPDATE docking_consents SET version=version+1")
        return httpx.Response(200, json=wire())
    async def run():
        async with rig.client(handle) as client:
            result = await rig.call(rig.owner(client))
            assert not result.available and result.proposal is None
    asyncio.run(run())
    assert len(rig.calls) == 1
    assert rig.read()["draft"]["budget"]["proposal_slots_spent"] == 1
    if change == "run_owner": assert not rig.journals()  # Never write to foreign run.


def test_run_owner_changed_during_reservation_prevents_dispatch(rig, monkeypatch):
    rig = rig()
    original = rig.tasks.reserve_docking_draft_budget
    def change_owner(**kwargs):
        result = original(**kwargs)
        rig.state.start_run(dict(trace_id="root-1", session_id="foreign", metadata={}))
        return result
    monkeypatch.setattr(rig.tasks, "reserve_docking_draft_budget", change_owner)
    async def run():
        async with rig.client() as client:
            assert not (await rig.call(rig.owner(client))).available
    asyncio.run(run())
    assert not rig.calls


def test_total_budget_blocks_before_four_slots(rig):
    rig = rig()
    async def run():
        async with rig.client() as client:
            owner = rig.owner(client)
            assert (await rig.call(owner, call_slice_ms=170000)).available
            assert not (await rig.call(owner, segment=rig.segment(1), call_slice_ms=10001)).available
    asyncio.run(run())
    assert len(rig.calls) == 1 and rig.read()["draft"]["budget"]["spent_ms"] == 170000


def test_private_return_drops_freeform_model_configuration(rig):
    rig = rig()
    async def run():
        async with rig.client() as client:
            owner = rig.owner(client)
            rig.model.model_name = "free form model configuration marker"
            result = await rig.call(owner)
            assert result.available
            assert "free form model configuration marker" not in json.dumps(result.journal)
    asyncio.run(run())


def test_competing_real_connection_wins_reservation_cas_without_duplicate_dispatch(rig, monkeypatch):
    rig = rig()
    original = rig.tasks.reserve_docking_draft_budget
    other = TaskStore(rig.tasks.db_path)
    def competing(**kwargs):
        other.reserve_docking_draft_budget(**kwargs)
        return original(**kwargs)
    monkeypatch.setattr(rig.tasks, "reserve_docking_draft_budget", competing)
    async def run():
        async with rig.client() as client:
            assert not (await rig.call(rig.owner(client), expected_version=1)).available
    asyncio.run(run())
    assert not rig.calls and rig.read()["version"] == 2
    assert rig.read()["draft"]["budget"]["proposal_slots_spent"] == 1


def test_queue_delay_is_subtracted_from_transport_timeout_without_refunding_slice(rig):
    rig = rig()
    timeout = []
    def handle(request):
        timeout.append(request.extensions["timeout"]["read"])
        return httpx.Response(200, json=wire())
    async def run():
        async with rig.client(handle) as client:
            owner = rig.owner(client)
            async with rig.gate.exclusive():
                task = asyncio.create_task(rig.call(owner))
                await asyncio.sleep(.02)
                rig.mono += 7
                rig.wall += 7000
            assert (await task).available
    asyncio.run(run())
    assert len(timeout) == 1 and 0 < timeout[0] <= 3
    assert rig.read()["draft"]["budget"]["spent_ms"] == 10000


def test_final_journal_sql_time_is_inside_original_slice(rig, monkeypatch):
    rig = rig()
    original = rig.api.DockingProposalOwner._write_journal
    def late(self, **kwargs):
        result = original(self, **kwargs)
        if kwargs["outcome"] == "proposed":
            rig.mono += 11
        return result
    monkeypatch.setattr(rig.api.DockingProposalOwner, "_write_journal", late)
    async def run():
        async with rig.client() as client:
            result = await rig.call(rig.owner(client))
            assert not result.available and result.proposal is None
    asyncio.run(run())
    assert rig.journals()[0]["outcome"] != "proposed"
    assert rig.journals()[0]["journal"]["stage"] == "parsed"


def test_capture_rejects_custom_containers_without_running_user_iteration():
    class HostileList(list):
        def __iter__(self): raise AssertionError("must not iterate custom data")
    class HostileDict(dict):
        def __iter__(self): raise AssertionError("must not iterate custom data")
    mod = api()
    with pytest.raises(ValueError):
        mod.capture_source_snapshot(query="query", history=HostileList(), descriptors=[])
    with pytest.raises(ValueError):
        mod.capture_source_snapshot(query="query", history=[HostileDict()], descriptors=[])
    with pytest.raises(ValueError):
        mod.capture_source_snapshot(query="query", history=[], descriptors=[HostileDict()])


def test_snapshot_digest_covers_each_source_component():
    mod = api()
    data = dict(query="query", history=[dict(role="user", content="history")],
                descriptors=[dict(source_id="s1", kind="ligand", ref="sha256:" + "a" * 64)])
    original = mod.capture_source_snapshot(**data)
    for path, value in [("query", "query "), ("history", [dict(role="assistant", content="history")]),
                        ("descriptors", [dict(source_id="s2", kind="ligand", ref="sha256:" + "a" * 64)])]:
        changed = mod.capture_source_snapshot(**{**data, path: value})
        assert changed.source_snapshot_digest != original.source_snapshot_digest
        if path != "query": assert changed.query_digest == original.query_digest


@pytest.mark.parametrize("interrupt", ["cancel", "timeout"])
def test_actual_owned_http_client_close_retains_reader_until_settled(rig, monkeypatch, interrupt):
    from src.agent import decision_transport
    rig = rig()
    async def run():
        entered, release, closed = (asyncio.Event() for _ in range(3))
        clients = []
        class IO(httpx.MockTransport):
            async def aclose(self):
                entered.set()
                try: await release.wait()
                except asyncio.CancelledError: await release.wait()
                closed.set()
        def handle(request):
            rig.calls.append(request)
            return httpx.Response(200, json=wire())
        def constructor(*args, **kwargs):
            assert kwargs["event_hooks"]["response"][0] is docking_preparation_response_guard
            client = httpx.AsyncClient(*args, transport=IO(handle), **kwargs)
            clients.append(client)
            return client
        dependency = SimpleNamespace(**vars(httpx))
        dependency.AsyncClient = constructor
        monkeypatch.setattr(decision_transport, "httpx", dependency)
        owner = rig.owner(None)
        task = asyncio.create_task(rig.call(owner, call_slice_ms=2000 if interrupt == "timeout" else 10000))
        await asyncio.wait_for(entered.wait(), 3)
        try:
            if interrupt == "cancel": task.cancel()
            await asyncio.sleep(2.1 if interrupt == "timeout" else .03)
            assert rig.gate._readers == 1 and not task.done() and not closed.is_set()
            assert not (await rig.call(owner)).available
        finally:
            release.set()
        result = (await asyncio.gather(task, return_exceptions=True))[0]
        if interrupt == "cancel": assert isinstance(result, asyncio.CancelledError)
        else: assert not result.available
        assert closed.is_set() and rig.gate._readers == 0
        assert len(clients) == 1 and clients[0].is_closed
    asyncio.run(run())
    assert len(rig.calls) == 1
    assert rig.journals()[0]["outcome"] == ("cancelled" if interrupt == "cancel" else "timeout")


@pytest.mark.parametrize("mode", ["native", "json"])
def test_four_calls_require_four_distinct_explicit_text_segments(rig, mode):
    rig = rig()
    async def run():
        async with rig.client() as client:
            for index in range(4):
                owner = rig.owner(client, mode, tasks=TaskStore(rig.tasks.db_path))
                result = await rig.call(owner, segment=rig.segment(index), call_slice_ms=45000)
                assert result.available
                assert result.journal["turn_id"] == rig.segment(index).server_turn_id
            assert not (await rig.call(rig.owner(client, mode), segment=rig.segment(3))).available
    asyncio.run(run())
    assert len(rig.calls) == len(rig.journals()) == 4
    assert rig.read()["draft"]["budget"] == dict(limit_ms=180000, spent_ms=180000, proposal_slots_spent=4)
    assert rig.read()["identity"]["refinement_revision"] == 0  # Text identity is independent of draft revision.
    assert rig.read()["identity"]["query_digest"] == rig.snapshot.query_digest
    assert rig.read()["draft"]["source_snapshot_digest"] == rig.snapshot.source_snapshot_digest
    for index, request in enumerate(rig.calls):
        messages = json.loads(request.content)["messages"][1:]
        assert messages == rig.segment(index).messages()
        if index:
            refinements = json.loads(messages[-1]["content"])["refinements"]
            assert [item["text"] for item in refinements] == [f"accepted refinement text {i}" for i in range(1, index + 1)]
    assert "accepted refinement text" not in json.dumps(rig.state.get_run("root-1")["metadata"])


def test_structured_patch_revision_does_not_create_text_segment(rig):
    rig = rig()
    async def run():
        async with rig.client() as client:
            owner = rig.owner(client)
            assert (await rig.call(owner)).available
            rig.tasks.refine_docking_consent_draft(preparation_id="prep-1", owner_session_id="owner-1",
                expected_revision=0, expected_version=2, policy=rig.policy, model_generation="model-1",
                capability_generation="capability-1", wall_time_ms=lambda: rig.wall,
                monotonic=lambda: rig.mono, current_context=lambda: rig.context,
                patch={"center": [1, 2, 3]})
            assert not (await rig.call(owner, expected_revision=1)).available
            assert (await rig.call(owner, expected_revision=1, segment=rig.segment(1))).available
    asyncio.run(run())
    assert len(rig.calls) == len(rig.journals()) == 2


@pytest.mark.parametrize("revision", [4, 5])
def test_large_draft_revision_preserves_only_four_text_opportunities(rig, revision):
    rig = rig()
    patches = [{"center": [1, 2, 3]}, {"size": [20, 20, 20]},
               {"exhaustiveness": 8}, {"num_modes": 9}, {"ligand_ref": "sha256:" + "b" * 64}]
    def refine(patch):
        record = rig.read()
        return rig.tasks.refine_docking_consent_draft(
            preparation_id="prep-1", owner_session_id="owner-1",
            expected_revision=record["identity"]["refinement_revision"],
            expected_version=record["version"], policy=rig.policy, model_generation="model-1",
            capability_generation="capability-1", wall_time_ms=lambda: rig.wall,
            monotonic=lambda: rig.mono, current_context=lambda: rig.context,
            patch=patch)
    async def run():
        async with rig.client() as client:
            assert (await rig.call(rig.owner(client))).available
            for patch in patches[:revision]:
                refine(patch)
            assert rig.read()["state"] == "AWAITING_INPUT"
            assert rig.read()["identity"]["refinement_revision"] == revision
            for index in range(1, 4):
                current_revision = rig.read()["identity"]["refinement_revision"]
                # Structured revisions alone never reopen the preceding text slot.
                assert not (await rig.call(rig.owner(client), expected_revision=current_revision,
                                          segment=rig.segment(index - 1))).available
                result = await rig.call(rig.owner(client), expected_revision=current_revision,
                                        segment=rig.segment(index))
                assert result.available, result.reason_code
                assert result.journal["refinement_revision"] == index
                assert rig.journals()[-1]["binding"]["draft_revision"] == current_revision
            assert not (await rig.call(rig.owner(client),
                expected_revision=rig.read()["identity"]["refinement_revision"],
                segment=rig.segment(3))).available
    asyncio.run(run())
    assert len(rig.calls) == len(rig.journals()) == 4
    assert rig.read()["draft"]["budget"]["proposal_slots_spent"] == 4


@pytest.mark.parametrize("fault", ["wrong_origin_turn", "skip", "reuse_origin", "rewrite_prior", "reuse_prior", "rewrite_current", "tampered_digest", "typed_index_conflict"])
def test_text_segment_binding_conflicts_fail_closed(rig, fault):
    rig = rig()
    async def run():
        async with rig.client() as client:
            owner = rig.owner(client)
            if fault == "wrong_origin_turn":
                with pytest.raises(ValueError): rig.segment(server_turn_id="other-turn")
                assert not rig.calls and not rig.journals()
                return
            else:
                assert (await rig.call(owner)).available
                before = 1
                if fault == "skip": segment = rig.segment(2)
                elif fault == "reuse_origin":
                    with pytest.raises(ValueError): rig.segment(1, server_turn_id="turn-1")
                    assert len(rig.calls) == len(rig.journals()) == before
                    return
                elif fault in {"tampered_digest", "typed_index_conflict"}:
                    key, entry = next((k, v) for k, v in rig.state.get_run("root-1")["metadata"].items()
                                      if k.startswith("docking_proposal_"))
                    if fault == "tampered_digest": entry["binding"]["segment"]["context_digest"] = "f" * 64
                    else: entry["binding"]["segment"]["text_segment_index"] = False
                    rig.state.update_run_metadata("root-1", {key: entry})
                    segment = rig.segment(1)
                else:
                    assert (await rig.call(owner, segment=rig.segment(1))).available
                    before = 2
                    if fault == "rewrite_prior":
                        segment = rig.segment(2, preceding_refinements=[dict(server_turn_id="refinement-turn-1", text="changed prior text")])
                    elif fault == "reuse_prior":
                        # Well-shaped text still cannot reissue an accepted turn.
                        with pytest.raises(ValueError):
                            rig.segment(2, server_turn_id="refinement-turn-1",
                                preceding_refinements=[dict(server_turn_id="different-turn", text="accepted refinement text 1")])
                        assert len(rig.calls) == len(rig.journals()) == before
                        return
                    else:
                        with pytest.raises(ValueError): rig.segment(1, current_text="changed current text")
                        assert len(rig.calls) == len(rig.journals()) == before
                        return
            assert not (await rig.call(owner, segment=segment)).available
            assert len(rig.calls) == len(rig.journals()) == before
    asyncio.run(run())


@pytest.mark.parametrize("fault", ["index_bool", "index4", "original_text", "original_history", "missing_prefix", "duplicate_turn", "custom_list", "extra_field", "large_text", "aggregate"])
def test_refinement_capture_is_exact_bounded_and_rejects_bad_shapes(rig, fault):
    rig = rig()
    class CustomList(list):
        def __iter__(self): raise AssertionError("custom container executed")
    args = {}
    index = 0
    if fault == "index_bool": args["text_segment_index"] = True
    elif fault == "index4": args["text_segment_index"] = 4
    elif fault == "original_text": args["current_text"] = "not original query"
    elif fault == "original_history": args["preceding_refinements"] = [dict(server_turn_id="x", text="x")]
    elif fault == "missing_prefix": index, args = 2, dict(preceding_refinements=[])
    elif fault == "duplicate_turn": index, args = 2, dict(server_turn_id="refinement-turn-1")
    elif fault == "custom_list": args["preceding_refinements"] = CustomList()
    elif fault == "extra_field": index, args = 2, dict(preceding_refinements=[dict(server_turn_id="x", text="x", extra=True)])
    elif fault == "large_text": index, args = 1, dict(current_text="x" * 8193)
    else:
        index, args = 3, dict(current_text="🧪" * 8192,
            preceding_refinements=[dict(server_turn_id=f"prior-{i}", text="🧪" * 8192) for i in range(2)])
    with pytest.raises(ValueError): rig.segment(index, **args)


def test_refinement_capture_is_frozen_and_detached(rig):
    rig = rig()
    prefix = [dict(server_turn_id="refinement-turn-1", text="accepted refinement text 1")]
    segment = rig.segment(2, preceding_refinements=prefix)
    messages = segment.messages()
    prefix[0]["text"] = "mutation"
    assert segment.messages() == messages and not hasattr(segment, "__dict__")
    with pytest.raises(FrozenInstanceError): segment.current_text = "mutation"


def _claim_in_process(task_path, state_path, snapshot_args, barrier, results, full_call=False):
    """Spawn entry point: separate process, stores/connections/locks, same durable root."""
    mod = importlib.import_module("src.web.docking_proposal")
    snapshot = mod.capture_source_snapshot(**snapshot_args)
    issuer = mod.TextSegmentIssuer(root_id="root-1", owner_session_id="owner-1", preparation_id="prep-1",
        origin_turn_id="turn-1", original_snapshot=snapshot, runtime_generation="runtime-1")
    segment = mod.capture_text_segment(issuer=issuer, original_snapshot=snapshot, text_segment_index=0,
        server_turn_id="turn-1", current_text=snapshot.query, preceding_refinements=[])
    owner = mod.DockingProposalOwner(task_store=TaskStore(task_path),
        state_store=SQLiteAgentStateStore(state_path), gate=ModelRequestGate(), capture_current=lambda: None)
    record = owner.tasks.get_chat_docking_draft(preparation_id="prep-1", owner_session_id="owner-1")
    barrier.wait(timeout=15)
    if full_call:
        async def run():
            calls = []
            def handle(request):
                calls.append(request)
                return httpx.Response(200, json=wire())
            async with httpx.AsyncClient(transport=httpx.MockTransport(handle),
                event_hooks={"response": [docking_preparation_response_guard]}) as client:
                model = OpenAICompatibleModel("synthetic-only-key", "test-model", "https://example.invalid/v1", client=client)
                policy = DockingConsentPolicy("e" * 64, "adapter-1", "local", "policy-1", "runtime-1", 300, 420)
                def capture():
                    assert owner.gate._readers == 1
                    return dict(model=model, policy=policy, model_generation="model-1", capability_generation="capability-1")
                owner.capture_current = capture
                owner.wall_time_ms, owner.monotonic = lambda: 1000000, lambda: 100.0
                result = await owner.propose(root_id="root-1", preparation_id="prep-1", owner_session_id="owner-1",
                    expected_revision=0, expected_version=record["version"], snapshot=snapshot, segment=segment,
                    policy=policy, model_generation="model-1", capability_generation="capability-1",
                    mode="native", call_slice_ms=10000)
                results.put((result.available, len(calls)))
        asyncio.run(run())
        return
    try:
        owner._claim_segment(root_id="root-1", owner_session_id="owner-1", record=record,
                             snapshot=snapshot, segment=segment)
    except mod._Unavailable:
        results.put("rejected")
    else:
        results.put("claimed")


@pytest.mark.parametrize("full_call", [False, True])
def test_atomic_claim_has_one_winner_across_processes(rig, full_call):
    rig = rig()
    assert callable(getattr(rig.api.DockingProposalOwner, "_claim_segment", None)), "atomic segment claim is missing"
    context = multiprocessing.get_context("spawn")
    barrier, results = context.Barrier(2), context.Queue()
    args = (str(rig.tasks.db_path), str(rig.state.db_path),
        dict(query=rig.snapshot.query, history=[dict(role="user", content="history marker")],
             descriptors=[dict(source_id="source-1", kind="receptor", ref="sha256:" + "a" * 64)]), barrier, results, full_call)
    workers = [context.Process(target=_claim_in_process, args=args) for _ in range(2)]
    try:
        for worker in workers: worker.start()
        outcomes = [results.get(timeout=25) for _ in workers]
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0
    finally:
        for worker in workers:
            if worker.is_alive(): worker.terminate()
            worker.join(timeout=5)
        results.close()
        results.join_thread()
    assert sorted(outcomes) == ([(False, 0), (True, 1)] if full_call else ["claimed", "rejected"])
    assert len(rig.journals()) == 1
    assert rig.read()["draft"]["budget"]["proposal_slots_spent"] == int(full_call)


def test_atomic_claim_connections_and_receipt_bound_update_only(rig):
    rig = rig()
    assert callable(getattr(rig.api.DockingProposalOwner, "_claim_segment", None)), "atomic segment claim is missing"
    owners = [rig.api.DockingProposalOwner(task_store=TaskStore(rig.tasks.db_path),
        state_store=SQLiteAgentStateStore(rig.state.db_path), gate=ModelRequestGate(),
        capture_current=lambda: None) for _ in range(2)]
    barrier = threading.Barrier(2)
    def claim(owner):
        barrier.wait(timeout=5)
        try:
            return owner._claim_segment(root_id="root-1", owner_session_id="owner-1", record=rig.read(),
                                         snapshot=rig.snapshot, segment=rig.segment())
        except rig.api._Unavailable:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, owners))
    assert sum(result is not None for result in results) == 1
    receipt, journal = next(result for result in results if result is not None)
    before = rig.journals()
    with pytest.raises(rig.api._Unavailable):
        owners[0]._write_journal(root_id="root-1", owner_session_id="owner-1", journal=journal,
                                outcome="cancelled", claim=replace(receipt, token="f" * 32))
    assert rig.journals() == before
    with rig.state._connect() as conn:
        conn.execute("UPDATE agent_runs SET metadata_json='{}' WHERE trace_id='root-1'")
    with pytest.raises(rig.api._Unavailable):
        owners[0]._write_journal(root_id="root-1", owner_session_id="owner-1", journal=journal,
                                outcome="cancelled", claim=receipt)
    assert rig.journals() == []  # Lost slot is never recreated by finally/update.


def test_six_failed_calls_across_accepted_segments_still_have_at_most_four_slots(rig):
    rig = rig()
    rig.policy = replace(rig.policy, vina_limit_seconds=299)
    rig.context["policy"] = rig.policy
    async def run():
        async with rig.client() as client:
            for index in (0, 1, 2, 3, 3, 0):
                assert not (await rig.call(rig.owner(client), segment=rig.segment(index))).available
    asyncio.run(run())
    assert len(rig.journals()) == 4 and not rig.calls
    assert rig.read()["version"] == 1 and rig.read()["draft"]["budget"]["proposal_slots_spent"] == 0


def test_cancellation_during_claim_commit_never_recreates_or_authorizes(rig, monkeypatch):
    rig = rig()
    committed, release = threading.Event(), threading.Event()
    original = rig.api.DockingProposalOwner._claim_segment
    def delayed(self, **kwargs):
        receipt = original(self, **kwargs)
        committed.set()
        assert release.wait(5)
        return receipt
    monkeypatch.setattr(rig.api.DockingProposalOwner, "_claim_segment", delayed)
    async def run():
        async with rig.client() as client:
            owner = rig.owner(client)
            task = asyncio.create_task(rig.call(owner))
            assert await asyncio.to_thread(committed.wait, 3)
            task.cancel()
            try:
                await asyncio.sleep(.03)
                assert rig.gate._readers == 1 and not task.done()
                task.cancel()
            finally:
                release.set()
            result = (await asyncio.gather(task, return_exceptions=True))[0]
            assert isinstance(result, asyncio.CancelledError)
            assert not (await rig.call(rig.owner(client))).available
    asyncio.run(run())
    assert not rig.calls and len(rig.journals()) == 1
    assert rig.journals()[0]["journal"]["stages"] == ["created"]
    assert rig.journals()[0]["outcome"] == "claim_uncertain"
    assert rig.read()["draft"]["budget"]["proposal_slots_spent"] == 0


@pytest.mark.parametrize("fault", ["before_claim_commit", "budget_receipt", "malformed", "timeout"])
def test_failed_or_uncertain_attempt_does_not_reopen_same_segment(rig, monkeypatch, fault):
    rig = rig()
    if fault == "before_claim_commit":
        original = rig.state._connect
        class Connection:
            def __init__(self): self.conn = original()
            def __getattr__(self, name): return getattr(self.conn, name)
            def __enter__(self):
                self.conn.__enter__()
                return self
            def execute(self, sql, args=()):
                if sql.startswith("UPDATE agent_runs SET metadata_json"):
                    raise OSError("private write failure")
                return self.conn.execute(sql, args)
            def __exit__(self, *args): return self.conn.__exit__(*args)
        monkeypatch.setattr(rig.state, "_connect", Connection)
    elif fault == "budget_receipt":
        original = rig.tasks.reserve_docking_draft_budget
        def lost(**kwargs):
            original(**kwargs)
            raise OSError("private receipt failure")
        monkeypatch.setattr(rig.tasks, "reserve_docking_draft_budget", lost)
    def handle(request):
        rig.calls.append(request)
        if fault == "timeout": raise httpx.ReadTimeout("private timeout")
        return httpx.Response(200, content=b"malformed")
    async def run():
        async with rig.client(handle) as client:
            for _ in range(6):
                assert not (await rig.call(rig.owner(client))).available
    asyncio.run(run())
    assert len(rig.journals()) == (0 if fault == "before_claim_commit" else 1)
    assert len(rig.calls) == (1 if fault in {"malformed", "timeout"} else 0)
    assert rig.read()["draft"]["budget"]["proposal_slots_spent"] == (0 if fault == "before_claim_commit" else 1)


@pytest.mark.parametrize("fault", ["legacy", "gap"])
def test_existing_unbounded_or_gapped_journal_history_is_not_truncated(rig, fault):
    rig = rig()
    legacy = {f"docking_proposal_legacy{i}": {"retain": True} for i in range(6)}
    existing = legacy if fault == "legacy" else {"docking_proposal_2": {"retain": True}}
    rig.state.update_run_metadata("root-1", existing)
    before = rig.state.get_run("root-1")["metadata"]
    async def run():
        async with rig.client() as client:
            assert not (await rig.call(rig.owner(client))).available
    asyncio.run(run())
    assert not rig.calls and rig.state.get_run("root-1")["metadata"] == before


@pytest.mark.parametrize("recapture", [False, True])
def test_precommit_failure_then_recovery_cannot_reinvoke_accepted_segment(rig, monkeypatch, recapture):
    rig = rig()
    original = rig.state._connect
    class Connection:
        def __init__(self): self.conn = original()
        def __getattr__(self, name): return getattr(self.conn, name)
        def __enter__(self):
            self.conn.__enter__()
            return self
        def execute(self, sql, args=()):
            if sql.startswith("UPDATE agent_runs SET metadata_json"):
                raise OSError("synthetic precommit failure")
            return self.conn.execute(sql, args)
        def __exit__(self, *args): return self.conn.__exit__(*args)

    async def run():
        segment = rig.segment()
        async with rig.client() as client:
            monkeypatch.setattr(rig.state, "_connect", Connection)
            first = await rig.call(rig.owner(client), segment=segment)
            assert first.reason_code == "claim_uncertain"
            assert not rig.journals() and not rig.calls
            assert rig.read()["version"] == 1
            assert rig.read()["draft"]["budget"]["proposal_slots_spent"] == 0
            monkeypatch.setattr(rig.state, "_connect", original)
            second = await rig.call(rig.owner(client), segment=rig.segment() if recapture else segment)
            assert not second.available, "storage recovery must not reopen an accepted segment invocation"
    asyncio.run(run())
    assert not rig.calls and not rig.journals()
    assert rig.read()["draft"]["budget"]["proposal_slots_spent"] == 0
    assert rig.gate._readers == 0


def test_issuer_recapture_returns_same_capability_with_four_bounded_slots(rig):
    rig = rig()
    first = [rig.segment(i) for i in range(4)]
    with ThreadPoolExecutor(max_workers=8) as pool:
        repeated = list(pool.map(lambda i: rig.segment(i % 4), range(80)))
    assert all(value is first[i % 4] for i, value in enumerate(repeated))
    with pytest.raises(ValueError): rig.segment(1, current_text="changed accepted text")
    with pytest.raises(ValueError): rig.segment(1, server_turn_id="turn-1")
    with pytest.raises(ValueError): rig.segment(4)


@pytest.mark.parametrize("fault", ["root_id", "owner_session_id", "preparation_id", "origin_turn_id", "snapshot", "runtime", "unissued", "partial", "forged"])
def test_issuer_binding_and_unissued_capability_reject_before_claim_sql(rig, monkeypatch, fault):
    rig = rig()
    if fault in {"root_id", "owner_session_id", "preparation_id", "origin_turn_id"}:
        issuer = rig.make_issuer(**{fault: "foreign"})
        segment = rig.segment(issuer=issuer, server_turn_id="foreign" if fault == "origin_turn_id" else "turn-1")
    else:
        segment = rig.segment()
    args = dict(segment=segment)
    if fault == "snapshot":
        # Identical bytes in a newly captured object are not the bound snapshot.
        args["snapshot"] = rig.api.capture_source_snapshot(
            query=rig.snapshot.query, history=[dict(role="user", content="history marker")],
            descriptors=[dict(source_id="source-1", kind="receptor", ref="sha256:" + "a" * 64)])
    elif fault == "runtime":
        rig.policy = replace(rig.policy, runtime_generation="runtime-2")
        rig.context["policy"] = rig.policy
    elif fault in {"unissued", "partial"}:
        args["segment"] = object.__new__(rig.api.CapturedTextSegment)
        if fault == "partial": object.__setattr__(args["segment"], "_issuer", rig.issuer)
    elif fault == "forged":
        forged = object.__new__(rig.api.CapturedTextSegment)
        for name in rig.api.CapturedTextSegment.__slots__:
            object.__setattr__(forged, name, getattr(segment, name))
        args["segment"] = forged
    async def run():
        async with rig.client() as client:
            owner = rig.owner(client)
            def no_sql(**kwargs): pytest.fail("invalid capability reached claim SQL")
            monkeypatch.setattr(owner, "_claim_segment", no_sql)
            assert not (await rig.call(owner, **args)).available
    asyncio.run(run())
    assert not rig.calls and not rig.journals()


def test_issuer_close_invalidates_capabilities_and_cannot_reopen(rig):
    rig = rig()
    segment = rig.segment()
    rig.issuer.close()
    rig.issuer.close()
    with pytest.raises(ValueError): rig.segment()
    async def run():
        async with rig.client() as client:
            assert not (await rig.call(rig.owner(client), segment=segment)).available
    asyncio.run(run())
    assert not rig.calls and not rig.journals()


def test_issuer_close_during_transport_suppresses_late_success(rig):
    rig = rig()
    def handle(request):
        rig.calls.append(request)
        rig.issuer.close()
        return httpx.Response(200, json=wire())
    async def run():
        async with rig.client(handle) as client:
            assert not (await rig.call(rig.owner(client))).available
    asyncio.run(run())
    assert len(rig.calls) == len(rig.journals()) == 1
    assert rig.gate._readers == 0


def test_issuer_and_capability_cannot_be_serialized_for_restart(rig):
    rig = rig()
    for value in (rig.issuer, rig.segment()):
        with pytest.raises(TypeError): pickle.dumps(value)


def test_one_shot_is_consumed_before_waiting_for_model_reader(rig):
    rig = rig()
    async def run():
        async with rig.client() as client:
            async with rig.gate.exclusive():
                task = asyncio.create_task(rig.call(rig.owner(client)))
                await asyncio.sleep(.02)
                task.cancel()
                result = (await asyncio.gather(task, return_exceptions=True))[0]
                assert isinstance(result, asyncio.CancelledError)
            # Cancellation before any SQL cannot reopen the accepted invocation.
            assert not (await rig.call(rig.owner(client))).available
    asyncio.run(run())
    assert not rig.calls and not rig.journals()


def test_capability_consumption_time_counts_in_original_slice(rig, monkeypatch):
    rig = rig()
    original = rig.api.TextSegmentIssuer._consume
    def delayed(self, *args, **kwargs):
        original(self, *args, **kwargs)
        rig.wall += 10001
        rig.mono += 10.001
    monkeypatch.setattr(rig.api.TextSegmentIssuer, "_consume", delayed)
    async def run():
        async with rig.client() as client:
            assert not (await rig.call(rig.owner(client))).available
    asyncio.run(run())
    assert not rig.calls and not rig.journals()
