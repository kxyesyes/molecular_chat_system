"""Offline WebSocket wiring checks, not real model/browser acceptance."""
import asyncio
import pytest

from tests.agent.test_browser_s02_context import make_handler, Socket
from tests.agent.test_browser_property_constraints import _generation_result
from tests.test_scientific_reference_target_context import confirmed, pointer, FOLLOWUP, SOURCE


def test_handler_passes_confirmed_source_target_to_optimizer(tmp_path, confirmed):
    _, service, view, _ = confirmed
    handler, _, _ = make_handler(tmp_path)
    handler.scientific_references = service
    calls = []

    async def capture(query, *args, **kwargs):
        calls.append((query, kwargs))
        return {"success": False, "status": "failed", "message": "offline execution capture"}

    handler._execute_agent = capture
    asyncio.run(handler._process_message(Socket(), FOLLOWUP, enable_rag=False, enable_tools=True,
        conversation_history=[], session_id="owner", reference=pointer(view)))
    assert len(calls) == 1
    assert calls[0][0].endswith("\n靶点: PDE")
    assert calls[0][1]["resolved_molecule"].canonical_smiles == "CCN"


def test_handler_displays_independent_generation_constraint_summary(tmp_path):
    handler, _, _ = make_handler(tmp_path)

    async def evidence(*args, **kwargs):
        return {**_generation_result(), "success": True, "status": "completed",
                "final_answer": "offline original result retained", "active_skill": "target_driven_design",
                "workflow_plan": {"workflow_name": "target_driven_design"}}

    handler._execute_agent = evidence
    socket = Socket()
    asyncio.run(handler._process_message(socket, SOURCE, enable_rag=False, enable_tools=True,
        conversation_history=[], session_id="owner"))
    answer = [frame["content"] for frame in socket.messages if frame["type"] == "complete"][-1]
    assert "用户约束逐项检查" in answer
    assert "46.07" in answer and "3.1 | 不满足" in answer
    assert "offline original result retained" in answer


@pytest.mark.parametrize("target", ["PDE", "BuChE"])
def test_resolved_candidate_optimization_query_reaches_activity_parser(target):
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    payload = {"query": FOLLOWUP + "\n靶点: " + target, "smiles": ["CCN"], "target": target}
    _, smiles, resolved_target = parse_activity_input(payload, ActivityPredictorTool())
    assert smiles == ["CCN"] and resolved_target == target
    payload["query"] += "\n针对 UNKNOWN"
    with pytest.raises(ValueError):
        parse_activity_input(payload, ActivityPredictorTool())


def test_candidate_ordinal_prefix_cannot_hide_unknown_protein_label():
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    with pytest.raises(ValueError):
        parse_activity_input({"query": "对第2个候选蛋白 UNKNOWN 的活性；靶点: PDE", "smiles": ["CCN"]}, ActivityPredictorTool())


@pytest.mark.parametrize("target", ["UNKNOWN", "EGFR"])
def test_ordinal_possessive_cannot_hide_explicit_target(target):
    from src.agent.tools.activity_input import parse_activity_input
    from src.agent.tools.activity_predictor_tool import ActivityPredictorTool
    with pytest.raises(ValueError):
        parse_activity_input({"query": "对第2个候选的 " + target + " 活性进行预测；靶点: PDE",
                              "smiles": ["CCN"]}, ActivityPredictorTool())
