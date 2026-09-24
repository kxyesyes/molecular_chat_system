"""Offline deterministic generation fixture; descriptors/ranking/SQLite are real."""
from src.agent.contracts import AgentContext, AgentErrorCode, ToolResult
from src.agent.orchestrators import WorkflowOrchestrator, WorkflowStep
from src.agent.persistence.sqlite_store import SQLiteAgentStateStore
from src.agent.tools.property_calculator import PropertyCalculator
from src.agent.tools.candidate_ranker import CandidateRanker
from src.web.scientific_references import ScientificReferenceService


class Generator:
    name = "llm_molecular_generator"

    def execute(self, query):
        return ToolResult.success_result(self.name,
            data=[{"smiles": "CCO"}, {"smiles": "CCN"}],
            quality={"requested_count": 2, "model": "offline-deterministic-fixture"})


class Unavailable:
    name = "activity_predictor"

    def __init__(self, name="activity_predictor"):
        self.name = name

    def execute(self, query):
        return ToolResult.error_result(self.name, code=AgentErrorCode.MODEL_UNAVAILABLE,
                                      message="Offline fixture: activity unavailable")


def execute(tmp_path, *, partial=False, target="PDE5A", partial_properties=False):
    store = SQLiteAgentStateStore(tmp_path / "report.sqlite")
    metadata = {"target_hint": target, "docking_top_n": 1, "requested_count": 2}
    steps = [WorkflowStep("generate", "llm_molecular_generator", output_key="molecules"),
             WorkflowStep("properties", "property_calculator",
                          input_binding="$.outputs.molecules", input_transform="smiles_text",
                          output_key="properties", metadata={"candidate_source": "molecules"})]
    if partial:
        steps.append(WorkflowStep("activity", "activity_predictor", input_data="CCO\nCCN",
                                  required=False, continue_on_error=True, output_key="activity"))
    steps.append(WorkflowStep("ranking", "candidate_ranker", input_binding="$.workflow",
        output_key="ranking", metadata={"docking_top_n": 1,
        "workflow_output_keys": ("molecules", "properties", "admet", "activity"),
        "workflow_optional_output_keys": ("admet", "activity"),
        "workflow_metadata_keys": ("docking_top_n",)}))
    class SubsetProperties(PropertyCalculator):
        def execute(self, query):
            raw = super().execute(query)
            raw["data"] = raw["data"][:1]  # Real descriptors, deliberately incomplete fixture.
            return raw
    result = WorkflowOrchestrator(state_store=store).run(
        context=AgentContext(query=f"design {target}", trace_id="report-fixture",
            session_id="owner", active_skill="target_driven_design", metadata=metadata, mol_count=2),
        steps=steps, tools={"llm_molecular_generator": Generator(),
            "property_calculator": SubsetProperties() if partial_properties else PropertyCalculator(), "candidate_ranker": CandidateRanker(),
            "activity_predictor": Unavailable()})
    execution = result.to_legacy_dict()
    execution.update(trace_id=result.trace_id, agent_result=result, status="partial" if result.partial else "completed",
        workflow_plan={"workflow_name": "target_driven_design",
                       "steps": [s.tool_name for s in steps], "metadata": metadata})
    events = ScientificReferenceService(store).project(execution, session_id="owner")
    assert events and "reference" in events[0]
    return store, execution, events


def execute_standard_plan(tmp_path):
    """Actual planner/compiler/executor/session; target/generation are fixtures only."""
    from src.agent.supervisor import SupervisorAgent
    from src.agent.tooling.factory import build_tool_registry
    class Target:
        name = "target_database_search"
        def execute(self, query):
            return {"success": True, "data": [{"gene_symbol": "PDE5A",
                "source_record_id": "LOCAL_PDE5A", "source": "local_target_db"}]}
    store = SQLiteAgentStateStore(tmp_path / "standard.sqlite")
    tools = {t.name: t for t in [Target(), Generator(), PropertyCalculator(), CandidateRanker(),
                                Unavailable(), Unavailable("admet_predictor")]}
    agent = SupervisorAgent(tools=tools, state_store=store, tool_registry=build_tool_registry(tools.values()))
    execution = agent.execute("基于 PDE5A 设计 2 个候选分子", active_skill="target_driven_design",
                              mol_count=2, session_id="owner")
    events = ScientificReferenceService(store).project(execution, session_id="owner")
    assert events and "reference" in events[0], {
        "status": execution.get("status"), "message": execution.get("message"),
        "steps": [(o.get("tool_name"), o.get("status"), o.get("error")) for o in execution.get("tool_result_sequence", [])]}
    return store, execution, events


def snapshot(store, execution, events):
    return store.get_scientific_report_snapshot(execution["trace_id"], session_id="owner",
                                               references=[e["reference"] for e in events])
