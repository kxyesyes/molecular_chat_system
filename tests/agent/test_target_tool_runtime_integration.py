from __future__ import annotations

from src.agent.orchestrators import WorkflowStep
from src.agent.planning import WorkflowPlan
from src.agent.specialists import build_default_specialists
from src.agent.supervisor import SupervisorAgent
from src.agent.tooling import build_tool_registry
from src.agent.tools.target_database_tool import TargetDatabaseTool
from src.agent.workflows import WorkflowCatalog, WorkflowPolicy


class _ResolvedTargetService:
    def __init__(self):
        self.calls: list[object] = []

    def search_targets(self, query):
        self.calls.append(query)
        return {
            "status": "resolved",
            "results": [
                {
                    "gene_symbol": "EGFR",
                    "uniprot_id": "P00533",
                    "source": "fixture",
                    "source_record_id": "fixture-record",
                    "structure_count": 1,
                    "structure_evidence_status": "available",
                    "recommended_structures": [],
                }
            ],
            "lookup_path": ["fixture-local"],
            "evidence": [
                {"source": "fixture", "id": "fixture-record", "stale": False}
            ],
            "warnings": [],
        }

    def close(self):
        return None


class _TargetPlanner:
    def plan(self, context):
        return WorkflowPlan(
            workflow_name="target_database_search",
            steps=[
                WorkflowStep(
                    name="target_search",
                    tool_name="target_database_search",
                    input_data={"query": "EGFR"},
                    output_key="target",
                )
            ],
        )


def test_supervisor_uses_typed_target_adapter_for_raw_chat_tools():
    tool = TargetDatabaseTool()
    service = _ResolvedTargetService()
    tool._service = service
    policy = WorkflowPolicy(
        name="target_database_search",
        description="target search",
        allowed_tools=("target_database_search",),
    )
    supervisor = SupervisorAgent(
        tools={tool.name: tool},
        tool_registry=build_tool_registry([tool]),
        planner=_TargetPlanner(),
        catalog=WorkflowCatalog((policy,)),
        specialists=build_default_specialists(),
    )

    response = supervisor.execute("搜索 EGFR 结构", active_skill=policy.name)

    assert response["success"] is True
    target_result = response["tool_results"]["target_database_search"]
    assert target_result["status"] == "succeeded"
    assert target_result["quality"]["lookup_status"] == "resolved"
    assert service.calls == ["EGFR"]
