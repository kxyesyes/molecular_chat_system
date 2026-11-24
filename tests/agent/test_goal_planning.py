import asyncio
import json
import threading
from types import SimpleNamespace

import pytest

from src.agent.contracts.decision import FinishDecision, ToolDecision
from src.agent.decision_transport import DecisionResponse
from src.web.goal_planning import GoalState, run_goal


PROPERTY = ("property_calculator", {"smiles": ["CCO"]})
RULES = ("drug_likeness_assessment", {"smiles": ["CCO"]})
TARGET = ("target_database_search", {"uniprot_id": "P06276"})


@pytest.fixture(autouse=True)
def controlled_scheduler(request, monkeypatch):
    if request.node.name == "test_official_pi_drives_the_whole_goal_until_verified_finish":
        return
    from src.web import goal_planning
    class Scheduler:
        async def run(self, options, *, request_model, execute_tool):
            for _ in range(options["maxModelCalls"]):
                response = await request_model({"messages": [{"role": "user", "content": options["prompt"]}]})
                if (await execute_tool(response["toolCall"])).get("terminate"):
                    return {"runtime": "pi", "terminated": True}
            pytest.fail("goal did not finish within the Pi budget")
    monkeypatch.setattr(goal_planning, "PiRuntimeBridge", Scheduler)


def plan(*steps):
    return ToolDecision(version="1", action="tool", tool_name="plan_goal",
        arguments={"steps": [{"tool_name": name, "arguments": args} for name, args in steps]},
        purpose="choose the relevant checks")


def finish(*ids):
    return FinishDecision(version="1", action="finish", response_kind="scientific",
        text="INVENTED molecular weight: 99999", evidence_ids=list(ids))


class Model:
    def __init__(self, decide):
        self.decisions = decide
        self.inputs = []

    async def decide(self, messages, **kwargs):
        current = json.loads(messages[-1]["content"])
        self.inputs.append(current)
        decision = await self.decisions(current, len(self.inputs))
        return DecisionResponse(decision, None, "c1", {})


class Tools:
    catalog = {name: {} for name, _ in (PROPERTY, RULES, TARGET)}

    def __init__(self):
        self.calls = []
        self.version = 1
        self.execute_hook = None

    def bind(self, name, arguments, *, user_texts):
        if name not in self.catalog:
            raise ValueError("not allowed")
        return SimpleNamespace(key=json.dumps([name, arguments, self.version]), arguments=arguments, tool_name=name)

    def execute(self, step, *, dispatch_guard=None):
        if dispatch_guard and not dispatch_guard():
            return None
        self.calls.append((step.tool_name, step.arguments))
        if self.execute_hook:
            return self.execute_hook(step)
        return {"ok": True, "message": "verified " + step.tool_name, "facts": {"value": 46.07}}


async def run(state, model, tools, progress=None, **budget):
    async def quiet(*args):
        pass
    return await run_goal(state, model=model, mode="native", tools=tools, progress=progress or quiet,
        cancelled=lambda: False, input_limit=20000,
        max_model_requests=budget.get("calls", 8), max_tool_attempts=budget.get("tools", 5))


def test_goal_revision_during_real_worker_reuses_only_shared_verified_inputs():
    async def scenario():
        state = GoalState("compare CCO and look up P06276")
        tools = Tools()
        entered = asyncio.Event()
        release = threading.Event()
        loop = asyncio.get_running_loop()
        events = []
        def actual(step):
            if step.tool_name == PROPERTY[0]:
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(4)
            return {"ok": True, "message": f"verified {step.tool_name}", "facts": {"value": 46.07}}
        tools.execute_hook = actual
        async def decide(payload, n):
            if n == 1:
                return plan(PROPERTY, TARGET)
            assert payload["goal"] == state.goal and payload["revision"] == 2
            if n == 2:
                assert payload["observations"][0]["facts"] == {"value": 46.07}
                return plan(PROPERTY, RULES)
            return finish(*(r["id"] for r in payload["observations"]))
        async def progress(*event):
            events.append(event)
        task = asyncio.create_task(run(state, Model(decide), tools, progress))
        await asyncio.wait_for(entered.wait(), 3)
        assert state.revise("Keep CCO properties, replace structure lookup with drug-likeness.") == 2
        release.set()
        result = await task
        assert tools.calls == [PROPERTY, RULES]
        assert result["status"] == "completed" and "99999" not in result["text"]
        assert "verified property_calculator" in result["text"] and "verified drug_likeness_assessment" in result["text"]
        assert any(e[0:2] == (2, "reused") and e[3] == PROPERTY[0] for e in events)
        assert not any(e[0:2] == (1, "observed") for e in events)
    asyncio.run(scenario())


def test_late_plan_and_revision_during_running_notification_never_dispatch_old_work():
    async def scenario():
        state = GoalState("look up P06276")
        tools = Tools()
        async def decide(payload, n):
            if n == 1:
                state.revise("compare CCO")
                return plan(TARGET)
            if n == 2:
                return plan(PROPERTY)
            if n == 3:
                return plan(RULES)
            return finish(payload["observations"][0]["id"])
        async def progress(rev, phase, message, tool=None):
            if rev == 2 and phase == "running":
                state.revise("check the CCO rules only")
        result = await run(state, Model(decide), tools, progress)
        assert tools.calls == [RULES] and result["status"] == "completed"
    asyncio.run(scenario())


def test_missing_and_unverified_evidence_cannot_finish_or_invent_science():
    async def scenario():
        state, tools = GoalState("compare CCO and then apply rules"), Tools()
        async def decide(payload, n):
            if n == 1:
                return plan(PROPERTY, RULES)
            if n == 2:
                return finish("unknown")
            return finish(payload["observations"][0]["id"])
        result = await run(state, Model(decide), tools, calls=3)
        assert result["status"] == "partial" and "99999" not in result["text"]
        assert tools.calls == [PROPERTY]
    asyncio.run(scenario())


def test_change_of_tool_version_requires_a_new_execution_before_finish():
    async def scenario():
        state, tools = GoalState("compare CCO"), Tools()
        async def decide(payload, n):
            if n == 1:
                return plan(PROPERTY)
            if n == 2:
                tools.version += 1
                return finish(payload["observations"][0]["id"])
            if n == 3:
                return plan(PROPERTY)
            return finish(payload["observations"][-1]["id"])
        result = await run(state, Model(decide), tools)
        assert result["status"] == "completed" and tools.calls == [PROPERTY, PROPERTY]
    asyncio.run(scenario())


def test_failed_check_does_not_become_evidence_or_repeat_on_unchanged_plan():
    async def scenario():
        state, tools = GoalState("compare CCO"), Tools()
        tools.execute_hook = lambda step: {"ok": False, "message": "工具没有返回可核验结果。", "facts": []}
        async def decide(payload, n):
            if n < 3:
                return plan(PROPERTY)
            return finish("r1")
        result = await run(state, Model(decide), tools, calls=3)
        assert result["status"] != "completed" and tools.calls == [PROPERTY]
        assert "99999" not in result["text"]
    asyncio.run(scenario())


def test_timeout_on_superseded_model_call_leaves_credit_for_current_goal():
    async def scenario():
        state, tools = GoalState("compare CCO"), Tools()
        async def decide(payload, n):
            if n == 1:
                state.revise("look up P06276")
                raise asyncio.TimeoutError
            if n == 2:
                return plan(TARGET)
            return finish(payload["observations"][0]["id"])
        result = await run(state, Model(decide), tools, calls=3)
        assert result["status"] == "completed" and tools.calls == [TARGET]
        assert state.sealed and state.model_requests == 3
    asyncio.run(scenario())


def test_official_pi_drives_the_whole_goal_until_verified_finish():
    async def scenario():
        state, tools = GoalState("compare CCO"), Tools()
        async def decide(payload, n):
            if n == 1:
                return plan(PROPERTY)
            return finish(payload["observations"][0]["id"])
        result = await run(state, Model(decide), tools)
        assert result["status"] == "completed" and tools.calls == [PROPERTY]
        assert len(state.receipts) == 1 and state.receipts[0]["id"] == "r1"
        assert state.sealed
        with pytest.raises(ValueError, match="goal_closed"):
            state.revise("late goal")
    asyncio.run(scenario())


@pytest.mark.parametrize("observed_value,expected", [(46.07, RULES), (150.0, TARGET)])
def test_observed_values_change_the_next_planned_check(observed_value, expected):
    async def scenario():
        state = GoalState("Check CCO; choose the follow-up based on its recorded molecular weight. UniProt P06276.")
        tools = Tools()
        tools.execute_hook = lambda step: dict(ok=True, facts={"molecular_weight": observed_value},
                                               message=f"verified {step.tool_name}")
        async def decide(payload, n):
            if n == 1:
                return plan(PROPERTY)
            if n == 2:
                mw = payload["observations"][0]["facts"]["molecular_weight"]
                return plan(PROPERTY, RULES if mw < 100 else TARGET)
            return finish(*(row["id"] for row in payload["observations"]))
        result = await run(state, Model(decide), tools)
        assert result["status"] == "completed"
        assert tools.calls == [PROPERTY, expected]
    asyncio.run(scenario())


def test_revision_cannot_cite_a_superseded_valid_result_in_final_answer():
    async def scenario():
        state, tools = GoalState("look up P06276 and check CCO"), Tools()
        async def decide(payload, n):
            if n == 1:
                return plan(TARGET, PROPERTY)
            if n == 2:
                return plan(PROPERTY)
            ids = [row["id"] for row in payload["observations"]]
            return finish(*ids) if n == 3 else finish(ids[-1])
        async def progress(revision, phase, message, tool=None):
            if revision == 1 and phase == "observed":
                state.revise("report CCO properties only")
        result = await run(state, Model(decide), tools, progress)
        assert result["status"] == "completed" and tools.calls == [TARGET, PROPERTY]
        assert "target_database_search" not in result["text"] and state.model_requests == 4
    asyncio.run(scenario())
