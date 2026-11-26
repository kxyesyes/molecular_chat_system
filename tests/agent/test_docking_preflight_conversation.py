import asyncio
from types import SimpleNamespace

import pytest

from src.system.model_lifecycle import ModelRequestGate
from tests.agent.test_docking_chat_controller import controller, frames, settle
from tests.agent.test_docking_control_feedback import runtime_sender
from tests.agent.test_docking_view_delivery import socket_for
from tests.agent.test_web_docking_coordinator import Rig


@pytest.mark.parametrize("items,ordinal", [
    ("bad_smiles、CCO、CCN", 1), ("CCO、bad_smiles、CCN", 2), ("CCO、CCN、bad_smiles", 3),
    ("未知，CCO，CCN", 1), ("CCO，CCN，未知", 3), ("bad_smiles、bad_smiles_2", 1),
])
def test_preflight_rejects_incomplete_ligand_list(items, ordinal):
    from src.web.docking_preflight import read_turn

    previous = read_turn("PDB 6SAM，配体 CCO、CCN，先预检。", "owner", None, 100)
    with pytest.raises(ValueError, match=f"第 {ordinal} 项"):
        read_turn(f"PDB 6SAM，配体 {items}，全部都要通过预检。", "owner", previous, 100)


def test_explicit_single_docking_replaces_previous_preflight(tmp_path, monkeypatch):
    from src.web.docking_preflight import read_turn

    async def run():
        query = "用配体 CCC 与 PDB 6SAM 进行分子对接。"
        rig = Rig(tmp_path)
        runtime, sender = runtime_sender(socket_for())
        sender._ligand_preflight = read_turn("PDB 6SAM，配体 CCO、CCN，先预检。",
            sender.websocket.scope["agent_session_id"], None, 100)
        runtime.application.model = object()
        received = []
        async with rig.client() as client:
            c = controller(runtime, rig.coordinator(client), enabled=True)
            async def begin(sender, attachment, text, history, structure, **kwargs):
                received.append((text, kwargs))
            monkeypatch.setattr(c, "_begin", begin)
            assert await c.handle(sender, {"message": query})
            await settle(runtime)
            assert received == [(query, {})]
            assert sender._ligand_preflight is None
    asyncio.run(run())


@pytest.mark.parametrize("text", [
    "请预检配体 CCO、CCN，与 PDB 6SAM 对接。",
    "Preflight ligands CCO and CCN against PDB 6SAM.",
])
def test_preflight_accepts_receptor_after_complete_list(text):
    from src.web.docking_preflight import read_turn

    batch = read_turn(text, "owner", None, 100)
    assert (batch.inputs, batch.pdb) == (("CCO", "CCN"), "6SAM")


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
