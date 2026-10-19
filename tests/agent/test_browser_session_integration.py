"""HTTP → task thread → real Supervisor/store; scientific tools are fixtures."""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from src.agent.persistence import SQLiteAgentStateStore
from src.agent.specialists import build_default_specialists
from src.agent.supervisor import SupervisorAgent
from src.task_runtime import routes as task_routes
from src.task_runtime.manager import TaskManager
from src.web.agent_session_config import setup_agent_sessions
from src.web.routes import agent_workflow_routes
from test_delegated_session_lifecycle import SinglePlanner
from test_supervisor_delegation import build_registry
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
import threading
from types import SimpleNamespace

import httpx
import pytest

from src.system.model_lifecycle import ModelRequestGate
from src.task_runtime.models import TaskStatus


@pytest.fixture
def anyio_backend():
    return "asyncio"


def workflow_app(tmp_path, monkeypatch, *, gate=None, planner=None, factory_hook=None):
    registry, tools = build_registry()
    store = SQLiteAgentStateStore(tmp_path / "runs.sqlite")
    manager = TaskManager(tmp_path / "tasks.sqlite", max_workers=1)
    monkeypatch.setattr(agent_workflow_routes, "get_task_manager", lambda: manager)
    monkeypatch.setattr(task_routes, "get_task_manager", lambda: manager)

    def supervisor():
        if factory_hook:
            factory_hook()
        return SupervisorAgent(
            tools=tools, planner=planner, state_store=store,
            tool_registry=registry, specialists=build_default_specialists(),
        )

    app = FastAPI()
    setup_agent_sessions(app)
    agent_workflow_routes.setup_agent_workflow_routes(app, supervisor, gate)
    task_routes.setup_task_routes(app)
    return app, manager, registry, tools, store


@pytest.mark.anyio
@pytest.mark.parametrize("during_initialization", [False, True])
async def test_http_cancel_stops_real_delegated_workflow(tmp_path, monkeypatch, during_initialization):
    started, release = threading.Event(), threading.Event()

    def hold():
        started.set()
        assert release.wait(10)

    app, manager, registry, tools, store = workflow_app(
        tmp_path, monkeypatch, factory_hook=hold if during_initialization else None,
    )
    target = tools["target_database_search"]
    invoke = target.execute

    def held_target(query):
        hold()
        return invoke(query)

    if not during_initialization:
        monkeypatch.setattr(target, "execute", held_target)
    adapter = registry.resolve("target_database_search")
    adapter.spec = replace(adapter.spec, timeout_seconds=10)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.post("/api/agent/workflows/run", json={
                "query": "Design 1 molecule targeting PDE5A", "skill_name": "target_driven_design",
            })
            assert response.status_code == 200
            task_id = response.json()["data"]["task_id"]
            assert await asyncio.to_thread(started.wait, 3)
            response = await client.post(f"/api/tasks/{task_id}/cancel")
            assert response.json()["data"]["status"] == "cancel_requested"
            release.set()
            await asyncio.to_thread(manager.wait_for_completion, task_id)
            assert manager.get(task_id).status is TaskStatus.CANCELED
            assert all(not tools[name].calls for name in (
                "llm_molecular_generator", "property_calculator", "admet_predictor",
                "activity_predictor", "candidate_ranker",
            )), "cancellation must reach the real session before downstream dispatch"
            assert len(target.calls) == (0 if during_initialization else 1)
            events = manager.store.events(task_id)
            assert sum(event.is_terminal for event in events) == 1
    finally:
        release.set()
        await asyncio.to_thread(manager.executor.shutdown, wait=True)
        registry.close()


@pytest.mark.anyio
@pytest.mark.parametrize("shutdown", [False, True])
async def test_http_adapter_timeout_retains_gate_until_physical_worker_exit(tmp_path, monkeypatch, shutdown):
    from src.agent.orchestrators import WorkflowStep
    from src.agent.planning import WorkflowPlan

    name = "reverse_target_predictor" if shutdown else "property_calculator"

    class Planner:
        def plan(self, context):
            return WorkflowPlan(workflow_name="comprehensive_evaluation", steps=[
                WorkflowStep("evaluate", name, input_data="CCO"),
            ])

    gate = ModelRequestGate()
    started, release = threading.Event(), threading.Event()
    app, manager, registry, tools, _ = workflow_app(tmp_path, monkeypatch, gate=gate, planner=Planner())
    tool = tools[name]
    invoke = tool.execute

    def held(query):
        started.set()
        assert release.wait(10)
        return invoke(query)

    monkeypatch.setattr(tool, "execute", held)
    adapter = registry.resolve(name)
    adapter.spec = replace(adapter.spec, timeout_seconds=.05)
    exclusive_entered = asyncio.Event()
    closed = []

    if shutdown:
        from src.web.app import MolecularChatApp
        application = MolecularChatApp.__new__(MolecularChatApp)
        application.model_request_gate = gate
        application._llm_watch_task = None
        application._reverse_preload_tool = tool
        application.agent_tool_registry = registry
        application.model = SimpleNamespace(close=lambda: closed.append("model"))
        application.molecular_generator_model = None
        tool.close = lambda: closed.append("tool")

    async def close_models():
        if shutdown:
            await application.shutdown()
            exclusive_entered.set()
        else:
            async with gate.exclusive():
                exclusive_entered.set()

    closing = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.post("/api/agent/workflows/run", json={
                "query": "CCO", "skill_name": "comprehensive_evaluation",
            })
            task_id = response.json()["data"]["task_id"]
            assert await asyncio.to_thread(started.wait, 3)
            await asyncio.wait_for(asyncio.to_thread(manager.wait_for_completion, task_id), 3)
            assert manager.get(task_id).status in {TaskStatus.FAILED, TaskStatus.TIMED_OUT}
            retry = adapter.execute({"query": "CCO"})
            assert retry.quality.get("capacity_exhausted") is True
            closing = asyncio.create_task(close_models())
            await asyncio.sleep(.05)
            assert not closed, "shutdown must not close the shared scientific tool before gate drain"
            assert not exclusive_entered.is_set(), "outer timeout must not release a live child worker's lease"
            assert gate._readers == 1
            release.set()
            await asyncio.wait_for(closing, 3)
            assert tool.calls == ["CCO"]
            assert gate._readers == 0
            if shutdown:
                assert closed.count("model") == closed.count("tool") == 1
    finally:
        release.set()
        if closing:
            await asyncio.wait_for(closing, 3)
        await asyncio.to_thread(manager.executor.shutdown, wait=True)
        registry.close()


@pytest.mark.anyio
async def test_background_cleanup_failure_keeps_lease_even_after_duplicate_submission(tmp_path, monkeypatch, caplog):
    class FailedJoin(ThreadPoolExecutor):
        def shutdown(self, wait=True, **kwargs):
            super().shutdown(wait=wait, **kwargs)
            if wait:
                raise RuntimeError("synthetic-sensitive-cleanup-detail")

    monkeypatch.setattr("src.agent.tooling.adapters.ThreadPoolExecutor", FailedJoin)
    gate = ModelRequestGate()
    app, manager, registry, _, _ = workflow_app(tmp_path, monkeypatch, gate=gate, planner=SinglePlanner())
    closing = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.post("/api/agent/workflows/run", json={
                "query": "CCO", "skill_name": "comprehensive_evaluation",
            })
            task_id = response.json()["data"]["task_id"]
            await asyncio.to_thread(manager.wait_for_completion, task_id)
            await asyncio.gather(*tuple(gate._background), return_exceptions=True)
            assert gate._readers == 1
            assert len(gate._background_owners) == 1
            owner, lease = next(iter(gate._background_owners.items()))
            assert owner.status == "unresolved"
            assert "synthetic-sensitive-cleanup-detail" not in caplog.text
            with pytest.raises(Exception):
                await gate.submit_background(manager, task_type="test", task_id=task_id,
                                             payload={}, handler=lambda _: {})
            assert gate._background_owners == {owner: lease}
            assert gate._readers == 1

            async def exclusive():
                async with gate.exclusive():
                    pytest.fail("failed cleanup must keep model replacement blocked")

            closing = asyncio.create_task(exclusive())
            await asyncio.sleep(.03)
            assert not closing.done()
    finally:
        if closing:
            closing.cancel()
            await asyncio.gather(closing, return_exceptions=True)
        await asyncio.to_thread(manager.executor.shutdown, wait=True)
        registry.close()
        # The injected shutdown error occurs AFTER the real join; release only
        # for test teardown. Production deliberately has no such forced release.
        for lease in tuple(gate._background_owners.values()):
            await lease.__aexit__(None, None, None)
        gate._background_owners.clear()


@pytest.mark.anyio
@pytest.mark.parametrize("failure", ["submit", "claim", "queued_cancel"])
async def test_background_unstarted_handler_releases_owner(tmp_path, monkeypatch, failure):
    gate = ModelRequestGate()
    app, manager, registry, tools, _ = workflow_app(tmp_path, monkeypatch, gate=gate, planner=SinglePlanner())
    started, release = threading.Event(), threading.Event()

    if failure == "submit":
        manager.executor.shutdown(wait=True)
    elif failure == "claim":
        def fail_claim(*args, **kwargs):
            raise RuntimeError("synthetic claim failure")
        monkeypatch.setattr(manager, "_mark_running", fail_claim)
    else:
        def blocker():
            started.set()
            assert release.wait(10)
        manager.executor.submit(blocker)
        assert await asyncio.to_thread(started.wait, 3)
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.post("/api/agent/workflows/run", json={
                "query": "CCO", "skill_name": "comprehensive_evaluation",
            })
            task_id = response.json()["data"]["task_id"]
            if failure == "queued_cancel":
                response = await client.post(f"/api/tasks/{task_id}/cancel")
                assert response.json()["data"]["status"] == "cancel_requested"
            release.set()
            await asyncio.wait_for(asyncio.gather(*tuple(gate._background)), 3)
            assert manager.get(task_id).status is (
                TaskStatus.CANCELED if failure == "queued_cancel" else TaskStatus.FAILED
            )
            assert gate._readers == 0
            assert not gate._background_owners
            assert all(not tool.calls for tool in tools.values())
    finally:
        release.set()
        await asyncio.to_thread(manager.executor.shutdown, wait=True)
        registry.close()


def test_browser_owner_reaches_task_and_run_with_scoped_replay(tmp_path, monkeypatch):
    registry, tools = build_registry()
    store = SQLiteAgentStateStore(tmp_path / "runs.sqlite")
    manager = TaskManager(tmp_path / "tasks.sqlite", max_workers=1)
    monkeypatch.setenv("MEDCHAT_AGENT_SESSION_DB", str(tmp_path / "sessions.sqlite"))
    monkeypatch.setattr(agent_workflow_routes, "get_task_manager", lambda: manager)
    monkeypatch.setattr(task_routes, "get_task_manager", lambda: manager)
    completed = []

    class RecordingSupervisor(SupervisorAgent):
        def run(self, *args, **kwargs):
            result = super().run(*args, **kwargs)
            completed.append(result)
            return result

    def supervisor():
        return RecordingSupervisor(
            tools=tools, planner=SinglePlanner(), state_store=store,
            tool_registry=registry, specialists=build_default_specialists(),
        )

    app = FastAPI()
    setup_agent_sessions(app)
    agent_workflow_routes.setup_agent_workflow_routes(app, supervisor)
    task_routes.setup_task_routes(app)
    payload = {
        "query": "CCO", "skill_name": "comprehensive_evaluation",
        "metadata": {"idempotency_key": "browser-retry", "session_id": "forged"},
    }
    try:
        with TestClient(app, base_url="http://localhost") as first, TestClient(
            app, base_url="http://localhost"
        ) as second:
            task_ids = []
            for client in (first, first, second):
                response = client.post("/api/agent/workflows/run", json=payload)
                assert response.status_code == 200
                task_ids.append(response.json()["data"]["task_id"])
                # A single-worker barrier waits for the real handler/settlement.
                manager.executor.submit(lambda: None).result(timeout=10)
            assert all(result["status"] == "succeeded" for result in completed)
            traces = [result["trace_id"] for result in completed]
            assert traces[0] == traces[1] and traces[0] != traces[2]
            assert tools["property_calculator"].calls == ["CCO", "CCO"]
            owners = [manager.store.get_agent_owner(task_id) for task_id in task_ids]
            assert owners[0] == owners[1] and owners[0] != owners[2]
            assert owners[0] != "forged"
            assert [store.get_run(trace)["session_id"] for trace in traces] == owners
            assert first.get(f"/api/tasks/{task_ids[0]}").status_code == 200
            assert second.get(f"/api/tasks/{task_ids[0]}").status_code == 404
            assert second.get(f"/api/tasks/{task_ids[0]}/events").status_code == 404
            assert second.post(f"/api/tasks/{task_ids[0]}/cancel").status_code == 404
            visible = second.get("/api/tasks").json()["data"]
            assert [record["task_id"] for record in visible] == [task_ids[2]]
            assert all("owner_session_id" not in record for record in visible)
    finally:
        manager.executor.shutdown(wait=True)
        registry.close()


@pytest.mark.anyio
async def test_http_routing_timeout_retains_model_until_provider_exits(tmp_path, monkeypatch):
    from src.agent.runtime import allocation
    from src.web.app import MolecularChatApp

    started, release, exited = threading.Event(), threading.Event(), threading.Event()
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(allocation, "_slots", slots)
    monkeypatch.setattr(allocation, "DEFAULT_ALLOCATION_TIMEOUT_SECONDS", .08)

    class Model:
        closed = False

        def generate(self, *args, **kwargs):
            started.set()
            try:
                assert release.wait(10)
                assert not self.closed
                return '{}'
            finally:
                exited.set()

        def close(self):
            self.closed = True

    model = Model()
    manager = TaskManager(tmp_path / "routing-tasks.sqlite", max_workers=1)
    monkeypatch.setattr(agent_workflow_routes, "get_task_manager", lambda: manager)
    gate = ModelRequestGate()
    app = FastAPI()
    setup_agent_sessions(app)
    agent_workflow_routes.setup_agent_workflow_routes(
        app, lambda: SupervisorAgent(tools={}, llm=model), gate,
    )
    application = MolecularChatApp.__new__(MolecularChatApp)
    application.model_request_gate = gate
    application._llm_watch_task = None
    application.model = model
    application.molecular_generator_model = None
    closing = None
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://localhost") as client:
            response = await client.post("/api/agent/workflows/run", json={
                "query": "Evaluate CCO pIC50 and ADMET",
            })
            task_id = response.json()["data"]["task_id"]
            assert await asyncio.to_thread(started.wait, 3)
            await asyncio.wait_for(asyncio.to_thread(manager.wait_for_completion, task_id), 3)
            assert manager.get(task_id).status in {TaskStatus.FAILED, TaskStatus.TIMED_OUT}
            assert not exited.is_set()
            assert not slots.acquire(blocking=False), "a timed-out provider still occupies bounded capacity"
            closing = asyncio.create_task(application.shutdown())
            await asyncio.sleep(.05)
            assert not model.closed, "routing timeout must not release a live model's lease"
            assert not closing.done()
            assert gate._readers == 1
            release.set()
            await asyncio.wait_for(closing, 3)
            assert exited.is_set() and model.closed
            assert gate._readers == 0
            assert slots.acquire(blocking=False)
            slots.release()
            assert await asyncio.to_thread(allocation.routing_model_call, lambda: "shared pool still usable") == "shared pool still usable"
    finally:
        release.set()
        if closing:
            await asyncio.wait_for(closing, 3)
        await asyncio.to_thread(manager.executor.shutdown, wait=True)
