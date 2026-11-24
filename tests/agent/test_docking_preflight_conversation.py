import asyncio
from types import SimpleNamespace

from src.system.model_lifecycle import ModelRequestGate
from tests.agent.test_docking_chat_controller import controller, frames, settle
from tests.agent.test_docking_control_feedback import runtime_sender
from tests.agent.test_docking_view_delivery import socket_for
from tests.agent.test_web_docking_coordinator import Rig


def test_unavailable_model_never_starts_a_preparation(tmp_path):
    async def unavailable(*args, **kwargs):
        raise ConnectionError("private provider diagnostic")
    async def run():
        rig = Rig(tmp_path)
        runtime, sender = runtime_sender(socket_for())
        runtime.application.model_request_gate = ModelRequestGate()
        runtime.application.model = SimpleNamespace(generate=unavailable)
        async with rig.client() as client:
            c = controller(runtime, rig.coordinator(client), enabled=True)
            assert await c.handle(sender, {"message": "预检两个配体 CCO 和 CCN。"})
            await settle(runtime)
            result = next(f for f in frames(sender.websocket) if f["type"] == "agent_result")
            assert result["status"] == "failed"
            assert "请重试" in result["display_answer"] and "diagnostic" not in str(result)
            assert not sender._ligand_preflight.rows and not c.coordinator._roots
    asyncio.run(run())


def test_preflight_followups_reuse_actual_rows_and_only_offer_requested_subset(tmp_path, monkeypatch):
    from src.docking import chat_preparation

    caffeine = "Cn1c(=O)c2c(ncn2C)n(C)c1=O"
    policies = iter(["inspect", "explain", "compare_history", "other", "require_all", "use_passed"])
    calls, confirmations = [], []

    async def generate(prompt, **kwargs):
        assert "6SAM" in prompt
        return next(policies)

    async def prepare(operation, *, inputs, deadline, owner):
        assert operation == "ligand"
        calls.append(inputs["smiles"])
        return chat_preparation.build_ligand_sdf(inputs["smiles"])

    async def run():
        rig = Rig(tmp_path)
        runtime, sender = runtime_sender(socket_for())
        runtime.application.model = SimpleNamespace(generate=generate)
        runtime.application.model_request_gate = ModelRequestGate()
        async with rig.client() as client:
            c = controller(runtime, rig.coordinator(client), enabled=True)
            async def begin(sender, attachment, query, history, structure, **kwargs):
                confirmations.append(kwargs)
            async def show(*args):
                pass
            monkeypatch.setattr(c, "_begin", begin)
            monkeypatch.setattr(c, "_show", show)
            monkeypatch.setattr(chat_preparation, "run_preparation", prepare)
            for message in [f"PDB 6SAM，配体 CCO、{caffeine}、CCN，先检查能不能用。",
                            "第二个卡在哪里？", "上次可以，是什么变了？", "今天聊点别的",
                            "得全部能做才继续。", "改主意了，可以用的就先做。"]:
                if message == "今天聊点别的":
                    assert not await c.handle(sender, {"message": message})
                    continue
                assert await c.handle(sender, {"message": message})
                await settle(runtime)
            answers = [f["display_answer"] for f in frames(sender.websocket) if f["type"] == "agent_result"]
            assert len(answers) == 4 and "候选 2" in answers[1] and "结构" in answers[1]
            assert "缺少可核对的上次制备条件" in answers[2] and "整批已暂停" in answers[3]
            assert len(calls) == 3
            assert len(confirmations) == 1
            assert confirmations[0]["batch"]["ligands"] == [
                {"ordinal": 1, "smiles": "CCO"}, {"ordinal": 3, "smiles": "CCN"}]
            assert [r["ordinal"] for r in confirmations[0]["skipped"]] == [2]
            assert not rig.calls and not c.coordinator._roots
            await c.disconnect(sender)
            assert not await c.handle(sender, {"message": "第二个卡在哪里？"})
    asyncio.run(run())
