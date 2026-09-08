"""Regressions found by real-weight acceptance; no checkpoints needed here."""
import pytest

from src.agent.supervisor import SupervisorAgent
from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
from src.web.chat_handler import ChatHandler


def test_abstaining_router_is_called_once_per_execution():
    class AbstainingRouter:
        calls = 0
        def route(self, query, llm=None):
            self.calls += 1
            return None
    router = AbstainingRouter()
    reply = SupervisorAgent(tools={}, skill_router=router).execute("你好")
    assert reply["tools_used"] == []
    assert router.calls == 1


def invalid_activity_reply():
    return SupervisorAgent(tools={"activity_predictor": ActivityPredictorTool()}).execute(
        "请预测 PDE5A 活性；SMILES: CC(C)((")


def test_supervisor_keeps_structured_failure_in_chat_envelope():
    reply = invalid_activity_reply()
    assert reply["success"] is False
    assert reply.get("status") == "failed"
    assert reply.get("error", {}).get("code") == "invalid_input"
    assert reply.get("warnings") == reply["agent_result"].warnings


def test_real_invalid_input_reason_reaches_chat_failure_content():
    reply = invalid_activity_reply()
    message = ChatHandler._agent_failure_content(reply)
    assert "SMILES" in message
    assert "无效" in message


@pytest.mark.parametrize("message", ["Workflow failed", "No workflow steps were executed"])
def test_generic_failure_caption_does_not_hide_safe_error(message):
    assert ChatHandler._agent_failure_content({
        "final_answer": message, "error": {"message": "请补充完整 SMILES。"}
    }) == "请补充完整 SMILES。"


def test_generic_failure_caption_still_redacts_sensitive_cause():
    secret = "sk" + "-" + "synthetic-secret-for-regression-only-123456789"
    result = ChatHandler._agent_failure_content({
        "final_answer": "Workflow failed", "error": {"message": secret}
    })
    assert secret not in result
    assert result == "科学计算未成功完成，请检查输入或工具状态后重试。"
