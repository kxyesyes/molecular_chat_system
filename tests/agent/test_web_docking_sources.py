"""Owned source integration contracts; synthetic providers are NOT real S10."""
import asyncio
from contextlib import closing
from copy import deepcopy
import json
import sqlite3
import threading
import time

import pytest

from src.docking import source_preparation
from src.task_runtime.database import connection
from src.web.docking_request import OwnedDockingStructureEvidence
from tests.agent.test_web_docking_consent import target_case, republish
from tests.agent.test_web_docking_coordinator import Rig, proposal
from tests.test_docking_source_preparation import _fixture_pipeline_child


QUERY = "用 CCO 与 1ABC 对接。使用共晶配体定义口袋。"


def source_rig(tmp_path, target_case):
    def accepted(rows):
        rows[0].update(uniprot_id="P06276", source_record_id="P06276", organism="Homo sapiens")
        rows[0]["recommended_structures"][0]["organism"] = "Homo sapiens"
    republish(target_case, accepted)
    rig = Rig(tmp_path)
    rig.state = target_case.store
    rig.wall, rig.mono = int(target_case.now * 1000), time.monotonic()
    rig.args.update(owner_session_id="owner-session", query=QUERY, descriptors=[])
    rig.output = proposal(QUERY, unresolved=True)
    with closing(rig.state._connect()) as conn, conn:
        conn.execute("UPDATE agent_runs SET session_id='owner-session' WHERE trace_id='root-1'")
    evidence = OwnedDockingStructureEvidence(rig.state, clock=lambda: rig.wall / 1000)
    pointer = evidence.capture(target_case.agent, session_id="owner-session")
    assert pointer
    return rig, pointer


def invalidate_source(rig, mutation):
    with closing(sqlite3.connect(rig.state.db_path, timeout=0)) as conn, conn:
        if mutation == "owner":
            conn.execute("UPDATE agent_runs SET session_id='foreign' WHERE trace_id='target-trace'")
        elif mutation == "source":
            conn.execute("UPDATE agent_runs SET status='failed' WHERE trace_id='target-trace'")
        elif mutation == "root":
            conn.execute("UPDATE agent_runs SET status='failed' WHERE trace_id='root-1'")
        elif mutation == "draft":
            with connection(rig.tasks.db_path) as tasks:
                tasks.execute("DELETE FROM docking_consents")
        elif mutation == "model":
            rig.context["model_generation"] = "model-2"
        elif mutation == "deadline":
            rig.wall += 180001


def assert_not_ready(view):
    assert view["input_ready"] is False and view["execution_authorized"] is False
    assert "nonce" not in json.dumps(view)


def test_exact_live_source_runs_real_retained_children_without_selecting_inputs(
        tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    # Actual retained child, actual compiler, actual inspection. Only the child
    # provider is synthetic, and does not establish any real RCSB authentication.
    monkeypatch.setattr(source_preparation, "_source_candidate", _fixture_pipeline_child)
    calls = []
    actual = source_preparation.run_source_preparation
    async def observed(**kwargs):
        calls.append({k: v for k, v in kwargs.items() if k != "owner"})
        assert rig.gate._readers == 0
        # The short read guard is released BEFORE child dispatch, not held
        # across process work or human waiting.
        for path, sql in ((rig.state.db_path, "UPDATE agent_runs SET metadata_json=metadata_json"),
                          (rig.tasks.db_path, "UPDATE docking_consents SET state=state")):
            with closing(sqlite3.connect(path, timeout=0)) as conn, conn:
                conn.execute(sql)
        result = await actual(**kwargs)
        assert kwargs["owner"].snapshot()["settled"]
        return result
    monkeypatch.setattr(source_preparation, "run_source_preparation", observed)

    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            view = await rig.begin(coordinator, structure_context=pointer)
            assert_not_ready(view)
            assert view["reason_code"] == "scientific_selection_required", view
            preview = view["source_preview"]
            assert preview["pdb_id"] == "1ABC"
            assert preview["target"] == {"uniprot_id": "P06276", "taxonomy_id": 9606}
            assert preview["choice_counts"]["protein_selection"] > 0
            assert "selection" not in preview
            assert "pdb_bytes" not in preview and "component_files" not in preview
            first = deepcopy(preview)
            preview["choice_counts"].clear()
            later = await coordinator.status(root_id="root-1", owner_session_id="owner-session")
            assert later["source_preview"] == first
            duplicate = await rig.begin(coordinator, structure_context={"changed": "ignored"})
            assert duplicate["task_id"] == view["task_id"]
            assert duplicate["source_preview"] == first
            assert len(calls) == 1 and len(rig.calls) == 1
            with connection(rig.tasks.db_path) as conn:
                row = conn.execute("SELECT draft_json FROM docking_consents").fetchone()
                assert all(item == {"state": "pending"} for item in json.loads(row["draft_json"])["slots"].values())
                assert conn.execute("SELECT COUNT(*) AS n FROM tasks").fetchone()["n"] == 0
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())
    assert calls[0]["pdb_id"] == "1ABC"
    assert calls[0]["target"] == {"uniprot_id": "P06276", "taxonomy_id": 9606}
    assert calls[0]["deadline"] <= rig.mono + 180


def test_inspection_pages_are_owned_detached_bounded_and_revalidated(
        tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, "_source_candidate", _fixture_pipeline_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            method = getattr(coordinator, "source_page", None)
            assert callable(method), "owner-bound inspection paging is missing"
            view = await rig.begin(coordinator, structure_context=pointer)
            expected = view["source_preview"]["source_sha256"]
            args = dict(root_id="root-1", owner_session_id="owner-session",
                        source_sha256=expected, section="residues", offset=0, limit=2)
            page = await method(**args)
            assert page["ok"] and len(page["items"]) == 2 and page["next_offset"] == 2
            assert len(json.dumps(page, ensure_ascii=False).encode("utf-8")) <= 48 * 1024
            pristine = deepcopy(page)
            page["items"][0].clear()
            assert await method(**args) == pristine
            next_page = await method(**{**args, "offset": 2})
            assert next_page["ok"] and len(next_page["items"]) == 2
            assert next_page["next_offset"] is None
            for changes in ({"owner_session_id": "foreign"}, {"source_sha256": "0" * 64},
                            {"section": "pdb_bytes"}, {"offset": -1}, {"offset": True},
                            {"limit": 33}, {"limit": 0}, {"limit": True}):
                assert (await method(**{**args, **changes}))["ok"] is False
            invalidate_source(rig, "source")
            assert (await method(**args))["ok"] is False
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["owner", "source", "root", "model", "deadline", "draft"])
def test_changed_live_binding_before_source_return_never_publishes(
        tmp_path, target_case, monkeypatch, mutation):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, "_source_candidate", _fixture_pipeline_child)
    actual = source_preparation.run_source_preparation
    async def changed(**kwargs):
        result = await actual(**kwargs)
        invalidate_source(rig, mutation)
        return result
    monkeypatch.setattr(source_preparation, "run_source_preparation", changed)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            view = await rig.begin(coordinator, structure_context=pointer)
            assert_not_ready(view)
            assert "source_preview" not in view
            assert view["phase"] == "UNAVAILABLE"
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["owner", "source", "model", "deadline", "draft"])
def test_refresh_rechecks_source_and_never_refetches_after_invalidation(
        tmp_path, target_case, monkeypatch, mutation):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, "_source_candidate", _fixture_pipeline_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            initial = await rig.begin(coordinator, structure_context=pointer)
            assert initial["reason_code"] == "scientific_selection_required"
            invalidate_source(rig, mutation)
            monkeypatch.setattr(source_preparation, "run_source_preparation",
                                lambda **kw: pytest.fail("source refetched on status"))
            view = await coordinator.status(root_id="root-1", owner_session_id="owner-session")
            assert_not_ready(view)
            assert "source_preview" not in view
            assert view["phase"] == "UNAVAILABLE"
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["none", "foreign", "changed", "unoffered", "wrong_species"])
def test_missing_or_ineligible_source_never_dispatches_provider(
        tmp_path, target_case, monkeypatch, kind):
    rig, pointer = source_rig(tmp_path, target_case)
    if kind == "none": pointer = None
    elif kind == "foreign": pointer["session_id"] = "foreign"
    elif kind == "changed": pointer["result_digest"] = "0" * 64
    elif kind == "unoffered":
        rig.args["query"] = QUERY.replace("1ABC", "2XYZ")
        rig.output = proposal(rig.args["query"], unresolved=True)
    elif kind == "wrong_species":
        def mutate(rows):
            rows[0]["organism"] = "unsupported synthetic species"
            rows[0]["recommended_structures"][0]["organism"] = rows[0]["organism"]
        republish(target_case, mutate)
        pointer = OwnedDockingStructureEvidence(rig.state, clock=lambda: rig.wall / 1000).capture(
            target_case.agent, session_id="owner-session")
    monkeypatch.setattr(source_preparation, "run_source_preparation",
                        lambda **kw: pytest.fail("ineligible source dispatched"))
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            view = await rig.begin(coordinator, structure_context=pointer)
            assert_not_ready(view)
            assert "source_preview" not in view
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())


@pytest.mark.parametrize("pointer", [True, [], "https://example.invalid/source", {"url": "x"},
    {"schema_version": 1, "unexpected": "x" * 10000}])
def test_source_context_is_closed_and_bounded_before_any_capture_or_model(
        tmp_path, target_case, monkeypatch, pointer):
    rig, _ = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, "run_source_preparation",
                        lambda **kw: pytest.fail("invalid context dispatched"))
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            view = await rig.begin(coordinator, structure_context=pointer)
            assert view["reason_code"] == "invalid_input"
            assert rig.captures == 0 and not rig.calls
            assert not rig.rows()
    asyncio.run(run())


def test_captured_pointer_cannot_be_replaced_during_model_dispatch(
        tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    from tests.agent.test_web_docking_coordinator import wire
    import httpx
    monkeypatch.setattr(source_preparation, "_source_candidate", _fixture_pipeline_child)
    async def handler(request):
        pointer.clear()
        pointer["url"] = "https://example.invalid/forged"
        return httpx.Response(200, json=wire(rig.mode, rig.output))
    async def run():
        async with rig.client(handler) as client:
            coordinator = rig.coordinator(client)
            view = await rig.begin(coordinator, structure_context=pointer)
            assert view["reason_code"] == "scientific_selection_required", view
            assert view["source_preview"]["pdb_id"] == "1ABC"
            assert_not_ready(view)
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())


def test_source_failure_is_not_model_retry_or_scientific_success(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    calls = []
    async def unavailable(**kwargs):
        calls.append(kwargs)
        return {"ok": False, "code": "SOURCE_PREPARATION_FAILED"}
    monkeypatch.setattr(source_preparation, "run_source_preparation", unavailable)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            view = await rig.begin(coordinator, structure_context=pointer)
            assert view["phase"] == "UNAVAILABLE" and "source_preview" not in view
            assert_not_ready(view)
            await coordinator.status(root_id="root-1", owner_session_id="owner-session")
            await rig.begin(coordinator, structure_context=pointer)
            assert len(calls) == len(rig.calls) == 1
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())


def test_failed_source_retains_actual_cleanup_until_physical_close_settles(
        tmp_path, target_case, monkeypatch):
    from src.docking import chat_preparation as lifecycle
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, "_source_candidate", _fixture_pipeline_child)
    original = lifecycle.IsolatedProcess
    release, produced, finished = (threading.Event() for _ in range(3))
    entered = loop = None

    class HeldClose(original):
        def wait(self, timeout=None):
            value = super().wait(timeout=timeout)
            assert value["ok"]
            produced.set()
            return value

        def close(self):
            super().close()
            loop.call_soon_threadsafe(entered.set)
            try:
                release.wait(60)
            finally:
                finished.set()

    monkeypatch.setattr(lifecycle, "IsolatedProcess", HeldClose)
    monkeypatch.setattr(lifecycle, "CHILD_MAX_SECONDS", 10)
    async def run():
        nonlocal entered, loop
        loop, entered = asyncio.get_running_loop(), asyncio.Event()
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            import inspect
            assert "structure_context" in inspect.signature(coordinator.begin).parameters, "source integration is missing"
            task = asyncio.create_task(rig.begin(coordinator, structure_context=pointer))
            try:
                await asyncio.wait_for(entered.wait(), timeout=8)
                assert produced.is_set() and not finished.is_set()
                view = await asyncio.wait_for(asyncio.shield(task), timeout=11)
                assert_not_ready(view)
                assert "source_preview" not in view
                assert view["cleanup_pending"] is True
                assert "root-1" in coordinator._roots
                assert rig.gate._readers == 0
                closed = await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
                assert closed["cleanup_pending"] is True
                assert "root-1" in coordinator._roots and not finished.is_set()
            finally:
                release.set()
                await asyncio.gather(task, return_exceptions=True)
                for _ in range(400):
                    if finished.is_set():
                        break
                    await asyncio.sleep(0.005)
                assert finished.is_set()
                # A later explicit owner close may release only after physical
                # cleanup completes; it never restores publication eligibility.
                for _ in range(400):
                    await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
                    if "root-1" not in coordinator._roots:
                        break
                    await asyncio.sleep(0.005)
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["publication", "refresh"])
def test_source_invalidation_during_final_draft_read_cannot_publish_old_inspection(
        tmp_path, target_case, monkeypatch, phase):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, "_source_candidate", _fixture_pipeline_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            original = coordinator._read_waiting_draft
            changed = []
            final_reads = []
            def invalidated(*, root, expected_version):
                record = original(root=root, expected_version=expected_version)
                if (not changed and len(root.preparation_owners) == 2
                        and all(owner.snapshot()["settled"] for owner in root.preparation_owners)):
                    final_reads.append(True)
                    if len(final_reads) == (2 if phase == "refresh" else 1):
                        changed.append(True)
                        invalidate_source(rig, "source")
                return record
            if phase == "publication":
                monkeypatch.setattr(coordinator, "_read_waiting_draft", invalidated)
            view = await rig.begin(coordinator, structure_context=pointer)
            if phase == "refresh":
                assert view["reason_code"] == "scientific_selection_required"
                monkeypatch.setattr(coordinator, "_read_waiting_draft", invalidated)
                view = await coordinator.status(root_id="root-1", owner_session_id="owner-session")
            assert changed
            assert "source_preview" not in view and view["phase"] == "UNAVAILABLE", view
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())


@pytest.mark.parametrize("mutation", ["source", "root"])
def test_duplicate_begin_cannot_republish_stale_source_preview(tmp_path, target_case, monkeypatch, mutation):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, "_source_candidate", _fixture_pipeline_child)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            first = await rig.begin(coordinator, structure_context=pointer)
            assert first["reason_code"] == "scientific_selection_required"
            invalidate_source(rig, mutation)
            monkeypatch.setattr(source_preparation, "run_source_preparation",
                                lambda **kw: pytest.fail("duplicate source dispatch"))
            second = await rig.begin(coordinator, structure_context=pointer)
            assert second["phase"] == "UNAVAILABLE" and "source_preview" not in second
            assert len(rig.calls) == 1
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())


@pytest.mark.parametrize("phase", ["acquisition", "publication", "refresh", "page"])
def test_draft_revocation_during_source_read_cannot_dispatch_or_publish(
        tmp_path, target_case, monkeypatch, phase):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, "_source_candidate", _fixture_pipeline_child)
    resolve = OwnedDockingStructureEvidence.resolve
    dispatched, attempted = [], []
    actual = source_preparation.run_source_preparation
    async def observed(**kwargs):
        dispatched.append(True)
        return await actual(**kwargs)
    monkeypatch.setattr(source_preparation, "run_source_preparation", observed)
    calls = []
    def revoked(self, *args, **kwargs):
        value = resolve(self, *args, **kwargs)
        calls.append(True)
        if len(calls) == (3 if phase == "publication" else 1):
            attempted.append(True)
            # Real second-connection writer, no sleep or manufactured receipt.
            # A coherent read guard may reject this contending mutation rather
            # than permit an invalidated draft to be published/dispatched.
            with closing(sqlite3.connect(rig.tasks.db_path, timeout=0)) as conn, conn:
                conn.execute("UPDATE docking_consents SET state='REVOKED', version=version+1")
        return value
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            if phase in {"acquisition", "publication"}:
                monkeypatch.setattr(OwnedDockingStructureEvidence, "resolve", revoked)
            view = await rig.begin(coordinator, structure_context=pointer)
            if phase in {"refresh", "page"}:
                assert view["reason_code"] == "scientific_selection_required"
                monkeypatch.setattr(OwnedDockingStructureEvidence, "resolve", revoked)
                if phase == "page":
                    page = await coordinator.source_page(root_id="root-1", owner_session_id="owner-session",
                        source_sha256=view["source_preview"]["source_sha256"], section="residues")
                    assert page["ok"] is False
                view = await coordinator.status(root_id="root-1", owner_session_id="owner-session")
            assert attempted
            if phase == "acquisition":
                assert not dispatched, "invalidated draft dispatched canonical acquisition"
            assert view["phase"] == "UNAVAILABLE" and "source_preview" not in view
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())


def test_coherent_source_read_excludes_both_writers_then_releases_them(tmp_path, target_case, monkeypatch):
    rig, pointer = source_rig(tmp_path, target_case)
    monkeypatch.setattr(source_preparation, "_source_candidate", _fixture_pipeline_child)
    original = OwnedDockingStructureEvidence.resolve
    conflicts = []
    def guarded(self, *args, **kwargs):
        value = original(self, *args, **kwargs)
        for path, statement in (
            (rig.state.db_path, "UPDATE agent_runs SET status='failed' WHERE trace_id='target-trace'"),
            (rig.tasks.db_path, "UPDATE docking_consents SET state='REVOKED'"),
        ):
            try:
                with closing(sqlite3.connect(path, timeout=0)) as conn, conn:
                    conn.execute(statement)
            except sqlite3.OperationalError:
                conflicts.append(path)
            else:
                pytest.fail("writer entered coherent source-read boundary")
        return value
    monkeypatch.setattr(OwnedDockingStructureEvidence, "resolve", guarded)
    async def run():
        async with rig.client() as client:
            coordinator = rig.coordinator(client)
            view = await rig.begin(coordinator, structure_context=pointer)
            assert view["reason_code"] == "scientific_selection_required", view
            assert len(conflicts) == 6
            monkeypatch.setattr(OwnedDockingStructureEvidence, "resolve", original)
            invalidate_source(rig, "source")  # The same writer now succeeds.
            assert (await coordinator.status(root_id="root-1", owner_session_id="owner-session"))["phase"] == "UNAVAILABLE"
            await coordinator.close_root(root_id="root-1", owner_session_id="owner-session")
    asyncio.run(run())
