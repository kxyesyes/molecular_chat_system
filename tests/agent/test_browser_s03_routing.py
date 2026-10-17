"""Lookup context is not authorization to execute a docking calculation."""
import asyncio
import json

import pytest

from src.agent.routing.hybrid import HybridSkillRouter
from src.agent.router import SkillRouter
from src.web.chat_handler import ChatHandler


@pytest.mark.parametrize("query", [
    "查找人源丁酰胆碱酯酶 BuChE（UniProt P06276），列出可用于对接的实验结构，说明物种、结构编号和共晶配体信息。",
    "搜索 BuChE 可用于 docking 的实验结构和共晶配体",
    "Find experimental structures of BuChE suitable for docking",
])
def test_target_lookup_docking_purpose_is_not_an_action(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == "target_database_search"
    assert not decision.requires_confirmation
    assert "molecular_docking" not in decision.allowed_tools


@pytest.mark.parametrize("query", [
    "对咖啡因 CN1C(=O)N(C)c2ncn(C)c2C1=O 做反向寻靶，给出前 5 个候选靶点、依据和局限。",
    "为 CCO 反向预测靶点，说明依据",
])
def test_chinese_reverse_target_action_enters_available_workflow(query):
    decision = HybridSkillRouter().decide(query)
    assert decision.selected_skill == "reverse_target_prediction"
    assert not decision.requires_confirmation


def test_explicit_docking_action_keeps_its_input_confirmation():
    decision = HybridSkillRouter().decide("用 CCO 与 BuChE 运行对接并计算结合能")
    assert decision.selected_skill == "docking_simulation"
    assert decision.requires_confirmation


class NoDispatchAgent:
    llm = None
    skill_router = SkillRouter()

    def __init__(self):
        self.calls = []

    def execute(self, query, **kwargs):
        self.calls.append(query)
        return {"success": False, "final_answer": "unexpected dispatch"}


class Socket:
    scope = {}

    def __init__(self):
        self.messages = []

    async def send_text(self, payload):
        self.messages.append(json.loads(payload))


@pytest.mark.parametrize("query", [
    "帮我把它和那个靶点做分子对接。",
    "查找排名第一的靶点结构，并说明能否继续对接。",
    "找一下 PDE 的资料，不要生成分子，也不要运行对接；如果具体亚型影响答案，请先澄清。",
])
def test_incomplete_or_negated_requests_clarify_without_dispatch(query):
    agent, socket = NoDispatchAgent(), Socket()
    handler = ChatHandler(None, None, agent, {})
    asyncio.run(handler._process_message(socket, query, enable_rag=False,
        enable_tools=True, conversation_history=[]))
    assert agent.calls == []
    content = [m["content"] for m in socket.messages if m["type"] == "complete"][-1]
    assert any(word in content for word in ("请提供", "请明确", "需要"))
    assert "Workflow did not" not in content
