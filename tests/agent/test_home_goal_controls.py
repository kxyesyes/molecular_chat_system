import asyncio
import json

from tests.agent.test_web_decision_runtime import ActualSocket, actual_app, cookie_for


def test_candidate_question_is_consumed_only_by_a_selection_on_its_socket(actual_app):
    async def scenario():
        batch = ["CCO", "CCN", "CO"]
        pending_goal = "只查我选中的分子的类药规则，先让我选择"
        async def respond(payload):
            current = json.loads(payload["messages"][-1]["content"])
            goal = current["goal"]
            observations = current["observations"]
            selection = current.get("candidate_selection")
            if goal == "换一批 CCCC、CCCCC，先让我选":
                return dict(version="1", action="clarify", question="要哪个？", missing_fields=["candidate"])
            if goal == "先别选，CO 的分子量是多少？":
                if not current["feedback"]:
                    return dict(version="1", action="tool", tool_name="plan_goal", purpose="错误地重建批次",
                                arguments={"replace_candidate_batch": True, "steps": [
                                    dict(tool_name="property_calculator", arguments={"smiles": ["CO"]})]})
                else:
                    step = dict(tool_name="candidate_view", arguments={"field": "molecular_weight", "show": 3})
                return dict(version="1", action="tool", tool_name="plan_goal",
                            purpose="读取所问行", arguments={"steps": [step]})
            replacing = goal == "先不选了，请用 CO 和 CCO 作为新的一批，重新计算分子量"
            if goal in ("哪个最轻", "重新比较一下这批，哪个最轻？", "把 logP 再列一遍"):
                assert [row["smiles"] for row in current["candidate_batch"]] == batch
                assert not selection
                view = {"field": "logp", "show": "all"} if "logP" in goal else {"field": "molecular_weight", "show": "min"}
                if not current["feedback"]:
                    view["value"] = 99999  # Only verified values can be displayed.
                return dict(version="1", action="tool", tool_name="plan_goal", purpose="查看已有结果",
                            arguments={"steps": [{"tool_name": "candidate_view", "arguments": view}]})
            if goal == pending_goal or goal == "第二个" and not selection:
                return dict(version="1", action="clarify", question="哪一个？", missing_fields=["candidate"])
            if selection:
                assert selection == dict(goal=pending_goal, position=2, smiles="CCN")
                tool, inputs = "drug_likeness_assessment", ["CCN"]
                if goal == "第二个" and not current["plan_revision"] and not current["feedback"]:
                    inputs = ["CO"]  # A stale model proposal must not dispatch for the third row.
            else:
                tool, inputs = "property_calculator", ["CO", "CCO"] if replacing else batch
                if replacing and not current["plan_revision"]:
                    assert not observations and not selection
            if current["plan_revision"]:
                return dict(version="1", action="finish", response_kind="scientific", text="完成",
                            evidence_ids=[r["id"] for r in observations if r["arguments"]["smiles"] == inputs])
            args = {"steps":[dict(tool_name=tool, arguments={"smiles": inputs})]}
            if replacing:
                args["replace_candidate_batch"] = True
            return dict(version="1", action="tool", tool_name="plan_goal", purpose="执行请求", arguments=args)
        async def ask(ws, message):
            await ws.send(dict(type="goal_start", message=message, enable_tools=True))
            calls = []
            while True:
                frame = await ws.receive()
                if frame.get("phase") == "running":
                    calls.append(frame["tool"])
                if frame["type"] == "complete":
                    if message in ("哪个最轻", "先别选，CO 的分子量是多少？"):
                        assert "第 3 个 CO" in frame["content"] and "32.04 Da" in frame["content"]
                    if message == "把 logP 再列一遍":
                        positions = [frame["content"].index(f"第 {i} 个 {s}（logP") for i, s in enumerate(batch, 1)]
                        assert positions == sorted(positions)
                    return frame["status"], calls
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as ws:
                await ws.ready()
                assert await ask(ws, "比较 CCO、CCN、CO") == ("completed", ["property_calculator"])
                assert await ask(ws, pending_goal) == ("waiting_for_input", [])
                assert await ask(ws, "哪个最轻") == ("waiting_for_input", [])
                assert await ask(ws, "先别选，CO 的分子量是多少？") == ("waiting_for_input", [])
                assert await ask(ws, "把 logP 再列一遍") == ("waiting_for_input", [])
                assert await ask(ws, "重新比较一下这批，哪个最轻？") == ("waiting_for_input", [])
                async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as other:
                    await other.ready()
                    assert await ask(other, "第二个") == ("not_calculated", [])
                assert await ask(ws, "第四个") == ("waiting_for_input", [])
                assert await ask(ws, "第二个") == ("completed", ["drug_likeness_assessment"])
                assert await ask(ws, pending_goal) == ("waiting_for_input", [])
                assert await ask(ws, "CCN") == ("completed", [])
                assert await ask(ws, pending_goal) == ("waiting_for_input", [])
                assert await ask(ws, "哪个最轻") == ("waiting_for_input", [])
                assert await ask(ws, "先不选了，请用 CO 和 CCO 作为新的一批，重新计算分子量") == ("completed", ["property_calculator"])
                assert await ask(ws, pending_goal) == ("waiting_for_input", [])
                assert await ask(ws, "取消") == ("cancelled", [])
                assert await ask(ws, "第二个") == ("not_calculated", [])
                assert await ask(ws, "比较 CCO、CCN、CO") == ("completed", ["property_calculator"])
                assert await ask(ws, pending_goal) == ("waiting_for_input", [])
                assert await ask(ws, "换一批 CCCC、CCCCC，先让我选") == ("not_calculated", [])
                assert await ask(ws, "第二个") == ("not_calculated", [])
    asyncio.run(scenario())


def test_finished_goal_followups_use_only_delivered_socket_evidence(actual_app):
    from src.agent.evidence import EvidenceLedger

    async def scenario():
        async def respond(payload):
            current = json.loads(payload["messages"][-1]["content"])
            goal = current["goal"]
            observations = current["observations"]
            prop = dict(tool_name="property_calculator", arguments={"smiles": ["CCO", "CCN"]})
            rule = dict(tool_name="drug_likeness_assessment", arguments={"smiles": ["CCN"]})
            if goal == "第二个呢" or ("第二个" in goal and not observations):
                assert not observations
                return dict(version="1", action="clarify", question="请提供分子。", missing_fields=["smiles"])
            step = rule if "类药" in goal else prop
            args = {"steps": [step]}
            if "轻多少" in goal:
                args["mass_difference"] = {"subject": 2, "reference": "max"}
            if current["plan_revision"]:
                return dict(version="1", action="finish", response_kind="scientific", text="完成",
                            evidence_ids=[r["id"] for r in observations if r["tool_name"] == step["tool_name"]])
            return dict(version="1", action="tool", tool_name="plan_goal", purpose="核对请求", arguments=args)

        async def ask(socket, message):
            await socket.send(dict(type="goal_start", message=message, enable_tools=True))
            frames = []
            while not frames or frames[-1]["type"] != "complete":
                frames.append(await socket.receive())
            return frames[-1], [f["tool"] for f in frames if f.get("phase") == "running"]

        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                first, calls = await ask(socket, "比较 CCO 和 CCN 的分子量")
                assert first["status"] == "completed" and calls == ["property_calculator"]
                difference, calls = await ask(socket, "第二个比分子量最大的那个轻多少")
                assert difference["status"] == "completed" and "0.99 Da" in difference["content"]
                assert "CCN" in difference["content"] and "CCO" in difference["content"] and calls == []
                rules, calls = await ask(socket, "只给较轻的那个查类药规则")
                assert rules["status"] == "completed" and calls == ["drug_likeness_assessment"]
                meta = bundle.app.decision_runtime.store.get_run(rules["trace_id"])["metadata"]
                receipt, = meta["goal_receipts"]
                assert receipt["provenance"]["input_digest"] == EvidenceLedger.output_digest(
                    ["drug_likeness_assessment", receipt["tool_version"], {"query": "CCN"}])
                again, calls = await ask(socket, "原来第二个轻多少")
                assert "0.99 Da" in again["content"] and not calls
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as other:
                await other.ready()
                empty, calls = await ask(other, "第二个呢")
                assert "请补充" in empty["content"] and not calls
    asyncio.run(scenario())


def test_goal_replans_on_the_actual_pi_socket_and_audits_only_real_calls(actual_app):
    async def check():
        awaiting_old_plan, release = asyncio.Event(), asyncio.Event()
        prop = dict(tool_name="property_calculator", arguments={"smiles": ["CCO"]})
        rule = dict(tool_name="drug_likeness_assessment", arguments={"smiles": ["CCO"]})
        async def respond(payload):
            current = json.loads(payload["messages"][-1]["content"])
            if current["revision"] == 1 and current["observations"]:
                awaiting_old_plan.set()
                await release.wait()
            if current["revision"] == current["plan_revision"] == 2:
                return dict(version="1", action="finish", response_kind="scientific", text="仅引用核验记录",
                            evidence_ids=[current["observations"][0]["id"]])
            return dict(version="1", action="tool", tool_name="plan_goal", purpose="核对性质",
                        arguments={"steps": [prop, rule] if current["revision"] == 1 else [prop]})
        async with actual_app(ordinary_policy=None, respond=respond) as bundle:
            async with ActualSocket(bundle.app, await cookie_for(bundle.app)) as socket:
                await socket.ready()
                await socket.send(dict(type="goal_start", message="先检查 CCO 的性质，再评估类药规则", enable_tools=True))
                ack = await socket.receive()
                await asyncio.wait_for(awaiting_old_plan.wait(), 12)
                await socket.send(dict(type="goal_update", trace_id=ack["trace_id"], turn_id=ack["turn_id"],
                                       revision=1, message="只保留刚才的性质结果"))
                frames = []
                while not frames or frames[-1]["type"] != "goal_updated":
                    frames.append(await socket.receive())
                release.set()
                while frames[-1]["type"] != "complete":
                    frames.append(await socket.receive())
                assert frames[-1]["status"] == "completed" and frames[-1]["goal_revision"] == 2
                assert "46.07" in frames[-1]["content"] and "Lipinski" not in frames[-1]["content"]
                assert [f["tool"] for f in frames if f.get("phase") == "running"] == ["property_calculator"]
                assert [f["revision"] for f in frames if f.get("phase") == "reused"] == [2]
                meta = bundle.app.decision_runtime.store.get_run(ack["trace_id"])["metadata"]
                receipt, = meta["goal_receipts"]
                assert meta["goal_revision"] == 2 and receipt["ok"] and receipt["revision"] == 1
                assert receipt["tool_name"] == "property_calculator" and receipt["provenance"]["output_digest"]
                assert not bundle.protocol_errors
    asyncio.run(check())


def test_goal_update_belongs_to_the_active_socket_and_revision(actual_app, monkeypatch):
    async def check():
        async with actual_app(ordinary_policy=None) as bundle:
            cookie = await cookie_for(bundle.app)
            async with ActualSocket(bundle.app, cookie) as first, ActualSocket(bundle.app, cookie) as other:
                assert (await first.ready())["capabilities"].get("goal_steering") is True
                await other.ready()
                for extra in ({"enable_tools": False}, {"observations": [{"ok": True}]}):
                    await first.send({**dict(type="goal_start", message="比较 CCO 的性质", enable_tools=True), **extra})
                    assert (await first.receive())["code"] == "invalid_goal_request"
                from src.web import goal_planning
                finish = asyncio.Event()
                async def run(state, **kwargs):
                    await finish.wait()
                    return {"status": "completed", "text": state.goal}
                monkeypatch.setattr(goal_planning, "run_goal", run)
                await first.send(dict(type="goal_start", message="比较 CCO 的性质", enable_tools=True))
                ack = await first.receive()
                assert ack["type"] == "request_accepted" and ack["goal_revision"] == 1
                change = dict(type="goal_update", message="改为查看 CCN", trace_id=ack["trace_id"],
                              turn_id=ack["turn_id"], revision=1)
                await other.send(change)
                assert (await other.receive())["code"] == "goal_update_rejected"
                await first.send(change)
                async def until(predicate):
                    for _ in range(30):
                        frame = await first.receive()
                        if predicate(frame):
                            return frame
                    raise AssertionError("missing goal frame")
                assert (await until(lambda f: f["type"] == "goal_updated"))["revision"] == 2
                await first.send(change)
                assert (await until(lambda f: f.get("code") == "goal_update_rejected"))["revision"] == 2
                finish.set()
                assert (await until(lambda f: f["type"] == "complete"))["content"] == change["message"]
                await first.send({**change, "revision": 2})
                assert (await first.receive())["code"] == "goal_update_rejected"
    asyncio.run(check())
